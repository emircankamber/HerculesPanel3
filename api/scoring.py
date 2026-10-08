"""
Hesaplama motoru. Excel şablonundaki formüllerin birebir Python karşılığı.
Eşikler burada sabit ama query parametresiyle override edilebilir hale
getirilebilir (bkz. main.py'deki /api/analyze).
"""
from dataclasses import dataclass, asdict


# ---- Varsayılan eşikler (Excel'deki ön değerlendirme paneliyle aynı) ----
DEFAULT_THRESHOLDS = {
    "min_avg_price": 25.0,        # ort. satış fiyatı >= 25
    "min_gross_margin": 0.65,     # gross margin >= %65
    "max_acos": 0.75,             # ACOS <= %75
    "max_brand_share": 0.35,      # en büyük marka payı <= %35
    "min_strong_new_brands": 2,   # son 1 yılda güçlü giren marka sayısı >= 2
    "min_net_margin": 0.15,       # kar analizindeki net marj >= %15
}

# ---------------------------------------------------------------------------
# ÖN ÖNERİ KURALI — TEK KAYNAK. Başka hiçbir yerde sayı yazma: sunucu bu sınırları
# analiz yanıtında (`pre_assessment.rule`) ve `/api/verdict-rule`'da gönderir; panel
# (canlı yeniden hesaplama, özet metni, açıklamalar) yalnızca bu değerleri kullanır.
#   olumsuz <= UYGUN_MAX_NEGATIVE          -> Uygun
#   UYGUN_MAX_NEGATIVE < olumsuz < ELIMINATE_AT -> Sınırda
#   olumsuz >= ELIMINATE_AT                -> Elenmiş
# (Geçmiş kayıtlardaki ön öneriler kaydedildikleri andaki kurala göredir; yeniden hesaplanmaz.)
# ---------------------------------------------------------------------------
UYGUN_MAX_NEGATIVE = 1   # 0-1 olumsuz -> Uygun
ELIMINATE_AT = 4         # 4 ve üzeri olumsuz -> Elenmiş; arası (2-3) -> Sınırda


def verdict_for(negative_count: int) -> str:
    if negative_count <= UYGUN_MAX_NEGATIVE:
        return "Uygun"
    if negative_count < ELIMINATE_AT:
        return "Sınırda"
    return "Elenmiş"


# ---------------------------------------------------------------------------
# KRİTER 03 — İLK 5 KEYWORD'ÜN AĞIRLIKLI ACOS'U (yalnızca SUNUCUDA hesaplanır)
#   ACOS = Σ(bid × clicks) ÷ Σ(purchases × fiyat)
#   = 5 keyword'e birlikte reklam verilse oluşacak toplam harcama ÷ toplam satış.
# Sıralama: keyword modunda relevancy, ASIN modunda trafficPercentage (büyükten küçüğe;
# eşitlikte orijinal sıra). bid/clicks/purchases/fiyat'tan biri eksik ya da geçersizse
# keyword atlanır, sıradaki alınır. Satışı 0 olan keyword harcamaya eklenir.
# (Lansman Raporu bunu KULLANMAZ — orada skill gereği ana keyword + kendi fiyatımız.)
# ---------------------------------------------------------------------------
TOP_ACOS_N = 5


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v == v and v not in (float("inf"), float("-inf")) else None
    try:
        f = float(str(v).replace(",", "").strip())
        return f if f == f and f not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def weighted_top_acos(rows: list, rank_field: str, price_of=None, n: int = TOP_ACOS_N) -> dict:
    """
    rows: keyword satırları; rank_field: "relevancy" (keyword modu) ya da "trafficPercentage" (ASIN).
    price_of(row) -> fiyat (keyword modunda satırın avgPrice'ı, ASIN modunda ürünün fiyatı).
    Dönüş: value (oran ya da None), count, keywords[{keyword, rank, bid, clicks, purchases, price,
    spend, sales, acos}], total_spend, total_sales, no_sales (harcama var satış yok), skipped.
    """
    price_of = price_of or (lambda r: r.get("avgPrice"))
    indexed = list(enumerate(rows or []))
    def rank_key(item):
        i, r = item
        rv = _num(r.get(rank_field))
        return (0 if rv is not None else 1, -(rv or 0), i)   # sayısal sıralama değeri olmayanlar sona
    used, skipped = [], []
    for _, r in sorted(indexed, key=rank_key):
        if len(used) >= n:
            break
        bid, clicks, purch, price = _num(r.get("bid")), _num(r.get("clicks")), _num(r.get("purchases")), _num(price_of(r))
        if None in (bid, clicks, purch, price) or bid < 0 or clicks < 0 or purch < 0 or price <= 0:
            skipped.append(r.get("keyword"))
            continue
        spend, sales = bid * clicks, purch * price
        used.append({"keyword": r.get("keyword"), "rank": _num(r.get(rank_field)), "bid": bid, "clicks": clicks,
                     "purchases": purch, "price": price, "spend": spend, "sales": sales,
                     "acos": (spend / sales) if sales > 0 else None})
    total_spend = sum(u["spend"] for u in used)
    total_sales = sum(u["sales"] for u in used)
    value = (total_spend / total_sales) if total_sales > 0 else None
    return {"value": value, "count": len(used), "n": n, "rank_field": rank_field, "keywords": used,
            "total_spend": total_spend, "total_sales": total_sales,
            "no_sales": bool(used) and total_sales <= 0 and total_spend > 0, "skipped": skipped}


def _span(lo: int, hi: int) -> str:
    return str(lo) if lo == hi else f"{lo}–{hi}"


def verdict_rule() -> dict:
    """Panelin kullandığı kural tanımı (sınırlar + hazır açıklama metni)."""
    uygun = _span(0, UYGUN_MAX_NEGATIVE)
    sinirda = _span(UYGUN_MAX_NEGATIVE + 1, ELIMINATE_AT - 1) if ELIMINATE_AT - 1 > UYGUN_MAX_NEGATIVE else None
    parts = [f"{uygun} olumsuz = Uygun"] + ([f"{sinirda} = Sınırda"] if sinirda else []) + [f"{ELIMINATE_AT}+ = Elenmiş"]
    return {"uygun_max_negative": UYGUN_MAX_NEGATIVE, "eliminate_at": ELIMINATE_AT, "text": " · ".join(parts)}


def calc_keyword_ad_metrics(clicks: int, purchases: int, bid: float, avg_price: float,
                             impressions: int | None = None, searches: int | None = None) -> dict:
    """
    keyword_miner'ın ham alanlarından hesaplanan reklam metrikleri.
    NOT: SellerSprite UI'daki "Conversion Rate" (ABA 3-tık payı) ile birebir
    AYNI DEĞİLDİR — farklı metodoloji. Panelde "(hesaplanan)" etiketiyle gösterilir.
    """
    # Eksik (null) alanlar hesabı düşürmesin: tek bir keyword'de purchases/clicks None gelirse
    # eskiden TypeError tüm analizi 502'ye çeviriyordu -> ilgili metrik None olur.
    click_cvr = (purchases / clicks) if (clicks and purchases is not None) else None
    ctr = (clicks / impressions) if (impressions and clicks is not None) else None
    search_cvr = (purchases / searches) if (searches and purchases is not None) else None
    cpa = (bid / click_cvr) if (bid and click_cvr) else None
    acos = (bid / (click_cvr * avg_price)) if (bid and click_cvr and avg_price) else None
    return {
        "click_cvr": click_cvr,
        "ctr": ctr,
        "search_cvr": search_cvr,
        "cpa": cpa,
        "acos": acos,
    }


def calc_profit(cogs: float, sale_price: float, fba_fee: float, referral_fee: float,
                 acos: float, return_rate: float, overhead_rate: float = 0.01) -> dict:
    """Kar analizi — Excel'deki 'KAR ANALİZİ' bloğuyla birebir aynı formüller."""
    if not sale_price:
        return {"ad_cost": 0, "overhead_cost": 0, "return_cost": 0,
                "total_cost": 0, "unit_profit": 0, "margin": 0, "roi": 0}
    ad_cost = acos * sale_price
    overhead_cost = overhead_rate * sale_price
    return_cost = return_rate * (cogs + fba_fee)
    total_cost = cogs + fba_fee + referral_fee + ad_cost + overhead_cost + return_cost
    unit_profit = sale_price - total_cost
    margin = unit_profit / sale_price
    roi = (unit_profit / cogs) if cogs else 0
    return {
        "ad_cost": round(ad_cost, 2),
        "overhead_cost": round(overhead_cost, 2),
        "return_cost": round(return_cost, 2),
        "total_cost": round(total_cost, 2),
        "unit_profit": round(unit_profit, 2),
        "margin": round(margin, 4),
        "roi": round(roi, 4),
    }


# ---------------------------------------------------------------------------
# KÂR HESAPLAYICISININ BAŞLANGIÇ DEĞERLERİ — TEK KAYNAK (sunucu).
# Panel analiz sonucunu açar açmaz kâr hesaplayıcısını bu değerlerle doldurup Kriter 06'yı (Net Kâr Marjı)
# hesaplar. Sunucu ön öneriyi AYNI değerlerle hesaplamazsa (eskiden net_margin=None idi) panelde "Sınırda"
# görünen analiz Geçmiş/Ana Sayfa'ya "Uygun" yazılıyordu. Panel bu sayıları yanıttaki `profit_inputs`'tan
# alır (yeniden yuvarlamaz) → iki taraf birebir aynı marjı bulur.
# Birimler panel alanlarıyla aynı: dolar, oranlar YÜZDE sayısı (ref_rate 15 = %15).
# ---------------------------------------------------------------------------
PROFIT_DEFAULTS = {"cogs": 6.0, "sale": 37.75, "fba": 5.5, "ref_rate": 15.0, "acos": 40.0, "ret": 3.0, "gen": 1.0}


def initial_profit_inputs(avg_price, weighted_acos, return_rate, pre_cost: dict | None = None) -> dict:
    """avg_price $, weighted_acos / return_rate 0-1 oran (None → varsayılan), pre_cost {cogs, fba, gen}
    (analiz öncesi girilen maliyet; yalnızca dolu alanlar). `sources` hangi değerin nereden geldiğini söyler."""
    inp = dict(PROFIT_DEFAULTS)
    src = {k: "default" for k in inp}
    price = _num(avg_price)
    if price and price > 0:
        inp["sale"], src["sale"] = round(price, 2), "market"
    a = _num(weighted_acos)
    if a is not None:
        inp["acos"], src["acos"] = round(a * 100, 1), "market"
    r = _num(return_rate)
    if r is not None:
        inp["ret"], src["ret"] = round(r * 100, 2), "market"
    for k in ("cogs", "fba", "gen"):
        v = _num((pre_cost or {}).get(k))
        if v is not None and v >= 0:
            inp[k], src[k] = v, "pre_cost"
    return {**inp, "sources": src}


def net_margin_from_inputs(inp: dict) -> float | None:
    """Panelin recalcProfit'iyle aynı: referral $ = oran × fiyat; fiyat yoksa/0 ise marj hesaplanmaz."""
    sale = inp.get("sale") or 0
    if sale <= 0:
        return None
    return calc_profit(cogs=inp["cogs"], sale_price=sale, fba_fee=inp["fba"],
                       referral_fee=inp["ref_rate"] / 100 * sale, acos=inp["acos"] / 100,
                       return_rate=inp["ret"] / 100, overhead_rate=inp["gen"] / 100)["margin"]


@dataclass
class PreAssessmentCriterion:
    label: str
    value: float | None
    threshold: float
    direction: str   # ">=" veya "<="
    flag: str        # "OK" | "OLUMSUZ" | "n/a"
    unit: str = "percent"   # "percent" (0-1 oran, %'ye çevrilir) | "count" (düz sayı, örn. marka adedi)


def pre_assessment(avg_price: float | None, gross_margin: float | None, acos: float | None,
                    top_brand_share: float | None, strong_new_brands: int | None,
                    net_margin: float | None, thresholds: dict = None, acos_detail: dict | None = None) -> dict:
    """
    Excel'deki 6 kriterli ön değerlendirme panelinin Python karşılığı.
    Ön öneri `verdict_for` ile (kural yukarıdaki sabitlerde: UYGUN_MAX_NEGATIVE, ELIMINATE_AT).
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    def flag(value, threshold, direction):
        if value is None:
            return "n/a"
        ok = (value >= threshold) if direction == ">=" else (value <= threshold)
        return "OK" if ok else "OLUMSUZ"

    criteria = [
        PreAssessmentCriterion("Ort. Satış Fiyatı", avg_price, th["min_avg_price"], ">=",
                                flag(avg_price, th["min_avg_price"], ">="), unit="usd"),
        PreAssessmentCriterion("Gross Margin", gross_margin, th["min_gross_margin"], ">=",
                                flag(gross_margin, th["min_gross_margin"], ">="), unit="percent"),
        # Kriter 03: ilk 5 keyword'ün ağırlıklı ACOS'u (weighted_top_acos). Harcama var ama hiç satış
        # yoksa ACOS sonsuzdur -> değer yok ama kriter OLUMSUZ.
        PreAssessmentCriterion("ACOS", acos, th["max_acos"], "<=",
                                "OLUMSUZ" if (acos is None and (acos_detail or {}).get("no_sales"))
                                else flag(acos, th["max_acos"], "<="), unit="percent"),
        PreAssessmentCriterion("En Büyük Marka Payı", top_brand_share, th["max_brand_share"], "<=",
                                flag(top_brand_share, th["max_brand_share"], "<="), unit="percent"),
        # KRİTİK: bu bir ORAN değil, DÜZ SAYI (kaç marka) — frontend'de yanlışlıkla
        # yüzdeye çevrilip "200.0%" gibi absürt bir değer gösteriliyordu (gerçek
        # kullanıcı ekran görüntüsüyle bulundu). unit="count" ile artık kesin ayrım var.
        PreAssessmentCriterion("Güçlü Yeni Marka (1 yıl)", strong_new_brands, th["min_strong_new_brands"], ">=",
                                flag(strong_new_brands, th["min_strong_new_brands"], ">="), unit="count"),
        PreAssessmentCriterion("Net Kar Marjı (kar analizi)", net_margin, th["min_net_margin"], ">=",
                                flag(net_margin, th["min_net_margin"], ">="), unit="percent"),
    ]

    negative_count = sum(1 for c in criteria if c.flag == "OLUMSUZ")

    return {
        "criteria": [asdict(c) for c in criteria],
        "negative_count": negative_count,
        "verdict": verdict_for(negative_count),
        "eliminate_at": ELIMINATE_AT,
        "rule": verdict_rule(),   # panel canlı yeniden hesaplamada bu sınırları kullanır
        "acos_detail": acos_detail,  # Kriter 03'ün kullandığı keyword'ler ve her birinin ACOS'u
    }
