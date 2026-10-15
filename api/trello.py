"""
TRELLO — panelden Trello'ya TEK YÖNLÜ aktarım (Trello'dan panele hiçbir şey gelmez). Şu an: Ekip Performansı.

Ortam değişkenleri (İSTEK ANINDA okunur; eksikse entegrasyon kapalı, uygulama çökmez):
- TRELLO_API_KEY : https://trello.com/power-ups/admin → entegrasyon → Trello Auth → API anahtarı
- TRELLO_TOKEN   : aynı sayfadaki "Token" bağlantısıyla (read,write; süresiz) üretilen izin kodu
Kimlik bilgisi URL'ye değil `Authorization: OAuth ...` başlığına yazılır (log'lara düşmesin).

Ne gider (owner panelden pano + listeler + kişi ↔ Trello üyesi eşleşmesi seçer, `app_settings.trello`):
- ÖZET KARTI (haftalık / aylık / elle seçilen dönem): kapak = ekip performans panosu görseli (KPI'lar + önceki döneme
  göre değişim, son 30 gün, kişi bazında etkinlik, karar dağılımı, ürün hattı); açıklama = özet, öne çıkanlar, ilgi
  gerektirenler, kişi satırları, "kim ne yaptı"; "Aksiyon listesi" kontrol listesi (yalnızca EKLENİR, işaretler korunur).
- KİŞİ KARTI (kişi başına TEK, kalıcı): kapak = kişinin panosu; durum etiketi (Aktif / Sessiz / İlgi gerekiyor);
  karta yüzünde görünen sayısal alanlar (Analiz, Karar, Aktif gün); eşleştirilmişse kişinin Trello üyesi karta atanır
  (Trello yorum bildirimleri ona gider); günlük ayrıntı yorum olarak.
Kart ↔ panel eşleşmesi `trello_cards` (ref → card_id, meta). Kart Trello'da silinmişse (404) yeniden açılır.
TEMEL metin (ad + açıklama) her zaman gider; görsel/etiket/üye/alan/kontrol listesi "en iyi çaba" — biri başarısız olsa
aktarım düşmez, hata loglanır ve sonuçta sayılır.
Saf biçimlendirme fonksiyonları (render_*, *_dashboard) veritabanına/ağa dokunmaz — test edilebilir.
"""
import asyncio
import os
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import httpx

import png_chart as pc

API = "https://api.trello.com/1"
TIMEOUT = 15.0
DESC_MAX = 16000          # Trello kart açıklaması sınırı 16384
MIN_INTERVAL = 0.12       # ≈8 istek/sn — Trello sınırı belirteç başına 10 sn'de 100 istek
STATUS_TR = {"attention": "İlgi gerekiyor", "active": "Aktif", "quiet": "Sessiz"}
STATUS_COLOR = {"attention": "red", "active": "green", "quiet": "yellow"}
LABEL_SUMMARY, LABEL_MONTH = "Ekip özeti", "Aylık rapor"
CUSTOM_FIELDS = ("Analiz", "Karar", "Aktif gün")      # kişi kartının yüzünde görünen sayılar (dönem)
CHECKLIST_NAME = "Aksiyon listesi"
TR_MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım",
             "Aralık")


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


_THROTTLE: dict = {"loop": None, "lock": None, "next": 0.0}


async def _throttle():
    """Tüm Trello istekleri arasında en az MIN_INTERVAL (eşzamanlı görevler dahil) — 429'a düşmemek için."""
    loop = asyncio.get_running_loop()
    if _THROTTLE["loop"] is not loop:
        _THROTTLE.update(loop=loop, lock=asyncio.Lock(), next=0.0)
    async with _THROTTLE["lock"]:
        now = time.monotonic()
        wait = _THROTTLE["next"] - now
        _THROTTLE["next"] = max(now, _THROTTLE["next"]) + MIN_INTERVAL
    if wait > 0:
        await asyncio.sleep(wait)


async def _call(method: str, path: str, params: dict | None = None, body: dict | None = None,
                files: dict | None = None, data: dict | None = None):
    if not enabled():
        raise TrelloError("Trello bağlı değil (TRELLO_API_KEY / TRELLO_TOKEN tanımlı değil)", 503)
    for attempt in range(3):   # 429 → artan beklemeyle yeniden dene
        await _throttle()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                r = await c.request(method, f"{API}{path}", params=params, json=body, files=files, data=data,
                                    headers=_headers())
        except httpx.HTTPError as e:
            raise TrelloError(f"Trello'ya ulaşılamadı: {e}", 502) from e
        if r.status_code != 429 or attempt == 2:
            break
        await asyncio.sleep(2 * (attempt + 1))
    if r.status_code >= 300:
        raise TrelloError(f"Trello {r.status_code}: {r.text[:200]}", r.status_code)
    return r.json() if r.content else None


# ---------------------------------------------------------------------------
# Pano / liste / üye
# ---------------------------------------------------------------------------
async def boards() -> list[dict]:
    rows = await _call("GET", "/members/me/boards", {"filter": "open", "fields": "name,url"})
    return [{"id": b["id"], "name": b["name"], "url": b.get("url")} for b in rows or []]


async def lists(board_id: str) -> list[dict]:
    rows = await _call("GET", f"/boards/{board_id}/lists", {"filter": "open", "fields": "name"})
    return [{"id": l["id"], "name": l["name"]} for l in rows or []]


async def members(board_id: str) -> list[dict]:
    rows = await _call("GET", f"/boards/{board_id}/members", {"fields": "fullName,username"})
    return [{"id": m["id"], "name": m.get("fullName") or m.get("username") or "", "username": m.get("username") or ""}
            for m in rows or []]


def norm_name(s: str) -> str:
    """Panel adı ↔ Trello tam adı eşleştirmesi için: küçük harf, aksansız, tek boşluk."""
    s = str(s or "").replace("İ", "i").replace("I", "ı").lower().replace("ı", "i")
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    return " ".join(s.split())


def suggest_members(people: list[dict], board_members: list[dict]) -> dict[int, str]:
    """Adı birebir (aksansız, harf duyarsız) tutan kişileri öner — yalnızca TEK aday varsa."""
    by = {}
    for m in board_members:
        by.setdefault(norm_name(m["name"]), []).append(m["id"])
    out = {}
    for p in people:
        c = by.get(norm_name(p["name"])) or []
        if len(c) == 1:
            out[p["id"]] = c[0]
    return out


# ---------------------------------------------------------------------------
# Kart işlemleri
# ---------------------------------------------------------------------------
def _card_out(c: dict, created: bool) -> dict:
    return {"id": c["id"], "url": c.get("shortUrl") or c.get("url"), "created": created,
            "idLabels": list(c.get("idLabels") or []), "idMembers": list(c.get("idMembers") or [])}


async def upsert_card(card_id: str | None, list_id: str, name: str, desc: str, label_ids: list[str] | None = None) -> dict:
    """Var olan kartı günceller (listesi değiştiyse taşır); yoksa ya da Trello'da silinmişse yeni kart açar.
    Yeni kartta etiketler doğrudan verilir; var olanda sync_labels ayrıca uygulanır (kullanıcının kendi etiketleri kalsın)."""
    body = {"name": name[:500], "desc": desc[:DESC_MAX], "idList": list_id}
    if card_id:
        try:
            return _card_out(await _call("PUT", f"/cards/{card_id}", body=body), False)
        except TrelloError as e:
            if e.status not in (400, 404):
                raise
    if label_ids:
        body["idLabels"] = ",".join(label_ids)
    return _card_out(await _call("POST", "/cards", body={**body, "pos": "top"}), True)


async def sync_labels(card: dict, managed: set[str], want: set[str]):
    """Yalnızca BİZİM yönettiğimiz etiketleri değiştir; karta elle eklenenler kalır."""
    cur = set(card["idLabels"])
    desired = (cur - managed) | want
    if desired != cur:
        await _call("PUT", f"/cards/{card['id']}", body={"idLabels": ",".join(sorted(desired))})


async def ensure_labels(board_id: str, wanted: dict[str, str]) -> dict[str, str]:
    """Panodaki etiketleri ada göre bul; yoksa oluştur. {ad: etiket_id}"""
    have = {(l.get("name") or ""): l["id"] for l in await _call("GET", f"/boards/{board_id}/labels", {"limit": 1000}) or []}
    out = {}
    for name, color in wanted.items():
        if name in have:
            out[name] = have[name]
        else:
            out[name] = (await _call("POST", "/labels", body={"name": name, "color": color, "idBoard": board_id}))["id"]
    return out


async def ensure_member(card: dict, member_id: str | None, old_member_id: str | None):
    """Eşleştirilen Trello üyesini karta ekle; eşleşme değiştiyse eskisini çıkar (diğer üyelere dokunma)."""
    if old_member_id and old_member_id != member_id and old_member_id in card["idMembers"]:
        await _call("DELETE", f"/cards/{card['id']}/idMembers/{old_member_id}")
    if member_id and member_id not in card["idMembers"]:
        await _call("POST", f"/cards/{card['id']}/idMembers", body={"value": member_id})


async def ensure_custom_fields(board_id: str, names: tuple[str, ...]) -> dict[str, str]:
    """Panoda sayısal özel alanları bul/oluştur (kartın yüzünde görünür). {ad: alan_id}"""
    have = {f.get("name"): f["id"] for f in await _call("GET", f"/boards/{board_id}/customFields") or []
            if f.get("type") == "number"}
    out = {}
    for n in names:
        if n in have:
            out[n] = have[n]
        else:
            f = await _call("POST", "/customFields", body={"idModel": board_id, "modelType": "board", "name": n,
                                                           "type": "number", "pos": "bottom", "display_cardFront": True})
            out[n] = f["id"]
    return out


async def set_number_field(card_id: str, field_id: str, value: int | float):
    await _call("PUT", f"/cards/{card_id}/customField/{field_id}/item", body={"value": {"number": str(value)}})


async def ensure_checklist(card_id: str, name: str, items: list[str]) -> int:
    """Karta kontrol listesi; yalnızca EKSİK maddeleri ekler (Trello'da işaretlenenler korunur). Eklenen sayısı."""
    if not items:
        return 0
    cl = next((c for c in await _call("GET", f"/cards/{card_id}/checklists") or [] if c.get("name") == name), None)
    if not cl:
        cl = await _call("POST", "/checklists", body={"idCard": card_id, "name": name})
        cl["checkItems"] = []
    have = {i.get("name") for i in cl.get("checkItems") or []}
    added = 0
    for it in items:
        if it[:500] not in have:
            await _call("POST", f"/checklists/{cl['id']}/checkItems", body={"name": it[:500]})
            added += 1
    return added


async def comment(card_id: str, text: str):
    await _call("POST", f"/cards/{card_id}/actions/comments", body={"text": text[:DESC_MAX]})


async def set_cover_image(card_id: str, png: bytes, name: str, old_attachment_id: str | None) -> str:
    """Görseli karta ek olarak yükleyip KAPAK yapar; bir önceki görsel eki silinir (kartta ek birikmesin)."""
    att = await _call("POST", f"/cards/{card_id}/attachments", files={"file": (name, png, "image/png")},
                      data={"name": name, "mimeType": "image/png", "setCover": "true"})
    if old_attachment_id and old_attachment_id != att["id"]:
        try:
            await _call("DELETE", f"/cards/{card_id}/attachments/{old_attachment_id}")
        except TrelloError:
            pass   # elle silinmiş olabilir
    return att["id"]


# ---------------------------------------------------------------------------
# Kim ne yaptı — database.activity_detail satırlarından (saf fonksiyonlar)
# ---------------------------------------------------------------------------
KINDS = ("analyses", "decisions", "lists_started", "lists_approved", "lessons", "threads", "replies")
C_ANALYSIS, C_DECISION, C_FORUM = (46, 125, 50), (234, 88, 12), (29, 78, 216)
C_TEAL, C_PURPLE = (13, 148, 136), (124, 58, 237)
DECISION_COLORS = {"Uygun": (22, 128, 61), "Sınırda": (217, 119, 6), "Elenmiş": (190, 18, 60)}
STATUS_RGB = {"attention": (190, 18, 60), "active": (22, 128, 61), "quiet": (100, 116, 139)}
CHART_LEGEND = "🟩 Analiz · 🟧 Karar · 🟦 Forum (başlık + cevap)"
CHART_DAYS = 30
ITEM_CAP = 12
THREAD_KIND_TR = {"question": "Soru", "discussion": "Tartışma", "bulletin": "Bülten"}


def md(v) -> str:
    """Kullanıcı metnini Trello Markdown'ında düz yazı olarak göster (biçim/bağlantı üretmesin)."""
    s = " ".join(str(v or "").split())
    for ch in "\\`*_[]#<>|":
        s = s.replace(ch, "\\" + ch)
    return s[:120]


def _key(k: str) -> str:
    k = str(k or "")
    return "ASIN " + k[5:] if k.upper().startswith("ASIN:") else k


def person_activity(det: dict, uid: int, since: int, until: int) -> dict:
    return {k: [r for r in det.get(k, []) if r["user_id"] == uid and since <= (r["ts"] or 0) < until] for k in KINDS}


def _cap(items: list[str]) -> str:
    more = len(items) - ITEM_CAP
    return ", ".join(items[:ITEM_CAP]) + (f" … (+{more} daha)" if more > 0 else "")


def _grouped(rows, keyf, labelf) -> list[str]:
    """Aynı öğeyi tek satırda topla (sıra: ilk görülme), tekrar sayısını ×N ile yaz; etiket SON kayıttan."""
    order, last, cnt = [], {}, {}
    for r in rows:
        k = keyf(r)
        if k not in cnt:
            order.append(k)
            cnt[k] = 0
        cnt[k] += 1
        last[k] = r
    return [labelf(last[k]) + (f" ×{cnt[k]}" if cnt[k] > 1 else "") for k in order]


def activity_lines(act: dict) -> list[str]:
    """Kişinin bir dönemde ne yaptığı — Markdown madde listesi (boşsa boş liste)."""
    out = []
    a = act["analyses"]
    if a:
        items = _grouped(a, lambda r: (_key(r["keyword"]).lower(), r["marketplace"]),
                         lambda r: f"{md(_key(r['keyword']))} ({md(r['marketplace'])}"
                                   + (f" · {md(r['verdict'])})" if r.get("verdict") else ")"))
        out.append(f"- **Analiz ({len(a)}):** {_cap(items)}")
    d = act["decisions"]
    if d:
        items = _grouped(d, lambda r: (_key(r["keyword"]).lower(), r["marketplace"]),
                         lambda r: f"{md(_key(r['keyword']))} ({md(r['marketplace'])}) → **{md(r['decision'])}**")
        out.append(f"- **Karar ({len(d)}):** {_cap(items)}")
    for k, label in (("lists_started", "Kontrol listesi başlattı"), ("lists_approved", "Kontrol listesi onaylandı")):
        if act[k]:
            items = _grouped(act[k], lambda r: (_key(r["analysis_key"]).lower(), r["marketplace"]),
                             lambda r: f"{md(_key(r['analysis_key']))} ({md(r['marketplace'])})")
            out.append(f"- **{label} ({len(act[k])}):** {_cap(items)}")
    if act["lessons"]:
        out.append(f"- **Tamamlanan ders ({len(act['lessons'])}):** {_cap([md(r['title']) for r in act['lessons']])}")
    if act["threads"]:
        items = [f"{THREAD_KIND_TR.get(r['kind'], 'Başlık')}: {md(r['title'])}" for r in act["threads"]]
        out.append(f"- **Forumda açtığı başlık ({len(act['threads'])}):** {_cap(items)}")
    if act["replies"]:
        items = _grouped(act["replies"], lambda r: r["thread_id"], lambda r: md(r["title"]))
        out.append(f"- **Forumda cevap ({len(act['replies'])}):** {_cap(items)}")
    return out


def day_log(act: dict, day_label: str) -> str | None:
    """Kişi kartına günlük yorum: o gün ne yaptı (hiçbir şey yoksa None — boş yorum atılmaz)."""
    lines = activity_lines(act)
    if not lines:
        return None
    n = {k: len(act[k]) for k in KINDS}
    head = " · ".join(s for s in (
        f"{n['analyses']} analiz" if n["analyses"] else "", f"{n['decisions']} karar" if n["decisions"] else "",
        f"{n['lessons']} ders" if n["lessons"] else "",
        f"{n['threads'] + n['replies']} forum" if n["threads"] + n["replies"] else "") if s)
    return f"**{day_label} — günlük etkinlik** ({head})\n" + "\n".join(lines)


def daily_buckets(det: dict, uids: set[int] | None, until: int, tz_min: int, days: int = CHART_DAYS):
    """Son `days` gün (until hariç) günlük analiz / karar / forum sayıları + "gg.aa" etiketleri."""
    since = until - days * 86400
    b = {"analyses": [0] * days, "decisions": [0] * days, "posts": [0] * days}
    for src, kind in (("analyses", "analyses"), ("decisions", "decisions"), ("threads", "posts"), ("replies", "posts")):
        for r in det.get(src, []):
            ts = r["ts"] or 0
            if (uids is None or r["user_id"] in uids) and since <= ts < until:
                b[kind][int((ts - since) // 86400)] += 1
    tz = timezone(timedelta(minutes=tz_min))
    labels = [datetime.fromtimestamp(since + i * 86400 + 43200, tz).strftime("%d.%m") for i in range(days)]
    return labels, b


def _series(b: dict):
    return [(C_ANALYSIS, b["analyses"]), (C_DECISION, b["decisions"]), (C_FORUM, b["posts"])]


def _legend_items():
    return [("Analiz", C_ANALYSIS), ("Karar", C_DECISION), ("Forum", C_FORUM)]


def _pct_txt(v) -> str:
    return f"%{round(v * 100)}" if v is not None else "-"


def _modern():
    """Modern çizim (Pillow + Inter). Yüklenemezse None → bit eşlem çizime düşülür."""
    try:
        import dash_render
        dash_render.font("Regular", 12)   # yazı tipi dosyası gerçekten açılıyor mu
        return dash_render
    except Exception as e:   # Pillow yok / yazı tipi paketlenmemiş — aktarım durmasın
        print(f"[trello] modern görsel kullanılamıyor, sade çizime geçildi: {e}", flush=True)
        return None


def person_dashboard(x: dict, det: dict, until: int, tz_min: int, period: str) -> bytes:
    """Kişi kartı kapağı: KPI kutuları (önceki döneme göre), son 30 gün, karar dağılımı, eğitim ve uyum."""
    dr = _modern()
    if dr:
        labels, b = daily_buckets(det, {x["user_id"]}, until, tz_min)
        return dr.person(x, labels, b, period, (STATUS_TR.get(x["status"], x["status"]),
                                                STATUS_RGB.get(x["status"], pc.MUTED)), CHART_DAYS)
    W, H, P, G = 1000, 640, 32, 12
    c = pc.Canvas(W, H)
    sub = " · ".join(s for s in (period, x.get("title") or "", ", ".join(x.get("teams") or [])) if s)
    pc.header(c, x["name"], sub, (STATUS_TR.get(x["status"], x["status"]), STATUS_RGB.get(x["status"], pc.MUTED)))
    pv = x["prev"]
    tw = (W - 2 * P - 4 * G) // 5
    tiles = [
        ("Analiz", pc.fmt_int(x["analyses"]), pc.delta_text(x["analyses"], pv["analyses"]), C_ANALYSIS),
        ("Karar", pc.fmt_int(x["decisions"]), pc.delta_text(x["decisions"], pv["decisions"]), C_DECISION),
        ("Aktif gün", pc.fmt_int(x["active_days"]), pc.delta_text(x["active_days"], pv["active_days"]), C_TEAL),
        ("Dönüşüm", _pct_txt(x["conversion"]), (pc.up(f"{x['decided_products']}/{x['products']} ürün"), pc.MUTED), C_PURPLE),
        ("Forum", pc.fmt_int(x["threads"] + x["replies"]), pc.delta_text(x["threads"] + x["replies"], pv["posts"]), C_FORUM),
    ]
    for i, (lab, val, d, col) in enumerate(tiles):
        pc.tile(c, P + i * (tw + G), 96, tw, 118, lab, val, d, col)
    c.text(P, 240, pc.up(f"Son {CHART_DAYS} gün · günlük etkinlik"), pc.INK, 2)
    pc.legend(c, W - P - 330, 240, _legend_items())
    labels, b = daily_buckets(det, {x["user_id"]}, until, tz_min)
    pc.stacked_bars(c, P, 272, W - 2 * P, 230, labels, _series(b))
    half = (W - 2 * P - 40) // 2
    c.text(P, 530, pc.up("Karar dağılımı (dönem)"), pc.INK, 2)
    pc.hbar_split(c, P, 556, half, 20, [(k, x["mix"].get(k, 0), col) for k, col in DECISION_COLORS.items()])
    rx = P + half + 40
    done, total = x["lessons_done"], x["lessons_assigned"]
    c.text(rx, 530, pc.up(f"Eğitim {done}/{total}" + (f" · %{round(done * 100 / total)}" if total else "")), pc.INK, 2)
    pc.progress(c, rx, 556, half, 20, done, total, C_TEAL)
    od = len(x.get("lessons_overdue") or [])
    c.text(rx, 590, pc.fit(pc.up(f"Ön öneriyle uyum {_pct_txt(x['agreement'])}" + (f" · {od} gecikmiş ders" if od else "")),
                           half, 2), pc.DOWN if od else pc.MUTED, 2)
    return c.png()


def team_dashboard(data: dict, det: dict, member_ids: set[int], until: int, tz_min: int, title: str, period: str) -> bytes:
    """Özet kartı kapağı: ekip KPI'ları, son 30 gün, kişi bazında etkinlik, karar dağılımı, ürün hattı."""
    dr = _modern()
    if dr:
        labels, b = daily_buckets(det, member_ids, until, tz_min)
        return dr.team(data, labels, b, title, period, CHART_DAYS)
    W, H, P, G = 1000, 760, 32, 12
    t, pv = data["totals"], data["prev"]
    c = pc.Canvas(W, H)
    pc.header(c, title, period, (f"{t['active_members']}/{t['members']} aktif", C_TEAL))
    tw = (W - 2 * P - 4 * G) // 5
    tiles = [
        ("Aktif kişi", f"{t['active_members']}/{t['members']}", pc.delta_text(t["active_members"], pv["active_members"]), C_TEAL),
        ("Analiz", pc.fmt_int(t["analyses"]), pc.delta_text(t["analyses"], pv["analyses"]), C_ANALYSIS),
        ("Karar", pc.fmt_int(t["decisions"]), pc.delta_text(t["decisions"], pv["decisions"]), C_DECISION),
        ("Dönüşüm", _pct_txt(t["conversion"]), (pc.up(f"Uyum {_pct_txt(t['agreement'])}"), pc.MUTED), C_PURPLE),
        ("Eğitim", _pct_txt(t["training_rate"]), (pc.up(f"{t['lessons_done']}/{t['lessons_assigned']} ders"), pc.MUTED), C_FORUM),
    ]
    for i, (lab, val, d, col) in enumerate(tiles):
        pc.tile(c, P + i * (tw + G), 96, tw, 118, lab, val, d, col)
    cw = 560
    c.text(P, 240, pc.up(f"Son {CHART_DAYS} gün · ekip"), pc.INK, 2)
    pc.legend(c, P + 250, 240, _legend_items())
    labels, b = daily_buckets(det, member_ids, until, tz_min)
    pc.stacked_bars(c, P, 272, cw, 250, labels, _series(b))
    rx, rw = P + cw + 36, W - (P + cw + 36) - P
    c.text(rx, 240, pc.up("Kişi bazında (dönem)"), pc.INK, 2)
    ppl = sorted(data["people"], key=lambda p: -(p["analyses"] + p["decisions"] + p["threads"] + p["replies"]))
    rows = [(pc.up(p["name"]), [(p["analyses"], C_ANALYSIS), (p["decisions"], C_DECISION),
                                (p["threads"] + p["replies"], C_FORUM)])
            for p in ppl if p["analyses"] + p["decisions"] + p["threads"] + p["replies"]][:8]
    pc.hbars(c, rx, 274, rw, rows)
    half = (W - 2 * P - 40) // 2
    c.text(P, 556, pc.up("Karar dağılımı"), pc.INK, 2)
    mix = data.get("mix") or {}
    pc.hbar_split(c, P, 582, half, 20, [(k, mix.get(k, 0), col) for k, col in DECISION_COLORS.items()])
    fx = P + half + 40
    c.text(fx, 556, pc.up("Ürün hattı"), pc.INK, 2)
    f = data.get("funnel") or [0, 0, 0, 0, 0]
    stages = ["Analiz", "Karar", "Uygun", "Liste", "Onay"]
    top = max(1, f[0])
    for i, (lab, v) in enumerate(zip(stages, f)):
        yy = 582 + i * 30
        c.text(fx, yy + 3, pc.up(lab), pc.MUTED, 2)
        bw = round((half - 150) * v / top)
        c.rect(fx + 90, yy, fx + 90 + bw, yy + 20, (C_ANALYSIS, C_DECISION, DECISION_COLORS["Uygun"], C_TEAL, C_PURPLE)[i])
        c.text(fx + 98 + bw, yy + 3, str(v), pc.INK, 2)
    return c.png()


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
    return f" ({'▲' if ch > 0 else '▼'} %{abs(round(ch * 100))} önceki döneme göre)"


def period_label(since: int, until: int, tz_min: int) -> str:
    tz = timezone(timedelta(minutes=tz_min))
    a = datetime.fromtimestamp(since, tz)
    b = datetime.fromtimestamp(until - 1, tz)
    return f"{a:%d.%m.%Y} – {b:%d.%m.%Y}"


def month_label(ts: int, tz_min: int) -> str:
    d = datetime.fromtimestamp(ts, timezone(timedelta(minutes=tz_min)))
    return f"{TR_MONTHS[d.month - 1]} {d.year}"


def highlights(people: list[dict]) -> list[str]:
    """Dönemin öne çıkanları — yalnızca gerçek sayılar (sıfırsa satır yok, eşitlikte iki kişiye kadar)."""
    def lead(key, label, fmt=lambda v: str(v), pool=None):
        pool = pool if pool is not None else people
        vals = [(key(p), p) for p in pool if key(p)]
        if not vals:
            return None
        best = max(v for v, _ in vals)
        names = [md(p["name"]) for v, p in vals if v == best]
        who = " ve ".join(names) if len(names) <= 2 else f"{names[0]}, {names[1]} ve {len(names) - 2} kişi daha"
        return f"- **{label}:** {who} ({fmt(best)})"
    rows = [
        lead(lambda p: p["analyses"], "En çok analiz"),
        lead(lambda p: p["decisions"], "En çok karar"),
        lead(lambda p: p["conversion"], "En yüksek dönüşüm (en az 3 ürün)", _pct,
             [p for p in people if p["products"] >= 3]),
        lead(lambda p: p["active_days"], "En istikrarlı", lambda v: f"{v} aktif gün"),
        lead(lambda p: p["lessons_done_period"], "Eğitimde önde", lambda v: f"{v} ders"),
        lead(lambda p: p["threads"] + p["replies"], "Forumda en aktif", lambda v: f"{v} paylaşım"),
        lead(lambda p: p["solutions"], "En çok çözüm", lambda v: f"{v} çözüm"),
    ]
    return [r for r in rows if r]


def action_items(people: list[dict]) -> list[str]:
    """Özet kartının "Aksiyon listesi" maddeleri (ilgi gerektiren her durum için bir madde)."""
    return [f"{x['name']}: {f}" for x in people if x["status"] == "attention" for f in x["flags"]]


def render_summary(data: dict, team_label: str, period: str, panel_url: str, name_period: str | None = None,
                   acts: dict | None = None, note: str | None = None, kind: str = "Ekip Performansı") -> tuple[str, str]:
    """acts: {user_id: person_activity(...)} — verilirse kişi başına "neler yaptı" ayrıntısı eklenir."""
    t, p = data["totals"], data["prev"]
    name = f"{kind} · {name_period or period} · {team_label}"
    f = data.get("funnel") or [0, 0, 0, 0, 0]
    mix = data.get("mix") or {}
    lines = [f"**Dönem:** {period} ({data['range']['days']} gün) · **Ekip:** {team_label}"] + ([note] if note else []) + [
        "Kapak görseli: ekip performans panosu (KPI'lar, son 30 gün, kişi bazında etkinlik, karar dağılımı, ürün hattı).",
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
        f"Analiz edilen **{f[0]}** → karar verilen **{f[1]}** → Uygun **{f[2]}** → kontrol listesi açılan **{f[3]}** → "
        f"onaylanan **{f[4]}**",
    ]
    hl = highlights(data["people"])
    if hl:
        lines += ["", "## Öne çıkanlar"] + hl
    attn = [x for x in data["people"] if x["status"] == "attention"]
    if attn:
        lines += ["", f"## İlgi gerekiyor ({len(attn)})"]
        lines += [f"- **{md(x['name'])}**: {', '.join(x['flags'])}" for x in attn]
        lines += ["", f"_Bu maddeler kartın \"{CHECKLIST_NAME}\" kontrol listesinde de var — ilgilenildikçe işaretleyin._"]
    people = sorted(data["people"], key=lambda x: (-x["analyses"], -x["decisions"], x["name"]))
    lines += ["", "## Kişiler"]
    for x in people:
        lines.append(f"- **{md(x['name'])}**" + (f" ({md(x['title'])})" if x.get("title") else "")
                     + f" — {STATUS_TR.get(x['status'], x['status'])}: {x['analyses']} analiz, {x['decisions']} karar, "
                     f"{x['active_days']} aktif gün, dönüşüm {_pct(x['conversion'])}, "
                     f"eğitim {x['lessons_done']}/{x['lessons_assigned']}, forum {x['threads'] + x['replies']}")
    if acts:
        lines += ["", "## Kim ne yaptı"]
        for x in people:
            al = activity_lines(acts.get(x["user_id"]) or {k: [] for k in KINDS})
            if al:
                lines += ["", f"### {md(x['name'])}"] + al
    lines += ["", "---", f"Panelden otomatik aktarıldı — ayrıntı: {panel_url}"]
    desc = "\n".join(lines)
    if len(desc) > DESC_MAX:   # çok kalabalık ekip: ayrıntı kişi kartlarında
        desc = desc[:DESC_MAX - 120].rsplit("\n", 1)[0] + "\n\n… (metin sınırı — kişi ayrıntısı kişi kartlarında)"
    return name, desc


def render_person(x: dict, period: str, panel_url: str, act: dict | None = None) -> tuple[str, str, str]:
    """(kart adı, açıklama, dönem yorumu). act: person_activity(...) — dönemde ne yaptığı."""
    name = x["name"] + (f" — {x['title']}" if x.get("title") else "")
    pv = x["prev"]
    od = x.get("lessons_overdue") or []
    lines = [
        "Kapak görseli: kişisel performans panosu (KPI'lar, son 30 gün, karar dağılımı, eğitim). "
        "Kartın yüzündeki Analiz / Karar / Aktif gün alanları aynı dönemin sayılarıdır.",
        "",
        f"**Dönem:** {period} · **Durum:** {STATUS_TR.get(x['status'], x['status'])}"
        + (f" ({', '.join(x['flags'])})" if x.get("flags") else ""),
        f"**Ekipler:** {', '.join(md(t) for t in x.get('teams') or []) or '—'}",
        "",
        "## Sayılar",
        f"- **Analiz:** {x['analyses']}{_delta(x['analyses'], pv['analyses'])} · {x['products']} tekil ürün, "
        f"{x['decided_products']} karara bağlandı (dönüşüm {_pct(x['conversion'])})",
        f"- **Karar:** {x['decisions']}{_delta(x['decisions'], pv['decisions'])} — Uygun {x['mix'].get('Uygun', 0)}, "
        f"Sınırda {x['mix'].get('Sınırda', 0)}, Elenmiş {x['mix'].get('Elenmiş', 0)}",
        f"- **Ön öneriyle uyum:** {_pct(x['agreement'])}" + (f" ({x['agreement_n']} karar)" if x.get("agreement_n") else ""),
        f"- **Aktif gün:** {x['active_days']}{_delta(x['active_days'], pv['active_days'])}",
        f"- **Kontrol listesi:** {x['lists_started']} başlatıldı, {x['lists_approved']} onaylandı, {x['lists_open']} açık"
        + (f" ({x['lists_stale']} bekliyor)" if x.get("lists_stale") else ""),
        f"- **Eğitim:** {x['lessons_done']}/{x['lessons_assigned']} tamamlandı"
        + (f", bu dönem {x['lessons_done_period']}" if x.get("lessons_done_period") else ""),
    ]
    if od:
        lines += ["  - Gecikmiş: " + ", ".join(f"{md(l['title'])} (son tarih {l['due_date']})" for l in od)]
    lines += [f"- **Forum:** {x['threads']} başlık, {x['replies']} cevap, {x['solutions']} çözüm"]
    al = activity_lines(act) if act else []
    lines += ["", "## Bu dönemde neler yaptı"] + (al or ["- Bu dönemde kayıtlı etkinlik yok."])
    lines += ["", "Günlük ayrıntı aşağıdaki yorumlarda (etkinlik olan her gün bir yorum).", "", "---",
              f"Panelden otomatik aktarıldı — ayrıntı: {panel_url}"]
    note = (f"**{period}** dönem özeti: {x['analyses']} analiz, {x['decisions']} karar, {x['active_days']} aktif gün, "
            f"eğitim {x['lessons_done']}/{x['lessons_assigned']}, forum {x['threads'] + x['replies']} — "
            f"{STATUS_TR.get(x['status'], x['status'])}")
    return name, "\n".join(lines), note
