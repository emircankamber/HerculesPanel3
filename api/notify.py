"""
PANEL İÇİ BİLDİRİMLER — veri katmanı (saf modül, MCP çağrısı YOK).

Olay olunca alıcı başına bir satır yazılır; okunma `read_at` ile işaretlenir. Metin çıktıda (index.py) üretilir,
burada yalnızca olayın türü, kimin yaptığı (actor_id) ve hedefi (başlık / cevap / ders) + küçük bir anlık görüntü
(`data_json`: başlık adı vb. — hedef sonradan silinse de bildirim okunabilsin) saklanır.

Görünürlük kuralı ÇIKTIDA da uygulanır: "Sadece ekip" başlığına ait bildirim, kişi artık hiçbir ekipte değilse
listede/sayımda görünmez (index._visible_notifications). E-posta / web push / Slack gibi kanallar ileride bu
tabloyu kaynak olarak kullanacak (giden kutusu).
"""
import json
import time

from db_adapter import execute, fetch_all, fetch_one

# tür -> kısa açıklama (panelde filtre/simge için; asıl cümle index._notif_text'te)
KINDS = {
    "reply": "Başlığına cevap",
    "follow_reply": "Takip ettiğin başlığa cevap",
    "mention": "Senden bahsedildi",
    "solution": "Cevabın çözüm seçildi",
    "vote": "Faydalı bulundu",
    "bulletin": "Yeni bülten",
    "lesson": "Yeni ders",
    "report": "Bildirilen içerik",
}
TEMPLATES = {
    "reply": "{actor}, “{title}” başlığına cevap yazdı",
    "follow_reply": "{actor}, takip ettiğin “{title}” başlığına cevap yazdı",
    "mention": "{actor} senden bahsetti: “{title}”",
    "solution": "{actor}, “{title}” başlığında cevabını çözüm olarak işaretledi",
    "vote": "{actor}{others} “{title}” içindeki {what} faydalı buldu",
    "bulletin": "Yeni bülten: “{title}” ({actor})",
    "lesson": "Sana yeni bir ders atandı: “{title}”",
    "report": "{actor} bir içeriği bildirdi: “{title}”",
}


def render(kind: str, actor_name: str, data: dict) -> str:
    """Bildirim cümlesi — panel (index._notif_out) ve e-posta (notify_email) AYNI metni kullanır."""
    cnt = int(data.get("count") or 1)
    return TEMPLATES.get(kind, "{title}").format(
        actor=actor_name or "Biri", title=data.get("title") or "—",
        others=f" ve {cnt - 1} kişi daha" if cnt > 1 else "",
        what="cevabını" if data.get("target") == "reply" else "başlığını")


LIST_LIMIT = 40
KEEP_DAYS = 90

SCHEMAS = [
    """CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL, actor_id INTEGER,
        thread_id INTEGER, reply_id INTEGER, lesson_id INTEGER, data_json TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL, read_at INTEGER)""",
    "CREATE INDEX IF NOT EXISTS ix_notifications_user ON notifications (user_id, created_at)",
]


def _ids(user_ids, actor_id) -> list[int]:
    return sorted({int(u) for u in user_ids if u is not None and int(u) != (actor_id or 0)})


async def add(user_ids, kind: str, actor_id: int | None, thread_id: int | None = None, reply_id: int | None = None,
              lesson_id: int | None = None, data: dict | None = None) -> int:
    """Her alıcıya bir bildirim (olayı yapan kişinin kendisine gitmez). Yazılan satır sayısını döner."""
    assert kind in KINDS
    ids = _ids(user_ids, actor_id)
    now, payload = int(time.time()), json.dumps(data or {}, ensure_ascii=False)
    for uid in ids:
        await execute("INSERT INTO notifications (user_id, kind, actor_id, thread_id, reply_id, lesson_id, data_json, "
                      "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (uid, kind, actor_id, thread_id, reply_id, lesson_id, payload, now))
    if ids:
        import notify_email   # döngüsel içe aktarmayı önlemek için geç yükleme
        await notify_email.dispatch(kind, set(ids), actor_id, thread_id, reply_id, lesson_id, data or {})
    return len(ids)


async def add_vote(owner_id: int, actor_id: int, thread_id: int, reply_id: int | None, data: dict):
    """"Faydalı" oyu: aynı içerik için OKUNMAMIŞ bir oy bildirimi varsa yeni satır yerine sayaç artar
    (10 oy = 10 bildirim olmasın)."""
    if owner_id == actor_id:
        return
    row = await fetch_one(
        "SELECT id, data_json FROM notifications WHERE user_id = ? AND kind = 'vote' AND thread_id = ? AND "
        + ("reply_id = ?" if reply_id else "reply_id IS NULL") + " AND read_at IS NULL",
        (owner_id, thread_id, reply_id) if reply_id else (owner_id, thread_id))
    if row:
        d = json.loads(row["data_json"] or "{}")
        d.update(data)
        d["count"] = int(d.get("count") or 1) + 1
        await execute("UPDATE notifications SET actor_id = ?, data_json = ?, created_at = ? WHERE id = ?",
                      (actor_id, json.dumps(d, ensure_ascii=False), int(time.time()), row["id"]))
    else:
        await add([owner_id], "vote", actor_id, thread_id, reply_id, data={**data, "count": 1})


async def broadcast(kind: str, actor_id: int, thread_id: int | None, data: dict, team_only: bool,
                    exclude: set[int] | None = None) -> int:
    """Tüm kullanıcılara (team_only ise yalnızca en az bir ekipte olanlara) TEK sorguyla bildirim."""
    excl = sorted({actor_id, *(exclude or set())})
    marks = ",".join("?" * len(excl))
    team = " AND EXISTS (SELECT 1 FROM team_members tm WHERE tm.user_id = u.id)" if team_only else ""
    await execute(
        f"INSERT INTO notifications (user_id, kind, actor_id, thread_id, data_json, created_at) "
        f"SELECT u.id, ?, ?, ?, ?, ? FROM users u WHERE u.id NOT IN ({marks}){team}",
        (kind, actor_id, thread_id, json.dumps(data, ensure_ascii=False), int(time.time()), *excl))
    import notify_email
    rows = await fetch_all(f"SELECT u.id FROM users u WHERE u.id NOT IN ({marks}){team}", tuple(excl))
    await notify_email.dispatch(kind, {r["id"] for r in rows}, actor_id, thread_id, None, None, data)
    return 1


async def unread_rows(user_id: int) -> list[dict]:
    return await fetch_all("SELECT id, thread_id FROM notifications WHERE user_id = ? AND read_at IS NULL", (user_id,))


async def list_for(user_id: int, limit: int = LIST_LIMIT) -> list[dict]:
    await execute("DELETE FROM notifications WHERE user_id = ? AND created_at < ?",
                  (user_id, int(time.time()) - KEEP_DAYS * 86400))
    rows = await fetch_all(
        "SELECT n.*, u.first_name, u.last_name FROM notifications n LEFT JOIN users u ON u.id = n.actor_id "
        "WHERE n.user_id = ? ORDER BY n.created_at DESC, n.id DESC LIMIT ?", (user_id, limit))
    for r in rows:
        r["data"] = json.loads(r.pop("data_json") or "{}")
    return rows


async def mark_read(user_id: int, ids: list[int] | None = None, thread_id: int | None = None):
    """ids verilirse onlar, thread_id verilirse o başlığın bildirimleri, ikisi de yoksa TÜMÜ okundu."""
    now = int(time.time())
    if ids:
        marks = ",".join("?" * len(ids))
        await execute(f"UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL AND id IN ({marks})",
                      (now, user_id, *ids))
    elif thread_id is not None:
        await execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL AND thread_id = ?",
                      (now, user_id, thread_id))
    else:
        await execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL", (now, user_id))


async def delete_for(thread_id: int | None = None, reply_ids: list[int] | None = None, lesson_id: int | None = None):
    if thread_id is not None:
        await execute("DELETE FROM notifications WHERE thread_id = ?", (thread_id,))
    for rid in reply_ids or []:
        await execute("DELETE FROM notifications WHERE reply_id = ?", (rid,))
    if lesson_id is not None:
        await execute("DELETE FROM notifications WHERE lesson_id = ?", (lesson_id,))


async def delete_user(user_id: int):
    """Hesap silme: kişiye giden ve kişinin tetiklediği bildirimler silinir."""
    await execute("DELETE FROM notifications WHERE user_id = ? OR actor_id = ?", (user_id, user_id))
