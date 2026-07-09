"""
Hercules Signal Engine v2.1 — tasarım dokümanına birebir sadık.

Ölçek kuralı: bileşenler [0,1], sinyaller bileşen ağırlıklı ortalamasının
x100'ü ([0,100]), Opportunity Score sinyallerin ağırlıklı ortalaması
(ek çarpan YOK).

NOT (dürüstlük): Doküman Market sinyali için tam ağırlıkları verir
(§2.2: 0.25/0.20/0.25/0.20/0.10). Demand (§2.3) ve Truth (§2.4) için
YALNIZCA bileşen listesi verilir, iç ağırlık verilmez. Bu modülde o
bileşenler için makul varsayılan ağırlıklar kullanılmıştır — bunlar
KESİN DEĞİLDİR, doküman kuralına göre "20-30 tamamlanmış döngüye kadar
manuel sabit" ve ekiple birlikte kalibre edilmelidir. Aşağıda her yerde
"# VARSAYIM" yorumuyla işaretlendi.
"""
import math
from dataclasses import dataclass, field


def clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ---------------------------------------------------------------------------
# 2.2 MARKET SIGNAL — dokümanda tam ağırlıklı, birebir
# ---------------------------------------------------------------------------
def market_signal(brand_shares: list[float], asin_revenue_shares: list[float],
                   top10_ratings_weighted: list[tuple], top10_review_counts: list[int],
                   new_product_revenue_share: float) -> dict:
    """
    brand_shares: top 10-50 markanın ciro payı [0,1] listesi (toplam ~1.0)
    asin_revenue_shares: ASIN bazlı ciro payı [0,1] listesi (entropi için)
    top10_ratings_weighted: [(rating, ciro_payı), ...] top 10 ürün
    top10_review_counts: top 10 ürünün review sayıları (medyan için)
    new_product_revenue_share: son 12 ay girenlerin ciro payı [0,1]
    """
    # HHI = Σ sᵢ² — düşük iyi
    hhi = sum(s ** 2 for s in brand_shares) if brand_shares else 1.0
    hhi = clip01(hhi)

    # Revenue Entropy = -Σ pᵢ ln(pᵢ) / ln(n) — yüksek iyi
    n = len(asin_revenue_shares)
    if n > 1:
        entropy = -sum(p * math.log(p) for p in asin_revenue_shares if p > 0) / math.log(n)
        entropy = clip01(entropy)
    else:
        entropy = 0.0

    # Quality Gap = Σ wᵢ·max(0, 4.4 − ratingᵢ), normalize / 3.0 (rating tabanı ~1.4)
    qg_raw = sum(w * max(0.0, 4.4 - r) for r, w in top10_ratings_weighted)
    qg_n = clip01(qg_raw / 3.0)

    # Review Moat = min(1, log10(medyan)/log10(5000)) — düşük iyi
    if top10_review_counts:
        sorted_rv = sorted(top10_review_counts)
        mid = len(sorted_rv) // 2
        median_rv = sorted_rv[mid] if len(sorted_rv) % 2 else (sorted_rv[mid - 1] + sorted_rv[mid]) / 2
        median_rv = max(median_rv, 1)
        rm = min(1.0, math.log10(median_rv) / math.log10(5000))
    else:
        rm = 0.0

    new_share = clip01(new_product_revenue_share)

    score = 100 * (0.25 * (1 - hhi) + 0.20 * entropy + 0.25 * qg_n + 0.20 * (1 - rm) + 0.10 * new_share)

    return {
        "components": {"hhi": hhi, "entropy": entropy, "quality_gap": qg_n,
                        "review_moat": rm, "new_product_share": new_share},
        "score": round(score, 1),
    }


# ---------------------------------------------------------------------------
# 2.3 DEMAND SIGNAL — doküman iç ağırlık vermiyor, VARSAYIM ile dolduruldu
# ---------------------------------------------------------------------------
def demand_signal(search_volume: float, sv_trend_pct_3m: float, sv_cv_12m: float,
                   click_cvr: float, acos: float, avg_price: float,
                   sv_min: float = 15000, sv_saturation: float = 150000) -> dict:
    """
    search_volume: aylık arama hacmi
    sv_trend_pct_3m: son 3 ay arama trendi (%, -0.10 = -%10)
    sv_cv_12m: 12 aylık arama hacmi varyasyon katsayısı (mevsimsellik cezası; yüksek=kötü)
    click_cvr, acos: keyword_miner'dan hesaplanan (bkz scoring.py)
    avg_price: >= $25 bandı beklenir
    """
    # SV mutlak — log ölçekli, sv_min altı 0'a yakın, sv_saturation üstü 1
    if search_volume <= 0:
        sv_component = 0.0
    else:
        sv_component = clip01(math.log(max(search_volume, 1) / sv_min) /
                               math.log(sv_saturation / sv_min)) if search_volume > sv_min else \
                       clip01(search_volume / sv_min * 0.5)  # VARSAYIM: eşik altı yarı-lineer

    # 3 ay ivme — VARSAYIM: -%20..+%20 aralığını [0,1]'e lineer eşle
    momentum = clip01((sv_trend_pct_3m + 0.20) / 0.40)

    # mevsimsellik cezası — VARSAYIM: cv 0'da 1, cv>=0.6'da 0
    seasonality = clip01(1 - sv_cv_12m / 0.6)

    # CVR-bid/ACOS dengesi — VARSAYIM: ACOS %75 üstünde 0, %10 altında 1 (lineer)
    acos_balance = clip01(1 - (acos - 0.10) / 0.65) if acos is not None else 0.5

    # fiyat bandı — VARSAYIM: $25 altı ceza, $25 üstü tam puan
    price_band = 1.0 if (avg_price or 0) >= 25 else clip01((avg_price or 0) / 25)

    # VARSAYIM ağırlıklar (eşit-ağırlığa yakın, fiyat bandı sert eşik olduğu için düşük ağırlık)
    score = 100 * (0.30 * sv_component + 0.20 * momentum + 0.15 * seasonality +
                   0.25 * acos_balance + 0.10 * price_band)

    return {
        "components": {"sv_component": round(sv_component, 3), "momentum": round(momentum, 3),
                        "seasonality": round(seasonality, 3), "acos_balance": round(acos_balance, 3),
                        "price_band": round(price_band, 3)},
        "score": round(score, 1),
        "weights_note": "VARSAYIM ağırlıklar — dokümanda Demand için iç ağırlık verilmedi, kalibrasyon gerekir",
    }


# ---------------------------------------------------------------------------
# 2.4 TRUTH SIGNAL — doküman iç ağırlık vermiyor, VARSAYIM ile dolduruldu
# ---------------------------------------------------------------------------
def truth_signal(reported_revenue: float, price: float, units: float,
                  bsr_sales_consistent: bool = True, snapshot_jump_detected: bool = False,
                  review_velocity_anomaly: bool = False) -> dict:
    """
    Ciro ≈ fiyat × adet çapraz kontrolü + tutarlılık bayrakları.
    Sapma >%20 → düşür (doküman kuralı, birebir).
    """
    expected_revenue = price * units if (price and units) else None
    if expected_revenue and reported_revenue:
        deviation = abs(reported_revenue - expected_revenue) / reported_revenue
        revenue_check = clip01(1 - deviation / 0.20) if deviation <= 0.20 else clip01(1 - deviation)
    else:
        deviation = None
        revenue_check = 0.5  # veri yok, nötr VARSAYIM

    bsr_check = 1.0 if bsr_sales_consistent else 0.3
    snapshot_check = 0.3 if snapshot_jump_detected else 1.0
    anomaly_check = 0.3 if review_velocity_anomaly else 1.0

    # VARSAYIM ağırlıklar: ciro çapraz kontrolü en ağır (doğrudan sayısal), diğerleri bayrak
    score = 100 * (0.40 * revenue_check + 0.25 * bsr_check + 0.20 * snapshot_check + 0.15 * anomaly_check)

    return {
        "components": {"revenue_deviation": round(deviation, 3) if deviation is not None else None,
                        "revenue_check": round(revenue_check, 3), "bsr_check": bsr_check,
                        "snapshot_check": snapshot_check, "anomaly_check": anomaly_check},
        "score": round(score, 1),
        "weights_note": "VARSAYIM ağırlıklar — dokümanda Truth için iç ağırlık verilmedi",
    }


# ---------------------------------------------------------------------------
# 2.6 RISK SIGNAL — doküman ağırlıkları birebir
# ---------------------------------------------------------------------------
def risk_signal(regulation_risk: float, ip_trademark_risk: float,
                 supplier_concentration_risk: float, return_risk: float,
                 seasonality_cashflow_risk: float, review_manipulation_risk: float) -> dict:
    """Tüm girdiler [0,100] — yüksek = riskli. Bazıları manuel/heuristik olabilir (bkz heuristics.py kullanımı main.py'de)."""
    vals = {"regulation": regulation_risk, "ip_trademark": ip_trademark_risk,
            "supplier_concentration": supplier_concentration_risk, "return": return_risk,
            "seasonality_cashflow": seasonality_cashflow_risk, "review_manipulation": review_manipulation_risk}
    weights = {"regulation": 0.25, "ip_trademark": 0.20, "supplier_concentration": 0.15,
               "return": 0.15, "seasonality_cashflow": 0.15, "review_manipulation": 0.10}
    score = sum(vals[k] * weights[k] for k in vals)
    return {"components": vals, "score": round(score, 1)}


# ---------------------------------------------------------------------------
# 2.5 PROOF SIGNAL (Aşama 2) — puan tablosu birebir
# ---------------------------------------------------------------------------
PROOF_POINTS = {
    "factory_video": 20, "sample_photo": 15, "measurement_video": 15,
    "packaging_proof": 10, "coa_lab_cert": 25, "real_use_content": 15,
}


def proof_signal(approved_assets: list[str], category_is_regulated: bool = False) -> dict:
    """
    approved_assets: onaylı proof_assets.type listesi.
    Regüle kategoride COA puan DEĞİL, veto kapısıdır (bkz compliance_veto) —
    bu fonksiyon yalnızca puanı hesaplar, veto ayrı kontrol edilir.
    """
    total = 0
    breakdown = {}
    for asset_type in approved_assets:
        if asset_type == "coa_lab_cert" and category_is_regulated:
            continue  # regüle kategoride puan değil, veto — burada sayılmaz
        pts = PROOF_POINTS.get(asset_type, 0)
        total += pts
        breakdown[asset_type] = pts
    return {"breakdown": breakdown, "score": min(100, total)}


# ---------------------------------------------------------------------------
# 2.7 KATEGORİ-SERTİFİKA VETO
# ---------------------------------------------------------------------------
def compliance_veto(category_key: str, is_blocking_categories: set[str],
                     required_certs: list[str], provided_certs: list[str],
                     advisor_approved: bool = False) -> dict:
    """
    IF kategori regüle AND zorunlu belge eksik THEN compliance_review_required=true.
    CEO override YOK — yalnızca advisor_approved=True kapıyı açar (loglanmalı).
    """
    is_regulated = category_key in is_blocking_categories
    missing = [c for c in required_certs if c not in provided_certs]
    blocked = is_regulated and bool(missing) and not advisor_approved
    return {
        "category": category_key, "is_regulated": is_regulated,
        "missing_certs": missing, "advisor_approved": advisor_approved,
        "compliance_review_required": blocked,
    }


# ---------------------------------------------------------------------------
# 2.8 BLUE OCEAN ROZETİ — talep tabanı eklenmiş, birebir
# ---------------------------------------------------------------------------
def blue_ocean(entropy_n: float, qg_n: float, rm: float, search_volume: float,
                avg_price: float, truth_score: float, sv_min: float = 15000,
                price_min: float = 25) -> bool:
    return (entropy_n >= 0.70 and qg_n >= 0.60 and rm <= 0.50 and
            search_volume >= sv_min and avg_price >= price_min and truth_score >= 60)


# ---------------------------------------------------------------------------
# OPPORTUNITY SCORE — ek çarpan yok, birebir
# ---------------------------------------------------------------------------
def opportunity_score(market: float, demand: float, truth: float, risk: float,
                       proof: float = None, stage: int = 1) -> float:
    if stage == 1:
        score = 0.30 * market + 0.30 * demand + 0.20 * truth + 0.20 * (100 - risk)
    else:
        proof = proof or 0
        score = (0.25 * market + 0.25 * demand + 0.15 * truth +
                 0.20 * (100 - risk) + 0.15 * proof)
    return round(score, 1)


# ---------------------------------------------------------------------------
# AŞAMA 1 KAPISI
# ---------------------------------------------------------------------------
def stage1_gate(opp_score: float, team_verdict: str, compliance_review_required: bool) -> dict:
    """
    Doküman kuralı: OpportunityScore >= 60 VE mevcut eşik kuralları (0 olumsuz).
    UYARI: Ekip daha önce eşik kuralını "4+ olumsuz = Elenmiş" olacak şekilde
    değiştirdi (bkz scoring.py ELIMINATE_AT). Doküman literal "0 olumsuz" diyor.
    Burada PRAGMATİK karar: ekip eşiğiyle çelişmemek için "mevcut eşik kuralı"nı
    team_verdict != 'Elenmiş' olarak yorumluyoruz (yani 0-3 olumsuz kabul).
    Bu bir TASARIM KARARI — ekiple netleştirilmeli, doküman "0 olumsuz" derken
    değişikliği bilmiyor olabilir.
    """
    score_ok = opp_score >= 60
    threshold_ok = team_verdict != "Elenmiş"
    veto_ok = not compliance_review_required
    passed = score_ok and threshold_ok and veto_ok
    reasons = []
    if not score_ok:
        reasons.append(f"OpportunityScore {opp_score} < 60")
    if not threshold_ok:
        reasons.append(f"Ekip ön değerlendirmesi: {team_verdict}")
    if not veto_ok:
        reasons.append("Compliance veto aktif — danışman onayı gerekli")
    return {"passed": passed, "reasons": reasons,
            "note": "Eşik yorumu: takım eşiği (4+ olumsuz=Elenmiş) ile uyumlu — doküman literal '0 olumsuz' ile netleştirilmeli"}
