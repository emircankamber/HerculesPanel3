"""
§3 QIPO — Portfolio Optimizer.
Production solver: CP-SAT (exact) — doküman kuralı gereği karar ekranları
YALNIZCA bununla çalışır. Annealing/QUBO burada YOK (§3.3 sadece iç/deneysel
format olarak tanımlı, production'da kullanılmıyor — doküman zaten böyle diyor).
"""
from dataclasses import dataclass
from ortools.sat.python import cp_model


@dataclass
class Candidate:
    id: str
    keyword: str
    v: float          # OpportunityScore x beklenen aylık net kar (normalize, tam sayıya yuvarlanacak)
    cost: float        # MOQ x landed_unit_cost
    category: str
    supplier: str


@dataclass
class PairPenalty:
    id_a: str
    id_b: str
    penalty: float     # aynı tedarikçi/kategori +ceza; bundle/kargo sinerjisi -bonus (negatif verilebilir)


def solve_portfolio(candidates: list[Candidate], budget: float,
                     k_cat: int = 2, k_sup: int = 2,
                     pair_penalties: list[PairPenalty] = None,
                     scale: int = 100) -> dict:
    """
    max  Σ vᵢxᵢ − Σᵢ<ⱼ rᵢⱼyᵢⱼ
    s.t. Σ cᵢxᵢ ≤ B
         Σ_{i∈kat k} xᵢ ≤ K_cat
         Σ_{i∈ted s} xᵢ ≤ K_sup
         yᵢⱼ ≥ xᵢ+xⱼ−1;  yᵢⱼ ≤ xᵢ;  yᵢⱼ ≤ xⱼ

    CP-SAT tam sayı ister; float değerler `scale` ile ölçeklenip yuvarlanır.
    n <= 50 için < 1sn, kanıtlanmış optimum (doküman iddiası, n küçükken doğrulanabilir).
    """
    pair_penalties = pair_penalties or []
    model = cp_model.CpModel()

    x = {c.id: model.NewBoolVar(f"x_{c.id}") for c in candidates}

    # Bütçe kısıtı
    scaled_costs = {c.id: int(round(c.cost * scale)) for c in candidates}
    scaled_budget = int(round(budget * scale))
    model.Add(sum(scaled_costs[c.id] * x[c.id] for c in candidates) <= scaled_budget)

    # Kategori / tedarikçi kısıtları
    categories = {}
    suppliers = {}
    for c in candidates:
        categories.setdefault(c.category, []).append(c.id)
        suppliers.setdefault(c.supplier, []).append(c.id)
    for cat, ids in categories.items():
        model.Add(sum(x[i] for i in ids) <= k_cat)
    for sup, ids in suppliers.items():
        model.Add(sum(x[i] for i in ids) <= k_sup)

    # İkili ceza/bonus terimleri (yᵢⱼ lineerleştirme)
    y_vars = {}
    penalty_terms = []
    scaled_values = {c.id: int(round(c.v * scale)) for c in candidates}
    for p in pair_penalties:
        if p.id_a not in x or p.id_b not in x:
            continue
        y = model.NewBoolVar(f"y_{p.id_a}_{p.id_b}")
        model.Add(y >= x[p.id_a] + x[p.id_b] - 1)
        model.Add(y <= x[p.id_a])
        model.Add(y <= x[p.id_b])
        y_vars[(p.id_a, p.id_b)] = y
        penalty_terms.append(int(round(p.penalty * scale)) * y)

    objective = sum(scaled_values[c.id] * x[c.id] for c in candidates) - sum(penalty_terms)
    model.Maximize(objective)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"status": "infeasible", "selected": [], "objective_value": None}

    selected = [c.id for c in candidates if solver.Value(x[c.id]) == 1]
    total_cost = sum(c.cost for c in candidates if c.id in selected)

    return {
        "status": "optimal" if status == cp_model.OPTIMAL else "feasible",
        "selected": selected,
        "objective_value": solver.ObjectiveValue() / scale,
        "total_cost": round(total_cost, 2),
        "solver": "CP-SAT (exact)",
        "wall_time_seconds": round(solver.WallTime(), 4),
    }
