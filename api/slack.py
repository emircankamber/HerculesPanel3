"""
SLACK — Web API istemcisi + imza doğrulama + metin dönüşümleri. Saf modül: veritabanına dokunmaz
(kayıtlar slack_notify.py'de).

Ortam değişkenleri (İSTEK ANINDA okunur — import anında değil; eksikse uygulama çökmez, Slack kapalı sayılır):
- SLACK_BOT_TOKEN      : Slack uygulamasının "Bot User OAuth Token"ı (xoxb-...). Yoksa Slack tamamen KAPALI.
- SLACK_SIGNING_SECRET : "Basic Information → Signing Secret". Yoksa Slack'ten gelen yanıtlar (çift yönlü) KAPALI;
                         bildirimler yine gider.

Mesajlar bot adına gider. Kullanıcı metni Slack'e giderken `esc()` ile kaçırılır (&, <, >) — böylece
"<!channel>" gibi toplu bahsetmeler ya da sahte bağlantılar üretilemez.
"""
import asyncio
import hashlib
import hmac
import html
import os
import re
import time

import httpx

API_URL = "https://slack.com/api"
TIMEOUT = 6.0
SIGNATURE_MAX_AGE = 300   # sn — Slack'in önerdiği tekrar oynatma penceresi

# Uygulama bildirimi (manifest) — panel "Slack kurulumu" penceresinde kopyalanır. Kapsamlar:
# chat:write (mesaj), channels:read/groups:read (kanal listesi), channels:join (herkese açık kanala katılma),
# users:read + users:read.email (kişiyi e-postasıyla eşleştirme), *:history (kanal/DM'deki yanıtları alma),
# reactions:write (panele eklenen yanıta ✅).
BOT_SCOPES = ["chat:write", "channels:read", "groups:read", "channels:join", "users:read", "users:read.email",
              "channels:history", "groups:history", "im:history", "reactions:write"]
BOT_EVENTS = ["message.channels", "message.groups", "message.im"]


class SlackError(RuntimeError):
    def __init__(self, error: str, status: int = 200):
        super().__init__(error)
        self.error = error
        self.status = status


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def enabled() -> bool:
    return _env("SLACK_BOT_TOKEN").startswith("xox")


def two_way() -> bool:
    return enabled() and bool(_env("SLACK_SIGNING_SECRET"))


def manifest(events_url: str | None) -> dict:
    m = {
        "display_information": {"name": "Hercules Panel",
                                "description": "Panel bildirimleri, forum paylaşımları ve ders hatırlatmaları"},
        "features": {"bot_user": {"display_name": "Hercules", "always_online": True},
                     "app_home": {"home_tab_enabled": False, "messages_tab_enabled": True,
                                  "messages_tab_read_only_enabled": False}},
        "oauth_config": {"scopes": {"bot": BOT_SCOPES}},
        "settings": {"org_deploy_enabled": False, "socket_mode_enabled": False, "token_rotation_enabled": False},
    }
    if events_url:
        m["settings"]["event_subscriptions"] = {"request_url": events_url, "bot_events": BOT_EVENTS}
    return m


# ---------------------------------------------------------------------------
# Web API
# ---------------------------------------------------------------------------
async def call(method: str, payload: dict | None = None, *, form: bool = False) -> dict:
    """Slack Web API çağrısı. ok=false → SlackError(error). 429'da Retry-After kadar bekleyip 2 kez daha dener."""
    if not enabled():
        raise SlackError("not_configured", 503)
    headers = {"Authorization": f"Bearer {_env('SLACK_BOT_TOKEN')}"}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                if form:
                    r = await c.post(f"{API_URL}/{method}", data=payload or {}, headers=headers)
                else:
                    r = await c.post(f"{API_URL}/{method}", json=payload or {},
                                     headers={**headers, "Content-Type": "application/json; charset=utf-8"})
        except httpx.HTTPError as e:
            raise SlackError(f"unreachable: {e}", 502) from e
        if r.status_code == 429 and attempt < 2:
            try:
                wait = min(float(r.headers.get("Retry-After") or 1), 5.0)
            except ValueError:
                wait = 1.0
            await asyncio.sleep(wait)
            continue
        if r.status_code >= 300:
            raise SlackError(f"http_{r.status_code}", r.status_code)
        try:
            data = r.json()
        except ValueError:
            raise SlackError("bad_response", 502)
        if not data.get("ok"):
            raise SlackError(str(data.get("error") or "unknown_error"))
        return data
    raise SlackError("ratelimited", 429)


async def auth_test() -> dict:
    d = await call("auth.test")
    return {"team": d.get("team"), "team_id": d.get("team_id"), "bot_user_id": d.get("user_id"), "url": d.get("url")}


async def channels() -> list[dict]:
    """Botun görebildiği kanallar (herkese açık + davet edildiği özel kanallar), ada göre sıralı."""
    out, cursor = [], ""
    for _ in range(10):
        d = await call("conversations.list", {"types": "public_channel,private_channel", "exclude_archived": "true",
                                              "limit": "200", **({"cursor": cursor} if cursor else {})}, form=True)
        for ch in d.get("channels") or []:
            out.append({"id": ch["id"], "name": ch.get("name") or ch["id"], "private": bool(ch.get("is_private")),
                        "is_member": bool(ch.get("is_member"))})
        cursor = ((d.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor:
            break
    return sorted(out, key=lambda c: c["name"].lower())


async def join(channel: str):
    await call("conversations.join", {"channel": channel})


async def workspace_users() -> list[dict]:
    """Çalışma alanındaki gerçek kişiler (bot/silinmiş/misafir olmayan) — elle eşleştirme listesi için."""
    out, cursor = [], ""
    for _ in range(10):
        d = await call("users.list", {"limit": "200", **({"cursor": cursor} if cursor else {})}, form=True)
        for u in d.get("members") or []:
            if u.get("deleted") or u.get("is_bot") or u.get("id") == "USLACKBOT":
                continue
            prof = u.get("profile") or {}
            out.append({"id": u["id"], "name": prof.get("real_name") or u.get("real_name") or u.get("name") or u["id"],
                        "handle": u.get("name") or "", "email": (prof.get("email") or "").lower()})
        cursor = ((d.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor:
            break
    return sorted(out, key=lambda u: u["name"].lower())


async def lookup_by_email(email: str) -> str | None:
    """E-postaya karşılık Slack kullanıcı kimliği; çalışma alanında yoksa None."""
    try:
        d = await call("users.lookupByEmail", {"email": email}, form=True)
    except SlackError as e:
        if e.error == "users_not_found":
            return None
        raise
    u = d.get("user") or {}
    return None if u.get("deleted") or u.get("is_bot") else u.get("id")


async def user_info(slack_user_id: str) -> dict | None:
    try:
        d = await call("users.info", {"user": slack_user_id}, form=True)
    except SlackError as e:
        if e.error == "user_not_found":
            return None
        raise
    u = d.get("user") or {}
    prof = u.get("profile") or {}
    return {"id": u.get("id"), "email": (prof.get("email") or "").strip().lower(), "deleted": bool(u.get("deleted")),
            "is_bot": bool(u.get("is_bot")), "name": prof.get("real_name") or u.get("name") or ""}


async def post(channel: str, text: str, blocks: list | None = None, thread_ts: str | None = None) -> dict:
    """Mesaj gönderir. channel bir kanal kimliği ya da kişinin Slack kimliği (U…) olabilir — ikincisinde
    uygulamanın o kişiyle DM'ine düşer. {channel, ts} döner (DM'de channel = D… kimliği)."""
    p = {"channel": channel, "text": text, "unfurl_links": False, "unfurl_media": False}
    if blocks:
        p["blocks"] = blocks
    if thread_ts:
        p["thread_ts"] = thread_ts
    d = await call("chat.postMessage", p)
    return {"channel": d.get("channel") or channel, "ts": d.get("ts")}


async def post_ephemeral(channel: str, user: str, text: str, thread_ts: str | None = None):
    p = {"channel": channel, "user": user, "text": text}
    if thread_ts:
        p["thread_ts"] = thread_ts
    await call("chat.postEphemeral", p)


async def react(channel: str, ts: str, name: str = "white_check_mark"):
    try:
        await call("reactions.add", {"channel": channel, "timestamp": ts, "name": name})
    except SlackError as e:
        if e.error != "already_reacted":
            raise


# ---------------------------------------------------------------------------
# Gelen istek imzası (Events API)
# ---------------------------------------------------------------------------
def verify_signature(body: bytes, timestamp: str | None, signature: str | None, now: float | None = None) -> bool:
    secret = _env("SLACK_SIGNING_SECRET")
    if not secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > SIGNATURE_MAX_AGE:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Metin
# ---------------------------------------------------------------------------
def esc(text) -> str:
    """Slack mrkdwn kaçışı — kullanıcı metni ASLA kaçırılmadan gönderilmez."""
    return str(text if text is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def link(url: str, label: str) -> str:
    return f"<{url}|{esc(label).replace('|', '¦')}>" if url else esc(label)


def quote(text: str, n: int = 300) -> str:
    t = " ".join((text or "").split())
    if len(t) > n:
        t = t[: n - 1].rstrip() + "…"
    return "> " + esc(t) if t else ""


_SLACK_LINK = re.compile(r"<((?:https?|mailto):[^|>]+)(?:\|([^>]*))?>")
_SLACK_USER = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
_SLACK_CHAN = re.compile(r"<#[A-Z0-9]+\|([^>]*)>")
_SLACK_SPECIAL = re.compile(r"<!(?:subteam\^[A-Z0-9]+\|)?([^>|]+)(?:\|[^>]*)?>")


def _link_plain(m) -> str:
    url, label = m.group(1), m.group(2)
    shown = url[len("mailto:"):] if url.startswith("mailto:") else url
    return f"{label} ({shown})" if label and label not in (url, shown) else shown


def to_plain(text: str, usernames: dict[str, str] | None = None) -> str:
    """Slack mesajını forum DÜZ METNİNE çevirir: <@U1> → @kullanıcıadı (eşleşmiyorsa kaldırılır),
    <url|etiket> → "etiket (url)", <#C1|genel> → #genel, *kalın* → **kalın**, &lt; &gt; &amp; çözülür."""
    usernames = usernames or {}
    t = text or ""
    t = _SLACK_USER.sub(lambda m: ("@" + usernames[m.group(1)]) if usernames.get(m.group(1)) else "", t)
    t = _SLACK_LINK.sub(_link_plain, t)
    t = _SLACK_CHAN.sub(lambda m: "#" + m.group(1), t)
    t = _SLACK_SPECIAL.sub(lambda m: "@" + m.group(1), t)
    t = re.sub(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*\w])", r"**\1**", t)
    t = html.unescape(t)
    return re.sub(r"[ \t]+\n", "\n", t).strip()


def mentioned_ids(text: str) -> set[str]:
    return set(_SLACK_USER.findall(text or ""))
