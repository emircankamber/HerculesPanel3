"""
§4 Bayesian Öğrenme Döngüsü — p ~ Beta(α, β), eşdeğer örneklem 4.
scipy.stats.beta ile %10-%90 kredi aralığı hesaplanır.
"""
from scipy.stats import beta as beta_dist

EVENT_UPDATES = {
    "sample_quality_pass": (0.5, 0.0),
    "sample_quality_fail": (0.0, 1.0),
    "proof_ge_70": (0.5, 0.0),
    "day60_acos_met": (1.0, 0.0),
    "day60_acos_missed": (0.0, 1.0),
    "day90_margin_ge_15": (1.5, 0.0),
    "day90_margin_below_15": (0.0, 1.5),
    "return_rate_below_avg": (0.5, 0.0),
    "return_rate_1_5x_above": (0.0, 1.0),
    "organic_top10": (1.0, 0.0),
}


def initial_prior(opportunity_score: float) -> dict:
    """α₀ = 1 + 4·(Score/100), β₀ = 1 + 4·(1 − Score/100) — eşdeğer örneklem 4."""
    s = opportunity_score / 100
    alpha0 = 1 + 4 * s
    beta0 = 1 + 4 * (1 - s)
    return {"alpha": alpha0, "beta": beta0}


def apply_event(alpha: float, beta_val: float, event_type: str) -> dict:
    if event_type not in EVENT_UPDATES:
        raise ValueError(f"Bilinmeyen event_type: {event_type}. Geçerli: {list(EVENT_UPDATES)}")
    da, db = EVENT_UPDATES[event_type]
    new_alpha = alpha + da
    new_beta = beta_val + db
    return {"alpha": new_alpha, "beta": new_beta, "alpha_delta": da, "beta_delta": db}


def p_hat_with_interval(alpha: float, beta_val: float) -> dict:
    """p̂ = α/(α+β) + Beta %10-%90 kredi aralığı."""
    p_hat = alpha / (alpha + beta_val)
    lo = beta_dist.ppf(0.10, alpha, beta_val)
    hi = beta_dist.ppf(0.90, alpha, beta_val)
    return {"p_hat": round(p_hat, 4), "ci_10": round(float(lo), 4), "ci_90": round(float(hi), 4)}


def scale_gate(p_hat: float, threshold: float = 0.6) -> bool:
    """Aşama 3 kapısı: p̂ >= 0.6 → yeniden sipariş/ölçek."""
    return p_hat >= threshold
