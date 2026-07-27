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


async def _pg_conn():
    import asyncpg
    # Vercel Postgres / Neon / Supabase SSL ister
    return await asyncpg.connect(DATABASE_URL, ssl="require" if "sslmode" not in DATABASE_URL else None)


async def execute(sql: str, params: tuple = ()):
    """INSERT/UPDATE/DELETE/CREATE — sonuç döndürmez."""
    if USE_POSTGRES:
        conn = await _pg_conn()
        try:
            await conn.execute(_to_pg_placeholders(_normalize_schema(sql)), *params)
        finally:
            await conn.close()
    else:
        async with aiosqlite.connect(SQLITE_PATH) as db:
            await db.execute(sql, params)
            await db.commit()


async def execute_returning_id(sql: str, params: tuple = ()) -> int:
    """INSERT yapıp yeni kaydın id'sini döndürür."""
    if USE_POSTGRES:
        conn = await _pg_conn()
        try:
            pg_sql = _to_pg_placeholders(sql)
            if "returning" not in pg_sql.lower():
                pg_sql += " RETURNING id"
            row = await conn.fetchrow(pg_sql, *params)
            return row["id"] if row else None
        finally:
            await conn.close()
    else:
        async with aiosqlite.connect(SQLITE_PATH) as db:
            cur = await db.execute(sql, params)
            await db.commit()
            return cur.lastrowid


async def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    if USE_POSTGRES:
        conn = await _pg_conn()
        try:
            rows = await conn.fetch(_to_pg_placeholders(sql), *params)
            return [dict(r) for r in rows]
        finally:
            await conn.close()
    else:
        async with aiosqlite.connect(SQLITE_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, params)
            return [dict(r) for r in await cur.fetchall()]


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
