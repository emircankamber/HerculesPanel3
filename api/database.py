"""
Veritabanı katmanı — db_adapter üzerinden Postgres (paylaşımlı/kalıcı) ya da
SQLite (yerel test) ile çalışır. Bkz. db_adapter.py docstring.
"""
import json
import os
import time
import hashlib
import secrets
import db_adapter
from db_adapter import execute, execute_returning_id, execute_fetch, fetch_all, fetch_one, storage_info, USE_POSTGRES
import forum

CACHE_TTL_SECONDS = 24 * 3600

# Payload biçimi her değiştiğinde bu sürüm artırılır. Eski sürümle kaydedilmiş
# önbellek kayıtları otomatik geçersiz sayılır ve veri yeniden çekilir.
# (v2: pre_assessment kriterlerine "unit" alanı eklendi
#  v3: search_volume_trend eklendi (keyword'ün kendi arama hacmi grafiği) —
#  bu artış YAPILMADAN önce "Search Volume Trend" grafiği boş kalıyordu çünkü
#  v2'de damgalanmış eski kayıtlarda bu alan hiç yoktu, panel önbellekten
#  dönüyordu ve gerçek MCP çağrısı hiç yapılmıyordu.)
PAYLOAD_VERSION = 3

_SCHEMAS = [
    """CREATE TABLE IF NOT EXISTS user_thresholds (
        user_id INTEGER PRIMARY KEY, min_avg_price REAL, min_gross_margin REAL,
        max_acos REAL, max_brand_share REAL, min_strong_new_brands REAL,
        min_net_margin REAL, updated_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS keyword_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL, marketplace TEXT NOT NULL,
        fetched_at INTEGER NOT NULL, fetched_by TEXT, payload_json TEXT NOT NULL, verdict TEXT,
        UNIQUE(keyword, marketplace))""",
    """CREATE TABLE IF NOT EXISTS market_decision (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, keyword TEXT NOT NULL,
        marketplace TEXT NOT NULL, decision TEXT NOT NULL, note TEXT, decided_by TEXT,
        decided_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS user_query_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, keyword TEXT NOT NULL,
        marketplace TEXT NOT NULL, queried_at INTEGER NOT NULL, verdict TEXT)""",
    """CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL, salt TEXT NOT NULL, created_at INTEGER NOT NULL)""",
    # EĞİTİM & GÖREVLER — dersler, atamalar (assign_all=1 ise herkese), tamamlamalar
    """CREATE TABLE IF NOT EXISTS training_lessons (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT,
        video_url TEXT, sort_order INTEGER NOT NULL DEFAULT 0, due_date TEXT,
        assign_all INTEGER NOT NULL DEFAULT 1, created_by INTEGER,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS training_assignments (
        lesson_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        PRIMARY KEY (lesson_id, user_id))""",
    # ARAŞTIRMA KONTROL LİSTESİ — tek şablon (owner düzenler), ürün başına liste + maddeler.
    # Liste oluşturulurken şablonun ve analizin KOPYASI saklanır (sonradan MCP çağrısı yok).
    """CREATE TABLE IF NOT EXISTS checklist_template (
        id INTEGER PRIMARY KEY, template_json TEXT NOT NULL, updated_at INTEGER NOT NULL, updated_by TEXT)""",
    """CREATE TABLE IF NOT EXISTS checklists (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, user_email TEXT,
        analysis_key TEXT NOT NULL, marketplace TEXT NOT NULL, title TEXT,
        snapshot_json TEXT NOT NULL, stages_json TEXT NOT NULL, thresholds_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open', locked_by TEXT, locked_at INTEGER,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS checklist_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT, checklist_id INTEGER NOT NULL, stage_key TEXT NOT NULL,
        item_order INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL, auto_key TEXT, text TEXT NOT NULL,
        checked INTEGER NOT NULL DEFAULT 0, checked_by TEXT, checked_at INTEGER, note TEXT,
        created_by TEXT, created_at INTEGER NOT NULL)""",
    # Onay / kilit açma geçmişi: kim, ne zaman, neden (kilit açılsa da kayıt silinmez).
    """CREATE TABLE IF NOT EXISTS checklist_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, checklist_id INTEGER NOT NULL, kind TEXT NOT NULL,
        by_email TEXT, at INTEGER NOT NULL, reason TEXT, details_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS training_completions (
        lesson_id INTEGER NOT NULL, user_id INTEGER NOT NULL, completed_at INTEGER NOT NULL,
        PRIMARY KEY (lesson_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL, email TEXT NOT NULL, created_at INTEGER NOT NULL)""",
    # ÇOKLU EKİP: bir kişi birden fazla ekipte olabilir; hiçbir ekipte olmayan "ekip dışı".
    """CREATE TABLE IF NOT EXISTS teams (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS team_members (
        team_id INTEGER NOT NULL, user_id INTEGER NOT NULL, added_at INTEGER NOT NULL,
        PRIMARY KEY (team_id, user_id))""",
    # Davet: kod YALNIZCA sha256 olarak saklanır (düz kod bir kez, oluşturulurken döner).
    # max_uses NULL = çok kullanımlık. created_by = kullanıcı id (hesap silinince NULL).
    """CREATE TABLE IF NOT EXISTS team_invites (
        id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INTEGER NOT NULL, code_hash TEXT NOT NULL UNIQUE,
        created_by INTEGER, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
        max_uses INTEGER, uses INTEGER NOT NULL DEFAULT 0, revoked_at INTEGER)""",
    # Derslerin ekip ataması (assign_mode='teams'): DİNAMİK — üyelik o an hesaplanır.
    """CREATE TABLE IF NOT EXISTS training_lesson_teams (
        lesson_id INTEGER NOT NULL, team_id INTEGER NOT NULL, PRIMARY KEY (lesson_id, team_id))""",
    # Tek seferlik migrasyon işaretleri (ör. "Genel" ekibinin ilk kurulumu; ekipler sonradan
    # silinse bile migrasyon onu yeniden oluşturup herkesi eklemesin diye).
    # YETKİNLİK FORMU: kişi başına tek kayıt. Yalnızca kişinin kendisi ve owner'lar okur (index.py).
    # status: draft | submitted. Hesap silinince satır da silinir (USER_ID_TABLES).
    """CREATE TABLE IF NOT EXISTS competency_forms (
        user_id INTEGER PRIMARY KEY, answers_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, submitted_at INTEGER)""",
    # TRENDLER: owner'ın yasakladığı kategoriler (SellerSprite'ın döndürdüğü görünen ad, ör. "Kindle Store").
    # name_key = küçük harfli ad (harf duyarsız tekillik). banned_by = kullanıcı id (hesap silinince NULL).
    """CREATE TABLE IF NOT EXISTS banned_categories (
        name_key TEXT PRIMARY KEY, name TEXT NOT NULL, banned_by INTEGER, banned_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS schema_flags (
        key TEXT PRIMARY KEY, value TEXT, set_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS product_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL, marketplace TEXT NOT NULL,
        stage TEXT NOT NULL, market_score REAL, demand_score REAL, truth_score REAL,
        risk_score REAL, proof_score REAL, opportunity_score REAL, is_blue_ocean INTEGER,
        compliance_review_required INTEGER DEFAULT 0, weights_version TEXT, computed_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS proof_assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL, competitor_id TEXT,
        supplier_ref TEXT, type TEXT NOT NULL, file_url TEXT, points INTEGER,
        status TEXT DEFAULT 'pending', approved_by TEXT, approved_at INTEGER, note TEXT)""",
    """CREATE TABLE IF NOT EXISTS category_cert_requirements (
        id INTEGER PRIMARY KEY AUTOINCREMENT, category_key TEXT NOT NULL, cert_type TEXT NOT NULL,
        is_blocking INTEGER DEFAULT 1, note TEXT, approved_by_advisor INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS portfolio_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_at INTEGER NOT NULL, run_by TEXT,
        budget REAL, k_cat INTEGER, k_sup INTEGER, solver TEXT DEFAULT 'exact',
        solver_params TEXT, objective_value REAL, total_cost REAL, selected_json TEXT,
        explanation_text TEXT, explanation_status TEXT DEFAULT 'pending')""",
    """CREATE TABLE IF NOT EXISTS learning_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL, competitor_id TEXT,
        event_type TEXT NOT NULL, alpha_delta REAL, beta_delta REAL, alpha_after REAL,
        beta_after REAL, p_hat_after REAL, source TEXT, occurred_at INTEGER NOT NULL, recorded_by TEXT)""",
    """CREATE TABLE IF NOT EXISTS discovery_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_at INTEGER NOT NULL, lane TEXT NOT NULL,
        credits_used INTEGER DEFAULT 0, candidates_found INTEGER DEFAULT 0, params_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS discovery_candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT, discovery_run_id INTEGER, keyword TEXT NOT NULL,
        source_lane TEXT, keepa_flags_json TEXT, trends_score REAL, status TEXT DEFAULT 'new')""",
    """CREATE TABLE IF NOT EXISTS suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, contact TEXT, country TEXT,
        is_factory INTEGER DEFAULT 0, notes TEXT)""",
    """CREATE TABLE IF NOT EXISTS supplier_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_id INTEGER NOT NULL, scored_by TEXT,
        scored_at INTEGER NOT NULL, factory_verified INTEGER, moq_fit INTEGER, us_export INTEGER,
        fba_knowledge INTEGER, response_speed INTEGER, video_willingness INTEGER,
        cert_authenticity INTEGER, sample_quality INTEGER, price_stability INTEGER,
        total_score INTEGER, blocked INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS creative_deliverables (
        id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL, deliverable_no INTEGER NOT NULL,
        status TEXT DEFAULT 'pending', proof_asset_id INTEGER, owner TEXT, due_date TEXT,
        UNIQUE(keyword, deliverable_no))""",
    """CREATE TABLE IF NOT EXISTS launch_checkpoints (
        id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL, asin TEXT,
        checkpoint_day TEXT NOT NULL, ctr REAL, cvr REAL, acos REAL, net_margin REAL,
        review_avg REAL, review_count INTEGER, return_rate REAL, verdict TEXT,
        entered_by TEXT, source TEXT DEFAULT 'manual', created_at INTEGER NOT NULL)""",
]


async def _add_column_if_missing(table: str, column: str, coltype: str):
    """
    KRİTİK: CREATE TABLE IF NOT EXISTS zaten var olan tabloyu DEĞİŞTİRMEZ.
    Bu proje geliştirilirken şema birkaç kez değişti (örn. market_decision'a
    sonradan user_id eklendi) — daha önce paylaşımlı şemayla kurulmuş canlı bir
    Postgres/SQLite varsa, yeni kod eksik sütunu sorgulayınca çöker. Bu
    fonksiyon eksik sütunu güvenle (var olsa bile hata vermeden) ekler.
    """
    if USE_POSTGRES:
        try:
            await execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {coltype}")
        except Exception:
            pass  # sütun zaten varsa ya da yetki sorunu olsa bile akışı bozma
    else:
        try:
            await execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        except Exception:
            pass  # SQLite'ta "duplicate column" hatası -> sütun zaten var, sorun değil


async def _migrate_schema():
    """Eski dağıtımlardan kalan eksik sütunları tamamlar (bkz. _add_column_if_missing)."""
    await _add_column_if_missing("market_decision", "user_id", "INTEGER")
    # Roller: canlı Postgres'te users tablosu zaten var -> sütun migrasyonla eklenir.
    # Owner ATANABİLİR bir roldür (birden fazla olabilir); yetki her istekte bu
    # sütundan okunur. Migrasyon idempotent: boş/geçersiz roller member olur;
    # yalnızca HİÇ owner yoksa en küçük id'li kullanıcı owner yapılır — mevcut
    # owner'lara (ve ek owner'lara) asla dokunulmaz.
    await _add_column_if_missing("users", "role", "TEXT")
    # Kontrol listesi gerekçeli onay: canlıda checklists tablosu bu sütunlar olmadan kurulmuş olabilir.
    await _add_column_if_missing("checklists", "approval_reason", "TEXT")
    await _add_column_if_missing("checklists", "overridden_json", "TEXT")
    await execute("UPDATE users SET role = 'member' WHERE role IS NULL OR role NOT IN ('owner', 'admin', 'member')")
    # Kalıcı owner'lar (OWNER_EMAILS): listede olan mevcut kullanıcılar owner yapılır.
    # Değişken yoksa küme boş -> hiçbir şey yapılmaz.
    perm = sorted(permanent_owner_emails())
    if perm:
        marks = ",".join("?" for _ in perm)
        await execute(f"UPDATE users SET role = 'owner' WHERE LOWER(email) IN ({marks})", tuple(perm))
    await execute(
        "UPDATE users SET role = 'owner' WHERE id = (SELECT MIN(id) FROM users) "
        "AND NOT EXISTS (SELECT 1 FROM users WHERE role = 'owner')")
    # Eğitim ataması: all | teams | users (eski kayıtlar assign_all'dan türetilir)
    await _add_column_if_missing("training_lessons", "assign_mode", "TEXT")
    await execute("UPDATE training_lessons SET assign_mode = CASE WHEN assign_all = 1 THEN 'all' ELSE 'users' END "
                  "WHERE assign_mode IS NULL OR assign_mode NOT IN ('all', 'teams', 'users')")
    # EKİP HİYERARŞİSİ (iki seviye): parent_id NULL = ana ekip. Mevcut ekipler ana ekip kalır, üyelik değişmez.
    await _add_column_if_missing("teams", "parent_id", "INTEGER")
    await _seed_teams_once()
    await _ensure_staff_in_team()
    # PROFİL: ad/soyad (kayıtta zorunlu; mevcut kullanıcılar girene kadar panel kapalı — index.require_auth),
    # kullanıcı adı (benzersiz, girişi DEĞİŞTİRMEZ), unvan, telefon.
    for col in ("first_name", "last_name", "username", "title", "phone"):
        await _add_column_if_missing("users", col, "TEXT")
    try:
        await execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username ON users (LOWER(username))")
    except Exception:
        pass  # benzersizlik ayrıca kodda denetlenir
    await forum.seed_once()   # forum varsayılan kategorileri (bir kez)


async def _seed_teams_once():
    """
    ÇOKLU EKİP ilk kurulumu (bir kez): "Genel" ekibi oluşturulur ve MEVCUT tüm kullanıcılar eklenir.
    Önceki tekli `users.in_team` alanı varsa ona göre dönüştürülür (1/NULL -> Genel, 0 -> ekip dışı).
    İşaret satırı ÖNCE yazılır (PRIMARY KEY): aynı anda açılan iki sunucusuz örnekten yalnızca biri
    kurar; ekipler sonradan silinse bile migrasyon "Genel"i yeniden oluşturup herkesi eklemez.
    """
    if await fetch_one("SELECT 1 AS ok FROM schema_flags WHERE key = 'teams_seeded'"):
        return
    now = int(time.time())
    try:
        await execute("INSERT INTO schema_flags (key, value, set_at) VALUES ('teams_seeded', '1', ?)", (now,))
    except Exception:
        return  # başka bir örnek kuruyor
    gid = await execute_returning_id("INSERT INTO teams (name, created_at) VALUES (?, ?)", ("Genel", now))
    try:
        rows = await fetch_all("SELECT id, in_team FROM users")
        ids = [r["id"] for r in rows if r.get("in_team") is None or int(r["in_team"]) != 0]
    except Exception:   # in_team sütunu hiç yok (tekli ekip işi bu veritabanında çalışmadı) -> herkes
        ids = [r["id"] for r in await fetch_all("SELECT id FROM users")]
    for uid in ids:
        await _add_member(gid, uid)


async def _ensure_staff_in_team():
    """Owner/admin (ve OWNER_EMAILS) en az bir ekipte olmalı; değilse varsayılan ekibe eklenir."""
    rows = await fetch_all("SELECT id, email, role FROM users u WHERE NOT EXISTS "
                           "(SELECT 1 FROM team_members m WHERE m.user_id = u.id)")
    for r in rows:
        if _norm_role(r.get("role")) in ("owner", "admin") or _is_permanent(r.get("email")):
            await _add_member(await _default_team_id(), r["id"])


# ---------------------------------------------------------------------------
# ŞEMA SÜRÜMÜ — açılışta TEK sorgu.
# Sürüm güncelse (ve OWNER_EMAILS değişmediyse) hiçbir CREATE/ALTER/UPDATE çalışmaz.
# Değilse tüm migrasyonlar TEK bağlantıda çalışır ve sürüm yükseltilir.
# ŞEMAYA/MİGRASYONA HER DEĞİŞİKLİKTE (yeni tablo, sütun, indeks, veri düzeltmesi, tohum) BU SAYIYI ARTIR —
# artırmazsan canlı veritabanında migrasyon hiç çalışmaz.
# ---------------------------------------------------------------------------
SCHEMA_VERSION = 2  # v2: banned_categories


def _migration_env_hash() -> str:
    """Migrasyonun sonucunu etkileyen ortam ayarı: OWNER_EMAILS (kalıcı owner'lar owner yapılır ve
    ekibe eklenir). Değişirse sürüm aynı olsa da migrasyon yeniden çalışır. E-postalar değil özet saklanır."""
    return hashlib.sha256(",".join(sorted(permanent_owner_emails())).encode()).hexdigest()


async def _schema_is_current() -> bool:
    try:
        row = await fetch_one("SELECT version, env_hash FROM schema_version WHERE id = 1")
    except Exception:
        return False   # tablo yok (ilk kurulum ya da sürüm kapısından önceki veritabanı)
    return bool(row) and int(row.get("version") or 0) >= SCHEMA_VERSION \
        and row.get("env_hash") == _migration_env_hash()


async def init_db():
    if await _schema_is_current():
        return
    async with db_adapter.single_connection():
        for schema in _SCHEMAS + forum.SCHEMAS:
            await execute(schema)
        await _migrate_schema()
        await seed_cert_requirements_if_empty()
        await execute("CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, "
                      "env_hash TEXT, updated_at INTEGER)")
        await execute("INSERT INTO schema_version (id, version, env_hash, updated_at) VALUES (1, ?, ?, ?) "
                      "ON CONFLICT (id) DO UPDATE SET version = excluded.version, env_hash = excluded.env_hash, "
                      "updated_at = excluded.updated_at",
                      (SCHEMA_VERSION, _migration_env_hash(), int(time.time())))


async def init_db_v3():
    pass  # tüm şemalar artık init_db içinde


# ---------------------------------------------------------------------------
# KİMLİK DOĞRULAMA
# ---------------------------------------------------------------------------
def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()


async def create_user(email: str, password: str, invite_code: str | None = None,
                      first_name: str = "", last_name: str = "") -> dict:
    """
    Yeni kayıt HİÇBİR ekipte olmadan başlar (ekip dışı) — iki istisna:
    owner olarak kaydolan (ilk kullanıcı / OWNER_EMAILS) varsayılan ekibe girer (owner en az bir ekipte
    olmalı); geçerli bir davet koduyla kaydolan davetin ekibine katılır. Geçersiz/süresi dolmuş/iptal
    edilmiş davet kaydı ENGELLEMEZ, kişi ekip dışı başlar. Davet hesap oluşturulduktan SONRA kullanılır
    (e-posta zaten kayıtlıysa tek kullanımlık davet boşa harcanmasın).
    """
    email = email.strip().lower()
    existing = await fetch_one("SELECT id FROM users WHERE email = ?", (email,))
    if existing:
        raise ValueError("Bu e-posta zaten kayıtlı")
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(password, salt)
    role = "owner" if email in permanent_owner_emails() or await user_count() == 0 else "member"
    user_id = await execute_returning_id(
        "INSERT INTO users (email, password_hash, salt, created_at, role, first_name, last_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (email, pw_hash, salt, int(time.time()), role, first_name or None, last_name or None))
    if role == "owner":
        await _add_member(await _default_team_id(), user_id)
    invite = None
    if invite_code:
        team = await redeem_invite(invite_code)
        if team:
            await _add_member(team["id"], user_id)
            invite = {"status": "joined", "team": team["name"]}
        else:
            invite = {"status": "invalid"}
    return {"id": user_id, "email": email, "invite": invite}


async def verify_user(email: str, password: str) -> dict | None:
    email = email.strip().lower()
    user = await fetch_one("SELECT * FROM users WHERE email = ?", (email,))
    if not user:
        return None
    if _hash_password(password, user["salt"]) != user["password_hash"]:
        return None
    return {"id": user["id"], "email": user["email"]}


async def create_session(user: dict) -> str:
    token = secrets.token_urlsafe(32)
    await execute(
        "INSERT INTO sessions (token, user_id, email, created_at) VALUES (?, ?, ?, ?)",
        (token, user["id"], user["email"], int(time.time())))
    return token


async def get_session(token: str) -> dict | None:
    """Oturum + kullanıcı bilgisi TEK sorguda: ad/soyad (ad soyad kapısı), rol (get_user_role ile aynı
    kural: OWNER_EMAILS -> owner, geçersiz -> member; hesap yoksa None) ve en az bir ekipte olup olmadığı."""
    if not token:
        return None
    row = await fetch_one(
        "SELECT s.*, u.first_name, u.last_name, u.id AS u_id, u.email AS u_email, u.role AS u_role, "
        "EXISTS (SELECT 1 FROM team_members m WHERE m.user_id = s.user_id) AS u_in_team "
        "FROM sessions s LEFT JOIN users u ON u.id = s.user_id WHERE s.token = ?", (token,))
    if not row:
        return None
    exists = row.pop("u_id") is not None
    email, role, in_team = row.pop("u_email"), row.pop("u_role"), row.pop("u_in_team")
    row["role"] = ("owner" if _is_permanent(email) else _norm_role(role)) if exists else None
    row["in_team"] = bool(in_team)
    return row


async def delete_session(token: str):
    await execute("DELETE FROM sessions WHERE token = ?", (token,))


async def user_count() -> int:
    row = await fetch_one("SELECT COUNT(*) AS c FROM users")
    return (row or {}).get("c", 0) or 0


# Kullanıcı olduğu bir kez görülünce bellekte tutulur (her istekte COUNT yok). Güvenli: kullanıcı sayısı
# sıfıra dönemez (kendi hesabını ve son owner'ı silmek yasak). Henüz kullanıcı yokken her seferinde sorulur.
_users_seen = False


async def has_users() -> bool:
    global _users_seen
    if not _users_seen and await user_count() > 0:
        _users_seen = True
    return _users_seen


# ---------------------------------------------------------------------------
# ANALİZ ÖNBELLEĞİ & GEÇMİŞ
# ---------------------------------------------------------------------------
async def get_cached(keyword: str, marketplace: str):
    row = await fetch_one(
        "SELECT * FROM keyword_analysis WHERE keyword = ? AND marketplace = ?", (keyword, marketplace))
    if not row:
        return None
    if time.time() - row["fetched_at"] > CACHE_TTL_SECONDS:
        return None
    payload = json.loads(row["payload_json"])
    # Eski biçimde kaydedilmiş kayıtları kullanma — yeniden çekilsin
    if payload.get("_v") != PAYLOAD_VERSION:
        return None
    return payload


async def save_analysis(keyword: str, marketplace: str, payload: dict, fetched_by: str = None):
    payload = {**payload, "_v": PAYLOAD_VERSION}
    verdict = payload.get("pre_assessment", {}).get("verdict")
    payload_json = json.dumps(payload)
    now = int(time.time())
    if USE_POSTGRES:
        sql = """INSERT INTO keyword_analysis (keyword, marketplace, fetched_at, fetched_by, payload_json, verdict)
                 VALUES (?, ?, ?, ?, ?, ?)
                 ON CONFLICT (keyword, marketplace) DO UPDATE SET
                 fetched_at = EXCLUDED.fetched_at, fetched_by = EXCLUDED.fetched_by,
                 payload_json = EXCLUDED.payload_json, verdict = EXCLUDED.verdict"""
    else:
        sql = """INSERT INTO keyword_analysis (keyword, marketplace, fetched_at, fetched_by, payload_json, verdict)
                 VALUES (?, ?, ?, ?, ?, ?)
                 ON CONFLICT(keyword, marketplace) DO UPDATE SET
                 fetched_at=excluded.fetched_at, fetched_by=excluded.fetched_by,
                 payload_json=excluded.payload_json, verdict=excluded.verdict"""
    await execute(sql, (keyword, marketplace, now, fetched_by, payload_json, verdict))


async def log_user_query(user_id: int, keyword: str, marketplace: str, verdict: str = None):
    """
    Kişiye özel 'Geçmiş' kaydı. Ham MCP verisi (keyword_analysis) PAYLAŞIMLIDIR
    (kota tasarrufu için — aynı keyword'ü iki kullanıcı sorgularsa tekrar
    SellerSprite'a gidilmez), ama "kim ne baktı" kaydı tamamen kişiye özeldir.
    """
    await execute(
        "INSERT INTO user_query_log (user_id, keyword, marketplace, queried_at, verdict) VALUES (?,?,?,?,?)",
        (user_id, keyword, marketplace, int(time.time()), verdict))


async def list_recent(user_id: int, limit: int = 50):
    """Yalnızca BU kullanıcının sorguladığı keyword'leri döner — herkese özel."""
    return await fetch_all(
        "SELECT id, keyword, marketplace, queried_at AS fetched_at, verdict FROM user_query_log "
        "WHERE user_id = ? ORDER BY queried_at DESC LIMIT ?", (user_id, limit))


async def delete_analysis(user_id: int, keyword: str, marketplace: str):
    """Yalnızca kullanıcının KENDİ geçmiş kaydını siler (paylaşımlı ham veriye dokunmaz)."""
    await execute(
        "DELETE FROM user_query_log WHERE user_id = ? AND keyword = ? AND marketplace = ?",
        (user_id, keyword, marketplace))


async def clear_all_analyses(user_id: int):
    await execute("DELETE FROM user_query_log WHERE user_id = ?", (user_id,))


# ---------------------------------------------------------------------------
# PAZAR KARARLARI — tamamen kullanıcıya özel
# ---------------------------------------------------------------------------
async def save_decision(user_id: int, keyword: str, marketplace: str, decision: str, note: str, decided_by: str):
    await execute(
        "INSERT INTO market_decision (user_id, keyword, marketplace, decision, note, decided_by, decided_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, keyword, marketplace, decision, note, decided_by, int(time.time())))


async def list_decisions_grouped(user_id: int):
    """Yalnızca BU kullanıcının kararlarını, her (keyword, marketplace) için EN SONU alıp gruplar."""
    rows = await fetch_all("""
        SELECT md.id, md.keyword, md.marketplace, md.decision, md.note, md.decided_by, md.decided_at
        FROM market_decision md
        WHERE md.user_id = ? AND md.id = (
            SELECT md2.id FROM market_decision md2
            WHERE md2.user_id = ? AND md2.keyword = md.keyword AND md2.marketplace = md.marketplace
            ORDER BY md2.decided_at DESC, md2.id DESC LIMIT 1)
        ORDER BY md.decided_at DESC
    """, (user_id, user_id))
    grouped = {"Uygun": [], "Sınırda": [], "Elenmiş": []}
    for r in rows:
        grouped.setdefault(r["decision"], []).append(r)
    return grouped


async def delete_decision(user_id: int, keyword: str, marketplace: str):
    """Yalnızca kullanıcının KENDİ kararını siler — başkasının kararına dokunamaz."""
    await execute(
        "DELETE FROM market_decision WHERE user_id = ? AND keyword = ? AND marketplace = ?",
        (user_id, keyword, marketplace))


async def delete_decisions_for(user_id: int, key: str, marketplace: str) -> int:
    """Geçmişten silinen ürünün kararlarını da siler (yalnızca kişinin KENDİ kayıtları). key = sorgu anahtarı
    ("ASIN:B0.." ya da keyword); eski panelin ASIN kararları "B0.. — başlık" biçiminde de olabilir → onlar da."""
    conds, params = ["LOWER(keyword) = LOWER(?)"], [key]
    if key[:5].upper() == "ASIN:":
        asin = key[5:].strip().upper()
        conds.append("UPPER(keyword) LIKE ?")
        params.append(f"{asin} —%")
    rows = await execute_fetch(
        f"DELETE FROM market_decision WHERE user_id = ? AND marketplace = ? AND ({' OR '.join(conds)}) RETURNING id",
        (user_id, marketplace, *params))
    return len(rows)


async def clear_all_decisions(user_id: int):
    await execute("DELETE FROM market_decision WHERE user_id = ?", (user_id,))


# ---------------------------------------------------------------------------
# EKİP AKTİVİTESİ — yalnızca owner'lar (yetki index.py'de require_owner). SALT OKUNUR:
# burada yazma/silme fonksiyonu yok; kayıtlar sahibinin kendi uçlarıyla (kendi user_id'si) değişir.
# ---------------------------------------------------------------------------
def _range_sql(col: str, user_id, since, until):
    where, args = [], []
    if user_id is not None:
        where.append("q.user_id = ?"); args.append(user_id)
    if since is not None:
        where.append(f"q.{col} >= ?"); args.append(since)
    if until is not None:
        where.append(f"q.{col} < ?"); args.append(until)
    return (" WHERE " + " AND ".join(where)) if where else "", args


async def team_queries(user_id: int | None = None, since: int | None = None, until: int | None = None) -> list[dict]:
    where, args = _range_sql("queried_at", user_id, since, until)
    return await fetch_all(
        "SELECT q.id, q.user_id, u.email, q.keyword, q.marketplace, q.queried_at, q.verdict "
        f"FROM user_query_log q LEFT JOIN users u ON u.id = q.user_id{where} "
        "ORDER BY q.queried_at DESC, q.id DESC", tuple(args))


async def team_decisions(user_id: int | None = None, since: int | None = None, until: int | None = None,
                         decision: str | None = None) -> list[dict]:
    where, args = _range_sql("decided_at", user_id, since, until)
    if decision:
        where += (" AND " if where else " WHERE ") + "q.decision = ?"
        args.append(decision)
    return await fetch_all(
        "SELECT q.id, q.user_id, u.email, q.keyword, q.marketplace, q.decision, q.note, q.decided_at "
        f"FROM market_decision q LEFT JOIN users u ON u.id = q.user_id{where} "
        "ORDER BY q.decided_at DESC, q.id DESC", tuple(args))


async def team_verdict_log(keywords=None) -> list[dict]:
    """Kararlara 'ön öneri' eşlemek için (user_id, keyword, marketplace) -> sorgu verdict'leri.
    keywords verilirse yalnızca o keyword'lerin sorguları (sayfadaki kararlar için; boş liste -> hiç)."""
    if keywords is not None:
        keywords = sorted(set(keywords))
        if not keywords:
            return []
        marks = ",".join("?" for _ in keywords)
        return await fetch_all("SELECT user_id, keyword, marketplace, queried_at, verdict FROM user_query_log "
                               f"WHERE verdict IS NOT NULL AND keyword IN ({marks}) ORDER BY queried_at", tuple(keywords))
    return await fetch_all("SELECT user_id, keyword, marketplace, queried_at, verdict FROM user_query_log "
                           "WHERE verdict IS NOT NULL ORDER BY queried_at")


async def team_counts(since: int | None = None, until: int | None = None) -> dict:
    """Kişi başına arama/karar sayısı; aralık verilmezse TÜM ZAMANLAR."""
    def rng(col):
        where, args = [], []
        if since is not None: where.append(f"{col} >= ?"); args.append(since)
        if until is not None: where.append(f"{col} < ?"); args.append(until)
        return (" WHERE " + " AND ".join(where)) if where else "", tuple(args)
    wq, aq = rng("queried_at"); wd, ad = rng("decided_at")
    q = await fetch_all(f"SELECT user_id, COUNT(*) AS c FROM user_query_log{wq} GROUP BY user_id", aq)
    d = await fetch_all(f"SELECT user_id, COUNT(*) AS c FROM market_decision{wd} GROUP BY user_id", ad)
    return {"queries": {r["user_id"]: r["c"] for r in q}, "decisions": {r["user_id"]: r["c"] for r in d}}


# ---------------------------------------------------------------------------
# SIGNAL ENGINE / PROOF / SERTİFİKA
# ---------------------------------------------------------------------------
async def save_product_signals(keyword: str, marketplace: str, stage: str, signals: dict):
    await execute("""INSERT INTO product_signals
        (keyword, marketplace, stage, market_score, demand_score, truth_score, risk_score,
         proof_score, opportunity_score, is_blue_ocean, compliance_review_required,
         weights_version, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (keyword, marketplace, stage, signals.get("market_score"), signals.get("demand_score"),
         signals.get("truth_score"), signals.get("risk_score"), signals.get("proof_score"),
         signals.get("opportunity_score"), int(bool(signals.get("is_blue_ocean"))),
         int(bool(signals.get("compliance_review_required"))),
         signals.get("weights_version", "v2.1-manual"), int(time.time())))


async def add_proof_asset(keyword: str, type_: str, points: int, file_url: str = None,
                           competitor_id: str = None, supplier_ref: str = None, note: str = None):
    return await execute_returning_id(
        "INSERT INTO proof_assets (keyword, competitor_id, supplier_ref, type, file_url, points, note) "
        "VALUES (?,?,?,?,?,?,?)",
        (keyword, competitor_id, supplier_ref, type_, file_url, points, note))


async def approve_proof_asset(asset_id: int, approved_by: str):
    await execute("UPDATE proof_assets SET status='approved', approved_by=?, approved_at=? WHERE id=?",
                  (approved_by, int(time.time()), asset_id))


async def list_proof_assets(keyword: str):
    return await fetch_all("SELECT * FROM proof_assets WHERE keyword=?", (keyword,))


async def delete_proof_asset(asset_id: int):
    await execute("DELETE FROM proof_assets WHERE id=?", (asset_id,))


async def get_cert_requirements(category_key: str):
    return await fetch_all(
        "SELECT * FROM category_cert_requirements WHERE category_key=? AND is_blocking=1", (category_key,))


async def seed_cert_requirements_if_empty():
    row = await fetch_one("SELECT COUNT(*) AS c FROM category_cert_requirements")
    if (row or {}).get("c", 0):
        return
    seed = [
        ("water_filtration", "NSF/ANSI 42-53 lab test raporu", 1, "Belge adları örnektir, danışman onayı gerekir"),
        ("air_purifier", "CARB / UL 2998 + elektrik güvenlik sertifikası", 1, ""),
        ("vitamin_showerhead", "Cilt teması güvenlik/malzeme raporu", 1, ""),
        ("supplement", "Danışman + tam regülasyon incelemesi", 1, "Faz 3 — kategoriye giriş şu an kapalı"),
    ]
    for s in seed:
        await execute("INSERT INTO category_cert_requirements (category_key, cert_type, is_blocking, note) "
                      "VALUES (?,?,?,?)", s)


# ---------------------------------------------------------------------------
# PORTFOLIO / LEARNING / DISCOVERY / SUPPLIER / CREATIVE / LAUNCH
# ---------------------------------------------------------------------------
async def save_portfolio_run(budget, k_cat, k_sup, result: dict, run_by: str = None) -> int:
    return await execute_returning_id("""INSERT INTO portfolio_runs
        (run_at, run_by, budget, k_cat, k_sup, solver, objective_value, total_cost, selected_json, explanation_status)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (int(time.time()), run_by, budget, k_cat, k_sup, result.get("solver", "exact"),
         result.get("objective_value"), result.get("total_cost"),
         json.dumps(result.get("selected", [])), "pending"))


async def update_portfolio_explanation(run_id: int, text: str, status: str):
    await execute("UPDATE portfolio_runs SET explanation_text=?, explanation_status=? WHERE id=?",
                  (text, status, run_id))


async def get_portfolio_run(run_id: int):
    return await fetch_one("SELECT * FROM portfolio_runs WHERE id=?", (run_id,))


async def record_learning_event(keyword: str, event_type: str, alpha_delta: float, beta_delta: float,
                                 alpha_after: float, beta_after: float, p_hat_after: float,
                                 source: str = None, recorded_by: str = None):
    await execute("""INSERT INTO learning_events
        (keyword, event_type, alpha_delta, beta_delta, alpha_after, beta_after, p_hat_after,
         source, occurred_at, recorded_by) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (keyword, event_type, alpha_delta, beta_delta, alpha_after, beta_after, p_hat_after,
         source, int(time.time()), recorded_by))


async def get_latest_learning_state(keyword: str):
    return await fetch_one(
        "SELECT * FROM learning_events WHERE keyword=? ORDER BY occurred_at DESC LIMIT 1", (keyword,))


async def create_discovery_run(lane: str, params: dict) -> int:
    return await execute_returning_id(
        "INSERT INTO discovery_runs (run_at, lane, params_json) VALUES (?,?,?)",
        (int(time.time()), lane, json.dumps(params)))


async def add_discovery_candidate(run_id: int, keyword: str, source_lane: str,
                                   keepa_flags: dict = None, trends_score: float = None):
    await execute("INSERT INTO discovery_candidates (discovery_run_id, keyword, source_lane, keepa_flags_json, trends_score) "
                  "VALUES (?,?,?,?,?)", (run_id, keyword, source_lane, json.dumps(keepa_flags or {}), trends_score))
    await execute("UPDATE discovery_runs SET candidates_found = candidates_found + 1 WHERE id=?", (run_id,))


async def list_discovery_candidates(run_id: int = None, status: str = None):
    q = "SELECT * FROM discovery_candidates WHERE 1=1"
    params = []
    if run_id:
        q += " AND discovery_run_id=?"
        params.append(run_id)
    if status:
        q += " AND status=?"
        params.append(status)
    return await fetch_all(q, tuple(params))


async def upsert_supplier(name: str, contact: str = None, country: str = None,
                           is_factory: bool = False, notes: str = None) -> int:
    return await execute_returning_id(
        "INSERT INTO suppliers (name, contact, country, is_factory, notes) VALUES (?,?,?,?,?)",
        (name, contact, country, int(is_factory), notes))


async def save_supplier_score(supplier_id: int, scored_by: str, scores: dict, total: int, blocked: bool) -> int:
    return await execute_returning_id("""INSERT INTO supplier_scores
        (supplier_id, scored_by, scored_at, factory_verified, moq_fit, us_export, fba_knowledge,
         response_speed, video_willingness, cert_authenticity, sample_quality, price_stability,
         total_score, blocked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (supplier_id, scored_by, int(time.time()), scores.get("factory_verified", 0),
         scores.get("moq_fit", 0), scores.get("us_export", 0), scores.get("fba_knowledge", 0),
         scores.get("response_speed", 0), scores.get("video_willingness", 0),
         scores.get("cert_authenticity", 0), scores.get("sample_quality", 0),
         scores.get("price_stability", 0), total, int(blocked)))


async def upsert_creative_deliverable(keyword: str, deliverable_no: int, status: str = "pending",
                                       owner: str = None, due_date: str = None):
    if USE_POSTGRES:
        sql = """INSERT INTO creative_deliverables (keyword, deliverable_no, status, owner, due_date)
                 VALUES (?,?,?,?,?) ON CONFLICT (keyword, deliverable_no) DO UPDATE SET
                 status = EXCLUDED.status, owner = EXCLUDED.owner, due_date = EXCLUDED.due_date"""
    else:
        sql = """INSERT INTO creative_deliverables (keyword, deliverable_no, status, owner, due_date)
                 VALUES (?,?,?,?,?) ON CONFLICT(keyword, deliverable_no) DO UPDATE SET
                 status=excluded.status, owner=excluded.owner, due_date=excluded.due_date"""
    await execute(sql, (keyword, deliverable_no, status, owner, due_date))


async def list_creative_deliverables(keyword: str):
    return await fetch_all("SELECT * FROM creative_deliverables WHERE keyword=? ORDER BY deliverable_no", (keyword,))


async def save_launch_checkpoint(keyword: str, asin: str, checkpoint_day: str, metrics: dict,
                                  verdict: str, entered_by: str, source: str = "manual") -> int:
    return await execute_returning_id("""INSERT INTO launch_checkpoints
        (keyword, asin, checkpoint_day, ctr, cvr, acos, net_margin, review_avg, review_count,
         return_rate, verdict, entered_by, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (keyword, asin, checkpoint_day, metrics.get("ctr"), metrics.get("cvr"), metrics.get("acos"),
         metrics.get("net_margin"), metrics.get("review_avg"), metrics.get("review_count"),
         metrics.get("return_rate"), verdict, entered_by, source, int(time.time())))


async def list_launch_checkpoints(keyword: str):
    return await fetch_all("SELECT * FROM launch_checkpoints WHERE keyword=? ORDER BY created_at", (keyword,))


async def get_hit_rate():
    rows = await fetch_all("SELECT verdict, COUNT(*) AS c FROM launch_checkpoints GROUP BY verdict")
    return {r["verdict"]: r["c"] for r in rows}


# ---------------------------------------------------------------------------
# KULLANICIYA ÖZEL EŞİK DEĞERLERİ (Ayarlar sayfası)
# ---------------------------------------------------------------------------
_THRESHOLD_KEYS = ["min_avg_price", "min_gross_margin", "max_acos",
                    "max_brand_share", "min_strong_new_brands", "min_net_margin"]


async def get_user_thresholds(user_id: int) -> dict:
    """
    Kullanıcının özelleştirdiği eşikleri döner (yalnızca override ettiği
    alanlar, None olanlar hariç) — boş dict dönerse kullanıcı hiç
    özelleştirme yapmamış demektir, çağıran taraf DEFAULT_THRESHOLDS'a düşer.
    """
    row = await fetch_one("SELECT * FROM user_thresholds WHERE user_id = ?", (user_id,))
    if not row:
        return {}
    return {k: row[k] for k in _THRESHOLD_KEYS if row.get(k) is not None}


async def save_user_thresholds(user_id: int, thresholds: dict):
    """
    Kısmi güncelleme kabul eder — yalnızca gönderilen alanlar değişir,
    gönderilmeyenler (None) mevcut değerini korur (upsert mantığı).
    """
    existing = await fetch_one("SELECT * FROM user_thresholds WHERE user_id = ?", (user_id,))
    merged = {k: (existing.get(k) if existing else None) for k in _THRESHOLD_KEYS}
    for k, v in thresholds.items():
        if k in _THRESHOLD_KEYS and v is not None:
            merged[k] = v

    now = int(time.time())
    if USE_POSTGRES:
        sql = f"""INSERT INTO user_thresholds (user_id, {', '.join(_THRESHOLD_KEYS)}, updated_at)
                  VALUES (?, {', '.join(['?'] * len(_THRESHOLD_KEYS))}, ?)
                  ON CONFLICT (user_id) DO UPDATE SET
                  {', '.join(f'{k} = EXCLUDED.{k}' for k in _THRESHOLD_KEYS)}, updated_at = EXCLUDED.updated_at"""
    else:
        sql = f"""INSERT INTO user_thresholds (user_id, {', '.join(_THRESHOLD_KEYS)}, updated_at)
                  VALUES (?, {', '.join(['?'] * len(_THRESHOLD_KEYS))}, ?)
                  ON CONFLICT(user_id) DO UPDATE SET
                  {', '.join(f'{k}=excluded.{k}' for k in _THRESHOLD_KEYS)}, updated_at=excluded.updated_at"""
    await execute(sql, (user_id, *[merged[k] for k in _THRESHOLD_KEYS], now))


async def reset_user_thresholds(user_id: int):
    """Tüm özelleştirmeleri siler, kullanıcı DEFAULT_THRESHOLDS'a döner."""
    await execute("DELETE FROM user_thresholds WHERE user_id = ?", (user_id,))


# ---------------------------------------------------------------------------
# ROLLER (owner / admin / member)
# Owner atanabilir bir roldür, birden fazla owner olabilir. Yetki HER İSTEKTE
# users.role sütunundan okunur. Kilitlenme koruması: son kalan owner düşürülemez.
# ---------------------------------------------------------------------------
VALID_ROLES = ("owner", "admin", "member")


class LastOwnerError(Exception):
    """Son kalan owner'ı düşürme girişimi."""


class PermanentOwnerError(Exception):
    """OWNER_EMAILS'teki kalıcı owner'ı düşürme girişimi."""


def permanent_owner_emails() -> frozenset[str]:
    """
    Kalıcı owner'lar Vercel ortam değişkeni OWNER_EMAILS'ten okunur (virgülle ayrılmış).
    E-postalar koda YAZILMAZ (repo herkese açık). İstek anında okunur; yoksa boş küme
    -> hiçbir davranış değişmez.
    """
    raw = os.environ.get("OWNER_EMAILS") or ""
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def _is_permanent(email) -> bool:
    return bool(email) and str(email).strip().lower() in permanent_owner_emails()


def _norm_role(role) -> str:
    return role if role in VALID_ROLES else "member"


async def get_user_role(user_id: int) -> str | None:
    row = await fetch_one("SELECT role, email FROM users WHERE id = ?", (user_id,))
    if not row:
        return None
    return "owner" if _is_permanent(row.get("email")) else _norm_role(row.get("role"))


async def owner_count() -> int:
    row = await fetch_one("SELECT COUNT(*) AS c FROM users WHERE role = 'owner'")
    return (row or {}).get("c", 0) or 0


async def list_users() -> list[dict]:
    rows = await fetch_all("SELECT id, email, role, created_at, first_name, last_name, username, title FROM users ORDER BY id")
    teams = await _memberships()
    parent = await _parents()
    return [{"id": r["id"], "email": r["email"], "created_at": r["created_at"],
             "first_name": r.get("first_name") or "", "last_name": r.get("last_name") or "",
             "name": display_name(r), "username": r.get("username") or "", "title": r.get("title") or "",
             "role": "owner" if _is_permanent(r["email"]) else _norm_role(r.get("role")),
             "permanent": _is_permanent(r["email"]),
             # team_ids = ETKİN üyelik (doğrudan + alt ekip üzerinden ana ekip); direct_team_ids = kayıtlı satırlar
             "team_ids": _effective(teams.get(r["id"], []), parent),
             "direct_team_ids": sorted(teams.get(r["id"], [])),
             "in_team": bool(teams.get(r["id"]))} for r in rows]


def display_name(row: dict | None) -> str:
    """Panelde e-posta yerine gösterilen ad: "Ad Soyad" (girilmemişse boş)."""
    if not row:
        return ""
    return " ".join(x for x in ((row.get("first_name") or "").strip(), (row.get("last_name") or "").strip()) if x)


async def email_name_map() -> dict[str, str]:
    """Kayıtlarda e-postayla anılan kişileri (onaylayan, işaretleyen…) ada çevirmek için: e-posta -> Ad Soyad."""
    rows = await fetch_all("SELECT email, first_name, last_name FROM users")
    return {(r["email"] or "").strip().lower(): display_name(r) for r in rows}


# ---------------------------------------------------------------------------
# PROFİL (yalnızca kişinin kendisi)
# ---------------------------------------------------------------------------
class UsernameTakenError(ValueError):
    pass


async def get_profile(user_id: int) -> dict | None:
    r = await fetch_one("SELECT id, email, role, created_at, first_name, last_name, username, title, phone "
                        "FROM users WHERE id = ?", (user_id,))
    if not r:
        return None
    labels = {t["id"]: t["label"] for t in await list_teams()}
    return {"id": r["id"], "email": r["email"], "created_at": r["created_at"],
            "first_name": r.get("first_name") or "", "last_name": r.get("last_name") or "", "name": display_name(r),
            "username": r.get("username") or "", "title": r.get("title") or "", "phone": r.get("phone") or "",
            "teams": [{"id": t, "name": labels[t]} for t in await user_team_ids(user_id) if t in labels]}


async def update_profile(user_id: int, fields: dict):
    """Yalnızca verilen alanlar güncellenir. Kullanıcı adı büyük/küçük harf duyarsız benzersiz (UsernameTakenError);
    benzersiz indeks yarışa karşı ikinci güvence."""
    if "username" in fields and fields["username"]:
        row = await fetch_one("SELECT id FROM users WHERE LOWER(username) = ? AND id <> ?",
                              (fields["username"].lower(), user_id))
        if row:
            raise UsernameTakenError("Bu kullanıcı adı başka biri tarafından kullanılıyor")
    cols = [c for c in ("first_name", "last_name", "username", "title", "phone") if c in fields]
    if not cols:
        return
    try:
        await execute(f"UPDATE users SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                      tuple((fields[c] or None) for c in cols) + (user_id,))
    except Exception as e:
        if "username" in cols and ("unique" in str(e).lower() or "ux_users_username" in str(e)):
            raise UsernameTakenError("Bu kullanıcı adı başka biri tarafından kullanılıyor")
        raise


async def change_password(user_id: int, current: str, new: str, keep_token: str) -> bool:
    """Mevcut şifre doğruysa değiştirir ve bu oturum DIŞINDAKİ tüm oturumları kapatır."""
    user = await fetch_one("SELECT password_hash, salt FROM users WHERE id = ?", (user_id,))
    if not user or _hash_password(current, user["salt"]) != user["password_hash"]:
        return False
    salt = secrets.token_hex(16)
    await execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?", (_hash_password(new, salt), salt, user_id))
    await execute("DELETE FROM sessions WHERE user_id = ? AND token <> ?", (user_id, keep_token))
    return True


# ---------------------------------------------------------------------------
# YETKİNLİK FORMU
# ---------------------------------------------------------------------------
async def get_competency(user_id: int) -> dict | None:
    r = await fetch_one("SELECT * FROM competency_forms WHERE user_id = ?", (user_id,))
    if not r:
        return None
    r["answers"] = json.loads(r.pop("answers_json") or "{}")
    return r


async def save_competency(user_id: int, answers: dict):
    now = int(time.time())
    await execute(
        "INSERT INTO competency_forms (user_id, answers_json, status, created_at, updated_at) VALUES (?, ?, 'draft', ?, ?) "
        "ON CONFLICT (user_id) DO UPDATE SET answers_json = excluded.answers_json, updated_at = excluded.updated_at",
        (user_id, json.dumps(answers, ensure_ascii=False), now, now))


async def submit_competency(user_id: int) -> int:
    now = int(time.time())
    await execute("UPDATE competency_forms SET status = 'submitted', submitted_at = ? WHERE user_id = ?", (now, user_id))
    return now


async def all_competency() -> dict[int, dict]:
    out = {}
    for r in await fetch_all("SELECT * FROM competency_forms"):
        r["answers"] = json.loads(r.pop("answers_json") or "{}")
        out[r["user_id"]] = r
    return out


class TeamRuleError(Exception):
    """Ekip kuralı ihlali (owner/admin yalnızca en az bir ekipte; son ekibinden çıkarılamaz)."""


class TeamNameError(ValueError):
    """Ekip adı boş/uzun ya da başka bir ekiple aynı."""


STAFF_ROLES = ("owner", "admin")


# ---------------------------------------------------------------------------
# ÇOKLU EKİP
# ---------------------------------------------------------------------------
async def _add_member(team_id: int, user_id: int):
    await execute("INSERT INTO team_members (team_id, user_id, added_at) VALUES (?, ?, ?) "
                  "ON CONFLICT (team_id, user_id) DO NOTHING", (team_id, user_id, int(time.time())))


async def _default_team_id() -> int:
    """En eski ekip; hiç ekip yoksa "Genel" oluşturulur (owner'ın girebileceği bir ekip her zaman olsun)."""
    row = await fetch_one("SELECT id FROM teams ORDER BY id LIMIT 1")
    if row:
        return row["id"]
    return await execute_returning_id("INSERT INTO teams (name, created_at) VALUES (?, ?)", ("Genel", int(time.time())))


async def _memberships() -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for r in await fetch_all("SELECT team_id, user_id FROM team_members"):
        out.setdefault(r["user_id"], []).append(r["team_id"])
    return out


async def _parents() -> dict[int, int | None]:
    return {r["id"]: r.get("parent_id") for r in await fetch_all("SELECT id, parent_id FROM teams")}


def _effective(direct: list[int], parent: dict) -> list[int]:
    """Alt ekip üyeliği ana ekip üyeliğini de içerir (kalıtım; ayrı satır yazılmaz)."""
    out = set(direct)
    out.update(parent[t] for t in direct if parent.get(t))
    return sorted(out)


async def user_team_ids(user_id: int) -> list[int]:
    return sorted(r["team_id"] for r in await fetch_all("SELECT team_id FROM team_members WHERE user_id = ?", (user_id,)))


async def is_in_team(user_id: int) -> bool:
    return bool(await fetch_one("SELECT 1 AS ok FROM team_members WHERE user_id = ? LIMIT 1", (user_id,)))


async def team_member_ids(team_id: int | None = None) -> set[int]:
    """team_id verilirse o ekibin ETKİN üyeleri: ana ekipte doğrudan üyeler + tüm alt ekiplerinin üyeleri,
    alt ekipte yalnızca kendi üyeleri. Verilmezse EN AZ BİR ekipte olan herkes (ekip dışı = hiç satırı yok)."""
    if team_id is None:
        rows = await fetch_all("SELECT DISTINCT user_id FROM team_members")
    else:
        rows = await fetch_all("SELECT m.user_id FROM team_members m JOIN teams t ON t.id = m.team_id "
                               "WHERE t.id = ? OR t.parent_id = ?", (team_id, team_id))
    return {r["user_id"] for r in rows}


async def get_team(team_id: int) -> dict | None:
    return await fetch_one("SELECT id, name, parent_id, created_at FROM teams WHERE id = ?", (team_id,))


TEAM_SEP = " › "


async def list_teams() -> list[dict]:
    """Ağaç sırasıyla (ana ekip, ardından alt ekipleri). label = "Ana › Alt". Sayılar: direct_count = doğrudan üye,
    member_count = ETKİN üye (ana ekipte doğrudan + alt ekipler, tekil kişi)."""
    rows = await fetch_all("SELECT id, name, parent_id, created_at FROM teams ORDER BY id")
    members: dict[int, set] = {}
    for m in await fetch_all("SELECT team_id, user_id FROM team_members"):
        members.setdefault(m["team_id"], set()).add(m["user_id"])
    by_id = {r["id"]: r for r in rows}
    roots = [r for r in rows if not r.get("parent_id") or r["parent_id"] not in by_id]
    out = []
    for root in sorted(roots, key=lambda r: (r["name"].casefold(), r["id"])):
        kids = sorted([r for r in rows if r.get("parent_id") == root["id"]], key=lambda r: (r["name"].casefold(), r["id"]))
        total = set(members.get(root["id"], set()))
        for k in kids:
            total |= members.get(k["id"], set())
        out.append({**root, "parent_id": None, "label": root["name"], "children": [k["id"] for k in kids],
                    "direct_count": len(members.get(root["id"], set())), "member_count": len(total)})
        for k in kids:
            n = len(members.get(k["id"], set()))
            out.append({**k, "label": root["name"] + TEAM_SEP + k["name"], "children": [],
                        "direct_count": n, "member_count": n})
    return out


def _clean_team_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise TeamNameError("Ekip adı boş olamaz")
    if len(name) > 60:
        raise TeamNameError("Ekip adı en fazla 60 karakter olabilir")
    return name


async def _check_name_free(name: str, parent_id: int | None, except_id: int | None = None):
    """Ad, aynı düzeydeki (aynı ana ekibin altındaki ya da ana ekipler arasındaki) ekipler arasında benzersiz."""
    for t in await fetch_all("SELECT id, name, parent_id FROM teams"):
        if t["id"] != except_id and (t.get("parent_id") or None) == (parent_id or None) and t["name"].casefold() == name.casefold():
            raise TeamNameError("Bu düzeyde aynı adla bir ekip zaten var")


async def _check_parent(team_id: int | None, parent_id: int | None):
    """İki seviye kuralı: ana ekip mevcut ve kendisi ana ekip olmalı; alt ekipleri olan ekip başka ekibin altına
    taşınamaz; ekip kendi altına alınamaz."""
    if parent_id is None:
        return
    parent = await get_team(parent_id)
    if not parent:
        raise ValueError("Ana ekip bulunamadı")
    if parent.get("parent_id"):
        raise TeamRuleError("Alt ekibin altına ekip açılamaz — yalnızca iki seviye (ana ekip › alt ekip) var")
    if team_id is not None:
        if parent_id == team_id:
            raise TeamRuleError("Ekip kendi altına taşınamaz")
        if await fetch_one("SELECT 1 AS ok FROM teams WHERE parent_id = ? LIMIT 1", (team_id,)):
            raise TeamRuleError("Alt ekipleri olan bir ana ekip başka bir ekibin altına taşınamaz — önce alt ekiplerini taşıyın")


async def create_team(name: str, parent_id: int | None = None) -> int:
    name = _clean_team_name(name)
    await _check_parent(None, parent_id)
    await _check_name_free(name, parent_id)
    return await execute_returning_id("INSERT INTO teams (name, parent_id, created_at) VALUES (?, ?, ?)",
                                      (name, parent_id, int(time.time())))


async def update_team(team_id: int, name: str | None = None, move: bool = False, parent_id: int | None = None):
    """Yeniden adlandır ve/veya taşı (move=True: parent_id None -> ana ekip yap). Kurallar _check_parent'ta."""
    team = await get_team(team_id)
    new_parent = parent_id if move else team.get("parent_id")
    if move:
        await _check_parent(team_id, parent_id)
    new_name = _clean_team_name(name) if name is not None else team["name"]
    await _check_name_free(new_name, new_parent, team_id)
    if move and parent_id is not None:
        # yarışa karşı: alt ekibi olan ekip ya da alt ekip olan hedef — koşul UPDATE'in içinde de
        await execute("UPDATE teams SET name = ?, parent_id = ? WHERE id = ? "
                      "AND NOT EXISTS (SELECT 1 FROM teams c WHERE c.parent_id = ?) "
                      "AND EXISTS (SELECT 1 FROM teams p WHERE p.id = ? AND p.parent_id IS NULL)",
                      (new_name, parent_id, team_id, team_id, parent_id))
        if (await get_team(team_id)).get("parent_id") != parent_id:
            raise TeamRuleError("Taşınamadı — ekip yapısı bu arada değişti, tekrar deneyin")
    else:
        await execute("UPDATE teams SET name = ?, parent_id = ? WHERE id = ?", (new_name, new_parent, team_id))


async def rename_team(team_id: int, name: str):
    await update_team(team_id, name=name)


async def staff_only_in_team(team_id: int) -> list[dict]:
    """Bu ekip, TEK ekibi olan owner/admin'ler (ekip silinirse ekipsiz kalırlardı)."""
    out = []
    for u in await list_users():
        if u["role"] in STAFF_ROLES and u["direct_team_ids"] == [team_id]:
            out.append({"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"]})
    return out


async def lessons_only_for_team(team_id: int) -> list[dict]:
    """Yalnızca bu ekibe atanmış dersler (ekip silinirse kimseye görünmez olurlar)."""
    return await fetch_all(
        "SELECT l.id, l.title FROM training_lessons l WHERE l.assign_mode = 'teams' "
        "AND EXISTS (SELECT 1 FROM training_lesson_teams lt WHERE lt.lesson_id = l.id AND lt.team_id = ?) "
        "AND NOT EXISTS (SELECT 1 FROM training_lesson_teams lt WHERE lt.lesson_id = l.id AND lt.team_id <> ?) "
        "ORDER BY l.sort_order, l.id", (team_id, team_id))


async def delete_team(team_id: int):
    """Ekibi siler: yalnızca ÜYELİKLER, ders-ekip atamaları ve ekibin davetleri kalkar. Kullanıcılar ve
    verileri (geçmiş, karar, liste, tamamlama) SİLİNMEZ. Owner/admin'in tek ekibiyse TeamRuleError."""
    if await fetch_one("SELECT 1 AS ok FROM teams WHERE parent_id = ? LIMIT 1", (team_id,)):
        raise TeamRuleError("Alt ekipleri olan ana ekip silinemez — önce alt ekipleri taşıyın ya da silin")
    blockers = await staff_only_in_team(team_id)
    if blockers:
        raise TeamRuleError("Bu ekip şu owner/admin'lerin tek ekibi: " + ", ".join(b["name"] or b["email"] for b in blockers)
                            + " — önce onları başka bir ekibe ekleyin ya da rollerini üyeye düşürün")
    await execute("DELETE FROM team_members WHERE team_id = ?", (team_id,))
    await execute("DELETE FROM training_lesson_teams WHERE team_id = ?", (team_id,))
    await execute("DELETE FROM team_invites WHERE team_id = ?", (team_id,))
    await execute("DELETE FROM teams WHERE id = ?", (team_id,))
    await _ensure_staff_in_team()   # eşzamanlı bir rol değişikliği araya girdiyse owner/admin ekipsiz kalmasın


async def set_user_teams(user_id: int, team_ids: list[int]):
    """Kişinin ekiplerini verilen kümeye eşitler (çoklu ekle/çıkar). Owner/admin (ve kalıcı owner)
    son ekibinden çıkarılamaz — boş küme reddedilir; koşul yarışa karşı sonradan da denetlenir."""
    row = await fetch_one("SELECT id, email FROM users WHERE id = ?", (user_id,))
    if not row:
        raise LookupError("Kullanıcı bulunamadı")
    wanted = set(team_ids)
    existing = {t["id"] for t in await fetch_all("SELECT id FROM teams")}
    unknown = sorted(wanted - existing)
    if unknown:
        raise ValueError(f"Bilinmeyen ekip id: {unknown}")
    staff = _is_permanent(row["email"]) or await get_user_role(user_id) in STAFF_ROLES
    if staff and not wanted:
        raise TeamRuleError("Owner ya da admin son ekibinden çıkarılamaz — önce rolünü üyeye düşürün")
    current = set(await user_team_ids(user_id))
    for tid in sorted(wanted - current):
        await _add_member(tid, user_id)
    removed = sorted(current - wanted)
    for tid in removed:
        await execute("DELETE FROM team_members WHERE team_id = ? AND user_id = ?", (tid, user_id))
    if removed and not await is_in_team(user_id) and (
            _is_permanent(row["email"]) or await get_user_role(user_id) in STAFF_ROLES):
        for tid in removed:   # bu arada owner/admin yapıldı -> geri al
            await _add_member(tid, user_id)
        raise TeamRuleError("Owner ya da admin son ekibinden çıkarılamaz — önce rolünü üyeye düşürün")


async def add_user_to_team(user_id: int, team_id: int):
    if not await fetch_one("SELECT id FROM users WHERE id = ?", (user_id,)):
        raise LookupError("Kullanıcı bulunamadı")
    if not await get_team(team_id):
        raise ValueError("Ekip bulunamadı")
    await _add_member(team_id, user_id)


async def remove_user_from_team(user_id: int, team_id: int) -> list[int]:
    """Ekipten çıkarır. Ana ekipten çıkarılan kişi o ana ekibin TÜM alt ekiplerinden de çıkar.
    Owner/admin hiçbir ekipte kalmayacaksa TeamRuleError (geri alınır). Kaldırılan ekip id'lerini döner."""
    row = await fetch_one("SELECT id, email FROM users WHERE id = ?", (user_id,))
    if not row:
        raise LookupError("Kullanıcı bulunamadı")
    team = await get_team(team_id)
    if not team:
        raise ValueError("Ekip bulunamadı")
    targets = {team_id}
    if not team.get("parent_id"):
        targets |= {r["id"] for r in await fetch_all("SELECT id FROM teams WHERE parent_id = ?", (team_id,))}
    current = set(await user_team_ids(user_id))
    removed = sorted(current & targets)
    staff = _is_permanent(row["email"]) or await get_user_role(user_id) in STAFF_ROLES
    if staff and current and not (current - targets):
        raise TeamRuleError("Owner ya da admin son ekibinden çıkarılamaz — önce rolünü üyeye düşürün")
    for tid in removed:
        await execute("DELETE FROM team_members WHERE team_id = ? AND user_id = ?", (tid, user_id))
    if removed and staff and not await is_in_team(user_id):
        for tid in removed:
            await _add_member(tid, user_id)
        raise TeamRuleError("Owner ya da admin son ekibinden çıkarılamaz — önce rolünü üyeye düşürün")
    return removed


async def new_outsider_count(days: int = 7) -> int:
    """Son N günde kaydolan ve hiçbir ekipte olmayan hesap sayısı (owner menü rozeti)."""
    row = await fetch_one("SELECT COUNT(*) AS c FROM users u WHERE u.created_at >= ? AND NOT EXISTS "
                          "(SELECT 1 FROM team_members m WHERE m.user_id = u.id)", (int(time.time()) - days * 86400,))
    return int((row or {}).get("c") or 0)


# --- Davetler -------------------------------------------------------------
def _invite_hash(code: str) -> str:
    return hashlib.sha256((code or "").strip().encode()).hexdigest()


async def create_invite(team_id: int, created_by: int, multi_use: bool, days: int) -> dict:
    code = secrets.token_urlsafe(24)
    now = int(time.time())
    iid = await execute_returning_id(
        "INSERT INTO team_invites (team_id, code_hash, created_by, created_at, expires_at, max_uses, uses) "
        "VALUES (?, ?, ?, ?, ?, ?, 0)",
        (team_id, _invite_hash(code), created_by, now, now + days * 86400, None if multi_use else 1))
    return {"id": iid, "code": code, "expires_at": now + days * 86400}


async def list_active_invites() -> list[dict]:
    now = int(time.time())
    rows = await fetch_all(
        "SELECT i.id, i.team_id, t.name AS team_name, i.created_at, i.expires_at, i.max_uses, i.uses, "
        "u.email AS created_by_email, u.first_name, u.last_name FROM team_invites i JOIN teams t ON t.id = i.team_id "
        "LEFT JOIN users u ON u.id = i.created_by "
        "WHERE i.revoked_at IS NULL AND i.expires_at > ? AND (i.max_uses IS NULL OR i.uses < i.max_uses) "
        "ORDER BY i.created_at DESC, i.id DESC", (now,))
    labels = {t["id"]: t["label"] for t in await list_teams()}
    for r in rows:
        r["team_name"] = labels.get(r["team_id"], r["team_name"])
        r["created_by_name"] = display_name(r)
        r.pop("first_name", None); r.pop("last_name", None)
    return rows


async def revoke_invite(invite_id: int) -> bool:
    rows = await execute_fetch("UPDATE team_invites SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL RETURNING id",
                               (int(time.time()), invite_id))
    return bool(rows)


async def redeem_invite(code: str) -> dict | None:
    """Geçerli davetin kullanım sayısını TEK ifadede artırır (tek kullanımlık davet iki kez kullanılamaz)
    ve ekibi döner; geçersiz/süresi dolmuş/iptal/tükenmiş ya da ekibi silinmişse None."""
    if not code or len(code) > 200:
        return None
    rows = await execute_fetch(
        "UPDATE team_invites SET uses = uses + 1 WHERE code_hash = ? AND revoked_at IS NULL AND expires_at > ? "
        "AND (max_uses IS NULL OR uses < max_uses) "
        "AND EXISTS (SELECT 1 FROM teams t WHERE t.id = team_invites.team_id) RETURNING team_id",
        (_invite_hash(code), int(time.time())))
    return await get_team(rows[0]["team_id"]) if rows else None


async def set_user_role(user_id: int, role: str):
    """
    Rol değiştirir. Owner'ı düşürürken (owner -> admin/member) başka en az bir
    owner kalmalı; aksi halde LastOwnerError. Koşul tek UPDATE içinde de
    uygulanır; ardından owner sayısı kontrol edilir ve (eşzamanlı iki düşürme
    gibi) beklenmedik bir durumda 0 owner kalırsa değişiklik geri alınır.
    """
    if role not in VALID_ROLES:
        raise ValueError("Geçersiz rol")
    current = await get_user_role(user_id)
    if current is None:
        raise ValueError("Kullanıcı bulunamadı")
    if role in ("owner", "admin") and not await is_in_team(user_id):
        raise TeamRuleError("Owner ve admin rolü yalnızca en az bir ekipte olan kişiye verilebilir — önce bir ekibe ekleyin")
    if role != "owner":
        row = await fetch_one("SELECT email FROM users WHERE id = ?", (user_id,))
        if row and _is_permanent(row["email"]):
            raise PermanentOwnerError("Bu kullanıcı kalıcı owner (OWNER_EMAILS) — rolü düşürülemez")
    if current == "owner" and role != "owner":
        if await owner_count() <= 1:
            raise LastOwnerError("Son kalan owner düşürülemez — önce başka birini owner yapın")
        await execute(
            "UPDATE users SET role = ? WHERE id = ? AND "
            "(SELECT COUNT(*) FROM users WHERE role = 'owner') > 1", (role, user_id))
        if await owner_count() == 0:
            await execute("UPDATE users SET role = 'owner' WHERE id = ?", (user_id,))
            raise LastOwnerError("Son kalan owner düşürülemez — önce başka birini owner yapın")
        if await get_user_role(user_id) == "owner":
            raise LastOwnerError("Son kalan owner düşürülemez — önce başka birini owner yapın")
    elif role in ("owner", "admin"):
        # eşzamanlı "son ekibinden çıkar"a karşı koşul UPDATE'in içinde
        await execute("UPDATE users SET role = ? WHERE id = ? AND EXISTS "
                      "(SELECT 1 FROM team_members m WHERE m.user_id = users.id)", (role, user_id))
        if await get_user_role(user_id) != role:
            raise TeamRuleError("Owner ve admin rolü yalnızca en az bir ekipte olan kişiye verilebilir — önce bir ekibe ekleyin")
    else:
        await execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


# ---------------------------------------------------------------------------
# HESABI KALICI SİLME (owner) — iz bırakmadan
# ---------------------------------------------------------------------------
# Başkalarına ait kayıtlarda kişiyi e-postayla anan alanlar: satır SİLİNMEZ, alan NULL yapılır
# (panelde "—"). Yeni bir tabloya kişi e-postası yazan bir sütun eklersen BURAYA DA EKLE.
EMAIL_REF_COLUMNS = [
    ("market_decision", "decided_by"),
    ("checklist_items", "checked_by"), ("checklist_items", "created_by"),
    ("checklists", "locked_by"), ("checklist_events", "by_email"),
    ("checklist_template", "updated_by"), ("keyword_analysis", "fetched_by"),
    ("proof_assets", "approved_by"), ("portfolio_runs", "run_by"), ("learning_events", "recorded_by"),
    ("supplier_scores", "scored_by"), ("creative_deliverables", "owner"), ("launch_checkpoints", "entered_by"),
]
# Kişinin KENDİ satırları (user_id ile) — tamamen silinir.
USER_ID_TABLES = ["sessions", "user_thresholds", "user_query_log", "market_decision",
                  "training_completions", "training_assignments", "team_members", "competency_forms"]
# Başkalarına ait kayıtlarda kişiyi id ile anan alanlar: NULL yapılır.
ID_REF_COLUMNS = [("training_lessons", "created_by"), ("team_invites", "created_by"),
                  ("banned_categories", "banned_by")]


def _scrub_json(value, email: str):
    if isinstance(value, dict):
        return {k: _scrub_json(v, email) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_json(v, email) for v in value]
    if isinstance(value, str) and value.strip().lower() == email:
        return None
    return value


async def delete_user_completely(user_id: int) -> dict:
    """
    Hesabı ve kişiye ait her şeyi siler: oturumlar, eşikler, arama geçmişi, kararlar, kendi
    kontrol listeleri (maddeleri + olayları), eğitim tamamlamaları/atamaları, ekip üyelikleri, hesap. Başkalarının
    kayıtlarındaki referanslar (onaylar, eklediği dersler, olay kayıtları) NULL'lanır; yer tutucu yok.
    Koruma kuralları (kalıcı owner, son owner, kendi hesabı) çağıran tarafta (index.py) uygulanır.
    """
    row = await fetch_one("SELECT id, email FROM users WHERE id = ?", (user_id,))
    if not row:
        raise ValueError("Kullanıcı bulunamadı")
    email = (row["email"] or "").strip().lower()
    # önce oturumlar: silme sürerken giriş yapılamasın
    await execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    await forum.delete_user_content(user_id)   # forum: kendi başlıkları (cevaplarıyla), cevapları, oy/kayıt/görüntüleme
    own_lists = [r["id"] for r in await fetch_all("SELECT id FROM checklists WHERE user_id = ?", (user_id,))]
    for cid in own_lists:
        await execute("DELETE FROM checklist_items WHERE checklist_id = ?", (cid,))
        await execute("DELETE FROM checklist_events WHERE checklist_id = ?", (cid,))
    await execute("DELETE FROM checklists WHERE user_id = ?", (user_id,))
    for t in USER_ID_TABLES:
        await execute(f"DELETE FROM {t} WHERE user_id = ?", (user_id,))
    for t, col in ID_REF_COLUMNS:
        await execute(f"UPDATE {t} SET {col} = NULL WHERE {col} = ?", (user_id,))
    for t, col in EMAIL_REF_COLUMNS:
        await execute(f"UPDATE {t} SET {col} = NULL WHERE LOWER({col}) = ?", (email,))
    # olay ayrıntılarındaki (JSON) e-posta referansları, ör. kilit açma olayındaki "önceki onaylayan"
    for ev in await fetch_all("SELECT id, details_json FROM checklist_events WHERE LOWER(details_json) LIKE ?",
                              (f"%{email}%",)):
        try:
            cleaned = json.dumps(_scrub_json(json.loads(ev["details_json"]), email), ensure_ascii=False)
        except (TypeError, ValueError):
            cleaned = None
        await execute("UPDATE checklist_events SET details_json = ? WHERE id = ?", (cleaned, ev["id"]))
    await execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"id": user_id, "email": email, "deleted_checklists": len(own_lists)}


# ---------------------------------------------------------------------------
# EĞİTİM & GÖREVLER
# ---------------------------------------------------------------------------
async def _lesson_assignees(lesson_ids: list[int]) -> dict[int, list[int]]:
    if not lesson_ids:
        return {}
    marks = ", ".join("?" * len(lesson_ids))
    rows = await fetch_all(f"SELECT lesson_id, user_id FROM training_assignments WHERE lesson_id IN ({marks})",
                           tuple(lesson_ids))
    out: dict[int, list[int]] = {i: [] for i in lesson_ids}
    for r in rows:
        out.setdefault(r["lesson_id"], []).append(r["user_id"])
    return out


async def _lesson_team_ids(lesson_ids: list[int]) -> dict[int, list[int]]:
    if not lesson_ids:
        return {}
    marks = ", ".join("?" * len(lesson_ids))
    rows = await fetch_all(f"SELECT lesson_id, team_id FROM training_lesson_teams WHERE lesson_id IN ({marks})",
                           tuple(lesson_ids))
    out: dict[int, list[int]] = {i: [] for i in lesson_ids}
    for r in rows:
        out.setdefault(r["lesson_id"], []).append(r["team_id"])
    return out


def _mode(row: dict) -> str:
    m = row.get("assign_mode")
    return m if m in ("all", "teams", "users") else ("all" if row.get("assign_all") else "users")


async def list_lessons_all() -> list[dict]:
    rows = await fetch_all("SELECT * FROM training_lessons ORDER BY sort_order, id")
    assignees = await _lesson_assignees([r["id"] for r in rows])
    teams = await _lesson_team_ids([r["id"] for r in rows])
    for r in rows:
        r["assign_mode"] = _mode(r)
        r["assign_all"] = r["assign_mode"] == "all"
        r["assignee_ids"] = sorted(assignees.get(r["id"], [])) if r["assign_mode"] == "users" else []
        r["team_ids"] = sorted(teams.get(r["id"], [])) if r["assign_mode"] == "teams" else []
    return rows


# Ders kime görünür (DİNAMİK): tüm ekipler | kişinin ŞU ANKİ ekiplerinden biri | kişisel atama.
# Ekip dışı kişi (hiçbir ekipte değil) hiçbir ders görmez — çağıranlar ayrıca denetler.
_LESSON_VISIBLE_SQL = """(l.assign_mode = 'all'
    OR (l.assign_mode = 'teams' AND EXISTS (SELECT 1 FROM training_lesson_teams lt, team_members m, teams mt
        WHERE lt.lesson_id = l.id AND m.user_id = ? AND mt.id = m.team_id
          AND (lt.team_id = m.team_id OR lt.team_id = mt.parent_id)))
    OR (l.assign_mode = 'users' AND EXISTS (SELECT 1 FROM training_assignments a
        WHERE a.lesson_id = l.id AND a.user_id = ?)))"""


async def list_lessons_for_user(user_id: int) -> list[dict]:
    """Yalnızca kullanıcıya atanmış dersler (herkese atananlar + kişisel atamalar)."""
    # Eğitim yalnızca EKİP üyeleri içindir: ekip dışı kişi hiçbir ders görmez.
    if not await is_in_team(user_id):
        return []
    return await fetch_all(f"SELECT l.* FROM training_lessons l WHERE {_LESSON_VISIBLE_SQL} ORDER BY l.sort_order, l.id",
                           (user_id, user_id))


async def is_lesson_assigned(lesson_id: int, user_id: int) -> bool:
    if not await is_in_team(user_id):
        return False
    row = await fetch_one(f"SELECT 1 AS ok FROM training_lessons l WHERE l.id = ? AND {_LESSON_VISIBLE_SQL}",
                          (lesson_id, user_id, user_id))
    return bool(row)


async def get_lesson(lesson_id: int) -> dict | None:
    return await fetch_one("SELECT * FROM training_lessons WHERE id = ?", (lesson_id,))


async def _replace_assignments(lesson_id: int, data: dict):
    mode = data.get("assign_mode") or "all"
    await execute("DELETE FROM training_assignments WHERE lesson_id = ?", (lesson_id,))
    await execute("DELETE FROM training_lesson_teams WHERE lesson_id = ?", (lesson_id,))
    for uid in sorted(set(data.get("assignee_ids") or [])) if mode == "users" else []:
        await execute("INSERT INTO training_assignments (lesson_id, user_id) VALUES (?, ?)", (lesson_id, uid))
    for tid in sorted(set(data.get("team_ids") or [])) if mode == "teams" else []:
        await execute("INSERT INTO training_lesson_teams (lesson_id, team_id) VALUES (?, ?)", (lesson_id, tid))


async def create_lesson(data: dict, created_by: int) -> int:
    now = int(time.time())
    lesson_id = await execute_returning_id(
        """INSERT INTO training_lessons (title, description, video_url, sort_order, due_date, assign_all, assign_mode,
           created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data["title"], data.get("description") or "", data.get("video_url") or None, data.get("sort_order", 0),
         data.get("due_date") or None, 1 if data["assign_mode"] == "all" else 0, data["assign_mode"],
         created_by, now, now))
    await _replace_assignments(lesson_id, data)
    return lesson_id


async def update_lesson(lesson_id: int, data: dict):
    await execute(
        """UPDATE training_lessons SET title = ?, description = ?, video_url = ?, sort_order = ?, due_date = ?,
           assign_all = ?, assign_mode = ?, updated_at = ? WHERE id = ?""",
        (data["title"], data.get("description") or "", data.get("video_url") or None, data.get("sort_order", 0),
         data.get("due_date") or None, 1 if data["assign_mode"] == "all" else 0, data["assign_mode"],
         int(time.time()), lesson_id))
    await _replace_assignments(lesson_id, data)


async def delete_lesson(lesson_id: int):
    await execute("DELETE FROM training_completions WHERE lesson_id = ?", (lesson_id,))
    await execute("DELETE FROM training_assignments WHERE lesson_id = ?", (lesson_id,))
    await execute("DELETE FROM training_lesson_teams WHERE lesson_id = ?", (lesson_id,))
    await execute("DELETE FROM training_lessons WHERE id = ?", (lesson_id,))


async def set_completion(lesson_id: int, user_id: int, completed: bool):
    """Yalnızca çağıranın KENDİ kaydı — user_id her zaman oturumdan gelir, istekten değil."""
    if completed:
        await execute(
            "INSERT INTO training_completions (lesson_id, user_id, completed_at) VALUES (?, ?, ?) "
            "ON CONFLICT (lesson_id, user_id) DO NOTHING", (lesson_id, user_id, int(time.time())))
    else:
        await execute("DELETE FROM training_completions WHERE lesson_id = ? AND user_id = ?", (lesson_id, user_id))


async def completions_for_user(user_id: int) -> dict[int, int]:
    rows = await fetch_all("SELECT lesson_id, completed_at FROM training_completions WHERE user_id = ?", (user_id,))
    return {r["lesson_id"]: r["completed_at"] for r in rows}


async def all_completions() -> list[dict]:
    return await fetch_all("SELECT lesson_id, user_id, completed_at FROM training_completions")



# ---------------------------------------------------------------------------
# ARAŞTIRMA KONTROL LİSTESİ
# Kilit kuralı her değişiklik sorgusunun İÇİNDE (AND status='open') — kilitli liste
# sunucu tarafında değiştirilemez; çağıran taraf etkilenen satır yoksa 423 döner.
# ---------------------------------------------------------------------------
async def get_analysis_record(analysis_key: str, marketplace: str) -> dict | None:
    """Son kaydedilen analiz payload'u (keyword_analysis log tablosu) — MCP çağrısı yok."""
    row = await fetch_one(
        "SELECT payload_json, fetched_at FROM keyword_analysis WHERE keyword = ? AND marketplace = ?",
        (analysis_key, marketplace))
    if not row:
        return None
    try:
        return {"payload": json.loads(row["payload_json"]), "fetched_at": row["fetched_at"]}
    except (TypeError, ValueError):
        return None


async def get_checklist_template() -> dict | None:
    row = await fetch_one("SELECT template_json, updated_at, updated_by FROM checklist_template WHERE id = 1")
    if not row:
        return None
    return {"template": json.loads(row["template_json"]), "updated_at": row["updated_at"], "updated_by": row["updated_by"]}


async def save_checklist_template(template: dict, updated_by: str):
    now = int(time.time())
    await execute("DELETE FROM checklist_template WHERE id = 1")
    await execute("INSERT INTO checklist_template (id, template_json, updated_at, updated_by) VALUES (1, ?, ?, ?)",
                  (json.dumps(template), now, updated_by))


async def reset_checklist_template():
    await execute("DELETE FROM checklist_template WHERE id = 1")


async def create_checklist(user_id: int, user_email: str, analysis_key: str, marketplace: str, title: str,
                           snapshot: dict, stages: list, thresholds: dict, items: list[dict]) -> int:
    now = int(time.time())
    cid = await execute_returning_id(
        """INSERT INTO checklists (user_id, user_email, analysis_key, marketplace, title, snapshot_json, stages_json,
           thresholds_json, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
        (user_id, user_email, analysis_key, marketplace, title, json.dumps(snapshot), json.dumps(stages),
         json.dumps(thresholds), now, now))
    for it in items:
        await execute(
            """INSERT INTO checklist_items (checklist_id, stage_key, item_order, kind, auto_key, text, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, it["stage_key"], it["item_order"], it["kind"], it.get("auto_key"), it["text"], user_email, now))
    return cid


def _checklist_row(r: dict) -> dict:
    r = dict(r)
    for k in ("snapshot_json", "stages_json", "thresholds_json"):
        r[k[:-5]] = json.loads(r.pop(k) or "null")
    r["overridden"] = json.loads(r.pop("overridden_json", None) or "null") or []
    return r


async def get_checklist(cid: int) -> dict | None:
    r = await fetch_one("SELECT * FROM checklists WHERE id = ?", (cid,))
    return _checklist_row(r) if r else None


async def list_checklists(user_id: int | None = None) -> list[dict]:
    """user_id verilirse yalnızca o kullanıcının listeleri (member); None ise tümü (owner/admin)."""
    if user_id is None:
        rows = await fetch_all("SELECT * FROM checklists ORDER BY updated_at DESC, id DESC")
    else:
        rows = await fetch_all("SELECT * FROM checklists WHERE user_id = ? ORDER BY updated_at DESC, id DESC", (user_id,))
    return [_checklist_row(r) for r in rows]


async def checklist_items(cid: int) -> list[dict]:
    return await fetch_all("SELECT * FROM checklist_items WHERE checklist_id = ? ORDER BY item_order, id", (cid,))


async def items_for_checklists(cids: list[int]) -> dict[int, list[dict]]:
    if not cids:
        return {}
    marks = ", ".join("?" * len(cids))
    rows = await fetch_all(f"SELECT * FROM checklist_items WHERE checklist_id IN ({marks}) ORDER BY item_order, id", tuple(cids))
    out: dict[int, list[dict]] = {c: [] for c in cids}
    for r in rows:
        out.setdefault(r["checklist_id"], []).append(r)
    return out


async def _touch(cid: int):
    await execute("UPDATE checklists SET updated_at = ? WHERE id = ?", (int(time.time()), cid))


async def set_item_state(cid: int, item_id: int, checked: bool, note: str | None, by: str) -> bool:
    """Yalnızca manuel/özel madde ve yalnızca açık listede. Değişti mi?"""
    now = int(time.time())
    before = await fetch_one(
        "SELECT id FROM checklist_items WHERE id = ? AND checklist_id = ? AND kind IN ('manual', 'custom') "
        "AND EXISTS (SELECT 1 FROM checklists WHERE id = ? AND status = 'open')", (item_id, cid, cid))
    if not before:
        return False
    await execute(
        "UPDATE checklist_items SET checked = ?, checked_by = ?, checked_at = ?, note = ? "
        "WHERE id = ? AND checklist_id = ? AND kind IN ('manual', 'custom') "
        "AND EXISTS (SELECT 1 FROM checklists WHERE id = ? AND status = 'open')",
        (1 if checked else 0, by if checked else None, now if checked else None, note, item_id, cid, cid))
    after = await fetch_one("SELECT checked, note FROM checklist_items WHERE id = ?", (item_id,))
    if not after or bool(after["checked"]) != bool(checked) or (after["note"] or None) != (note or None):
        return False  # bu arada kilitlendi -> değişmedi
    await _touch(cid)
    return True


async def add_custom_item(cid: int, stage_key: str, text: str, by: str) -> int | None:
    row = await fetch_one("SELECT status FROM checklists WHERE id = ?", (cid,))
    if not row or row["status"] != "open":
        return None
    mx = await fetch_one("SELECT MAX(item_order) AS m FROM checklist_items WHERE checklist_id = ?", (cid,))
    iid = await execute_returning_id(
        """INSERT INTO checklist_items (checklist_id, stage_key, item_order, kind, text, created_by, created_at)
           VALUES (?, ?, ?, 'custom', ?, ?, ?)""",
        (cid, stage_key, ((mx or {}).get("m") or 0) + 1, text, by, int(time.time())))
    # Kilitlenme ile ekleme yarışırsa: eklenen maddeyi geri al
    row = await fetch_one("SELECT status FROM checklists WHERE id = ?", (cid,))
    if not row or row["status"] != "open":
        await execute("DELETE FROM checklist_items WHERE id = ?", (iid,))
        return None
    await _touch(cid)
    return iid


async def delete_custom_item(cid: int, item_id: int) -> bool:
    row = await fetch_one(
        "SELECT id FROM checklist_items WHERE id = ? AND checklist_id = ? AND kind = 'custom' "
        "AND EXISTS (SELECT 1 FROM checklists WHERE id = ? AND status = 'open')", (item_id, cid, cid))
    if not row:
        return False
    await execute(
        "DELETE FROM checklist_items WHERE id = ? AND checklist_id = ? AND kind = 'custom' "
        "AND EXISTS (SELECT 1 FROM checklists WHERE id = ? AND status = 'open')", (item_id, cid, cid))
    await _touch(cid)
    return True


async def _add_checklist_event(cid: int, kind: str, by: str, at: int, reason: str | None, details: dict | None = None):
    await execute("INSERT INTO checklist_events (checklist_id, kind, by_email, at, reason, details_json) "
                  "VALUES (?, ?, ?, ?, ?, ?)",
                  (cid, kind, by, at, reason, json.dumps(details, ensure_ascii=False) if details else None))


async def checklist_events(cid: int) -> list[dict]:
    rows = await fetch_all("SELECT * FROM checklist_events WHERE checklist_id = ? ORDER BY at, id", (cid,))
    out = []
    for r in rows:
        r = dict(r)
        r["details"] = json.loads(r.pop("details_json") or "null")
        out.append(r)
    return out


async def lock_checklist(cid: int, by: str, reason: str | None = None, overridden: list | None = None) -> int | None:
    """Koşullu kilit (yalnızca status='open'). Başarılıysa kilit zaman damgasını döner."""
    now = int(time.time())
    ov = json.dumps(overridden, ensure_ascii=False) if overridden else None
    await execute("UPDATE checklists SET status = 'locked', locked_by = ?, locked_at = ?, updated_at = ?, "
                  "approval_reason = ?, overridden_json = ? WHERE id = ? AND status = 'open'",
                  (by, now, now, reason, ov, cid))
    row = await fetch_one("SELECT status, locked_by, locked_at FROM checklists WHERE id = ?", (cid,))
    if not (row and row["status"] == "locked" and row["locked_at"] == now and row["locked_by"] == by):
        return None
    return now


async def record_lock_event(cid: int, by: str, at: int, reason: str | None, overridden: list | None):
    await _add_checklist_event(cid, "lock", by, at, reason, {"overridden": overridden or []})


async def revert_lock(cid: int, locked_at: int):
    """Kilitleme sonrası yarış kontrolü başarısız olursa YALNIZCA bu kilidi geri alır."""
    await execute("UPDATE checklists SET status = 'open', locked_by = NULL, locked_at = NULL, "
                  "approval_reason = NULL, overridden_json = NULL WHERE id = ? AND status = 'locked' AND locked_at = ?",
                  (cid, locked_at))


async def unlock_checklist(cid: int, by: str, reason: str) -> bool:
    """Owner'ın gerekçeli kilit açması. Önceki onayın bilgisi olaya kopyalanır, sonra liste açılır."""
    row = await fetch_one("SELECT status, locked_by, locked_at, approval_reason, overridden_json "
                          "FROM checklists WHERE id = ?", (cid,))
    if not row or row["status"] != "locked":
        return False
    now = int(time.time())
    await execute("UPDATE checklists SET status = 'open', locked_by = NULL, locked_at = NULL, "
                  "approval_reason = NULL, overridden_json = NULL, updated_at = ? "
                  "WHERE id = ? AND status = 'locked' AND locked_at = ?", (now, cid, row["locked_at"]))
    after = await fetch_one("SELECT status, locked_at FROM checklists WHERE id = ?", (cid,))
    if not after or after["status"] != "open":
        return False
    await _add_checklist_event(cid, "unlock", by, now, reason, {
        "previous_locked_by": row["locked_by"], "previous_locked_at": row["locked_at"],
        "previous_approval_reason": row["approval_reason"],
        "previous_overridden": json.loads(row["overridden_json"] or "null") or []})
    return True


async def delete_checklist(cid: int) -> bool:
    row = await fetch_one("SELECT status FROM checklists WHERE id = ?", (cid,))
    if not row or row["status"] != "open":
        return False
    # Önce liste (koşullu); yalnızca liste gerçekten silindiyse maddeleri temizle
    await execute("DELETE FROM checklists WHERE id = ? AND status = 'open'", (cid,))
    if await fetch_one("SELECT id FROM checklists WHERE id = ?", (cid,)):
        return False
    await execute("DELETE FROM checklist_items WHERE checklist_id = ? "
                  "AND NOT EXISTS (SELECT 1 FROM checklists WHERE id = ?)", (cid, cid))
    await execute("DELETE FROM checklist_events WHERE checklist_id = ? "
                  "AND NOT EXISTS (SELECT 1 FROM checklists WHERE id = ?)", (cid, cid))
    return True


# ---------------------------------------------------------------------------
# TRENDLER — YASAKLI KATEGORİLER (yalnızca owner yönetir; index.py)
# ---------------------------------------------------------------------------
async def list_banned_categories() -> list[dict]:
    rows = await fetch_all(
        "SELECT b.name_key, b.name, b.banned_by, b.banned_at, u.first_name, u.last_name "
        "FROM banned_categories b LEFT JOIN users u ON u.id = b.banned_by ORDER BY b.name")
    return [{"name": r["name"], "key": r["name_key"], "banned_at": r["banned_at"],
             "banned_by_name": display_name(r) or "—"} for r in rows]


async def banned_category_keys() -> set[str]:
    return {r["name_key"] for r in await fetch_all("SELECT name_key FROM banned_categories")}


async def ban_category(name: str, user_id: int | None) -> bool:
    """True = yeni eklendi, False = zaten yasaklıydı (harf duyarsız)."""
    row = await execute_fetch(
        "INSERT INTO banned_categories (name_key, name, banned_by, banned_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (name_key) DO NOTHING RETURNING name_key",
        (name.lower(), name, user_id, int(time.time())))
    return bool(row)


async def unban_category(name: str) -> bool:
    row = await execute_fetch("DELETE FROM banned_categories WHERE name_key = ? RETURNING name_key", (name.lower(),))
    return bool(row)
