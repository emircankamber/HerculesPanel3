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
    click_cvr = (purchases / clicks) if clicks else None
    ctr = (clicks / impressions) if impressions else None
    search_cvr = (purchases / searches) if searches else None
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
                    net_margin: float | None, thresholds: dict = None) -> dict:
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
        PreAssessmentCriterion("ACOS", acos, th["max_acos"], "<=",
                                flag(acos, th["max_acos"], "<="), unit="percent"),
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
    }
