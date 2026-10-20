"""
SLACK BİLDİRİMLERİ — kişi eşleştirme, DM bildirimleri, kanal paylaşımları, son tarih hatırlatmaları ve
Slack'ten gelen yanıtların hangi forum başlığına ait olduğunu bulma. Slack API çağrıları slack.py'de.

- Kişi ↔ Slack hesabı: panel e-postası DOĞRULANMIŞ kişiler Slack'teki aynı e-postayla otomatik eşleşir
  (users.lookupByEmail; sonuç `slack_users`'ta, bulunamayan 1 günde bir yeniden denenir). Owner elle eşleştirebilir
  ya da eşleşmeyi kapatabilir (source=manual → otomatik eşleştirme o kişiye dokunmaz).
- DM: panel bildirimi (notify.add) yazılınca alıcıya Slack DM'i — owner ayarı `dm` açık + kişinin Slack tercihi o grup
  için açık + "Slack DM'lerini kapat" değil. Forumdaki herkese giden "yeni başlık/cevap" ve bültenler DM DEĞİL,
  kanala gider (aynı şey iki kez gelmesin).
- Kanal (owner ayarı `channel_id`): yeni başlık + bülten (`events.forum`), cevaplar (`events.replies` — başlığın kanal
  mesajının altına thread olarak), yeni ders/görev (`events.lessons`), son tarih hatırlatması (`events.due`, günlük cron).
  "Sadece ekip" başlıkları kanala yalnızca `team_threads` açıksa gider (kanal yalnızca ekipten oluşuyorsa açın).
- Çift yönlü: forum başlığına bağlı her Slack mesajı `slack_messages`'a yazılır (kanal, ts → thread_id). Bu mesajın
  altına (thread) yazılan yanıt, yazan kişinin panel hesabıyla o başlığa cevap olur (index.slack_events).
- Hiçbir Slack hatası panel isteğini düşürmez (loglanır).
"""
import asyncio
import json
import time
from datetime import date, timedelta

import mailer
import notify_email
import slack
from db_adapter import execute, execute_fetch, fetch_all, fetch_one

RECHECK_SECONDS = 86400       # bulunamayan e-posta 1 günde bir yeniden aranır
DM_CONCURRENCY = 4
EVENT_KEEP_SECONDS = 2 * 86400
OVERDUE_DAYS = 7              # son tarihi geçen ders en fazla bu kadar gün içinde (bir kez) hatırlatılır
REPLYABLE_KINDS = {"reply", "follow_reply", "mention", "solution", "vote", "report"}

SCHEMAS = [
    # kişi ↔ Slack kullanıcı kimliği (slack_user_id NULL = bulunamadı / elle kapatıldı)
    """CREATE TABLE IF NOT EXISTS slack_users (
        user_id INTEGER PRIMARY KEY, slack_user_id TEXT, source TEXT NOT NULL DEFAULT 'auto', checked_at INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_slack_users_sid ON slack_users (slack_user_id)",
    # Slack mesajı → forum başlığı (kind: root = kanalda başlığın mesajı, mirror = kanaldaki cevap kopyası, dm)
    """CREATE TABLE IF NOT EXISTS slack_messages (
        channel TEXT NOT NULL, ts TEXT NOT NULL, thread_id INTEGER, user_id INTEGER, kind TEXT NOT NULL,
        created_at INTEGER NOT NULL, PRIMARY KEY (channel, ts))""",
    "CREATE INDEX IF NOT EXISTS ix_slack_messages_thread ON slack_messages (thread_id, kind)",
    # Slack aynı olayı yeniden gönderebilir (3 sn'de yanıt alamazsa) — olay kimliğiyle bir kez işlenir
    "CREATE TABLE IF NOT EXISTS slack_events (event_id TEXT PRIMARY KEY, at INTEGER NOT NULL)",
    # son tarih hatırlatmaları (user_id 0 = kanal); due_date anahtarda → tarih değişirse yeniden hatırlatılır
    """CREATE TABLE IF NOT EXISTS slack_reminders (
        lesson_id INTEGER NOT NULL, user_id INTEGER NOT NULL, stage TEXT NOT NULL, due_date TEXT NOT NULL,
        sent_at INTEGER NOT NULL, PRIMARY KEY (lesson_id, user_id, stage, due_date))""",
]

DEFAULT_SETTINGS = {"channel_id": None, "channel_name": None,
                    "events": {"forum": True, "replies": True, "lessons": True, "due": True},
                    "team_threads": True, "due_days": 2, "dm": True, "tz": 180}


def _log(what: str, e: Exception):
    print(f"[slack] {what}: {e}", flush=True)


# ---------------------------------------------------------------------------
# Ayarlar (app_settings.slack)
# ---------------------------------------------------------------------------
async def get_settings() -> dict:
    row = await fetch_one("SELECT value_json FROM app_settings WHERE key = 'slack'")
    try:
        raw = json.loads(row["value_json"]) if row else {}
    except (TypeError, ValueError):
        raw = {}
    out = {**DEFAULT_SETTINGS, **{k: v for k, v in raw.items() if k != "events"}}
    out["events"] = {**DEFAULT_SETTINGS["events"], **(raw.get("events") or {})}
    return out


async def save_settings(cfg: dict):
    await execute("INSERT INTO app_settings (key, value_json, updated_at) VALUES ('slack', ?, ?) "
                  "ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
                  (json.dumps(cfg, ensure_ascii=False), int(time.time())))


async def _channel(event: str) -> dict | None:
    """Kanal paylaşımı açıksa ayarlar, değilse None."""
    if not slack.enabled():
        return None
    cfg = await get_settings()
    return cfg if cfg.get("channel_id") and cfg["events"].get(event) else None


def _url(path: str) -> str:
    return mailer.link(path) if mailer.base_url() else ""


# ---------------------------------------------------------------------------
# Kişi eşleştirme
# ---------------------------------------------------------------------------
async def mapping_rows(user_ids=None) -> dict[int, dict]:
    if user_ids is None:
        rows = await fetch_all("SELECT * FROM slack_users")
    else:
        ids = sorted({int(u) for u in user_ids})
        if not ids:
            return {}
        rows = await fetch_all(f"SELECT * FROM slack_users WHERE user_id IN ({','.join('?' * len(ids))})", tuple(ids))
    return {r["user_id"]: r for r in rows}


async def _store(user_id: int, slack_user_id: str | None, source: str):
    await execute("INSERT INTO slack_users (user_id, slack_user_id, source, checked_at) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT (user_id) DO UPDATE SET slack_user_id = excluded.slack_user_id, source = excluded.source, "
                  "checked_at = excluded.checked_at", (user_id, slack_user_id, source, int(time.time())))


async def slack_ids(user_ids, lookup: bool = True, force: bool = False) -> dict[int, str]:
    """Panel kullanıcı id → Slack kimliği (eşleşmeyenler dönmez). Eksikler e-postayla aranır (yalnızca DOĞRULANMIŞ
    e-posta; elle kapatılanlar aranmaz). force: bulunamayanlar beklemeden yeniden aranır."""
    ids = sorted({int(u) for u in user_ids})
    if not ids:
        return {}
    rows = await mapping_rows(ids)
    now = int(time.time())
    have = {u: rows[u]["slack_user_id"] for u in ids if rows.get(u) and rows[u].get("slack_user_id")}
    todo = [u for u in ids if u not in have and lookup and (
        not rows.get(u) or (rows[u]["source"] == "auto" and (force or rows[u]["checked_at"] < now - RECHECK_SECONDS)))]
    if todo:
        marks = ",".join("?" * len(todo))
        users = await fetch_all(f"SELECT id, email FROM users WHERE id IN ({marks}) AND email_verified_at IS NOT NULL",
                                tuple(todo))
        sem = asyncio.Semaphore(DM_CONCURRENCY)

        async def one(u):
            async with sem:
                try:
                    sid = await slack.lookup_by_email((u["email"] or "").strip().lower())
                except slack.SlackError as e:
                    _log(f"e-posta eşleştirme ({u['id']})", e)
                    return
                if sid and await fetch_one("SELECT 1 AS ok FROM slack_users WHERE slack_user_id = ? AND user_id <> ?",
                                           (sid, u["id"])):
                    sid = None   # bu Slack hesabı başka bir kişiye elle bağlanmış
                await _store(u["id"], sid, "auto")
                if sid:
                    have[u["id"]] = sid
        await asyncio.gather(*(one(u) for u in users))
    return have


async def set_manual(user_id: int, slack_user_id: str | None):
    """Owner'ın elle eşleştirmesi (None = bu kişiye Slack'ten hiç gitmesin / gelen yanıt bağlanmasın)."""
    await _store(user_id, slack_user_id, "manual")


async def clear_manual(user_id: int):
    """Elle eşleştirmeyi kaldırır → otomatik (e-posta) eşleştirmeye döner."""
    await execute("DELETE FROM slack_users WHERE user_id = ?", (user_id,))


async def panel_user_for(slack_user_id: str) -> int | None:
    """Slack'ten yazan kişinin panel hesabı: kayıtlı eşleşme, yoksa Slack profilindeki e-posta = panelde DOĞRULANMIŞ
    e-posta. Elle kapatılmış kişi eşleşmez."""
    row = await fetch_one("SELECT user_id FROM slack_users WHERE slack_user_id = ? ORDER BY user_id LIMIT 1",
                          (slack_user_id,))
    if row:
        return row["user_id"]
    info = await slack.user_info(slack_user_id)
    if not info or info["deleted"] or info["is_bot"] or not info["email"]:
        return None
    u = await fetch_one("SELECT id FROM users WHERE LOWER(email) = ? AND email_verified_at IS NOT NULL", (info["email"],))
    if not u:
        return None
    cur = await fetch_one("SELECT source, slack_user_id FROM slack_users WHERE user_id = ?", (u["id"],))
    if cur and cur["source"] == "manual":
        return None   # owner bu kişiyi başka bir Slack hesabına bağlamış ya da Slack'i kapatmış
    await _store(u["id"], slack_user_id, "auto")
    return u["id"]


# ---------------------------------------------------------------------------
# Slack mesajı ↔ forum başlığı
# ---------------------------------------------------------------------------
async def record(channel: str, ts: str | None, thread_id: int | None, user_id: int | None, kind: str):
    if not ts:
        return
    await execute("INSERT INTO slack_messages (channel, ts, thread_id, user_id, kind, created_at) VALUES (?, ?, ?, ?, ?, ?) "
                  "ON CONFLICT (channel, ts) DO NOTHING", (channel, ts, thread_id, user_id, kind, int(time.time())))


async def target(channel: str, ts: str) -> dict | None:
    return await fetch_one("SELECT * FROM slack_messages WHERE channel = ? AND ts = ?", (channel, ts))


async def root_of(thread_id: int, channel: str) -> dict | None:
    return await fetch_one("SELECT * FROM slack_messages WHERE thread_id = ? AND kind = 'root' AND channel = ? "
                           "ORDER BY created_at LIMIT 1", (thread_id, channel))


async def claim_event(event_id: str) -> bool:
    """Olay ilk kez mi geliyor? (Slack yeniden denemeleri bir kez işlenir)."""
    now = int(time.time())
    await execute("DELETE FROM slack_events WHERE at < ?", (now - EVENT_KEEP_SECONDS,))
    rows = await execute_fetch("INSERT INTO slack_events (event_id, at) VALUES (?, ?) ON CONFLICT (event_id) DO NOTHING "
                               "RETURNING event_id", (event_id, now))
    return bool(rows)


async def delete_user(user_id: int):
    await execute("DELETE FROM slack_users WHERE user_id = ?", (user_id,))
    await execute("DELETE FROM slack_reminders WHERE user_id = ?", (user_id,))
    await execute("DELETE FROM slack_messages WHERE user_id = ?", (user_id,))


# ---------------------------------------------------------------------------
# Mesaj biçimi
# ---------------------------------------------------------------------------
def _blocks(head: str, body: str = "", foot: str = "") -> list:
    b = [{"type": "section", "text": {"type": "mrkdwn", "text": (head + ("\n" + body if body else ""))[:2900]}}]
    if foot:
        b.append({"type": "context", "elements": [{"type": "mrkdwn", "text": foot[:2900]}]})
    return b


def _plain(text: str) -> str:
    """Bildirimin yedek metni (`text` — bildirim önizlemesi). Slack bunu da mrkdwn olarak okur → kullanıcı metni KAÇIRILIR
    (başlıkta "<!channel>" yazan biri kanalı topluca etiketleyemesin)."""
    return slack.esc(text.replace("*", ""))


REPLY_HINT = "Bu mesajı *yanıtla* (thread) → panelde başlığa senin adınla cevap olarak eklenir"


def _foot(url: str, label: str, replyable: bool) -> str:
    parts = [slack.link(url, label + " →")] if url else []
    if replyable and slack.two_way():
        parts.append(REPLY_HINT)
    return "  ·  ".join(parts)


def fmt_date(d: str | None) -> str:
    try:
        return date.fromisoformat(d).strftime("%d.%m.%Y") if d else "—"
    except ValueError:
        return d or "—"


# ---------------------------------------------------------------------------
# DM bildirimleri (notify.add → dispatch_dm)
# ---------------------------------------------------------------------------
async def dispatch_dm(kind: str, recipients: set[int], actor_name: str, thread_id: int | None, reply_id: int | None,
                      lesson_id: int | None, data: dict):
    """Hiçbir koşulda istisna fırlatmaz (bildirim kaydı zaten yazıldı)."""
    try:
        if not slack.enabled() or not recipients:
            return
        group = notify_email.GROUP_OF.get(kind)
        if group not in notify_email.SLACK_GROUPS:
            return   # forum geneli ve bültenler kanala gider
        cfg = await get_settings()
        if not cfg.get("dm", True):
            return
        prefs = await notify_email.prefs_for(recipients)
        ids = [u for u in recipients if not prefs[u]["slack_off"] and prefs[u]["slack"].get(group, True)]
        smap = await slack_ids(ids)
        if not smap:
            return
        import notify
        text = notify.render(kind, actor_name, data)
        if thread_id:
            url, label = _url(f"/#forum/{thread_id}"), "Başlığı aç"
        elif lesson_id:
            url, label = _url("/#training"), "Eğitim & Görevler"
        else:
            url, label = _url("/"), "Panele git"
        replyable = bool(thread_id) and kind in REPLYABLE_KINDS
        icon = {"lesson": ":mortar_board:", "vote": ":thumbsup:", "solution": ":white_check_mark:",
                "mention": ":wave:", "report": ":warning:"}.get(kind, ":speech_balloon:")
        head = f"{icon} *{slack.esc(text)}*"
        body = slack.quote(data.get("excerpt") or "")
        blocks = _blocks(head, body, _foot(url, label, replyable))
        sem = asyncio.Semaphore(DM_CONCURRENCY)

        async def one(uid, sid):
            async with sem:
                try:
                    r = await slack.post(sid, _plain(text), blocks)
                except slack.SlackError as e:
                    _log(f"DM ({kind} → {uid})", e)
                    return
                if replyable:
                    await record(r["channel"], r["ts"], thread_id, uid, "dm")
        await asyncio.gather(*(one(u, s) for u, s in smap.items()))
    except Exception as e:   # Slack hatası bildirimi/isteği düşürmez
        _log(f"{kind} DM'i gönderilemedi", e)


# ---------------------------------------------------------------------------
# Kanal paylaşımları
# ---------------------------------------------------------------------------
def _team_ok(cfg: dict, thread: dict) -> bool:
    return thread.get("visibility") != "team" or bool(cfg.get("team_threads"))


async def on_thread(thread: dict, actor_name: str):
    """Yeni başlık / bülten → kanala; bu mesaj başlığın Slack'teki kökü olur (cevaplar altına thread)."""
    try:
        cfg = await _channel("forum")
        if not cfg or not _team_ok(cfg, thread):
            return
        url = _url(f"/#forum/{thread['id']}")
        if thread.get("kind") == "bulletin":
            head = f":mega: *Yeni bülten* — {slack.esc(actor_name)}"
        else:
            head = f":speech_balloon: *{slack.esc(actor_name)}* forumda yeni bir " \
                   f"{'soru' if thread.get('kind') == 'question' else 'tartışma'} paylaştı"
        title = slack.link(url, thread["title"]) if url else slack.esc(thread["title"])
        body = f"*{title}*" + ("\n" + slack.quote(thread.get("body") or "") if thread.get("body") else "")
        foot = [x for x in (_foot("", "", True), ":lock: Sadece ekip" if thread.get("visibility") == "team" else "") if x]
        r = await slack.post(cfg["channel_id"], _plain(f"{actor_name}: {thread['title']}"),
                             _blocks(head, body, "  ·  ".join(foot)))
        await record(r["channel"], r["ts"], thread["id"], None, "root")
    except Exception as e:
        _log("kanal: yeni başlık", e)


async def on_reply(thread: dict, reply_id: int, body: str, actor_name: str, origin: tuple[str, str] | None = None):
    """Yeni cevap → başlığın kanal mesajının altına (thread). Kök yoksa (başlık Slack bağlanmadan önce açılmış) yeni
    bir kanal mesajı açılır ve sonraki cevapların kökü olur. Cevap zaten o Slack thread'inden geldiyse tekrar yazılmaz."""
    try:
        cfg = await _channel("replies")
        if not cfg or not _team_ok(cfg, thread):
            return
        ch = cfg["channel_id"]
        root = await root_of(thread["id"], ch)
        if root and origin and origin == (root["channel"], root["ts"]):
            return
        if root:
            r = await slack.post(ch, _plain(f"{actor_name}: {body[:200]}"),
                                 _blocks(f"*{slack.esc(actor_name)}*", slack.quote(body, 1500)), thread_ts=root["ts"])
            await record(r["channel"], r["ts"], thread["id"], None, "mirror")
            return
        url = _url(f"/#forum/{thread['id']}")
        title = slack.link(url, thread["title"]) if url else slack.esc(thread["title"])
        r = await slack.post(ch, _plain(f"{actor_name}, {thread['title']} başlığına cevap yazdı"),
                             _blocks(f":speech_balloon: *{slack.esc(actor_name)}*, *{title}* başlığına cevap yazdı",
                                     slack.quote(body), _foot("", "", True)))
        await record(r["channel"], r["ts"], thread["id"], None, "root")
    except Exception as e:
        _log("kanal: yeni cevap", e)


async def on_lesson(lesson: dict, actor_name: str):
    try:
        cfg = await _channel("lessons")
        if not cfg:
            return
        url = _url("/#training")
        due = f"\n:calendar: Son tarih: *{fmt_date(lesson.get('due_date'))}*" if lesson.get("due_date") else ""
        body = (slack.quote(lesson.get("description") or "", 300) + due).strip()
        await slack.post(cfg["channel_id"], _plain(f"Yeni ders/görev: {lesson['title']}"),
                         _blocks(f":mortar_board: *Yeni ders/görev:* {slack.esc(lesson['title'])}  ·  {slack.esc(actor_name)}",
                                 body, slack.link(url, "Eğitim & Görevler →") if url else ""))
    except Exception as e:
        _log("kanal: yeni ders", e)


async def test_message(actor_name: str) -> dict:
    cfg = await get_settings()
    if not cfg.get("channel_id"):
        raise slack.SlackError("no_channel", 422)
    return await slack.post(cfg["channel_id"], "Hercules Panel bağlantı denemesi",
                            _blocks(":white_check_mark: *Hercules Panel bağlandı*",
                                    f"{slack.esc(actor_name)} bu kanalı panel bildirimleri için seçti."))


# ---------------------------------------------------------------------------
# Son tarih hatırlatmaları (günlük cron)
# ---------------------------------------------------------------------------
STAGE_LABEL = {"soon": "Yaklaşan", "today": "Bugün son gün", "overdue": "Süresi geçti"}


def stage_for(due: date, today: date, due_days: int) -> str | None:
    delta = (due - today).days
    if delta == 0:
        return "today"
    if 0 < delta <= due_days:
        return "soon"
    if -OVERDUE_DAYS <= delta < 0:
        return "overdue"
    return None


def _when(due: date, today: date) -> str:
    delta = (due - today).days
    if delta == 0:
        return "bugün"
    if delta > 0:
        return f"{delta} gün kaldı"
    return f"{-delta} gün geçti"


async def _sent(lesson_id: int, user_id: int, stage: str, due: str) -> bool:
    return bool(await fetch_one("SELECT 1 AS ok FROM slack_reminders WHERE lesson_id = ? AND user_id = ? AND stage = ? "
                                "AND due_date = ?", (lesson_id, user_id, stage, due)))


async def _mark(lesson_id: int, user_id: int, stage: str, due: str):
    await execute("INSERT INTO slack_reminders (lesson_id, user_id, stage, due_date, sent_at) VALUES (?, ?, ?, ?, ?) "
                  "ON CONFLICT DO NOTHING", (lesson_id, user_id, stage, due, int(time.time())))


async def due_reminders(today: date, audience_fn) -> dict:
    """Son tarihi yaklaşan / bugün olan / geçen dersler: kanala TEK özet mesajı + tamamlamamış kişilere DM.
    Her (ders, kişi|kanal, aşama, son tarih) bir kez. audience_fn(lesson_id) → dersi görebilen kişiler."""
    out = {"channel": 0, "dms": 0, "lessons": 0}
    if not slack.enabled():
        return {**out, "skipped": "Slack bağlı değil"}
    cfg = await get_settings()
    due_days = max(0, min(14, int(cfg.get("due_days") or 0)))
    lessons = await fetch_all("SELECT id, title, due_date FROM training_lessons WHERE due_date IS NOT NULL AND due_date <> ''")
    items = []   # (lesson, due_date, stage, pending ids, done count, audience count)
    for l in lessons:
        try:
            d = date.fromisoformat(l["due_date"])
        except ValueError:
            continue
        st = stage_for(d, today, due_days)
        if not st:
            continue
        audience = await audience_fn(l["id"])
        if not audience:
            continue
        done = {r["user_id"] for r in await fetch_all("SELECT user_id FROM training_completions WHERE lesson_id = ?", (l["id"],))}
        pending = audience - done
        if pending:
            items.append((l, d, st, pending, len(audience & done), len(audience)))
    out["lessons"] = len(items)
    url = _url("/#training")
    # kanal
    if cfg.get("channel_id") and cfg["events"].get("due"):
        todo = [it for it in items if not await _sent(it[0]["id"], 0, it[2], it[0]["due_date"])]
        if todo:
            lines = []
            for stage in ("overdue", "today", "soon"):
                rows = [it for it in todo if it[2] == stage]
                if rows:
                    lines.append(f"*{STAGE_LABEL[stage]}*")
                    lines += [f"• {slack.esc(it[0]['title'])} — son tarih {fmt_date(it[0]['due_date'])} ({_when(it[1], today)})"
                              f" · {it[4]}/{it[5]} kişi tamamladı" for it in rows]
            try:
                await slack.post(cfg["channel_id"], "Ders/görev son tarih hatırlatması",
                                 _blocks(":alarm_clock: *Ders/görev son tarih hatırlatması*", "\n".join(lines),
                                         slack.link(url, "Eğitim & Görevler →") if url else ""))
                for it in todo:
                    await _mark(it[0]["id"], 0, it[2], it[0]["due_date"])
                out["channel"] = len(todo)
            except slack.SlackError as e:
                _log("kanal: son tarih hatırlatması", e)
    # kişiye DM
    if cfg.get("dm", True) and items:
        per_user: dict[int, list] = {}
        for it in items:
            for uid in it[3]:
                per_user.setdefault(uid, []).append(it)
        prefs = await notify_email.prefs_for(per_user)
        allowed = [u for u in per_user if not prefs[u]["slack_off"] and prefs[u]["slack"].get("lesson", True)]
        smap = await slack_ids(allowed)
        for uid, sid in smap.items():
            todo = [it for it in per_user[uid] if not await _sent(it[0]["id"], uid, it[2], it[0]["due_date"])]
            if not todo:
                continue
            lines = [f"• *{slack.esc(it[0]['title'])}* — son tarih {fmt_date(it[0]['due_date'])} ({_when(it[1], today)})"
                     for it in sorted(todo, key=lambda it: it[1])]
            try:
                await slack.post(sid, "Tamamlanmamış ders/görev hatırlatması",
                                 _blocks(":alarm_clock: *Tamamlamadığın ders/görevlerin son tarihi yaklaşıyor*",
                                         "\n".join(lines), slack.link(url, "Eğitim & Görevler →") if url else ""))
            except slack.SlackError as e:
                _log(f"DM: son tarih hatırlatması ({uid})", e)
                continue
            for it in todo:
                await _mark(it[0]["id"], uid, it[2], it[0]["due_date"])
            out["dms"] += 1
    return out


def local_today(tz_minutes: int, now: float | None = None) -> date:
    """Kayıtlı saat dilimindeki (UTC'ye göre dakika) bugünün tarihi."""
    ts = int(now if now is not None else time.time()) + int(tz_minutes) * 60
    return date(1970, 1, 1) + timedelta(days=ts // 86400)
