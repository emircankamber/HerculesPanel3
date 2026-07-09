"""
Ortak SQLite veritabanı. Bir keyword bir kez MCP'den çekilir, buraya yazılır;
ekip aynı keyword'ü tekrar sorgularsa MCP kotası harcanmadan buradan okunur
(varsayılan: 24 saatten taze kayıtlar tekrar çekilmez — TTL değiştirilebilir).
"""
import aiosqlite
import json
import time

import os

# Vercel serverless: yalnızca /tmp yazılabilir, KALICI DEĞİL — her soğuk başlangıçta
# sıfırlanabilir. Test aşaması için kabul edilebilir (cPanel'e geçince kalıcı sunucu
# ile bu sorun ortadan kalkar). Yerelde/Railway'de çalıştırıyorsan normal dosya kullanılır.
DB_PATH = "/tmp/sellersprite_panel.db" if os.environ.get("VERCEL") else "sellersprite_panel.db"
CACHE_TTL_SECONDS = 24 * 3600  # 24 saat


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS keyword_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                fetched_at INTEGER NOT NULL,
                fetched_by TEXT,
                payload_json TEXT NOT NULL,
                verdict TEXT,
                UNIQUE(keyword, marketplace)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS market_decision (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                decision TEXT NOT NULL,
                note TEXT,
                decided_by TEXT,
                decided_at INTEGER NOT NULL
            )
        """)
        # --- Hercules Signal Engine v2.1 ---
        await db.execute("""
            CREATE TABLE IF NOT EXISTS product_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                stage TEXT NOT NULL,               -- market | sourcing | launch
                market_score REAL, demand_score REAL, truth_score REAL,
                risk_score REAL, proof_score REAL,
                opportunity_score REAL,
                is_blue_ocean INTEGER,
                compliance_review_required INTEGER DEFAULT 0,
                weights_version TEXT,
                computed_at INTEGER NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS proof_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                competitor_id TEXT,
                supplier_ref TEXT,
                type TEXT NOT NULL,                -- factory_video | sample_photo | ... (bkz signal_engine.PROOF_POINTS)
                file_url TEXT,
                points INTEGER,
                status TEXT DEFAULT 'pending',      -- pending | approved | rejected
                approved_by TEXT,
                approved_at INTEGER,
                note TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS category_cert_requirements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_key TEXT NOT NULL,
                cert_type TEXT NOT NULL,
                is_blocking INTEGER DEFAULT 1,
                note TEXT,
                approved_by_advisor INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at INTEGER NOT NULL,
                run_by TEXT,
                budget REAL, k_cat INTEGER, k_sup INTEGER,
                solver TEXT DEFAULT 'exact',
                solver_params TEXT,
                objective_value REAL,
                total_cost REAL,
                selected_json TEXT,
                explanation_text TEXT,
                explanation_status TEXT DEFAULT 'pending'   -- pending | ready | failed
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS learning_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                competitor_id TEXT,
                event_type TEXT NOT NULL,
                alpha_delta REAL, beta_delta REAL,
                alpha_after REAL, beta_after REAL, p_hat_after REAL,
                source TEXT, occurred_at INTEGER NOT NULL, recorded_by TEXT
            )
        """)
        await db.commit()


async def get_cached(keyword: str, marketplace: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM keyword_analysis WHERE keyword = ? AND marketplace = ?",
            (keyword, marketplace),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        if time.time() - row["fetched_at"] > CACHE_TTL_SECONDS:
            return None  # taze değil, yeniden çekilecek
        return json.loads(row["payload_json"])


async def save_analysis(keyword: str, marketplace: str, payload: dict, fetched_by: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO keyword_analysis (keyword, marketplace, fetched_at, fetched_by, payload_json, verdict)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(keyword, marketplace) DO UPDATE SET
                fetched_at=excluded.fetched_at,
                fetched_by=excluded.fetched_by,
                payload_json=excluded.payload_json,
                verdict=excluded.verdict
        """, (keyword, marketplace, int(time.time()), fetched_by,
              json.dumps(payload), payload.get("pre_assessment", {}).get("verdict")))
        await db.commit()


async def save_decision(keyword: str, marketplace: str, decision: str, note: str, decided_by: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO market_decision (keyword, marketplace, decision, note, decided_by, decided_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (keyword, marketplace, decision, note, decided_by, int(time.time())))
        await db.commit()


async def list_recent(limit: int = 50):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT keyword, marketplace, fetched_at, verdict FROM keyword_analysis "
            "ORDER BY fetched_at DESC LIMIT ?", (limit,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Hercules Signal Engine v2.1 — yardımcı fonksiyonlar
# ---------------------------------------------------------------------------
async def save_product_signals(keyword: str, marketplace: str, stage: str, signals: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO product_signals
            (keyword, marketplace, stage, market_score, demand_score, truth_score,
             risk_score, proof_score, opportunity_score, is_blue_ocean,
             compliance_review_required, weights_version, computed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (keyword, marketplace, stage,
              signals.get("market_score"), signals.get("demand_score"), signals.get("truth_score"),
              signals.get("risk_score"), signals.get("proof_score"), signals.get("opportunity_score"),
              int(bool(signals.get("is_blue_ocean"))), int(bool(signals.get("compliance_review_required"))),
              signals.get("weights_version", "v2.1-manual"), int(time.time())))
        await db.commit()


async def add_proof_asset(keyword: str, type_: str, points: int, file_url: str = None,
                           competitor_id: str = None, supplier_ref: str = None, note: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO proof_assets (keyword, competitor_id, supplier_ref, type, file_url, points, note)
            VALUES (?,?,?,?,?,?,?)
        """, (keyword, competitor_id, supplier_ref, type_, file_url, points, note))
        await db.commit()
        return cur.lastrowid


async def approve_proof_asset(asset_id: int, approved_by: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE proof_assets SET status='approved', approved_by=?, approved_at=? WHERE id=?
        """, (approved_by, int(time.time()), asset_id))
        await db.commit()


async def list_proof_assets(keyword: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM proof_assets WHERE keyword=?", (keyword,))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_cert_requirements(category_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM category_cert_requirements WHERE category_key=? AND is_blocking=1",
            (category_key,))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def seed_cert_requirements_if_empty():
    """Hercules dokümanı §2.7 tablosundaki başlangıç kayıtları (Hydrelon gamı)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM category_cert_requirements")
        (count,) = await cur.fetchone()
        if count > 0:
            return
        seed = [
            ("water_filtration", "NSF/ANSI 42-53 lab test raporu", 1, "Belge adları örnektir, danışman onayı gerekir"),
            ("air_purifier", "CARB / UL 2998 + elektrik güvenlik sertifikası", 1, ""),
            ("vitamin_showerhead", "Cilt teması güvenlik/malzeme raporu", 1, ""),
            ("supplement", "Danışman + tam regülasyon incelemesi", 1, "Faz 3 — kategoriye giriş şu an kapalı"),
        ]
        await db.executemany("""
            INSERT INTO category_cert_requirements (category_key, cert_type, is_blocking, note)
            VALUES (?,?,?,?)
        """, seed)
        await db.commit()


async def save_portfolio_run(budget, k_cat, k_sup, result: dict, run_by: str = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO portfolio_runs
            (run_at, run_by, budget, k_cat, k_sup, solver, objective_value, total_cost,
             selected_json, explanation_status)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (int(time.time()), run_by, budget, k_cat, k_sup, result.get("solver", "exact"),
              result.get("objective_value"), result.get("total_cost"),
              json.dumps(result.get("selected", [])), "pending"))
        await db.commit()
        return cur.lastrowid


async def update_portfolio_explanation(run_id: int, text: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE portfolio_runs SET explanation_text=?, explanation_status=? WHERE id=?",
            (text, status, run_id))
        await db.commit()


async def get_portfolio_run(run_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM portfolio_runs WHERE id=?", (run_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def record_learning_event(keyword: str, event_type: str, alpha_delta: float, beta_delta: float,
                                 alpha_after: float, beta_after: float, p_hat_after: float,
                                 source: str = None, recorded_by: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO learning_events
            (keyword, event_type, alpha_delta, beta_delta, alpha_after, beta_after,
             p_hat_after, source, occurred_at, recorded_by)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (keyword, event_type, alpha_delta, beta_delta, alpha_after, beta_after,
              p_hat_after, source, int(time.time()), recorded_by))
        await db.commit()


async def get_latest_learning_state(keyword: str):
    """Bir keyword için en son alpha/beta durumunu döndürür (yoksa None)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM learning_events WHERE keyword=? ORDER BY occurred_at DESC LIMIT 1",
            (keyword,))
        row = await cur.fetchone()
        return dict(row) if row else None
