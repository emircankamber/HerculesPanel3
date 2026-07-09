"""
§4 Launch Control — Hercules PL Makinesi v3.
Gün 14/30/60/90 ve sürekli kontrol noktalarında Scale/Fix/Stop önerisi üretir.
Hiçbir otomatik durdurma yoktur — Stop her zaman CEO'ya öneri kartı olarak düşer.
"""

# Her checkpoint için (metrik_adı, scale_koşulu, fix_aralığı_açıklaması, stop_koşulu)
# Koşullar fonksiyon olarak tanımlı; None = bu checkpoint'te ölçülmüyor.

def _day14(ctr: float, has_impressions: bool) -> dict:
    if not has_impressions:
        return {"verdict": "stop_proposed", "reason": "Impression yok → listing/indeksleme sorunu, önce Fix denenmeli"}
    if ctr is not None and ctr >= 0.004:
        return {"verdict": "scale", "reason": f"CTR %{ctr*100:.2f} ≥ %0.4 ve tıklama maliyeti sürdürülebilir"}
    if ctr is not None and ctr < 0.003:
        return {"verdict": "fix", "reason": f"CTR %{ctr*100:.2f} < %0.3 → görsel/başlık testi gerekli"}
    return {"verdict": "fix", "reason": "CTR sınır bölgesinde, izlemeye devam"}


def _day30(cvr: float) -> dict:
    if cvr is None:
        return {"verdict": "fix", "reason": "CVR verisi eksik"}
    if cvr >= 0.10:
        return {"verdict": "scale", "reason": f"CVR %{cvr*100:.1f} ≥ %10"}
    if cvr < 0.03:
        return {"verdict": "stop_proposed", "reason": f"CVR %{cvr*100:.1f} < %3 ve iyileşme yok"}
    return {"verdict": "fix", "reason": f"CVR %{cvr*100:.1f} (%5-10 aralığı) → fiyat/görsel/A+ iyileştir"}


def _day60(acos: float, trend_flat_or_up: bool = False) -> dict:
    if acos is None:
        return {"verdict": "fix", "reason": "ACOS verisi eksik"}
    if acos <= 0.40:
        return {"verdict": "scale", "reason": f"ACOS %{acos*100:.1f} ≤ %40 ve düşüyor"}
    if acos > 0.75 and trend_flat_or_up:
        return {"verdict": "stop_proposed", "reason": f"ACOS %{acos*100:.1f} > %75 ve trend düz/yukarı"}
    return {"verdict": "fix", "reason": f"ACOS %{acos*100:.1f} (%40-75 aralığı) → kampanya yapısı revize"}


def _day90(net_margin: float) -> dict:
    if net_margin is None:
        return {"verdict": "fix", "reason": "Net marj verisi eksik"}
    if net_margin >= 0.15:
        return {"verdict": "scale", "reason": f"Net marj %{net_margin*100:.1f} ≥ %15 → yeniden sipariş + ölçek"}
    if net_margin < 0.05:
        return {"verdict": "stop_proposed", "reason": f"Net marj %{net_margin*100:.1f} < %5 veya negatif"}
    return {"verdict": "fix", "reason": f"Net marj %{net_margin*100:.1f} (%5-15 aralığı) → maliyet/fiyat aksiyonu"}


def _ongoing(review_avg: float, return_rate: float, category_avg_return_rate: float = 0.03) -> dict:
    reasons = []
    verdict = "scale"
    if review_avg is not None:
        if review_avg < 3.8:
            verdict = "stop_proposed"
            reasons.append(f"Review ort. {review_avg:.1f} < 3.8 → kök neden analizi, gerekirse Stop")
        elif review_avg < 4.2:
            verdict = "fix" if verdict == "scale" else verdict
            reasons.append(f"Review ort. {review_avg:.1f} (3.8-4.2) → ürün/beklenti düzeltmesi")
        else:
            reasons.append(f"Review ort. {review_avg:.1f} ≥ 4.2, sağlıklı")
    if return_rate is not None and category_avg_return_rate:
        ratio = return_rate / category_avg_return_rate
        if ratio > 1.5:
            verdict = "stop_proposed"
            reasons.append(f"İade oranı kategori ortalamasının {ratio:.1f}× üstü")
        elif ratio > 1.0:
            verdict = "fix" if verdict == "scale" else verdict
            reasons.append(f"İade oranı kategori ortalamasının {ratio:.1f}× (1-1.5× aralığı) → sebep analizi")
        else:
            reasons.append("İade oranı kategori ortalaması altında")
    return {"verdict": verdict, "reason": "; ".join(reasons) if reasons else "Yeterli veri yok"}


def evaluate_checkpoint(checkpoint_day: str, metrics: dict) -> dict:
    """
    checkpoint_day: "14" | "30" | "60" | "90" | "ongoing"
    metrics: ctr, cvr, acos, net_margin, review_avg, return_rate, has_impressions,
             trend_flat_or_up, category_avg_return_rate (opsiyonel alanlar)
    """
    if checkpoint_day == "14":
        result = _day14(metrics.get("ctr"), metrics.get("has_impressions", True))
    elif checkpoint_day == "30":
        result = _day30(metrics.get("cvr"))
    elif checkpoint_day == "60":
        result = _day60(metrics.get("acos"), metrics.get("trend_flat_or_up", False))
    elif checkpoint_day == "90":
        result = _day90(metrics.get("net_margin"))
    elif checkpoint_day == "ongoing":
        result = _ongoing(metrics.get("review_avg"), metrics.get("return_rate"),
                           metrics.get("category_avg_return_rate", 0.03))
    else:
        raise ValueError(f"Bilinmeyen checkpoint_day: {checkpoint_day}. Geçerli: 14, 30, 60, 90, ongoing")

    # Stop asla otomatik durdurma değil, her zaman CEO'ya öneri kartı
    result["requires_ceo_review"] = result["verdict"] == "stop_proposed"
    result["checkpoint_day"] = checkpoint_day
    return result


def suggested_action_owner(verdict: str, checkpoint_day: str) -> str | None:
    """Fix sinyalleri hangi role atanır."""
    if verdict != "fix":
        return None
    mapping = {
        "14": "içerik (görsel/başlık testi)",
        "30": "içerik + PPC (fiyat/görsel/A+)",
        "60": "PPC (kampanya yapısı revize)",
        "90": "CEO (maliyet/fiyat aksiyonu)",
        "ongoing": "ürün/içerik (kök neden analizi)",
    }
    return mapping.get(checkpoint_day)
