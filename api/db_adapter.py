"""
Veritabanı adaptörü — Postgres (üretim/paylaşımlı) veya SQLite (yerel geliştirme).

NEDEN GEREKLİ (kritik):
Vercel sunucusuz ortamında SQLite dosyası /tmp'de tutulur ve (a) her soğuk
başlangıçta silinir, (b) her sunucusuz örneğin kendine aittir. Yani ekip
üyeleri FARKLI veri görür ve kayıtlar rastgele kaybolur. Paylaşımlı ve kalıcı
geçmiş/karar için HARİCİ bir veritabanı şart.

KULLANIM:
- DATABASE_URL ortam değişkeni tanımlıysa  -> Postgres (paylaşımlı, kalıcı)
- Tanımlı değilse                          -> SQLite (yalnızca yerel test)

Placeholder farkı otomatik yönetilir: kod hep SQLite tarzı "?" yazar,
Postgres için "$1, $2, ..." formatına çevrilir.
"""
import asyncio
import contextlib
import contextvars
import os
import re

def _discover_database_url() -> str:
    """
    Neon/Supabase/Vercel entegrasyonları bağlantı adresini FARKLI isimlerle
    ekleyebiliyor. Hepsini sırayla dener — kullanıcının elle isim düzeltmesine
    gerek kalmaz.
    """
    for key in ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_URL_NON_POOLING",
                "POSTGRES_PRISMA_URL", "NEON_DATABASE_URL", "STORAGE_URL"):
        val = os.environ.get(key, "").strip()
        if val:
            return val

    # SON ÇARE: Vercel/Neon entegrasyonunda "Custom Prefix" alanı serbest metin
    # olduğu için değişken adı herhangi bir şey olabilir (STORAGE_URL, DB_URL...).
    # Bu yüzden TÜM ortam değişkenlerini tarayıp Postgres bağlantı adresi
    # biçiminde olan ilk değeri kullanırız — isim ne olursa olsun çalışır.
    for key, val in os.environ.items():
        v = (val or "").strip()
        if v.startswith("postgres://") or v.startswith("postgresql://"):
            return v
    return ""


def _clean_pg_url(url: str) -> str:
    """
    asyncpg bazı query parametrelerini (channel_binding, pgbouncer, connect_timeout
    vb.) anlamaz ve hata verir. Neon/Supabase adresleri bunları içerebiliyor.
    Yalnızca asyncpg'nin desteklediği 'sslmode' korunur, diğerleri atılır.
    """
    if "?" not in url:
        return url
    base, _, query = url.partition("?")
    kept = [p for p in query.split("&") if p.lower().startswith("sslmode=")]
    return base + ("?" + "&".join(kept) if kept else "")


DATABASE_URL = _clean_pg_url(_discover_database_url())
USE_POSTGRES = bool(DATABASE_URL)

if not USE_POSTGRES:
    import aiosqlite
    SQLITE_PATH = "/tmp/sellersprite_panel.db" if os.environ.get("VERCEL") else "sellersprite_panel.db"


def _to_pg_placeholders(sql: str) -> str:
    """SQLite '?' placeholder'larını Postgres '$1, $2...' formatına çevirir."""
    counter = {"n": 0}

    def repl(_):
        counter["n"] += 1
        return f"${counter['n']}"

    return re.sub(r"\?", repl, sql)


def _normalize_schema(sql: str) -> str:
    """SQLite şema sözdizimini Postgres'e uyarlar."""
    if not USE_POSTGRES:
        return sql
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    return sql


# ---------------------------------------------------------------------------
# POSTGRES BAĞLANTI HAVUZU
# Eskiden her sorgu yeni bir asyncpg.connect açıp kapatıyordu (her biri TCP + TLS + auth turu).
# Artık ilk kullanımda bir kez kurulan küçük bir havuz (en fazla 5) tekrar kullanılır.
# statement_cache_size=0 ŞART: Neon adresi PgBouncer (transaction pooling) üzerinden olabilir;
# hazırlanmış ifade önbelleği orada "prepared statement ... does not exist" hataları verir.
# Havuz olay döngüsüne bağlıdır: döngü değişirse (testler, sunucusuz çalışma zamanı) yeniden kurulur.
# SQLite yolu değişmedi.
# ---------------------------------------------------------------------------
POOL_MAX_SIZE = 5
_pool = None
_pool_loop = None
_pool_lock = None
# Migrasyonların TEK bağlantıda çalışması için: bu bağlam içinde tüm sorgular aynı bağlantıyı kullanır.
_pinned = contextvars.ContextVar("db_pinned_conn", default=None)


async def _reset_conn(conn):
    """Havuza dönen bağlantı için asyncpg'nin varsayılan sıfırlaması her bırakışta fazladan bir sorgu
    (advisory unlock/CLOSE ALL/UNLISTEN/RESET ALL) = fazladan bir ağ turu demek. Bu uygulama oturum durumu
    (SET, LISTEN, advisory kilit, imleç) kullanmıyor; yalnızca yarım kalmış bir işlem varsa geri alınır."""
    if conn.is_in_transaction():
        await conn.execute("ROLLBACK")


def _ssl_arg():
    # Vercel Postgres / Neon / Supabase SSL ister
    return "require" if "sslmode" not in DATABASE_URL else None


async def _get_pool():
    global _pool, _pool_loop, _pool_lock
    import asyncpg
    loop = asyncio.get_running_loop()
    if _pool is not None and _pool_loop is loop:
        return _pool
    if _pool_lock is None or _pool_lock[0] is not loop:
        _pool_lock = (loop, asyncio.Lock())
    async with _pool_lock[1]:
        if _pool is not None and _pool_loop is loop:
            return _pool
        old = _pool
        _pool, _pool_loop = None, None
        if old is not None:
            try:
                old.terminate()   # eski (kapanmış) döngünün bağlantıları
            except Exception:
                pass
        _pool = await asyncpg.create_pool(
            DATABASE_URL, ssl=_ssl_arg(), min_size=0, max_size=POOL_MAX_SIZE,
            statement_cache_size=0, max_inactive_connection_lifetime=60, reset=_reset_conn)
        _pool_loop = loop
        return _pool


def _is_dead_connection(exc: Exception) -> bool:
    """Havuzdaki bağlantı sunucu/ağ tarafından kapatılmışsa (ör. sunucusuz örnek uzun süre donduysa)."""
    import asyncpg
    if isinstance(exc, (asyncpg.exceptions.ConnectionDoesNotExistError, ConnectionResetError,
                        BrokenPipeError, ConnectionAbortedError)):
        return True
    # Sunucu boştaki bağlantıyı kapattığında (Neon idle timeout / pg_terminate_backend) gelen kendiliğinden
    # hata mesajı protokolü meşgul bırakır; yeni sorgu GÖNDERİLMEDEN bu hata atılır -> tekrar denemek güvenli.
    if isinstance(exc, asyncpg.exceptions.InternalClientError) and "another operation" in str(exc):
        return True
    return isinstance(exc, asyncpg.exceptions.InterfaceError) and "closed" in str(exc).lower()


async def _pg_run(fn):
    """fn(conn) çalıştırır: sabitlenmiş bağlantı varsa onu, yoksa havuzdan bir bağlantı kullanır.
    Havuzdan gelen bağlantı ölü çıkarsa (sorgu sunucuya ulaşmadan) bir kez taze bağlantıyla dener."""
    pinned = _pinned.get()
    if pinned is not None:
        return await fn(pinned)
    pool = await _get_pool()
    for attempt in (0, 1):
        conn = await pool.acquire()
        try:
            return await fn(conn)
        except Exception as e:
            if attempt == 0 and _is_dead_connection(e):
                try:
                    conn.terminate()          # havuz bunu yeni bir bağlantıyla değiştirir
                    await pool.release(conn)
                except Exception:
                    pass
                conn = None
                await pool.expire_connections()
                continue
            raise
        finally:
            if conn is not None:
                try:
                    await pool.release(conn)
                except Exception:
                    pass


@contextlib.asynccontextmanager
async def single_connection():
    """Postgres'te bu blok içindeki TÜM sorgular tek bir bağlantıyı kullanır (migrasyonlar).
    SQLite'ta etkisiz (yol aynı kaldı)."""
    if not USE_POSTGRES or _pinned.get() is not None:
        yield
        return
    pool = await _get_pool()
    conn = await pool.acquire()
    tok = _pinned.set(conn)
    try:
        yield
    finally:
        _pinned.reset(tok)
        await pool.release(conn)


async def execute(sql: str, params: tuple = ()):
    """INSERT/UPDATE/DELETE/CREATE — sonuç döndürmez."""
    if USE_POSTGRES:
        q = _to_pg_placeholders(_normalize_schema(sql))
        await _pg_run(lambda conn: conn.execute(q, *params))
    else:
        async with aiosqlite.connect(SQLITE_PATH) as db:
            await db.execute(sql, params)
            await db.commit()


async def execute_returning_id(sql: str, params: tuple = ()) -> int:
    """INSERT yapıp yeni kaydın id'sini döndürür."""
    if USE_POSTGRES:
        pg_sql = _to_pg_placeholders(sql)
        if "returning" not in pg_sql.lower():
            pg_sql += " RETURNING id"
        row = await _pg_run(lambda conn: conn.fetchrow(pg_sql, *params))
        return row["id"] if row else None
    else:
        async with aiosqlite.connect(SQLITE_PATH) as db:
            cur = await db.execute(sql, params)
            await db.commit()
            return cur.lastrowid


async def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    if USE_POSTGRES:
        q = _to_pg_placeholders(sql)
        rows = await _pg_run(lambda conn: conn.fetch(q, *params))
        return [dict(r) for r in rows]
    else:
        async with aiosqlite.connect(SQLITE_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, params)
            return [dict(r) for r in await cur.fetchall()]


async def execute_fetch(sql: str, params: tuple = ()) -> list[dict]:
    """Değiştiren + satır döndüren sorgu (UPDATE/INSERT ... RETURNING) — SQLite'ta COMMIT eder.
    (fetch_all commit etmez; RETURNING'li bir UPDATE'i onunla çalıştırmak değişikliği kaybeder.)
    Tek ifadede koşul + değişiklik: ör. tek kullanımlık davetin eşzamanlı iki kayıtta iki kez kullanılmaması."""
    if USE_POSTGRES:
        q = _to_pg_placeholders(sql)
        rows = await _pg_run(lambda conn: conn.fetch(q, *params))
        return [dict(r) for r in rows]
    else:
        async with aiosqlite.connect(SQLITE_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, params)
            rows = [dict(r) for r in await cur.fetchall()]
            await db.commit()
            return rows


async def fetch_one(sql: str, params: tuple = ()) -> dict | None:
    rows = await fetch_all(sql, params)
    return rows[0] if rows else None


def storage_info() -> dict:
    """Panelde 'verileriniz paylaşımlı mı' uyarısı göstermek için."""
    return {
        "backend": "postgres" if USE_POSTGRES else "sqlite",
        "shared_and_persistent": USE_POSTGRES,
        "warning": None if USE_POSTGRES else (
            "Postgres bağlantısı bulunamadı (DATABASE_URL / POSTGRES_URL) — veriler geçici SQLite'ta tutuluyor. "
            "Vercel'de bu KALICI DEĞİLDİR ve ekip üyeleri farklı veri görebilir. "
            "Paylaşımlı kullanım için bir Postgres bağlantısı (DATABASE_URL) ekleyin."
        ),
    }
