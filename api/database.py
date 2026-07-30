"""
Veritabanı katmanı — db_adapter üzerinden Postgres (paylaşımlı/kalıcı) ya da
SQLite (yerel test) ile çalışır. Bkz. db_adapter.py docstring.
"""
import json
import time
import hashlib
import secrets
from db_adapter import execute, execute_returning_id, fetch_all, fetch_one, storage_info, USE_POSTGRES

CACHE_TTL_SECONDS = 24 * 3600

_SCHEMAS = [
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


async def init_db():
    for schema in _SCHEMAS:
        await execute(schema)


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
    user_id = await execute_returning_id(
        "INSERT INTO users (email, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
        (email, pw_hash, salt, int(time.time())))
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
    return json.loads(row["payload_json"])


async def save_analysis(keyword: str, marketplace: str, payload: dict, fetched_by: str = None):
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
