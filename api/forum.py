"""
TOPLULUK & FORUM — veri katmanı ve doğrulama. MCP çağrısı YOK.

- Erişim/görünürlük kuralları index.py'de (her uçta `_visible_thread`): "Sadece ekip üyeleri" başlıkları
  hiçbir ekipte olmayan kullanıcıya listede, aramada, bültende, istatistikte ve doğrudan linkte GÖRÜNMEZ (404).
- Metin DÜZ METİN olarak saklanır; HTML'e çevirme (bağlantı, @bahsetme, **kalın**, `kod`, ``` blok ```, listeler)
  yalnızca panelde (app.js::forumRich) her parça kaçırılarak yapılır.
- Bildirimler notify.py'de; "takip" = forum_saves (başlığı açan ve cevap yazan otomatik takipçi olur).
- Forum küçük ölçekli: listeler görünür başlıkların tamamı çekilip Python'da süzülür/sıralanır/sayfalanır.
"""
import json
import re
import time

from db_adapter import execute, execute_fetch, execute_returning_id, fetch_all, fetch_one
import notify

KINDS = {"question": "Soru", "discussion": "Tartışma", "bulletin": "Bülten"}
VISIBILITIES = ("public", "team")
DEFAULT_CATEGORIES = ["Ürün Araştırma & ASIN Doğrulama", "PPC & ACOS Optimizasyonu", "Çin Tedarik & 1688 / Alibaba",
                      "Amazon Hukuk, Patent & Askı", "Lojistik & Gümrük", "Yapay Zeka & Otomasyon", "Genel"]
TITLE_MIN, TITLE_MAX = 5, 160
BODY_MAX, REPLY_MAX = 10000, 5000
MAX_TAGS, TAG_MAX = 5, 30
PAGE_SIZE = 10
HOT_DAYS = 7
# Paylaşım sınırı (başlık + cevap birlikte): (pencere sn, en fazla). Ayrıca yeni başlık için ayrı sınır.
POST_LIMITS = ((60, 5), (3600, 40))
THREAD_LIMITS = ((600, 3),)
# Okunmamış: hiç açılmamış başlıklar için başlangıç çizgisi (kişi forumu ilk açtığında "son 7 gün yeni" sayılır)
UNREAD_BASELINE_DAYS = 7
UNANSWERED_STALE_SECONDS = 24 * 3600
# @bahsetme: profildeki kullanıcı adı biçimi (index.USERNAME_RE ile aynı); gönderi başına en fazla
MENTION_RE = re.compile(r"(?<![\w@./])@([a-z0-9][a-z0-9._-]{1,28}[a-z0-9])", re.IGNORECASE)
MAX_MENTIONS = 10
# Görseller: istemci küçültür/sıkıştırır; sunucu türü dosya imzasından (magic bytes) belirler. SVG YOK.
IMAGE_MAX_BYTES = 1_500_000
IMAGES_PER_POST = 4
IMAGE_UPLOADS_PER_HOUR = 30
ORPHAN_IMAGE_SECONDS = 24 * 3600
REPORT_REASON_MAX = 500

SCHEMAS = [
    """CREATE TABLE IF NOT EXISTS forum_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS forum_threads (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, category_id INTEGER,
        kind TEXT NOT NULL, visibility TEXT NOT NULL DEFAULT 'public', title TEXT NOT NULL, body TEXT NOT NULL,
        tags_json TEXT NOT NULL DEFAULT '[]', pinned INTEGER NOT NULL DEFAULT 0, locked INTEGER NOT NULL DEFAULT 0,
        solution_reply_id INTEGER, solved_at INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        edited_at INTEGER, last_activity_at INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS forum_replies (
        id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        body TEXT NOT NULL, created_at INTEGER NOT NULL, edited_at INTEGER)""",
    # "Faydalı" oyu: kişi başına içerik başına BİR (birincil anahtar).
    """CREATE TABLE IF NOT EXISTS forum_votes (
        target_type TEXT NOT NULL, target_id INTEGER NOT NULL, user_id INTEGER NOT NULL, created_at INTEGER NOT NULL,
        PRIMARY KEY (target_type, target_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS forum_saves (
        thread_id INTEGER NOT NULL, user_id INTEGER NOT NULL, created_at INTEGER NOT NULL,
        PRIMARY KEY (thread_id, user_id))""",
    # Görüntülenme = başlığı açan BENZERSİZ kişi sayısı (yenilemeyle şişmez).
    """CREATE TABLE IF NOT EXISTS forum_views (
        thread_id INTEGER NOT NULL, user_id INTEGER NOT NULL, created_at INTEGER NOT NULL,
        PRIMARY KEY (thread_id, user_id))""",
    # Okunma: kişinin başlığı en son açtığı an + o anda görülen EN SON cevap kimliği (saniye çözünürlüğünde aynı anda
    # yazılan cevap kaçmasın / yanlışlıkla "yeni" kalmasın diye kimlikle karşılaştırılır). thread_id = 0 satırı kişinin
    # başlangıç çizgisi ("tümünü okundu say").
    """CREATE TABLE IF NOT EXISTS forum_reads (
        thread_id INTEGER NOT NULL, user_id INTEGER NOT NULL, last_read_at INTEGER NOT NULL,
        last_reply_id INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (thread_id, user_id))""",
    # İçerik bildirme: kişi başına içerik başına bir kayıt; owner/admin çözer.
    """CREATE TABLE IF NOT EXISTS forum_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, target_type TEXT NOT NULL, target_id INTEGER NOT NULL,
        thread_id INTEGER NOT NULL, user_id INTEGER NOT NULL, reason TEXT NOT NULL, created_at INTEGER NOT NULL,
        resolved_at INTEGER, resolved_by INTEGER)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_forum_reports ON forum_reports (target_type, target_id, user_id)",
    # Görseller veritabanında (base64) — erişim başlığın görünürlüğüyle aynı (index.forum_image). Önce yüklenir
    # (thread_id NULL), gönderi kaydedilince bağlanır; 24 saat bağlanmayan silinir.
    """CREATE TABLE IF NOT EXISTS forum_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, thread_id INTEGER, reply_id INTEGER,
        mime TEXT NOT NULL, size INTEGER NOT NULL, data TEXT NOT NULL, created_at INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_forum_images_thread ON forum_images (thread_id)",
]
# Hesap silinince kişinin satırları (USER_ID_TABLES gibi); başlık/cevapları ayrıca delete_user_content siler.
USER_ROWS = ["forum_votes", "forum_saves", "forum_views", "forum_reads", "forum_reports", "forum_images"]


class ForumError(ValueError):
    pass


# --- Doğrulama --------------------------------------------------------------
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TAG_RE = re.compile(r"^[\w.+-]{1,%d}$" % TAG_MAX)


def clean_text(v, label: str, lo: int, hi: int, single_line: bool = False) -> str:
    v = _CTRL.sub("", v or "").replace("\r\n", "\n").replace("\r", "\n")
    v = " ".join(v.split()) if single_line else re.sub(r"\n{4,}", "\n\n\n", v).strip()
    if len(v) < lo:
        raise ForumError(f"{label} en az {lo} karakter olmalı" if lo > 1 else f"{label} boş olamaz")
    if len(v) > hi:
        raise ForumError(f"{label} en fazla {hi} karakter olabilir")
    return v


def clean_tags(tags) -> list[str]:
    out, seen = [], set()
    for t in tags if isinstance(tags, list) else []:
        t = "-".join(str(t or "").strip().lstrip("#").split())
        if not t:
            continue
        if not _TAG_RE.match(t):
            raise ForumError(f"Etiket yalnızca harf, rakam, '-', '_', '.', '+' içerebilir (en fazla {TAG_MAX} karakter): {t[:40]}")
        if t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
    if len(out) > MAX_TAGS:
        raise ForumError(f"En fazla {MAX_TAGS} etiket eklenebilir")
    return out


# --- Kategoriler -------------------------------------------------------------
async def seed_once():
    """Varsayılan kategoriler bir kez (schema_flags.forum_seeded); owner sonradan değiştirse de geri gelmez."""
    if await fetch_one("SELECT 1 AS ok FROM schema_flags WHERE key = 'forum_seeded'"):
        return
    now = int(time.time())
    try:
        await execute("INSERT INTO schema_flags (key, value, set_at) VALUES ('forum_seeded', '1', ?)", (now,))
    except Exception:
        return
    for i, name in enumerate(DEFAULT_CATEGORIES):
        await execute("INSERT INTO forum_categories (name, sort_order, created_at) VALUES (?, ?, ?)", (name, (i + 1) * 10, now))


async def list_categories() -> list[dict]:
    return await fetch_all("SELECT id, name, sort_order FROM forum_categories ORDER BY sort_order, id")


async def get_category(cid: int) -> dict | None:
    return await fetch_one("SELECT id, name, sort_order FROM forum_categories WHERE id = ?", (cid,))


async def _name_free(name: str, except_id: int | None = None):
    for c in await list_categories():
        if c["id"] != except_id and c["name"].casefold() == name.casefold():
            raise ForumError("Bu adla bir kategori zaten var")


async def create_category(name: str) -> int:
    name = clean_text(name, "Kategori adı", 2, 60, single_line=True)
    await _name_free(name)
    row = await fetch_one("SELECT MAX(sort_order) AS m FROM forum_categories")
    return await execute_returning_id("INSERT INTO forum_categories (name, sort_order, created_at) VALUES (?, ?, ?)",
                                      (name, int((row or {}).get("m") or 0) + 10, int(time.time())))


async def update_category(cid: int, name: str | None, sort_order: int | None):
    if name is not None:
        name = clean_text(name, "Kategori adı", 2, 60, single_line=True)
        await _name_free(name, cid)
        await execute("UPDATE forum_categories SET name = ? WHERE id = ?", (name, cid))
    if sort_order is not None:
        await execute("UPDATE forum_categories SET sort_order = ? WHERE id = ?", (sort_order, cid))


async def delete_category(cid: int) -> bool:
    """Yalnızca BOŞ kategori silinir (başlıklar kategorisiz kalmasın)."""
    if await fetch_one("SELECT 1 AS ok FROM forum_threads WHERE category_id = ? LIMIT 1", (cid,)):
        return False
    await execute("DELETE FROM forum_categories WHERE id = ?", (cid,))
    return True


# --- Başlıklar ---------------------------------------------------------------
def visible(thread: dict | None, can_see_team: bool) -> bool:
    return bool(thread) and (thread["visibility"] == "public" or can_see_team)


async def get_thread(tid: int) -> dict | None:
    t = await fetch_one("SELECT * FROM forum_threads WHERE id = ?", (tid,))
    if t:
        t["tags"] = json.loads(t.pop("tags_json") or "[]")
    return t


async def get_reply(rid: int) -> dict | None:
    return await fetch_one("SELECT * FROM forum_replies WHERE id = ?", (rid,))


async def rate_limited(user_id: int, new_thread: bool) -> str | None:
    """Sınır aşıldıysa kullanıcıya gösterilecek mesaj; değilse None."""
    now = int(time.time())
    for window, limit in POST_LIMITS:
        a = await fetch_one("SELECT COUNT(*) AS c FROM forum_threads WHERE user_id = ? AND created_at > ?", (user_id, now - window))
        b = await fetch_one("SELECT COUNT(*) AS c FROM forum_replies WHERE user_id = ? AND created_at > ?", (user_id, now - window))
        if int(a["c"]) + int(b["c"]) >= limit:
            return f"Çok hızlı paylaşım: {window // 60 or 1} dakikada en fazla {limit} paylaşım yapılabilir — biraz sonra tekrar deneyin"
    if new_thread:
        for window, limit in THREAD_LIMITS:
            a = await fetch_one("SELECT COUNT(*) AS c FROM forum_threads WHERE user_id = ? AND created_at > ?", (user_id, now - window))
            if int(a["c"]) >= limit:
                return f"Çok hızlı paylaşım: {window // 60} dakikada en fazla {limit} yeni başlık açılabilir — biraz sonra tekrar deneyin"
    return None


async def create_thread(user_id: int, d: dict) -> int:
    """d: category_id, kind, visibility, title, body, tags; isteğe bağlı analysis {key, marketplace, brief} ve
    lesson_id (ders soru-cevap başlığı — ders başına tek, ux_forum_threads_lesson)."""
    now = int(time.time())
    a = d.get("analysis") or {}
    tid = await execute_returning_id(
        "INSERT INTO forum_threads (user_id, category_id, kind, visibility, title, body, tags_json, pinned, locked, "
        "created_at, updated_at, last_activity_at, analysis_key, analysis_market, analysis_json, lesson_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, d["category_id"], d["kind"], d["visibility"], d["title"], d["body"],
         json.dumps(d["tags"], ensure_ascii=False), now, now, now,
         a.get("key"), a.get("marketplace"), json.dumps(a["brief"], ensure_ascii=False) if a.get("brief") else None,
         d.get("lesson_id")))
    await set_saved(tid, user_id, True)   # başlığı açan otomatik takipçi
    await mark_read(tid, user_id)
    return tid


async def update_thread(tid: int, d: dict):
    now = int(time.time())
    await execute("UPDATE forum_threads SET category_id = ?, kind = ?, visibility = ?, title = ?, body = ?, tags_json = ?, "
                  "updated_at = ?, edited_at = ? WHERE id = ?",
                  (d["category_id"], d["kind"], d["visibility"], d["title"], d["body"],
                   json.dumps(d["tags"], ensure_ascii=False), now, now, tid))
    if d["kind"] == "bulletin":   # bültenin "çözümü" olmaz
        await execute("UPDATE forum_threads SET solution_reply_id = NULL, solved_at = NULL WHERE id = ?", (tid,))


async def delete_thread(tid: int):
    rids = [r["id"] for r in await fetch_all("SELECT id FROM forum_replies WHERE thread_id = ?", (tid,))]
    for rid in rids:
        await execute("DELETE FROM forum_votes WHERE target_type = 'reply' AND target_id = ?", (rid,))
    await execute("DELETE FROM forum_votes WHERE target_type = 'thread' AND target_id = ?", (tid,))
    await execute("DELETE FROM forum_replies WHERE thread_id = ?", (tid,))
    for t in ("forum_saves", "forum_views", "forum_reads", "forum_reports", "forum_images"):
        await execute(f"DELETE FROM {t} WHERE thread_id = ?", (tid,))
    await notify.delete_for(thread_id=tid)
    await execute("DELETE FROM forum_threads WHERE id = ?", (tid,))


async def set_flag(tid: int, flag: str, value: bool):
    assert flag in ("pinned", "locked")
    await execute(f"UPDATE forum_threads SET {flag} = ? WHERE id = ?", (1 if value else 0, tid))


async def add_reply(tid: int, user_id: int, body: str) -> int | None:
    """Kilit koşulu INSERT'in İÇİNDE: kilitli başlığa (eşzamanlı kilitlense bile) cevap eklenmez -> None.
    Cevap yazan başlığı otomatik takip eder."""
    now = int(time.time())
    rows = await execute_fetch(
        "INSERT INTO forum_replies (thread_id, user_id, body, created_at) SELECT ?, ?, ?, ? "
        "WHERE EXISTS (SELECT 1 FROM forum_threads WHERE id = ? AND locked = 0) RETURNING id",
        (tid, user_id, body, now, tid))
    if not rows:
        return None
    await execute("UPDATE forum_threads SET last_activity_at = ? WHERE id = ?", (now, tid))
    await set_saved(tid, user_id, True)
    await mark_read(tid, user_id)
    return rows[0]["id"]


async def update_reply(rid: int, body: str):
    await execute("UPDATE forum_replies SET body = ?, edited_at = ? WHERE id = ?", (body, int(time.time()), rid))


async def delete_reply(rid: int):
    await execute("UPDATE forum_threads SET solution_reply_id = NULL, solved_at = NULL WHERE solution_reply_id = ?", (rid,))
    await execute("DELETE FROM forum_votes WHERE target_type = 'reply' AND target_id = ?", (rid,))
    await execute("DELETE FROM forum_reports WHERE target_type = 'reply' AND target_id = ?", (rid,))
    await execute("DELETE FROM forum_images WHERE reply_id = ?", (rid,))
    await notify.delete_for(reply_ids=[rid])
    await execute("DELETE FROM forum_replies WHERE id = ?", (rid,))


async def set_solution(tid: int, rid: int | None):
    if rid is None:
        await execute("UPDATE forum_threads SET solution_reply_id = NULL, solved_at = NULL WHERE id = ?", (tid,))
    else:
        await execute("UPDATE forum_threads SET solution_reply_id = ?, solved_at = ? WHERE id = ?", (rid, int(time.time()), tid))


async def add_vote(target_type: str, target_id: int, user_id: int) -> bool:
    """False = bu kişi zaten oy vermiş (birincil anahtar; eşzamanlı çift tıklamada da tek oy)."""
    rows = await execute_fetch(
        "INSERT INTO forum_votes (target_type, target_id, user_id, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (target_type, target_id, user_id) DO NOTHING RETURNING user_id",
        (target_type, target_id, user_id, int(time.time())))
    return bool(rows)


async def remove_vote(target_type: str, target_id: int, user_id: int):
    await execute("DELETE FROM forum_votes WHERE target_type = ? AND target_id = ? AND user_id = ?", (target_type, target_id, user_id))


async def set_saved(tid: int, user_id: int, saved: bool):
    if saved:
        await execute("INSERT INTO forum_saves (thread_id, user_id, created_at) VALUES (?, ?, ?) "
                      "ON CONFLICT (thread_id, user_id) DO NOTHING", (tid, user_id, int(time.time())))
    else:
        await execute("DELETE FROM forum_saves WHERE thread_id = ? AND user_id = ?", (tid, user_id))


async def record_view(tid: int, user_id: int):
    await execute("INSERT INTO forum_views (thread_id, user_id, created_at) VALUES (?, ?, ?) "
                  "ON CONFLICT (thread_id, user_id) DO NOTHING", (tid, user_id, int(time.time())))


# --- Okuma (toplu) -----------------------------------------------------------
def _counts(rows, key="k") -> dict:
    return {r[key]: int(r["c"]) for r in rows}


async def visible_threads(can_see_team: bool, viewer_id: int) -> list[dict]:
    """Görünür TÜM başlıklar + sayaçlar + yazar adı/unvanı + çözüm özeti. Kısıtlı başlık filtresi SQL'de."""
    where = "" if can_see_team else " WHERE t.visibility = 'public'"
    rows = await fetch_all(
        "SELECT t.*, u.first_name, u.last_name, u.username AS author_username, u.title AS author_title FROM forum_threads t "
        f"LEFT JOIN users u ON u.id = t.user_id{where}")
    if not rows:
        return []
    replies = _counts(await fetch_all("SELECT thread_id AS k, COUNT(*) AS c FROM forum_replies GROUP BY thread_id"))
    views = _counts(await fetch_all("SELECT thread_id AS k, COUNT(*) AS c FROM forum_views GROUP BY thread_id"))
    votes = _counts(await fetch_all("SELECT target_id AS k, COUNT(*) AS c FROM forum_votes WHERE target_type = 'thread' GROUP BY target_id"))
    mine_v = {r["target_id"] for r in await fetch_all("SELECT target_id FROM forum_votes WHERE target_type = 'thread' AND user_id = ?", (viewer_id,))}
    mine_s = {r["thread_id"] for r in await fetch_all("SELECT thread_id FROM forum_saves WHERE user_id = ?", (viewer_id,))}
    sol_ids = [r["solution_reply_id"] for r in rows if r.get("solution_reply_id")]
    sols = {}
    if sol_ids:
        marks = ",".join("?" * len(sol_ids))
        for s in await fetch_all(f"SELECT r.id, r.body, r.user_id, u.first_name, u.last_name, u.username AS author_username, "
                                 f"u.title AS author_title "
                                 f"FROM forum_replies r LEFT JOIN users u ON u.id = r.user_id WHERE r.id IN ({marks})", tuple(sol_ids)):
            sols[s["id"]] = s
    baseline, reads = await read_state(viewer_id)
    fresh: dict[int, int] = {}   # başlık -> kişinin son okumasından sonra BAŞKASININ yazdığı cevap sayısı
    for x in await fetch_all("SELECT id, thread_id, user_id FROM forum_replies WHERE created_at > ?", (baseline,)):
        if x["user_id"] != viewer_id and x["id"] > reads.get(x["thread_id"], 0):
            fresh[x["thread_id"]] = fresh.get(x["thread_id"], 0) + 1
    for r in rows:
        r["tags"] = json.loads(r.pop("tags_json") or "[]")
        r["new_replies"] = fresh.get(r["id"], 0)
        r["is_new"] = r["id"] not in reads and r["created_at"] > baseline and r["user_id"] != viewer_id
        r["reply_count"] = replies.get(r["id"], 0)
        r["views"] = views.get(r["id"], 0)
        r["useful"] = votes.get(r["id"], 0)
        r["voted"] = r["id"] in mine_v
        r["saved"] = r["id"] in mine_s
        r["solution"] = sols.get(r.get("solution_reply_id"))
    return rows


async def replies_of(tid: int, viewer_id: int) -> list[dict]:
    rows = await fetch_all("SELECT r.*, u.first_name, u.last_name, u.username AS author_username, u.title AS author_title "
                           "FROM forum_replies r "
                           "LEFT JOIN users u ON u.id = r.user_id WHERE r.thread_id = ? ORDER BY r.created_at, r.id", (tid,))
    if rows:
        ids = [r["id"] for r in rows]
        marks = ",".join("?" * len(ids))
        votes = _counts(await fetch_all(f"SELECT target_id AS k, COUNT(*) AS c FROM forum_votes WHERE target_type = 'reply' "
                                        f"AND target_id IN ({marks}) GROUP BY target_id", tuple(ids)))
        mine = {r["target_id"] for r in await fetch_all(f"SELECT target_id FROM forum_votes WHERE target_type = 'reply' "
                                                        f"AND user_id = ? AND target_id IN ({marks})", (viewer_id, *ids))}
        for r in rows:
            r["useful"] = votes.get(r["id"], 0)
            r["voted"] = r["id"] in mine
    return rows


async def interactions_since(since: int) -> dict[int, int]:
    """Başlık başına son dönemdeki etkileşim: cevap + "Faydalı" oyu (başlığa ya da cevaplarına)."""
    out: dict[int, int] = {}
    for r in await fetch_all("SELECT thread_id AS k, COUNT(*) AS c FROM forum_replies WHERE created_at >= ? GROUP BY thread_id", (since,)):
        out[r["k"]] = out.get(r["k"], 0) + int(r["c"])
    for r in await fetch_all("SELECT target_id AS k, COUNT(*) AS c FROM forum_votes WHERE target_type = 'thread' AND created_at >= ? "
                             "GROUP BY target_id", (since,)):
        out[r["k"]] = out.get(r["k"], 0) + int(r["c"])
    for r in await fetch_all("SELECT fr.thread_id AS k, COUNT(*) AS c FROM forum_votes v JOIN forum_replies fr ON fr.id = v.target_id "
                             "WHERE v.target_type = 'reply' AND v.created_at >= ? GROUP BY fr.thread_id", (since,)):
        out[r["k"]] = out.get(r["k"], 0) + int(r["c"])
    return out


async def contributors_since(since: int, thread_ids: set[int]) -> list[dict]:
    """Son dönemde (görünür başlıklarda) yazılan cevap ve çözüm seçilen cevap sayısı — gerçek veri, puan yok."""
    stats: dict[int, dict] = {}
    for r in await fetch_all("SELECT r.user_id, r.thread_id, u.first_name, u.last_name, u.title AS author_title FROM forum_replies r "
                             "LEFT JOIN users u ON u.id = r.user_id WHERE r.created_at >= ?", (since,)):
        if r["thread_id"] in thread_ids:
            s = stats.setdefault(r["user_id"], {"user": r, "replies": 0, "solutions": 0})
            s["replies"] += 1
    for r in await fetch_all("SELECT t.id AS thread_id, r.user_id, u.first_name, u.last_name, u.title AS author_title "
                             "FROM forum_threads t JOIN forum_replies r ON r.id = t.solution_reply_id "
                             "LEFT JOIN users u ON u.id = r.user_id WHERE t.solved_at >= ?", (since,)):
        if r["thread_id"] in thread_ids:
            s = stats.setdefault(r["user_id"], {"user": r, "replies": 0, "solutions": 0})
            s["solutions"] += 1
    return list(stats.values())


async def delete_user_content(user_id: int):
    """Hesap silme: kişinin başlıkları (cevaplarıyla), cevapları, oyları, kayıtları, görüntülemeleri, okumaları,
    bildirdiği içerikler, görselleri ve bildirimleri silinir."""
    for t in await fetch_all("SELECT id FROM forum_threads WHERE user_id = ?", (user_id,)):
        await delete_thread(t["id"])
    for r in await fetch_all("SELECT id FROM forum_replies WHERE user_id = ?", (user_id,)):
        await delete_reply(r["id"])
    for t in USER_ROWS:
        await execute(f"DELETE FROM {t} WHERE user_id = ?", (user_id,))
    await execute("UPDATE forum_reports SET resolved_by = NULL WHERE resolved_by = ?", (user_id,))
    await notify.delete_user(user_id)


# --- Takip & okunma ------------------------------------------------------------
async def followers(tid: int) -> set[int]:
    return {r["user_id"] for r in await fetch_all("SELECT user_id FROM forum_saves WHERE thread_id = ?", (tid,))}


async def mark_read(tid: int, user_id: int):
    """Başlık okundu: o andaki en son cevap kimliği saklanır (çağıran, cevapları BUNDAN SONRA okumalı)."""
    await execute("INSERT INTO forum_reads (thread_id, user_id, last_read_at, last_reply_id) "
                  "VALUES (?, ?, ?, (SELECT COALESCE(MAX(id), 0) FROM forum_replies WHERE thread_id = ?)) "
                  "ON CONFLICT (thread_id, user_id) DO UPDATE SET last_read_at = excluded.last_read_at, "
                  "last_reply_id = excluded.last_reply_id", (tid, user_id, int(time.time()), tid))


async def read_state(user_id: int) -> tuple[int, dict[int, int]]:
    """(başlangıç çizgisi, {başlık: görülen son cevap kimliği}). Çizgi yoksa (forumu ilk açış) son 7 gün "yeni"
    sayılacak şekilde yazılır. Bir cevap "yeni" = çizgiden sonra yazılmış VE kimliği görülenden büyük."""
    rows = await fetch_all("SELECT thread_id, last_read_at, last_reply_id FROM forum_reads WHERE user_id = ?", (user_id,))
    reads = {r["thread_id"]: int(r["last_reply_id"] or 0) for r in rows if r["thread_id"] != 0}
    base = next((r["last_read_at"] for r in rows if r["thread_id"] == 0), None)
    if base is None:
        base = int(time.time()) - UNREAD_BASELINE_DAYS * 86400
        await execute("INSERT INTO forum_reads (thread_id, user_id, last_read_at, last_reply_id) VALUES (0, ?, ?, 0) "
                      "ON CONFLICT (thread_id, user_id) DO NOTHING", (user_id, base))
    return base, reads


async def mark_all_read(user_id: int):
    await mark_read(0, user_id)


# --- @bahsetme ----------------------------------------------------------------
def extract_mentions(text: str) -> set[str]:
    out = []
    for m in MENTION_RE.finditer(text or ""):
        name = m.group(1).lower()
        if name not in out:
            out.append(name)
    return set(out[:MAX_MENTIONS])


async def users_by_username(names: set[str]) -> dict[str, int]:
    if not names:
        return {}
    names = sorted(names)
    marks = ",".join("?" * len(names))
    rows = await fetch_all(f"SELECT id, LOWER(username) AS u FROM users WHERE LOWER(username) IN ({marks})", tuple(names))
    return {r["u"]: r["id"] for r in rows}


async def search_people(q: str, limit: int = 8) -> list[dict]:
    """@bahsetme önerisi: kullanıcı adı ya da ad soyad içinde geçen (kullanıcı adı OLAN) kişiler."""
    q = (q or "").strip().lstrip("@").lower()
    rows = await fetch_all("SELECT id, username, first_name, last_name, title FROM users "
                           "WHERE username IS NOT NULL AND username <> '' ORDER BY username")
    def hit(r):
        name = f"{r.get('first_name') or ''} {r.get('last_name') or ''}".lower()
        return not q or r["username"].lower().startswith(q) or q in name
    return [r for r in rows if hit(r)][:limit]


# --- Görseller -----------------------------------------------------------------
def sniff_image(raw: bytes) -> str | None:
    """Dosya imzasından tür: png | jpeg | gif | webp; başka her şey (SVG dahil) None."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


async def image_upload_blocked(user_id: int) -> bool:
    row = await fetch_one("SELECT COUNT(*) AS c FROM forum_images WHERE user_id = ? AND created_at > ?",
                          (user_id, int(time.time()) - 3600))
    return int(row["c"]) >= IMAGE_UPLOADS_PER_HOUR


async def save_image(user_id: int, mime: str, size: int, data_b64: str) -> int:
    now = int(time.time())
    await execute("DELETE FROM forum_images WHERE thread_id IS NULL AND created_at < ?", (now - ORPHAN_IMAGE_SECONDS,))
    return await execute_returning_id("INSERT INTO forum_images (user_id, thread_id, reply_id, mime, size, data, created_at) "
                                      "VALUES (?, NULL, NULL, ?, ?, ?, ?)", (user_id, mime, size, data_b64, now))


async def get_image(iid: int) -> dict | None:
    return await fetch_one("SELECT * FROM forum_images WHERE id = ?", (iid,))


async def post_image_ids(tid: int, rid: int | None) -> list[int]:
    if rid is None:
        rows = await fetch_all("SELECT id FROM forum_images WHERE thread_id = ? AND reply_id IS NULL ORDER BY id", (tid,))
    else:
        rows = await fetch_all("SELECT id FROM forum_images WHERE reply_id = ? ORDER BY id", (rid,))
    return [r["id"] for r in rows]


async def thread_images(tid: int) -> dict:
    """{0: [başlık görselleri], reply_id: [...]}"""
    out: dict = {}
    for r in await fetch_all("SELECT id, reply_id FROM forum_images WHERE thread_id = ? ORDER BY id", (tid,)):
        out.setdefault(r["reply_id"] or 0, []).append(r["id"])
    return out


async def set_post_images(user_id: int, tid: int, rid: int | None, ids: list[int]):
    """Gönderinin görsellerini verilen kümeye eşitler: yazarın bağlanmamış görselleri bağlanır, listede olmayan
    eski görseller silinir. Başkasının ya da başka gönderinin görseli SESSİZCE yok sayılır."""
    ids = list(dict.fromkeys(int(i) for i in ids))[:IMAGES_PER_POST]
    current = await post_image_ids(tid, rid)
    for iid in current:
        if iid not in ids:
            await execute("DELETE FROM forum_images WHERE id = ?", (iid,))
    for iid in ids:
        if iid in current:
            continue
        await execute("UPDATE forum_images SET thread_id = ?, reply_id = ? WHERE id = ? AND user_id = ? AND thread_id IS NULL",
                      (tid, rid, iid, user_id))


# --- İçerik bildirme -------------------------------------------------------------
async def add_report(target_type: str, target_id: int, tid: int, user_id: int, reason: str) -> int | None:
    """None = bu kişi bu içeriği zaten bildirmiş."""
    rows = await execute_fetch(
        "INSERT INTO forum_reports (target_type, target_id, thread_id, user_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (target_type, target_id, user_id) DO NOTHING RETURNING id",
        (target_type, target_id, tid, user_id, reason, int(time.time())))
    return rows[0]["id"] if rows else None


async def open_reports() -> list[dict]:
    return await fetch_all(
        "SELECT p.*, t.title AS thread_title, u.first_name, u.last_name, "
        "CASE WHEN p.target_type = 'reply' THEN (SELECT body FROM forum_replies WHERE id = p.target_id) ELSE t.body END AS body "
        "FROM forum_reports p JOIN forum_threads t ON t.id = p.thread_id LEFT JOIN users u ON u.id = p.user_id "
        "WHERE p.resolved_at IS NULL ORDER BY p.created_at DESC")


async def resolve_report(rid: int, user_id: int) -> bool:
    rows = await execute_fetch("UPDATE forum_reports SET resolved_at = ?, resolved_by = ? WHERE id = ? AND resolved_at IS NULL "
                               "RETURNING id", (int(time.time()), user_id, rid))
    return bool(rows)


# --- Ders soru-cevap başlıkları ---------------------------------------------------
async def lesson_threads() -> dict[int, dict]:
    rows = await fetch_all("SELECT t.id, t.lesson_id, (SELECT COUNT(*) FROM forum_replies r WHERE r.thread_id = t.id) AS c "
                           "FROM forum_threads t WHERE t.lesson_id IS NOT NULL")
    return {r["lesson_id"]: {"thread_id": r["id"], "replies": int(r["c"])} for r in rows}


async def thread_for_lesson(lesson_id: int) -> dict | None:
    return await fetch_one("SELECT * FROM forum_threads WHERE lesson_id = ?", (lesson_id,))


async def unlink_lesson(lesson_id: int):
    """Ders silinince tartışma başlığı kalır (cevaplar kaybolmasın), yalnızca bağı kopar."""
    await execute("UPDATE forum_threads SET lesson_id = NULL WHERE lesson_id = ?", (lesson_id,))
