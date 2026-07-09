"""
§3A Supplier Score — Hercules PL Makinesi v3.
Tedarik sorumlusu basit bir form doldurur, puan otomatik hesaplanır.
Kapı: <35 bloklu, 35-49 uyarılı, >=50 sorunsuz.
"""

MAX_POINTS = {
    "factory_verified": 15,   # Fabrika mı aracı mı (business license + video)
    "moq_fit": 10,            # MOQ ilk sipariş bütçesine uygun mu
    "us_export": 10,          # ABD'ye ihracat geçmişi
    "fba_knowledge": 10,      # FNSKU/karton spec/prep deneyimi
    "response_speed": 10,     # <12 saat tam puan, >48 saat 0
    "video_willingness": 10,  # İstenince video veriyor mu
    "cert_authenticity": 15,  # Belge doğruluğu (sahte = kara liste)
    "sample_quality": 10,     # Numune değerlendirme formu
    "price_stability": 10,    # İlk teklif vs PI farkı
}

BLOCK_THRESHOLD = 35
WARN_THRESHOLD = 50


def score_supplier(criteria: dict) -> dict:
    """
    criteria: her anahtar için 0..MAX_POINTS[anahtar] arası puan
    (örn. {"factory_verified": 15, "moq_fit": 10, ...}).
    Eksik anahtar 0 kabul edilir. Her değer kendi max'ına clip'lenir.
    """
    clipped = {}
    for key, max_p in MAX_POINTS.items():
        v = criteria.get(key, 0) or 0
        clipped[key] = max(0, min(max_p, v))

    total = sum(clipped.values())
    max_total = sum(MAX_POINTS.values())

    if total < BLOCK_THRESHOLD:
        status = "blocked"
    elif total < WARN_THRESHOLD:
        status = "warning"
    else:
        status = "ok"

    return {
        "scores": clipped,
        "total_score": total,
        "max_total": max_total,
        "status": status,
        "blocked": status == "blocked",
    }


def cert_authenticity_flag(declared_cert_number: str, verified: bool) -> dict:
    """Sahte belge tespit edilirse tedarikçi kara listeye alınmalı — bu bir uyarı üretir."""
    if declared_cert_number and not verified:
        return {"flag": "FRAUD_SUSPECTED", "action": "Tedarikçiyi kara listeye al, siparişi durdur"}
    return {"flag": "OK", "action": None}
