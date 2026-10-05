"""
Veritabanı katmanı — db_adapter üzerinden Postgres (paylaşımlı/kalıcı) ya da
SQLite (yerel test) ile çalışır. Bkz. db_adapter.py docstring.
"""
import json
import os
import time
import hashlib
import secrets
from db_adapter import execute, execute_returning_id, fetch_all, fetch_one, storage_info, USE_POSTGRES

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
    # EKİP ÜYELİĞİ (rolden ayrı): sütun eklenmeden önce var olan TÜM kullanıcılar ekip üyesi
    # sayılır (NULL -> 1). Yeni kayıtlar her zaman açıkça 0/1 yazılır, bu yüzden bu satır
    # idempotenttir ve sonradan ekipten çıkarılanları geri eklemez. Owner/admin her zaman ekipte.
    await _add_column_if_missing("users", "in_team", "INTEGER")
    await execute("UPDATE users SET in_team = 1 WHERE in_team IS NULL")
    await execute("UPDATE users SET in_team = 1 WHERE role IN ('owner', 'admin') AND in_team <> 1")


async def init_db():
    for schema in _SCHEMAS:
        await execute(schema)
    await _migrate_schema()


async def init_db_v3():
    pass  # tüm şemalar artık init_db içinde


# ---------------------------------------------------------------------------
# KİMLİK DOĞRULAMA
# ---------------------------------------------------------------------------
def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()


async def create_user(email: str, password: str) -> dict:
    email = email.strip().lower()
    existing = await fetch_one("SELECT id FROM users WHERE email = ?", (email,))
    if existing:
        raise ValueError("Bu e-posta zaten kayıtlı")
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(password, salt)
    role = "owner" if email in permanent_owner_emails() or await user_count() == 0 else "member"
    in_team = 1 if role == "owner" else 0   # yeni kayıt ekip DIŞI başlar (owner hariç)
    user_id = await execute_returning_id(
        "INSERT INTO users (email, password_hash, salt, created_at, role, in_team) VALUES (?, ?, ?, ?, ?, ?)",
        (email, pw_hash, salt, int(time.time()), role, in_team))
    return {"id": user_id, "email": email}


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
    if not token:
        return None
    return await fetch_one("SELECT * FROM sessions WHERE token = ?", (token,))


async def delete_session(token: str):
    await execute("DELETE FROM sessions WHERE token = ?", (token,))


async def user_count() -> int:
    row = await fetch_one("SELECT COUNT(*) AS c FROM users")
    return (row or {}).get("c", 0) or 0


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


async def team_verdict_log() -> list[dict]:
    """Kararlara 'ön öneri' eşlemek için (user_id, keyword, marketplace) -> sorgu verdict'leri."""
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
    rows = await fetch_all("SELECT id, email, role, created_at, in_team FROM users ORDER BY id")
    return [{"id": r["id"], "email": r["email"], "created_at": r["created_at"],
             "role": "owner" if _is_permanent(r["email"]) else _norm_role(r.get("role")),
             "permanent": _is_permanent(r["email"]),
             "in_team": bool(r.get("in_team")) or _is_permanent(r["email"])} for r in rows]


class TeamRuleError(Exception):
    """Ekip üyeliği kuralı ihlali (owner/admin yalnızca ekipte; owner/admin ekipten çıkarılamaz)."""


async def is_in_team(user_id: int) -> bool:
    row = await fetch_one("SELECT in_team, email FROM users WHERE id = ?", (user_id,))
    return bool(row) and (bool(row.get("in_team")) or _is_permanent(row.get("email")))


async def team_member_ids() -> set[int]:
    return {u["id"] for u in await list_users() if u["in_team"]}


async def set_team_membership(user_id: int, in_team: bool):
    """Ekibe ekle / ekipten çıkar. Owner ya da admin olan biri çıkarılamaz (önce rolü düşürülmeli);
    kural UPDATE'in içinde de var (eşzamanlı rol değişikliğine karşı)."""
    row = await fetch_one("SELECT id, email FROM users WHERE id = ?", (user_id,))
    if not row:
        raise ValueError("Kullanıcı bulunamadı")
    if in_team:
        await execute("UPDATE users SET in_team = 1 WHERE id = ?", (user_id,))
        return
    if _is_permanent(row["email"]) or await get_user_role(user_id) in ("owner", "admin"):
        raise TeamRuleError("Owner ya da admin ekipten çıkarılamaz — önce rolünü üyeye düşürün")
    await execute("UPDATE users SET in_team = 0 WHERE id = ? AND role NOT IN ('owner', 'admin')", (user_id,))
    if await is_in_team(user_id):
        raise TeamRuleError("Owner ya da admin ekipten çıkarılamaz — önce rolünü üyeye düşürün")


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
        raise TeamRuleError("Owner ve admin rolü yalnızca ekip üyelerine verilebilir — önce ekibe ekleyin")
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
        # eşzamanlı "ekipten çıkar"a karşı koşul UPDATE'in içinde
        await execute("UPDATE users SET role = ? WHERE id = ? AND in_team = 1", (role, user_id))
        if await get_user_role(user_id) != role:
            raise TeamRuleError("Owner ve admin rolü yalnızca ekip üyelerine verilebilir — önce ekibe ekleyin")
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
                  "training_completions", "training_assignments"]


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
    kontrol listeleri (maddeleri + olayları), eğitim tamamlamaları/atamaları, hesap. Başkalarının
    kayıtlarındaki referanslar (onaylar, eklediği dersler, olay kayıtları) NULL'lanır; yer tutucu yok.
    Koruma kuralları (kalıcı owner, son owner, kendi hesabı) çağıran tarafta (index.py) uygulanır.
    """
    row = await fetch_one("SELECT id, email FROM users WHERE id = ?", (user_id,))
    if not row:
        raise ValueError("Kullanıcı bulunamadı")
    email = (row["email"] or "").strip().lower()
    # önce oturumlar: silme sürerken giriş yapılamasın
    await execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    own_lists = [r["id"] for r in await fetch_all("SELECT id FROM checklists WHERE user_id = ?", (user_id,))]
    for cid in own_lists:
        await execute("DELETE FROM checklist_items WHERE checklist_id = ?", (cid,))
        await execute("DELETE FROM checklist_events WHERE checklist_id = ?", (cid,))
    await execute("DELETE FROM checklists WHERE user_id = ?", (user_id,))
    for t in USER_ID_TABLES:
        await execute(f"DELETE FROM {t} WHERE user_id = ?", (user_id,))
    await execute("UPDATE training_lessons SET created_by = NULL WHERE created_by = ?", (user_id,))
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


async def list_lessons_all() -> list[dict]:
    rows = await fetch_all("SELECT * FROM training_lessons ORDER BY sort_order, id")
    assignees = await _lesson_assignees([r["id"] for r in rows])
    for r in rows:
        r["assign_all"] = bool(r["assign_all"])
        r["assignee_ids"] = sorted(assignees.get(r["id"], []))
    return rows


async def list_lessons_for_user(user_id: int) -> list[dict]:
    """Yalnızca kullanıcıya atanmış dersler (herkese atananlar + kişisel atamalar)."""
    # Eğitim yalnızca EKİP üyeleri içindir: ekip dışı kişi hiçbir ders görmez.
    if not await is_in_team(user_id):
        return []
    return await fetch_all(
        """SELECT l.* FROM training_lessons l
           WHERE l.assign_all = 1
              OR EXISTS (SELECT 1 FROM training_assignments a WHERE a.lesson_id = l.id AND a.user_id = ?)
           ORDER BY l.sort_order, l.id""", (user_id,))


async def is_lesson_assigned(lesson_id: int, user_id: int) -> bool:
    if not await is_in_team(user_id):
        return False
    row = await fetch_one(
        """SELECT 1 AS ok FROM training_lessons l
           WHERE l.id = ? AND (l.assign_all = 1
              OR EXISTS (SELECT 1 FROM training_assignments a WHERE a.lesson_id = l.id AND a.user_id = ?))""",
        (lesson_id, user_id))
    return bool(row)


async def get_lesson(lesson_id: int) -> dict | None:
    return await fetch_one("SELECT * FROM training_lessons WHERE id = ?", (lesson_id,))


async def _replace_assignments(lesson_id: int, assignee_ids: list[int]):
    await execute("DELETE FROM training_assignments WHERE lesson_id = ?", (lesson_id,))
    for uid in sorted(set(assignee_ids)):
        await execute("INSERT INTO training_assignments (lesson_id, user_id) VALUES (?, ?)", (lesson_id, uid))


async def create_lesson(data: dict, created_by: int) -> int:
    now = int(time.time())
    lesson_id = await execute_returning_id(
        """INSERT INTO training_lessons (title, description, video_url, sort_order, due_date, assign_all,
           created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data["title"], data.get("description") or "", data.get("video_url") or None, data.get("sort_order", 0),
         data.get("due_date") or None, 1 if data.get("assign_all", True) else 0, created_by, now, now))
    await _replace_assignments(lesson_id, [] if data.get("assign_all", True) else data.get("assignee_ids", []))
    return lesson_id


async def update_lesson(lesson_id: int, data: dict):
    await execute(
        """UPDATE training_lessons SET title = ?, description = ?, video_url = ?, sort_order = ?, due_date = ?,
           assign_all = ?, updated_at = ? WHERE id = ?""",
        (data["title"], data.get("description") or "", data.get("video_url") or None, data.get("sort_order", 0),
         data.get("due_date") or None, 1 if data.get("assign_all", True) else 0, int(time.time()), lesson_id))
    await _replace_assignments(lesson_id, [] if data.get("assign_all", True) else data.get("assignee_ids", []))


async def delete_lesson(lesson_id: int):
    await execute("DELETE FROM training_completions WHERE lesson_id = ?", (lesson_id,))
    await execute("DELETE FROM training_assignments WHERE lesson_id = ?", (lesson_id,))
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
