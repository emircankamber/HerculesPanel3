# -*- coding: utf-8 -*-
"""
Lansman Raporu — "Yapılacaklar Planı + Reklam–Lansman Bütçe Raporu".

Hesaplama metodolojisi `amazon-urun-lansman-raporu` skill'inden BİREBİR taşındı
(2 varyasyonlu şablonun formülleri, işlem sırası dahil — test oracle'ı ile
rakam rakam karşılaştırılır). Skill'in izin verdiği gibi 1-4 varyasyona
genelleştirildi; reklam ve Vine TEK varyasyonda açılır, diğerleri organik büyür.

Saf modül: MCP çağrısı, veritabanı ya da ağ erişimi YOK. Rapor HTML olarak
üretilir (weasyprint yok — Vercel'de sistem kütüphaneleri eksik); panel yeni
sekmede açar, tarayıcının "PDF olarak kaydet"i ile yazdırılır.

Skill kuralları (değiştirme):
- TOPLAM MALİYET yalnızca ürün alış fiyatı (COGS). Vine ek gideri (FBA+kayıt),
  Reklam ve Creator Connections AYRI gider satırları.
- ACOS direkt: kampanya reklam bütçesi / Bölüm 4 brüt satış geliri.
- Kampanya gün sayısı yalnızca iç hesapta; rapor metninde YOK.
- "optimal" / "%100" gibi etiketler yok; bölüm sırası 1-10.
- SellerSprite verisi AYLIK -> Bölüm 2'de "Aylık Satış".
- Bölüm 2 "Önerilen ACOS" = Bid ÷ (Dönüşüm × reklam varyasyonunun fiyatı)
  (panelin pazar-ortalama-fiyatlı ACOS'u KOPYALANMAZ).
"""
from __future__ import annotations

import html as _html
import secrets

MAX_VARIATIONS = 4
CHANNEL_SP_DIVISOR = 1.75      # SP = günlük toplam / 1.75
CHANNEL_SD_RATIO = 0.35        # SD = SP × 0.35
CHANNEL_SB_RATIO = 0.40        # SB = SP × 0.40
DAYS_PER_MONTH = 30.4
ACOS_UPPER_EXTRA = 0.05        # "ACOS %Y'e kadar çıkabilir" -> Y = X + 5 puan


class ReportError(ValueError):
    """Girdi tutarsızlığı (422 olarak döner)."""


# ---------------------------------------------------------------------------
# Girdi normalizasyonu (form yüzdeleri -> oranlar)
# ---------------------------------------------------------------------------
def normalize(raw: dict) -> dict:
    """API/form girdisini hesaplama yapılandırmasına çevirir; çapraz kuralları doğrular."""
    variations = [dict(v) for v in (raw.get("variations") or [])]
    if not 1 <= len(variations) <= MAX_VARIATIONS:
        raise ReportError(f"1-{MAX_VARIATIONS} varyasyon girilmeli")
    ai = int(raw.get("ads_index") or 0)
    if not 0 <= ai < len(variations):
        raise ReportError("Reklam/Vine varyasyonu geçersiz")

    budget_mode = raw.get("budget_mode") or "acos"
    if budget_mode == "daily":
        if not raw.get("daily_budget") or raw["daily_budget"] <= 0:
            raise ReportError("Günlük reklam bütçesi girilmeli")
        target_daily_total, target_acos = float(raw["daily_budget"]), None
    elif budget_mode == "acos":
        pct = raw.get("target_acos_pct")
        pct = 11 if pct is None else pct
        if not 0 < pct <= 100:
            raise ReportError("Hedef ACOS 0-100 arasında olmalı")
        target_daily_total, target_acos = None, pct / 100
    else:
        raise ReportError("Bütçe tipi geçersiz")

    sv = raw.get("second_vine")
    second = None
    if sv:
        total = int(sv.get("total") or 0)
        enrolled = int(sv.get("enrolled") or total)
        if total < 1:
            raise ReportError("İkinci Vine pazarı adedi en az 1 olmalı")
        if enrolled > total:
            raise ReportError("Vine'a kaydedilen adet gönderilen adetten büyük olamaz")
        second = {"name": str(sv.get("name") or "").strip() or "Kanada (CA)", "total": total,
                  "enrolled": enrolled, "from_main_batch": bool(sv.get("from_main_batch"))}

    us_vine = int(raw.get("us_vine_units") or 0)
    if us_vine < 1:
        raise ReportError("ABD Vine adedi en az 1 olmalı")
    reserved = us_vine + (second["total"] if second and second["from_main_batch"] else 0)
    if int(variations[ai].get("units") or 0) < reserved:
        raise ReportError(f"Reklam/Vine varyasyonunun sevkiyat adedi Vine'a ayrılan adetten ({reserved}) az olamaz")

    units = [int(v.get("units") or 0) for v in variations]
    shares_in = [v.get("share") for v in variations]
    if all(s is None for s in shares_in):
        tot = sum(units)
        if tot <= 0:
            raise ReportError("Sevkiyat adedi girilmeli")
        shares = [u / tot for u in units]
    elif any(s is None for s in shares_in):
        raise ReportError("Sepet ağırlığı ya tüm varyasyonlar için girilmeli ya da hiç girilmemeli")
    else:
        if abs(sum(shares_in) - 100) > 0.01:
            raise ReportError("Sepet ağırlıklarının toplamı %100 olmalı")
        shares = [s / 100 for s in shares_in]

    cc_mode = raw.get("cc_mode") or "scenarios"
    if cc_mode == "expected":
        if raw.get("cc_expected") is None:
            raise ReportError("Beklenen aylık influencer satışı girilmeli")
        scenarios = {"Beklenen": int(raw["cc_expected"])}
    elif cc_mode == "scenarios":
        scenarios = {"Düşük": int(raw.get("cc_low", 30)), "Orta": int(raw.get("cc_mid", 80))}
    else:
        raise ReportError("Creator Connections senaryo tipi geçersiz")

    days = int(raw.get("campaign_days") or 60)
    if days < 1:
        raise ReportError("Kampanya günü en az 1 olmalı")

    kw = dict(raw.get("keyword") or {})
    return {
        "product_name": str(raw.get("product_name") or "").strip(),
        "main_keyword": str(raw.get("main_keyword") or "").strip(),
        "market_label": "ABD PAZARI",
        "variations": [{"name": str(v.get("name") or "").strip(), "price": float(v["price"]), "cogs": float(v["cogs"]),
                        "fba": float(v["fba"]), "units": units[i], "share": shares[i]} for i, v in enumerate(variations)],
        "ads_index": ai,
        "referral_rate": (raw["referral_pct"] if raw.get("referral_pct") is not None else 15) / 100,
        "target_daily_total": target_daily_total,
        "target_acos": target_acos,
        "us_vine_units": us_vine,
        "second_vine": second,
        "vine_fee": float(raw["vine_fee"]) if raw.get("vine_fee") is not None else 200.0,
        "return_rate": (raw["return_pct"] if raw.get("return_pct") is not None else 5) / 100,
        "cc_rate": (raw["cc_pct"] if raw.get("cc_pct") is not None else 15) / 100,
        "scenarios": scenarios,
        "campaign_days": days,
        "acos_upper_extra": ACOS_UPPER_EXTRA,
        "keyword": {k: (float(kw[k]) if kw.get(k) is not None else None)
                    for k in ("monthly_purchases", "monthly_clicks", "bid", "market_avg_price")},
    }


# ---------------------------------------------------------------------------
# Hesaplama (skill şablonu — formüller ve işlem sırası korunur)
# ---------------------------------------------------------------------------
def compute(cfg: dict) -> dict:
    V = cfg["variations"]
    ai = cfg["ads_index"]
    ads = V[ai]
    rr = cfg["referral_rate"]
    sv = cfg["second_vine"]
    vine_fee = cfg["vine_fee"]
    return_rate = cfg["return_rate"]
    cc_rate = cfg["cc_rate"]
    days = cfg["campaign_days"]

    fba_ads = ads["fba"]
    refs = [v["price"] * rr for v in V]
    amazonfees = [refs[i] + v["fba"] for i, v in enumerate(V)]
    gross = [v["price"] - v["cogs"] - amazonfees[i] for i, v in enumerate(V)]
    us_vine_cogs = ads["cogs"]

    ca_total = sv["total"] if sv else 0
    ca_from_main = sv["from_main_batch"] if sv else False
    ca_enrolled = sv["enrolled"] if sv else 0
    us_vine_units = cfg["us_vine_units"]

    sold = [v["units"] - (us_vine_units if i == ai else 0) - (ca_total if (ca_from_main and i == ai) else 0)
            for i, v in enumerate(V)]

    us_vine_product = us_vine_units * us_vine_cogs
    us_vine_fba = us_vine_units * fba_ads
    us_vine_extra = us_vine_units * fba_ads + vine_fee            # sadece FBA+kayıt -> gider satırı
    us_vine_total = us_vine_product + us_vine_extra               # görünürlük (Bölüm 5)
    vine_rows = [{"market": "ABD (US)", "source": f"{ads['name']} — ana stoktan", "units": us_vine_units,
                  "product": us_vine_product, "fba": us_vine_fba, "fee": vine_fee, "total": us_vine_total}]
    if sv:
        ca_vine_product = ca_total * us_vine_cogs
        ca_vine_extra = ca_enrolled * fba_ads + vine_fee
        ca_vine_total = ca_vine_product + ca_vine_extra
        vine_rows.append({"market": sv["name"],
                          "source": f"{ads['name']} — {'ana partiden ayrılan' if ca_from_main else 'ayrı sevkiyat'}",
                          "units": ca_total, "product": ca_vine_product, "fba": ca_enrolled * fba_ads,
                          "fee": vine_fee, "total": ca_vine_total})
        vine_total = us_vine_total + ca_vine_total
        vine_ek_gider = us_vine_extra + ca_vine_extra
    else:
        vine_total = us_vine_total
        vine_ek_gider = us_vine_extra

    returned = [s * return_rate for s in sold]
    kept = [s - returned[i] for i, s in enumerate(sold)]

    batch_revenue = sum(kept[i] * v["price"] for i, v in enumerate(V))
    if batch_revenue <= 0:
        raise ReportError("Satılacak adet yok — sevkiyat adetlerini kontrol edin")
    batch_amazon_fee = (sum(kept[i] * amazonfees[i] for i in range(len(V)))
                        + sum(returned[i] * v["fba"] for i, v in enumerate(V)))
    batch_geri_donecek = batch_revenue - batch_amazon_fee

    # TOPLAM MALİYET = yalnızca ürün alış fiyatı (COGS toplamı)
    toplam_maliyet = (sum(v["units"] * v["cogs"] for v in V)
                      + (ca_total * us_vine_cogs if not ca_from_main else 0))
    cost_units = sum(v["units"] for v in V) + (ca_total if not ca_from_main else 0)
    kar_urun_sonrasi = batch_geri_donecek - toplam_maliyet

    avg_basket = sum(v["price"] * v["share"] for v in V)
    scen = cfg["scenarios"]
    scen_results = {name: {"units": u, "rev": u * avg_basket, "comm": u * avg_basket * cc_rate}
                    for name, u in scen.items()}
    fallback_key = "Beklenen" if "Beklenen" in scen else ("Orta" if "Orta" in scen else list(scen)[-1])

    campaign_months = days / DAYS_PER_MONTH
    if cfg["target_daily_total"] is not None:
        total_daily = cfg["target_daily_total"]
    else:
        ads_campaign_target = cfg["target_acos"] * batch_revenue
        total_daily = ads_campaign_target / days
    sp = total_daily / CHANNEL_SP_DIVISOR
    sd = sp * CHANNEL_SD_RATIO
    sb = sp * CHANNEL_SB_RATIO
    total_monthly = total_daily * DAYS_PER_MONTH

    ads_campaign = total_daily * days
    blended_acos = ads_campaign / batch_revenue
    acos_upper = blended_acos + cfg["acos_upper_extra"]
    influencer_campaign = scen_results[fallback_key]["units"] * avg_basket * cc_rate * campaign_months

    toplam_ek_gider = vine_ek_gider + ads_campaign + influencer_campaign
    net_geri_donecek = batch_geri_donecek - toplam_ek_gider
    net_kar = kar_urun_sonrasi - toplam_ek_gider
    grand_total = ads_campaign + vine_total + influencer_campaign

    kw = cfg["keyword"]
    p, c = kw.get("monthly_purchases"), kw.get("monthly_clicks")
    kw_cvr = p / c if (p is not None and c) else None
    kw_acos = (kw["bid"] / kw_cvr) / ads["price"] if (kw_cvr and kw.get("bid") is not None) else None

    return {
        "refs": refs, "amazonfees": amazonfees, "gross": gross, "sold": sold, "returned": returned, "kept": kept,
        "us_vine_product": us_vine_product, "us_vine_extra": us_vine_extra, "us_vine_total": us_vine_total,
        "vine_rows": vine_rows, "vine_total": vine_total, "vine_ek_gider": vine_ek_gider,
        "batch_revenue": batch_revenue, "batch_amazon_fee": batch_amazon_fee, "batch_geri_donecek": batch_geri_donecek,
        "toplam_maliyet": toplam_maliyet, "cost_units": cost_units, "kar_urun_sonrasi": kar_urun_sonrasi,
        "avg_basket": avg_basket, "scen_results": scen_results, "fallback_key": fallback_key,
        "campaign_months": campaign_months, "total_daily": total_daily, "sp": sp, "sd": sd, "sb": sb,
        "total_monthly": total_monthly, "ads_campaign": ads_campaign, "blended_acos": blended_acos,
        "acos_upper": acos_upper, "influencer_campaign": influencer_campaign, "toplam_ek_gider": toplam_ek_gider,
        "net_geri_donecek": net_geri_donecek, "net_kar": net_kar, "grand_total": grand_total,
        "kw_cvr": kw_cvr, "kw_acos": kw_acos,
        # Çapraz kontrol (skill kontrol listesi): NET KÂR iki yoldan
        "net_kar_check": net_geri_donecek - toplam_maliyet,
    }


# ---------------------------------------------------------------------------
# Biçimlendirme (skill şablonuyla aynı)
# ---------------------------------------------------------------------------
def m(x) -> str:
    return f"${x:,.2f}"


def pct(x) -> str:
    return f"%{x*100:.1f}"


def rate(x) -> str:
    """Kullanıcının girdiği oranlar (referral, CC, iade) için: tam sayıysa şablonla aynı (%15), değilse ondalık (%6.4).
    ACOS değerleri (Bölüm 7) bunu KULLANMAZ — skill kuralı gereği tam sayıya yuvarlanır."""
    v = x * 100
    if abs(v - round(v)) < 1e-9:
        return f"{v:.0f}"
    return f"{v:.2f}".rstrip("0").rstrip(".")


def esc(s) -> str:
    return _html.escape(str(s), quote=True)


def css_str(s) -> str:
    """CSS string içeriği (@page content) için kaçış: harf/rakam/boşluk ve Türkçe harfler dışında her şey \\HEX."""
    out = []
    for ch in str(s):
        if ch.isalnum() or ch == " ":
            out.append(ch)
        else:
            out.append(f"\\{ord(ch):x} ")
    return "".join(out)


def tr_lower(s: str) -> str:
    return s.replace("I", "ı").replace("İ", "i").lower()


def join_tr(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " ve " + items[-1]


def _dash(v, f):
    return f(v) if v is not None else "—"


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
_CSS = """
  @page { size: A4; margin: 2cm 1.8cm 2.2cm 1.8cm;
    @bottom-center { content: "__FOOTER__  |  Sayfa " counter(page) " / " counter(pages);
      font-family: 'DejaVu Sans', Arial, sans-serif; font-size: 8pt; color: #8a8f98; } }
  * { box-sizing: border-box; }
  html { background: #e9e6e0; }
  body { font-family: 'DejaVu Sans', Arial, sans-serif; font-size: 9.5pt; color: #23262b; line-height: 1.42; margin: 0; overflow-wrap: anywhere; }
  .sheet { background: #fff; max-width: 21cm; margin: 0 auto 24px; padding: 1.6cm 1.8cm; box-shadow: 0 1px 6px rgba(0,0,0,.12); }
  .toolbar { position: sticky; top: 0; z-index: 2; background: #171a1f; color: #f5f2ec; padding: 10px 16px;
             display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; justify-content: center; font-size: 9pt; margin-bottom: 18px; }
  .toolbar button { background: #b45309; color: #fff; border: 0; border-radius: 4px; padding: 7px 14px; font: inherit; font-weight: bold; cursor: pointer; }
  .toolbar span { color: #c9c4ba; }
  .cover { min-height: 18cm; display: flex; flex-direction: column; justify-content: center; page-break-after: always; break-after: page; }
  .cover .kicker { color: #b45309; font-size: 11pt; letter-spacing: 2px; font-weight: bold; margin-bottom: 14px; }
  .cover h1 { font-size: 30pt; color: #171a1f; margin: 0 0 10px 0; line-height: 1.2; overflow-wrap: anywhere; }
  .cover .sub { font-size: 13pt; color: #52575f; margin-bottom: 30px; }
  .cover-tags { margin-top: 24px; }
  .tag { display: inline-block; max-width: 100%; background: #f4ede2; color: #92400e; border: 1px solid #e8d5b8; border-radius: 3px;
         padding: 4px 10px; font-size: 8.3pt; margin-right: 6px; margin-bottom: 6px; }
  .section { break-inside: avoid; page-break-inside: avoid; margin-bottom: 14px; }
  h1.sec { font-size: 14.5pt; color: #171a1f; border-bottom: 2.2px solid #b45309; padding-bottom: 5px;
           margin-top: 0; margin-bottom: 10px; break-after: avoid; page-break-after: avoid; }
  h2.sub { font-size: 10.8pt; color: #171a1f; margin-top: 12px; margin-bottom: 6px;
           border-left: 3.5px solid #b45309; padding-left: 8px; break-after: avoid; page-break-after: avoid; }
  p { margin: 4px 0 8px 0; text-align: justify; }
  ul, ol { margin: 4px 0 10px 0; padding-left: 18px; }
  li { margin-bottom: 3px; }
  .tw { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; margin: 8px 0 12px 0; font-size: 8.5pt;
          break-inside: avoid; page-break-inside: avoid; }
  th { background: #171a1f; color: #f5f2ec; text-align: left; padding: 5.5px 8px; font-size: 8.1pt; font-weight: bold; }
  td { padding: 5px 8px; border-bottom: 1px solid #e5e7eb; overflow-wrap: anywhere; }
  tr:nth-child(even) td { background: #faf9f7; }
  td.ctr { text-align: center; white-space: nowrap; }
  td.tname { font-weight: bold; color: #92400e; }
  td.strong { font-weight: bold; }
  .highlight-row td { background: #fdf3e3 !important; font-weight: bold; }
  .callout { background: #f6f4ef; border-left: 3.5px solid #b45309; padding: 8px 12px; margin: 8px 0; font-size: 8.9pt; }
  .callout b { color: #92400e; }
  .kpi-grid { width: 100%; border-collapse: collapse; margin: 8px 0; }
  .kpi-grid td { width: 25%; border: 1px solid #e5e7eb; padding: 9px; text-align: center; vertical-align: top; }
  .kpi-num { font-size: 13pt; font-weight: bold; color: #171a1f; display: block; white-space: nowrap; }
  .kpi-label { font-size: 7.3pt; color: #7a7f88; text-transform: uppercase; letter-spacing: 0.3px; }
  .grandtotal { background: #171a1f; color: #f5f2ec; padding: 14px 18px; margin-top: 10px; font-size: 12pt;
                font-weight: bold; display: flex; flex-wrap: wrap; gap: 6px; justify-content: space-between;
                -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  .small-note { font-size: 7.7pt; color: #8a8f98; margin-top: 3px; }
  th, .highlight-row td, tr:nth-child(even) td, .tag, .callout { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  @media screen and (max-width: 640px) {
    .sheet { padding: 20px 16px; }
    .cover { min-height: 0; padding: 40px 0; }
    .cover h1 { font-size: 22pt; }
    .kpi-grid td { display: block; width: auto; }
  }
  @media print {
    html, body { background: #fff; }
    .toolbar { display: none; }
    .sheet { max-width: none; margin: 0; padding: 0; box-shadow: none; }
    .cover { height: 25cm; min-height: 0; }
    .tw { overflow: visible; }
  }
"""


def build_html(cfg: dict, r: dict, nonce: str | None = None) -> str:
    nonce = nonce or secrets.token_urlsafe(16)
    V = cfg["variations"]
    ai = cfg["ads_index"]
    ads = V[ai]
    sv = cfg["second_vine"]
    URUN = esc(cfg["product_name"])
    KW = esc(cfg["main_keyword"])
    ads_name = esc(ads["name"])
    others = [esc(v["name"]) for i, v in enumerate(V) if i != ai]
    names = [esc(v["name"]) for v in V]
    SV = esc(sv["name"]) if sv else ""
    rr, cc_rate, return_rate = cfg["referral_rate"], cfg["cc_rate"], cfg["return_rate"]
    total_daily = r["total_daily"]
    kw = cfg["keyword"]

    tags = "".join(f'<span class="tag">{esc(v["name"])} — {m(v["price"])}</span>' for v in V)
    tags += f'<span class="tag">Vine: ABD{" + " + SV if sv else ""} ({ads_name})</span>'
    tags += f'<span class="tag">Creator Connections %{rate(cc_rate)}</span>'
    tags += f'<span class="tag">Reklam Bütçesi: {m(total_daily)}/gün</span>'

    vine_line = (f"ABD + {SV}'da, yalnızca <b>{ads_name}</b> varyasyonunda; {SV}'da satış yok, stok yalnızca Vine için gönderilir."
                 if sv else f"ABD'de, yalnızca <b>{ads_name}</b> varyasyonunda.")
    if not others:
        ads_others_line = ""
    elif len(others) == 1:
        ads_others_line = f" {others[0]}'e reklam açılmaz."
    else:
        ads_others_line = f" {join_tr(others)} varyasyonlarına reklam açılmaz."

    # Bölüm 3
    unit_rows = "".join(
        f'<tr><td class="tname">{esc(v["name"])}</td><td class="ctr">{m(v["price"])}</td><td class="ctr">{m(v["cogs"])}</td>'
        f'<td class="ctr">{m(r["amazonfees"][i])}</td><td class="ctr strong">{m(r["gross"][i])}</td></tr>'
        for i, v in enumerate(V))
    fba_list = " / ".join(f'{m(v["fba"])} {esc(tr_lower(v["name"]))}' for v in V)

    # Bölüm 4
    ship_parts = [f'<b>{v["units"]:,} {esc(v["name"])} (ABD)</b>' for v in V]
    if sv:
        ship_parts.append(f'<b>{sv["total"]} {ads_name} ({SV}, yalnızca Vine)</b>')

    # Bölüm 5
    vine_rows_html = "".join(
        f'<tr><td>{esc(row["market"])}</td><td>{esc(row["source"])}</td><td class="ctr">{row["units"]}</td>'
        f'<td class="ctr">{m(row["product"])}</td><td class="ctr">{m(row["fba"])}</td><td class="ctr">{m(row["fee"])}</td>'
        f'<td class="ctr strong">{m(row["total"])}</td></tr>' for row in r["vine_rows"])
    if sv:
        sv_text = (f" {SV}'daki {sv['total']} birim ise satışı olmayan, ana partiden ayrılan stoktur."
                   if sv["from_main_batch"] else f" {SV}'daki {sv['total']} birim ise satışı olmayan ayrı bir sevkiyattır.")
    else:
        sv_text = ""

    # Bölüm 6
    single = len(r["scen_results"]) == 1
    scen_rows = "".join(
        f'<tr><td class="tname">{esc(name)}</td><td class="ctr">{d["units"]} adet</td>'
        f'<td class="ctr">{m(d["rev"])}</td><td class="ctr">{m(d["comm"])}</td></tr>'
        for name, d in r["scen_results"].items())

    # Bölüm 7
    if others:
        b7 = f"Bütçenin tamamı {ads_name}'e ayrılır; {join_tr(others)} organik + Creator Connections ile büyür."
    else:
        b7 = f"Bütçenin tamamı {ads_name}'e ayrılır."
    if cfg["target_daily_total"] is None:
        b7 += f" Bütçe, kampanya dönemi genelinde %{cfg['target_acos']*100:.0f} hedef ACOS baz alınarak hesaplanmıştır."

    # Bölüm 9
    stock_line = " + ".join(f'{v["units"]:,} {esc(v["name"])}' for v in V) + " (ABD)"
    if sv:
        stock_line += f"; {sv['total']} adet {ads_name} ({SV}, Vine-özel)"
    vine_reg = (f"Vine kaydı: ABD ve {SV}'da yalnızca {ads_name} için açılır" if sv
                else f"Vine kaydı: ABD'de yalnızca {ads_name} için açılır")

    # Bölüm 10
    share_row = ""
    if len(V) > 1:
        shares_txt = " / ".join("%{:.0f}".format(v["share"] * 100) for v in V)
        share_row = (f'<tr><td>Creator Connections sepet ağırlığı ({"/".join(names)})</td>'
                     f'<td class="ctr">{shares_txt}</td></tr>')
    acos_row = (f'<tr><td>Reklam bütçesi hedef ACOS</td><td class="ctr">{pct(cfg["target_acos"])}</td></tr>'
                if cfg["target_daily_total"] is None else "")

    footer = css_str(f"{cfg['product_name']} — Lansman Planı")
    css = _CSS.replace("__FOOTER__", footer)
    csp = (f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; img-src data:; "
           "font-src data:; base-uri 'none'; form-action 'none'")
    fb = r["scen_results"][r["fallback_key"]]["units"]

    return f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{URUN} — Lansman Planı</title>
<style>{css}</style></head><body>
<div class="toolbar"><button type="button" id="print-btn">PDF olarak kaydet</button>
<span>Yazdır penceresinde hedef: “PDF olarak kaydet”, kağıt: A4, “Üstbilgi ve altbilgiler” kapalı.</span></div>
<div class="sheet">

<div class="cover">
  <div class="kicker">{esc(cfg["market_label"])} &nbsp;·&nbsp; ÜRÜN LANSMANI</div>
  <h1>{URUN}</h1>
  <div class="sub">Yapılacaklar Planı &amp; Reklam–Lansman Bütçe Raporu</div>
  <div class="cover-tags">{tags}</div>
</div>

<div class="section">
<h1 class="sec">1 · Özet</h1>
<table class="kpi-grid"><tr>
  <td><span class="kpi-num">{m(r["toplam_maliyet"])}</span><span class="kpi-label">İlk Sevkiyat Toplam Maliyeti</span></td>
  <td><span class="kpi-num">{m(total_daily)}</span><span class="kpi-label">Günlük Reklam Bütçesi</span></td>
  <td><span class="kpi-num">{m(r["vine_total"])}</span><span class="kpi-label">Vine Toplam Gider</span></td>
  <td><span class="kpi-num">{m(r["net_kar"])}</span><span class="kpi-label">Net Kâr (Tüm Giderler Sonrası)</span></td>
</tr></table>
<div class="callout">
1) <b>Ürün:</b> {join_tr([f'{esc(v["name"])} ({m(v["price"])})' for v in V])}.<br>
2) <b>Vine:</b> {vine_line}<br>
3) <b>Creator Connections:</b> %{rate(cc_rate)} komisyon, katılımcı sayısında sınır yok, ödeme yalnızca satışta doğar.<br>
4) <b>Reklam:</b> Günlük bütçe {m(total_daily)}, <b>yalnızca {ads_name}'de</b>.{ads_others_line}
</div></div>

<div class="section">
<h1 class="sec">2 · Anahtar Kelime &amp; Pazar Verisi</h1>
<p>Bid ve dönüşüm oranı, ana anahtar kelime <b>"{KW}"</b> için SellerSprite verisinden alınmıştır:</p>
<div class="tw"><table><tr><th>Metrik</th><th>Değer</th></tr>
<tr><td>Aylık Satış</td><td class="ctr">{_dash(kw.get("monthly_purchases"), lambda x: f"{x:,.0f}")}</td></tr>
<tr><td>Dönüşüm Oranı (tık→satış)</td><td class="ctr">{_dash(r["kw_cvr"], pct)}</td></tr>
<tr><td>Ortalama PPC Bid</td><td class="ctr">{_dash(kw.get("bid"), m)}</td></tr>
<tr><td>Ortalama Piyasa Fiyatı</td><td class="ctr">{_dash(kw.get("market_avg_price"), m)}</td></tr>
<tr><td>Önerilen ACOS</td><td class="ctr">{_dash(r["kw_acos"], pct)}</td></tr>
</table></div>
<p class="small-note">SellerSprite verileri aylıktır. Önerilen ACOS, Bid ÷ (Dönüşüm Oranı × {ads_name} satış fiyatı {m(ads["price"])}) formülüyle türetilmiştir; Bölüm 7'deki kampanya ACOS'undan farklıdır.</p>
</div>

<div class="section">
<h1 class="sec">3 · Fiyatlandırma &amp; Birim Ekonomisi</h1>
<div class="tw"><table><tr><th>Varyasyon</th><th>Satış Fiyatı</th><th>Ürün Maliyeti</th><th>Amazon Fee (Referral %{rate(rr)} + FBA)</th><th>Reklam Öncesi Brüt Kâr</th></tr>
{unit_rows}
</table></div>
<p class="small-note">FBA ücreti tahmini ({fba_list}), kesin paket ölçü/ağırlığıyla teyit edilecek.</p>
</div>

<div class="section">
<h1 class="sec">4 · İlk Sevkiyat — Maliyet, Kâr ve Geri Dönecek Para</h1>
<p>Başlangıç sevkiyatı: {", ".join(ship_parts)}. %{rate(return_rate)} kategori iade oranı satılan adetlere uygulanmıştır
(iadede referral fee geri ödenir, FBA ücreti ödenmez, ürün maliyeti geri kazanılamaz kabul edilmiştir).</p>
<div class="tw"><table><tr><th>Kalem</th><th>Tutar</th></tr>
<tr class="highlight-row"><td>TOPLAM MALİYET (ürün alış fiyatı, {r["cost_units"]:,} adet)</td><td class="ctr">{m(r["toplam_maliyet"])}</td></tr>
<tr><td>Brüt Satış Geliri (%{rate(return_rate)} iade sonrası)</td><td class="ctr">{m(r["batch_revenue"])}</td></tr>
<tr><td>Amazon Fee (Referral + FBA; iadelerde yalnızca FBA)</td><td class="ctr">−{m(r["batch_amazon_fee"])}</td></tr>
<tr class="highlight-row"><td>TOPLAM GERİ DÖNECEK PARA (reklam öncesi)</td><td class="ctr">{m(r["batch_geri_donecek"])}</td></tr>
<tr class="highlight-row"><td>ORTALAMA TOPLAM KÂR (ürün maliyeti düşülmüş)</td><td class="ctr">{m(r["kar_urun_sonrasi"])}</td></tr>
<tr><td>Vine Ek Gideri (Bölüm 5)</td><td class="ctr">−{m(r["vine_ek_gider"])}</td></tr>
<tr><td>Reklam Gideri (Bölüm 8)</td><td class="ctr">−{m(r["ads_campaign"])}</td></tr>
<tr><td>Creator Connections Gideri (Bölüm 8)</td><td class="ctr">−{m(r["influencer_campaign"])}</td></tr>
<tr class="highlight-row"><td>NET GERİ DÖNECEK PARA</td><td class="ctr">{m(r["net_geri_donecek"])}</td></tr>
<tr class="highlight-row"><td>NET KÂR</td><td class="ctr">{m(r["net_kar"])}</td></tr>
</table></div></div>

<div class="section">
<h1 class="sec">5 · Amazon Vine — ABD{" + " + SV if sv else ""} (Yalnızca {ads_name})</h1>
<p>Vine yalnızca <b>{ads_name}</b> varyasyonunda çalıştırılır. ABD'deki {cfg["us_vine_units"]} birim ana stoktan ayrılır;
satılmadığı için ürün maliyeti zarar olarak yazılır.{sv_text} Amazon, satış olmasa da FBA yerine getirme ücreti tahsil eder.</p>
<div class="tw"><table><tr><th>Pazar</th><th>Kaynak</th><th>Birim</th><th>Ürün Maliyeti</th><th>Amazon (FBA) Ücreti</th><th>Vine Kayıt Ücreti</th><th>Toplam</th></tr>
{vine_rows_html}
<tr class="highlight-row"><td colspan="6">VİNE TOPLAM GİDER (ürün + FBA + kayıt, tek seferlik, görünürlük amaçlı — ürün maliyeti Bölüm 4'te ayrıca düşülmez)</td><td class="ctr">{m(r["vine_total"])}</td></tr>
</table></div></div>

<div class="section">
<h1 class="sec">6 · Creator Connections (Influencer)</h1>
<ul>
<li>Marka, %{rate(cc_rate)} sabit komisyon oranı ve ürün belirler; kampanya tüm uygun Amazon Creator'lara açılır.</li>
<li><b>Katılımcı sayısında üst sınır yoktur.</b></li>
<li><b>Ödeme yalnızca gerçekleşen satışta doğar</b> — gösterim/tıklama/içerik başına ödeme yapılmaz.</li>
<li>Kazanılan komisyon, ilgili ayın bitiminden ~60 gün sonra Amazon tarafından ödenir.</li>
</ul>
<div class="tw"><table><tr><th>{"Tahmin" if single else "Senaryo"}</th><th>Aylık Influencer Satışı</th><th>Tahmini Ciro</th><th>Komisyon (%{rate(cc_rate)})</th></tr>
{scen_rows}
</table></div></div>

<div class="section">
<h1 class="sec">7 · Reklam Bütçesi (Yalnızca {ads_name})</h1>
<p>{b7}</p>
<div class="tw"><table><tr><th>Kanal</th><th>Günlük</th><th>Aylık (≈30,4 gün)</th></tr>
<tr><td class="tname">SP</td><td class="ctr">{m(r["sp"])}</td><td class="ctr">{m(r["sp"]*DAYS_PER_MONTH)}</td></tr>
<tr><td class="tname">SD</td><td class="ctr">{m(r["sd"])}</td><td class="ctr">{m(r["sd"]*DAYS_PER_MONTH)}</td></tr>
<tr><td class="tname">SB</td><td class="ctr">{m(r["sb"])}</td><td class="ctr">{m(r["sb"]*DAYS_PER_MONTH)}</td></tr>
<tr class="highlight-row"><td>TOPLAM ({ads_name})</td><td class="ctr">{m(total_daily)}</td><td class="ctr">{m(r["total_monthly"])}</td></tr>
</table></div>
<p><b>Beklenen ACOS: %{r["blended_acos"]*100:.0f}.</b> Dönüşüm oranı ve CPC farklı stratejilerle değişebileceğinden ACOS
%{r["acos_upper"]*100:.0f}'e kadar çıkabilir.</p>
<p class="small-note">Reklam, lansmanla birlikte başlar; tam bütçeli/full agresif harcamaya Vine yorumları görünmeye başladıktan sonra geçilir.</p>
</div>

<div class="section">
<h1 class="sec">8 · İlk Parti Kampanya Bütçesi</h1>
<p class="small-note">Bölüm 4'teki ürün maliyetine ek pazarlama/lansman gideridir; ilk parti {ads_name} stoku tükenene kadarki dönemi kapsar.</p>
<div class="tw"><table><tr><th>Kalem</th><th>Açıklama</th><th>Tutar</th></tr>
<tr><td class="tname">Reklam</td><td>Günlük {m(total_daily)} bütçe üzerinden kampanya toplamı</td><td class="ctr strong">{m(r["ads_campaign"])}</td></tr>
<tr><td class="tname">Vine</td><td>Toplam gider (ürün + FBA + kayıt), tek seferlik</td><td class="ctr strong">{m(r["vine_total"])}</td></tr>
<tr><td class="tname">Creator Connections</td><td>Kampanya dönemi toplamı</td><td class="ctr strong">{m(r["influencer_campaign"])}</td></tr>
</table></div>
<div class="grandtotal"><span>GENEL TOPLAM — İLK PARTİ KAMPANYASI</span><span>{m(r["grand_total"])}</span></div>
<p class="small-note">Bu toplamın kâra etkisi Bölüm 4'te net rakamlarla gösterilmiştir.</p>
</div>

<div class="section">
<h1 class="sec">9 · Yapılacaklar</h1>
<h2 class="sub">Lansman Öncesi</h2>
<ul>
<li>{"/".join(names)} listing, görsel, A+ içerik hazırlığı</li>
<li>Stok sevkiyatı: {stock_line}</li>
<li>{vine_reg}</li>
<li>Creator Connections kampanyası kurulur (%{rate(cc_rate)} sabit komisyon)</li>
<li>Negatif kelime listesi (rakip marka adları dahil) hazırlanır</li>
</ul>
<h2 class="sub">Lansman</h2>
<ul>
<li>Listing canlıya alınır, Vine havuzu eş zamanlı açılır</li>
<li>SP+SD+SB kampanyaları yalnızca {ads_name}'de başlatılır, Creator Connections yayına alınır</li>
<li>Vine yorumları görünmeye başladıktan sonra tam bütçeli harcamaya geçilir</li>
</ul>
<h2 class="sub">Takip (Sürekli)</h2>
<ul>
<li>Haftalık ACOS, Vine yorum sayısı, influencer satış katkısı izlenir</li>
<li>İlk parti stoku tükenene kadar gerçekleşen harcama bu rapordaki bütçeyle karşılaştırılır</li>
</ul></div>

<div class="section">
<h1 class="sec">10 · Varsayımlar</h1>
<div class="tw"><table><tr><th>Varsayım</th><th>Değer</th></tr>
<tr><td>Amazon referral fee</td><td class="ctr">%{rate(rr)}</td></tr>
<tr><td>FBA yerine getirme ücreti (tahmini)</td><td class="ctr">{fba_list}</td></tr>
{share_row}
<tr><td>Influencer aylık satış</td><td class="ctr">{fb} adet</td></tr>
{acos_row}
</table></div></div>

</div>
<script nonce="{nonce}">document.getElementById("print-btn").addEventListener("click", function () {{ window.print(); }});</script>
</body></html>
"""


def generate(raw: dict) -> tuple[str, dict, dict]:
    """Girdi -> (html, hesaplanan rakamlar, normalize yapılandırma)."""
    cfg = normalize(raw)
    r = compute(cfg)
    return build_html(cfg, r), r, cfg
