"""
TRELLO — panelden Trello'ya TEK YÖNLÜ aktarım (Trello'dan panele hiçbir şey gelmez). Şu an: Ekip Performansı.

Ortam değişkenleri (İSTEK ANINDA okunur; eksikse entegrasyon kapalı, uygulama çökmez):
- TRELLO_API_KEY : https://trello.com/power-ups/admin → Power-Up/entegrasyon → API anahtarı
- TRELLO_TOKEN   : aynı sayfadaki "Token" bağlantısıyla (read,write; süresiz) üretilen izin kodu
Kimlik bilgisi URL'ye değil `Authorization: OAuth ...` başlığına yazılır (log'lara düşmesin).

Ne gider (owner panelden pano + liste seçer, `app_settings.trello`):
- Dönem özeti kartı: "Ekip Performansı · <tarih aralığı> · <ekip>" — KPI'lar, önceki döneme göre değişim, ürün hattı,
  ilgi gerektirenler. Aynı dönem + ekip yeniden gönderilirse YENİ kart açılmaz, aynı kart güncellenir.
- Kişi kartları (isteğe bağlı ayrı liste): kişi başına TEK kalıcı kart; açıklama son gönderilen dönemle güncellenir,
  her YENİ dönem için karta tek satırlık yorum eklenir (Trello'da kişinin haftalık geçmişi birikir).
Kart ↔ panel eşleşmesi `trello_cards` (ref → card_id). Kart Trello'da silinmişse (404) yeniden açılır.
Saf biçimlendirme fonksiyonları (render_*) veritabanına/ağa dokunmaz — test edilebilir.
"""
import os
from datetime import datetime, timedelta, timezone

import httpx

API = "https://api.trello.com/1"
TIMEOUT = 10.0
DESC_MAX = 16000      # Trello kart açıklaması sınırı 16384
STATUS_TR = {"attention": "İlgi gerekiyor", "active": "Aktif", "quiet": "Sessiz"}


class TrelloError(RuntimeError):
    def __init__(self, msg: str, status: int = 0):
        super().__init__(msg)
        self.status = status


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def enabled() -> bool:
    return bool(_env("TRELLO_API_KEY") and _env("TRELLO_TOKEN"))


def _headers() -> dict:
    return {"Authorization": f'OAuth oauth_consumer_key="{_env("TRELLO_API_KEY")}", oauth_token="{_env("TRELLO_TOKEN")}"',
            "Accept": "application/json"}


async def _call(method: str, path: str, params: dict | None = None, body: dict | None = None):
    if not enabled():
        raise TrelloError("Trello bağlı değil (TRELLO_API_KEY / TRELLO_TOKEN tanımlı değil)", 503)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.request(method, f"{API}{path}", params=params, json=body, headers=_headers())
    except httpx.HTTPError as e:
        raise TrelloError(f"Trello'ya ulaşılamadı: {e}", 502) from e
    if r.status_code >= 300:
        raise TrelloError(f"Trello {r.status_code}: {r.text[:200]}", r.status_code)
    return r.json() if r.content else None


async def boards() -> list[dict]:
    rows = await _call("GET", "/members/me/boards", {"filter": "open", "fields": "name,url"})
    return [{"id": b["id"], "name": b["name"], "url": b.get("url")} for b in rows or []]


async def lists(board_id: str) -> list[dict]:
    rows = await _call("GET", f"/boards/{board_id}/lists", {"filter": "open", "fields": "name"})
    return [{"id": l["id"], "name": l["name"]} for l in rows or []]


async def upsert_card(card_id: str | None, list_id: str, name: str, desc: str) -> dict:
    """Var olan kartı günceller (listesi değiştiyse taşır); yoksa ya da Trello'da silinmişse yeni kart açar."""
    body = {"name": name[:500], "desc": desc[:DESC_MAX], "idList": list_id}
    if card_id:
        try:
            c = await _call("PUT", f"/cards/{card_id}", body=body)
            return {"id": c["id"], "url": c.get("shortUrl") or c.get("url"), "created": False}
        except TrelloError as e:
            if e.status not in (400, 404):
                raise
    c = await _call("POST", "/cards", body={**body, "pos": "top"})
    return {"id": c["id"], "url": c.get("shortUrl") or c.get("url"), "created": True}


async def comment(card_id: str, text: str):
    await _call("POST", f"/cards/{card_id}/actions/comments", body={"text": text[:DESC_MAX]})


# ---------------------------------------------------------------------------
# Biçimlendirme (Trello Markdown) — panelle aynı tanımlar (team_perf.build çıktısı)
# ---------------------------------------------------------------------------
def _n(v) -> str:
    return f"{int(v):,}".replace(",", ".") if v is not None else "—"


def _pct(v) -> str:
    return f"%{round(v * 100):d}" if v is not None else "—"


def _delta(cur, prev) -> str:
    if not cur and not prev:
        return ""
    if not prev:
        return " (yeni hareket)"
    ch = (cur - prev) / prev
    if abs(ch) < 0.005:
        return " (önceki dönemle aynı)"
    return f" ({'+' if ch > 0 else '−'}%{abs(round(ch * 100))} önceki döneme göre)"


def period_label(since: int, until: int, tz_min: int) -> str:
    tz = timezone(timedelta(minutes=tz_min))
    a = datetime.fromtimestamp(since, tz)
    b = datetime.fromtimestamp(until - 1, tz)
    return f"{a:%d.%m.%Y} – {b:%d.%m.%Y}"


def render_summary(data: dict, team_label: str, period: str, panel_url: str) -> tuple[str, str]:
    t, p = data["totals"], data["prev"]
    name = f"Ekip Performansı · {period} · {team_label}"
    f = data.get("funnel") or [0, 0, 0, 0, 0]
    mix = data.get("mix") or {}
    lines = [
        f"**Dönem:** {period} ({data['range']['days']} gün) · **Ekip:** {team_label}",
        "",
        "## Özet",
        f"- **Aktif kişi:** {t['active_members']} / {t['members']}{_delta(t['active_members'], p['active_members'])}",
        f"- **Analiz:** {_n(t['analyses'])}{_delta(t['analyses'], p['analyses'])} · {_n(t['products'])} tekil ürün",
        f"- **Karar:** {_n(t['decisions'])}{_delta(t['decisions'], p['decisions'])} — "
        f"Uygun {mix.get('Uygun', 0)}, Sınırda {mix.get('Sınırda', 0)}, Elenmiş {mix.get('Elenmiş', 0)}",
        f"- **Dönüşüm (analiz → karar):** {_pct(t['conversion'])}",
        f"- **Ön öneriyle uyum:** {_pct(t['agreement'])}" + (f" ({t['agreement_n']} karar)" if t.get("agreement_n") else ""),
        f"- **Kontrol listesi:** {t['lists_started']} başlatıldı, {t['lists_approved']} onaylandı, {t['lists_open']} açık"
        + (f" ({t['lists_stale']} bekliyor)" if t.get("lists_stale") else ""),
        f"- **Eğitim:** {t['lessons_done']}/{t['lessons_assigned']} tamamlandı ({_pct(t['training_rate'])})"
        + (f", {t['lessons_overdue']} gecikmiş" if t.get("lessons_overdue") else "")
        + (f", bu dönem {t['lessons_done_period']}" if t.get("lessons_done_period") else ""),
        f"- **Forum:** {_n(t['posts'])} paylaşım{_delta(t['posts'], p['posts'])}, {t['solutions']} çözüm",
        "",
        "## Ürün hattı",
        f"Analiz edilen {f[0]} → karar verilen {f[1]} → Uygun {f[2]} → kontrol listesi açılan {f[3]} → onaylanan {f[4]}",
    ]
    attn = [x for x in data["people"] if x["status"] == "attention"]
    if attn:
        lines += ["", f"## İlgi gerekiyor ({len(attn)})"]
        lines += [f"- **{x['name']}**: {', '.join(x['flags'])}" for x in attn]
    people = sorted(data["people"], key=lambda x: (-x["analyses"], -x["decisions"], x["name"]))
    lines += ["", "## Kişiler"]
    for x in people:
        lines.append(f"- **{x['name']}**" + (f" ({x['title']})" if x.get("title") else "")
                     + f" — {STATUS_TR.get(x['status'], x['status'])}: {x['analyses']} analiz, {x['decisions']} karar, "
                     f"{x['active_days']} aktif gün, eğitim {x['lessons_done']}/{x['lessons_assigned']}, "
                     f"forum {x['threads'] + x['replies']}")
    lines += ["", "---", f"Panelden otomatik aktarıldı — ayrıntı: {panel_url}"]
    return name, "\n".join(lines)


def render_person(x: dict, period: str, panel_url: str) -> tuple[str, str, str]:
    """(kart adı, açıklama, dönem yorumu)."""
    name = x["name"] + (f" — {x['title']}" if x.get("title") else "")
    pv = x["prev"]
    od = x.get("lessons_overdue") or []
    lines = [
        f"**Son aktarılan dönem:** {period} · **Durum:** {STATUS_TR.get(x['status'], x['status'])}"
        + (f" ({', '.join(x['flags'])})" if x.get("flags") else ""),
        f"**Ekipler:** {', '.join(x.get('teams') or []) or '—'}",
        "",
        f"- **Analiz:** {x['analyses']}{_delta(x['analyses'], pv['analyses'])} · {x['products']} tekil ürün, "
        f"{x['decided_products']} karara bağlandı ({_pct(x['conversion'])})",
        f"- **Karar:** {x['decisions']}{_delta(x['decisions'], pv['decisions'])} — Uygun {x['mix'].get('Uygun', 0)}, "
        f"Sınırda {x['mix'].get('Sınırda', 0)}, Elenmiş {x['mix'].get('Elenmiş', 0)}",
        f"- **Ön öneriyle uyum:** {_pct(x['agreement'])}",
        f"- **Aktif gün:** {x['active_days']}{_delta(x['active_days'], pv['active_days'])}",
        f"- **Kontrol listesi:** {x['lists_started']} başlatıldı, {x['lists_approved']} onaylandı, {x['lists_open']} açık",
        f"- **Eğitim:** {x['lessons_done']}/{x['lessons_assigned']} tamamlandı"
        + (f", bu dönem {x['lessons_done_period']}" if x.get("lessons_done_period") else ""),
    ]
    if od:
        lines += [f"  - Gecikmiş: " + ", ".join(f"{l['title']} (son tarih {l['due_date']})" for l in od)]
    lines += [f"- **Forum:** {x['threads']} başlık, {x['replies']} cevap, {x['solutions']} çözüm",
              "", "---", f"Panelden otomatik aktarıldı — ayrıntı: {panel_url}"]
    note = (f"{period}: {x['analyses']} analiz, {x['decisions']} karar, {x['active_days']} aktif gün, "
            f"eğitim {x['lessons_done']}/{x['lessons_assigned']}, forum {x['threads'] + x['replies']} — "
            f"{STATUS_TR.get(x['status'], x['status'])}")
    return name, "\n".join(lines), note
