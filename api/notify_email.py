"""
BİLDİRİM E-POSTALARI — kişi bazında tercihler + anlık gönderim (Resend, mailer.py). Haftalık özet index.py'de
(görünürlük yardımcıları orada) ve Vercel Cron ile tetiklenir.

- Bildirim kaydı (notify.py) her zaman yazılır; e-posta yalnızca: e-posta servisi açık + alıcının e-postası
  DOĞRULANMIŞ + tercihi o grup için "anlık" + tüm e-postaları kapatmamış.
- Gruplar (panelde "Bildirim Ayarları"): direct (başlığına cevap, bahsetme, çözüm), follow (takip ettiğin başlık),
  vote (faydalı), bulletin (bülten), lesson (yeni ders), report (içerik bildirimi — owner/admin).
- Gürültü sınırı: aynı başlık için kişiye son 30 dakikada e-posta gittiyse ve o bildirimi henüz okumadıysa yenisi
  gitmez (panelde yine görünür, haftalık özete girer).
- Gönderim isteğin İÇİNDE tek bir toplu (/emails/batch) çağrıyla yapılır; hata isteği düşürmez, yalnızca loglanır.
"""
import json
import secrets
import time

import mailer
from db_adapter import execute, fetch_all, fetch_one

GROUPS = {
    "direct": ("reply", "mention", "solution"),
    "follow": ("follow_reply",),
    "vote": ("vote",),
    "bulletin": ("bulletin",),
    "lesson": ("lesson",),
    "report": ("report",),
}
GROUP_OF = {k: g for g, kinds in GROUPS.items() for k in kinds}
GROUP_LABELS = {
    "direct": "Bana yazılanlar — başlığıma cevap, benden bahsedilmesi, cevabımın çözüm seçilmesi",
    "lesson": "Eğitim — bana yeni ders atanması",
    "follow": "Takip ettiğim başlıklara gelen cevaplar",
    "bulletin": "Yeni bültenler",
    "vote": "İçeriğimin faydalı bulunması",
    "report": "Bildirilen içerikler (owner/admin)",
}
# Varsayılan: HEPSİ açık (kullanıcı isteği) — isteyen Profilim → Bildirim Ayarları'ndan kapatır.
DEFAULTS = {"instant": {"direct": True, "lesson": True, "report": True, "follow": True, "bulletin": True, "vote": True},
            "weekly": True, "email_off": False}
# İlk sürümün varsayılanı (takip/bülten/oy kapalı). Bu değerde KAYITLI satırlar (kişi ayarı hiç değiştirmemiş, satır
# abonelik bağlantısı için oluşmuş) bir kez yeni varsayılana çekilir — migrate_defaults_once.
_OLD_DEFAULTS = {"instant": {"direct": True, "lesson": True, "report": True, "follow": False, "bulletin": False, "vote": False},
                 "weekly": True, "email_off": False}
COALESCE_SECONDS = 30 * 60

SCHEMAS = [
    # Kişi başına tek satır. unsub_token = e-postalardaki "tüm e-postaları kapat" bağlantısı (girişsiz çalışır).
    """CREATE TABLE IF NOT EXISTS notification_prefs (
        user_id INTEGER PRIMARY KEY, prefs_json TEXT NOT NULL, unsub_token TEXT UNIQUE, digest_sent_at INTEGER,
        updated_at INTEGER NOT NULL)""",
]


def clean_prefs(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    inst = raw.get("instant") if isinstance(raw.get("instant"), dict) else {}
    return {"instant": {g: bool(inst.get(g, DEFAULTS["instant"][g])) for g in GROUPS},
            "weekly": bool(raw.get("weekly", DEFAULTS["weekly"])),
            "email_off": bool(raw.get("email_off", DEFAULTS["email_off"]))}


async def migrate_defaults_once():
    """Bir kez (schema_flags): eski varsayılanda duran tercihleri yeni varsayılana (hepsi açık) çeker. Kişinin
    kendi değiştirdiği (eski varsayılandan farklı) tercihlere ve 'tümünü kapat'a dokunmaz."""
    if await fetch_one("SELECT 1 AS ok FROM schema_flags WHERE key = 'notif_defaults_all_on'"):
        return
    try:
        await execute("INSERT INTO schema_flags (key, value, set_at) VALUES ('notif_defaults_all_on', '1', ?)", (int(time.time()),))
    except Exception:
        return   # başka bir örnek yapıyor
    for r in await fetch_all("SELECT user_id, prefs_json FROM notification_prefs"):
        if clean_prefs(json.loads(r["prefs_json"])) == _OLD_DEFAULTS:
            await execute("UPDATE notification_prefs SET prefs_json = ? WHERE user_id = ?", (json.dumps(DEFAULTS), r["user_id"]))


async def _row(user_id: int) -> dict | None:
    return await fetch_one("SELECT * FROM notification_prefs WHERE user_id = ?", (user_id,))


async def get_prefs(user_id: int) -> dict:
    r = await _row(user_id)
    return clean_prefs(json.loads(r["prefs_json"]) if r else None)


async def set_prefs(user_id: int, prefs: dict) -> dict:
    prefs = clean_prefs(prefs)
    await execute("INSERT INTO notification_prefs (user_id, prefs_json, unsub_token, updated_at) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT (user_id) DO UPDATE SET prefs_json = excluded.prefs_json, updated_at = excluded.updated_at",
                  (user_id, json.dumps(prefs), secrets.token_urlsafe(24), int(time.time())))
    return prefs


async def unsub_token(user_id: int) -> str:
    r = await _row(user_id)
    if r and r.get("unsub_token"):
        return r["unsub_token"]
    if not r:
        await set_prefs(user_id, DEFAULTS)
        return (await _row(user_id))["unsub_token"]
    tok = secrets.token_urlsafe(24)
    await execute("UPDATE notification_prefs SET unsub_token = ? WHERE user_id = ?", (tok, user_id))
    return tok


async def unsubscribe(token: str) -> bool:
    """E-postadaki bağlantı: TÜM bildirim e-postalarını kapatır (panel içi bildirimler sürer)."""
    if not token:
        return False
    r = await fetch_one("SELECT user_id, prefs_json FROM notification_prefs WHERE unsub_token = ?", (token,))
    if not r:
        return False
    p = clean_prefs(json.loads(r["prefs_json"]))
    p["email_off"] = True
    await execute("UPDATE notification_prefs SET prefs_json = ?, updated_at = ? WHERE user_id = ?",
                  (json.dumps(p), int(time.time()), r["user_id"]))
    return True


async def prefs_for(user_ids) -> dict[int, dict]:
    ids = sorted({int(u) for u in user_ids})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = await fetch_all(f"SELECT user_id, prefs_json FROM notification_prefs WHERE user_id IN ({marks})", tuple(ids))
    got = {r["user_id"]: clean_prefs(json.loads(r["prefs_json"])) for r in rows}
    return {u: got.get(u, clean_prefs(None)) for u in ids}


def target_link(thread_id: int | None, reply_id: int | None, lesson_id: int | None) -> tuple[str, str]:
    if thread_id:
        return mailer.link(f"/#forum/{thread_id}"), "Başlığı aç"
    if lesson_id:
        return mailer.link("/#training"), "Eğitime git"
    return mailer.link("/"), "Panele git"


async def footer_links(user_id: int) -> tuple[str, str]:
    return mailer.link("/#bildirim-ayarlari"), mailer.link(f"/api/notifications/unsubscribe?t={await unsub_token(user_id)}")


async def dispatch(kind: str, recipients: set[int], actor_id: int | None, thread_id: int | None, reply_id: int | None,
                   lesson_id: int | None, data: dict):
    """Anlık bildirim e-postası. Hiçbir koşulda istisna fırlatmaz (bildirim kaydı zaten yazıldı)."""
    try:
        if not mailer.enabled() or not recipients:
            return
        group = GROUP_OF.get(kind)
        if not group:
            return
        ids = sorted(recipients)
        marks = ",".join("?" * len(ids))
        users = await fetch_all(f"SELECT id, email, first_name FROM users WHERE id IN ({marks}) AND email_verified_at IS NOT NULL", tuple(ids))
        prefs = await prefs_for([u["id"] for u in users])
        users = [u for u in users if not prefs[u["id"]]["email_off"] and prefs[u["id"]]["instant"][group]]
        now = int(time.time())
        if thread_id and users:   # gürültü sınırı: aynı başlık için okunmamış yakın tarihli e-posta varsa atla
            umarks = ",".join("?" * len(users))
            busy = {r["user_id"] for r in await fetch_all(
                f"SELECT DISTINCT user_id FROM notifications WHERE thread_id = ? AND emailed_at > ? AND read_at IS NULL "
                f"AND user_id IN ({umarks})", (thread_id, now - COALESCE_SECONDS, *[u["id"] for u in users]))}
            users = [u for u in users if u["id"] not in busy]
        if not users:
            return
        actor = await fetch_one("SELECT first_name, last_name FROM users WHERE id = ?", (actor_id,)) if actor_id else None
        actor_name = " ".join(x for x in ((actor or {}).get("first_name") or "", (actor or {}).get("last_name") or "") if x)
        import notify
        text = notify.render(kind, actor_name, data)
        url, button = target_link(thread_id, reply_id, lesson_id)
        msgs = []
        for u in users:
            settings_url, unsub_url = await footer_links(u["id"])
            subject, html_body, plain = mailer.notification_email((u.get("first_name") or "").strip(), text,
                                                                  data.get("excerpt") or "", url, button,
                                                                  settings_url, unsub_url)
            # List-Unsubscribe YALNIZCA haftalık özette: Gmail bu başlığı toplu/pazarlama postası işareti sayıp
            # kişisel bildirimleri "Tanıtımlar"a atıyordu. Kapatma bağlantısı gövdede duruyor.
            msgs.append({"to": u["email"], "subject": subject, "html": html_body, "text": plain})
        await mailer.send_batch(msgs)
        umarks = ",".join("?" * len(users))
        await execute(
            f"UPDATE notifications SET emailed_at = ? WHERE kind = ? AND emailed_at IS NULL AND created_at >= ? "
            f"AND COALESCE(thread_id, 0) = ? AND COALESCE(reply_id, 0) = ? AND COALESCE(lesson_id, 0) = ? "
            f"AND user_id IN ({umarks})",
            (now, kind, now - 60, thread_id or 0, reply_id or 0, lesson_id or 0, *[u["id"] for u in users]))
    except Exception as e:   # e-posta hatası bildirimi/isteği düşürmez
        print(f"[notify_email] {kind} e-postası gönderilemedi: {e}", flush=True)
