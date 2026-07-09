"""
SellerSprite Private Label Panel — Backend

Akış (bir keyword sorgusunda):
  1. keyword_miner           -> talep + rekabet + reklam ham verisi + relevancy>50 KW listesi
  2. product_node            -> kategori node_id_path (market_* tool'ları için gerekli)
  3. market_research_statistics -> pazar özeti (fiyat, margin, rating, yeni ürünler)
  4. market_brand_concentration -> marka payı dağılımı
  5. market_price_distribution  -> fiyat dağılımı
  6. market_listing_date_distribution -> launch time dağılımı
  7. market_product_demand_trend -> aylık trafik trendi + return rate
  8. competitor_lookup        -> top rakiplerin ASIN-bazlı satış/ciro/BSR (returnFields KULLANMADAN)

Test edilmiş gerçek tool davranışları için mcp_client.py'nin docstring'ine bak.
"""
import os
import sys

# Vercel'in Python runtime'ı bu dosyayı importlib ile dosya-yolu üzerinden
# yüklüyor ve api/ klasörünü otomatik olarak sys.path'e eklemiyor — bu yüzden
# aşağıdaki kardeş modül importları (mcp_client, database, scoring vb.)
# eklemeden ModuleNotFoundError verir. Yerelde (api/ içinden çalıştırınca)
# sorun çıkmaz, sadece Vercel'in çalıştırma şeklinde ortaya çıkar.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from mcp_client import call_tool
from scoring import calc_keyword_ad_metrics, calc_profit, pre_assessment, DEFAULT_THRESHOLDS
import signal_engine as se

# Bayesian (scipy) ve Portfolio (ortools) opsiyonel — Vercel deploy boyutunu
# küçük tutmak için requirements.txt'den çıkarıldı (henüz frontend'e bağlı
# değiller). Paketler kuruluysa (örn. Railway/yerel) normal çalışır; değilse
# ilgili endpoint'ler 501 döner, geri kalan her şey (analyze/signals/proof/
# compliance) etkilenmez.
try:
    import bayesian as bys
    BAYESIAN_AVAILABLE = True
except ImportError:
    BAYESIAN_AVAILABLE = False

try:
    from portfolio import Candidate, PairPenalty, solve_portfolio
    PORTFOLIO_AVAILABLE = True
except ImportError:
    PORTFOLIO_AVAILABLE = False
import database as db

app = FastAPI(title="SellerSprite PL Panel API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # prod'da ekibin domainiyle kısıtla
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await db.init_db()
    await db.seed_cert_requirements_if_empty()


# ---------------------------------------------------------------------------
# Yardımcı: kategori node bul (market_* tool'ları için zorunlu)
# ---------------------------------------------------------------------------
async def resolve_category_node(seed_keyword: str, marketplace: str) -> dict | None:
    result = await call_tool("product_node", {"keyword": seed_keyword, "marketplace": marketplace})
    nodes = result.get("data") or result.get("nodes") or []
    if isinstance(nodes, dict):
        nodes = nodes.get("list", [])
    if not nodes:
        return None
    # En yüksek ürün sayılı / en spesifik (en derin) node'u tercih et
    best = max(nodes, key=lambda n: n.get("productCount", 0))
    return best


# ---------------------------------------------------------------------------
# Ana analiz endpoint'i
# ---------------------------------------------------------------------------
class AnalyzeRequest(BaseModel):
    keyword: str
    marketplace: str = "US"
    top_relevancy: int = 50
    keyword_list_size: int = 20
    requested_by: str | None = None
    force_refresh: bool = False


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    # 1) Önbellek kontrolü — ekip aynı keyword'ü tekrar sorgularsa MCP'ye gitme
    if not req.force_refresh:
        cached = await db.get_cached(req.keyword, req.marketplace)
        if cached:
            return {**cached, "source": "cache"}

    try:
        # 2) Ana keyword verisi
        kw_data = await call_tool("keyword_miner", {
            "keyword": req.keyword,
            "marketplace": req.marketplace,
            "minRelevancy": req.top_relevancy,
            "size": req.keyword_list_size,
            "order": {"field": "searches", "desc": True},
        })

        # 3) Kategori node'u bul
        node = await resolve_category_node(req.keyword, req.marketplace)
        node_id_path = node.get("nodeIdPath") if node else None

        market_stats = {}
        brand_conc = {}
        price_dist = {}
        launch_dist = {}
        demand_trend = {}
        if node_id_path:
            market_stats, brand_conc, price_dist, launch_dist, demand_trend = await asyncio.gather(
                call_tool("market_research_statistics", {"marketplace": req.marketplace, "nodeIdPath": node_id_path, "topN": 10}),
                call_tool("market_brand_concentration", {"marketplace": req.marketplace, "nodeIdPath": node_id_path, "topN": 10}),
                call_tool("market_price_distribution", {"marketplace": req.marketplace, "nodeIdPath": node_id_path, "topN": 10}),
                call_tool("market_listing_date_distribution", {"marketplace": req.marketplace, "nodeIdPath": node_id_path, "topN": 10}),
                call_tool("market_product_demand_trend", {"marketplace": req.marketplace, "nodeIdPath": node_id_path, "topN": 10}),
            )

        # 4) Keyword listesindeki her satır için hesaplanan reklam metrikleri
        raw_items = kw_data.get("data", {}).get("items", []) if isinstance(kw_data.get("data"), dict) else kw_data.get("items", [])
        keyword_rows = []
        for item in raw_items:
            metrics = calc_keyword_ad_metrics(
                clicks=item.get("clicks", 0),
                purchases=item.get("purchases", 0),
                bid=item.get("bid"),
                avg_price=item.get("avgPrice"),
                impressions=item.get("impressions"),
                searches=item.get("searches"),
            )
            keyword_rows.append({**item, **metrics})

        # 5) Ana hedef keyword'ün metrikleri (ön değerlendirme için)
        main_row = next((r for r in keyword_rows if r.get("keyword", "").lower() == req.keyword.lower()), None)
        main_acos = main_row["acos"] if main_row else None

        # 6) Top brand share (brand_conc'tan)
        brand_items = brand_conc.get("data", {}).get("items", []) if isinstance(brand_conc.get("data"), dict) else brand_conc.get("items", [])
        top_brand_share = max((b.get("share", 0) for b in brand_items), default=None) if brand_items else None

        # 7) Ön değerlendirme (kar analizi girdisi olmadan ilk taslak; kullanıcı kar
        #    analizine değer girince /api/profit ile net_margin güncellenir)
        stats_data = market_stats.get("data", market_stats)
        assessment = pre_assessment(
            avg_price=stats_data.get("avgPrice"),
            gross_margin=None,  # gerçek gross margin alanı doğrulanmadı; kar analizinden gelir
            acos=main_acos,
            top_brand_share=top_brand_share,
            strong_new_brands=None,  # brand_conc + launch history'den türetilecek (ayrı hesap)
            net_margin=None,
        )

        payload = {
            "keyword": req.keyword,
            "marketplace": req.marketplace,
            "keyword_data_raw": kw_data,
            "keyword_rows": keyword_rows,
            "market_stats": stats_data,
            "brand_concentration": brand_items,
            "price_distribution": price_dist,
            "launch_distribution": launch_dist,
            "demand_trend": demand_trend,
            "top_competitors": [],  # bkz /api/competitors — ayrı çağrı (ASIN listesi gerektirir)
            "pre_assessment": assessment,
            # GEÇİCİ DEBUG ALANI — gerçek MCP yanıt şeklini görmek için.
            # Veri şekli netleşince bu alan kaldırılacak.
            "_debug": {
                "node_id_path": node_id_path,
                "product_node_raw": node,
                "market_stats_raw": market_stats,
                "brand_concentration_raw": brand_conc,
                "price_distribution_raw": price_dist,
                "launch_distribution_raw": launch_dist,
                "demand_trend_raw": demand_trend,
            },
        }

        await db.save_analysis(req.keyword, req.marketplace, payload, req.requested_by)
        return {**payload, "source": "live"}

    except KeyError as e:
        raise HTTPException(500, f"Ortam değişkeni eksik: {e}")
    except Exception as e:
        raise HTTPException(502, f"SellerSprite MCP hatası: {e}")


# ---------------------------------------------------------------------------
# Top rakipler — ASIN listesi verilince gerçek satış/ciro/BSR çeker
# ---------------------------------------------------------------------------
class CompetitorsRequest(BaseModel):
    asins: list[str]
    marketplace: str = "US"


@app.post("/api/competitors")
async def competitors(req: CompetitorsRequest):
    """
    NOT: competitor_lookup verdiğin ASIN listesini birebir DÖNDÜRMEYEBİLİR —
    kategori/başlık eşleşmesiyle en güçlü rakipleri getirir. Birebir ASIN
    verisi gerekiyorsa asin_detail tool'unu ayrı ayrı çağır.
    """
    result = await call_tool("competitor_lookup", {"asins": req.asins, "marketplace": req.marketplace})
    return result


# ---------------------------------------------------------------------------
# Kar analizi — kullanıcı panelde girdi değiştirdikçe çağrılır
# ---------------------------------------------------------------------------
class ProfitRequest(BaseModel):
    cogs: float
    sale_price: float
    fba_fee: float
    referral_fee: float
    acos: float
    return_rate: float
    overhead_rate: float = 0.01


@app.post("/api/profit")
async def profit(req: ProfitRequest):
    return calc_profit(
        cogs=req.cogs, sale_price=req.sale_price, fba_fee=req.fba_fee,
        referral_fee=req.referral_fee, acos=req.acos,
        return_rate=req.return_rate, overhead_rate=req.overhead_rate,
    )


# ---------------------------------------------------------------------------
# Pazar kararını kaydet (ekibin manuel Uygun/Sınırda/Elenmiş kararı)
# ---------------------------------------------------------------------------
class DecisionRequest(BaseModel):
    keyword: str
    marketplace: str = "US"
    decision: str  # "Uygun" | "Sınırda" | "Elenmiş"
    note: str = ""
    decided_by: str = ""


@app.post("/api/decision")
async def save_decision(req: DecisionRequest):
    await db.save_decision(req.keyword, req.marketplace, req.decision, req.note, req.decided_by)
    return {"ok": True}


@app.get("/api/recent")
async def recent(limit: int = Query(50, le=200)):
    return await db.list_recent(limit)


@app.get("/api/thresholds")
async def thresholds():
    return DEFAULT_THRESHOLDS


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# HERCULES SIGNAL ENGINE v2.1
# ---------------------------------------------------------------------------
class SignalsRequest(BaseModel):
    keyword: str
    marketplace: str = "US"
    stage: int = 1
    # Market
    brand_shares: list[float]
    asin_revenue_shares: list[float]
    top10_ratings_weighted: list[tuple[float, float]]
    top10_review_counts: list[int]
    new_product_revenue_share: float
    # Demand
    search_volume: float
    sv_trend_pct_3m: float
    sv_cv_12m: float = 0.15
    click_cvr: float
    acos: float
    avg_price: float
    # Truth
    reported_revenue: float
    units: float
    bsr_sales_consistent: bool = True
    snapshot_jump_detected: bool = False
    review_velocity_anomaly: bool = False
    # Risk (manuel/heuristik girdiler — otomatik regülasyon/IP tespiti yok)
    regulation_risk: float = 20
    ip_trademark_risk: float = 20
    supplier_concentration_risk: float = 30
    return_risk: float = 30
    seasonality_cashflow_risk: float = 20
    review_manipulation_risk: float = 15
    # Proof (Aşama 2+)
    proof_score: float | None = None
    # Compliance
    category_key: str | None = None
    provided_certs: list[str] = []
    advisor_approved: bool = False
    # Gate için ekip verdict'i (mevcut pre_assessment sonucundan)
    team_verdict: str = "Sınırda"


@app.post("/api/signals")
async def compute_signals(req: SignalsRequest):
    market = se.market_signal(req.brand_shares, req.asin_revenue_shares,
                               req.top10_ratings_weighted, req.top10_review_counts,
                               req.new_product_revenue_share)
    demand = se.demand_signal(req.search_volume, req.sv_trend_pct_3m, req.sv_cv_12m,
                               req.click_cvr, req.acos, req.avg_price)
    truth = se.truth_signal(req.reported_revenue, req.avg_price, req.units,
                             req.bsr_sales_consistent, req.snapshot_jump_detected,
                             req.review_velocity_anomaly)
    risk = se.risk_signal(req.regulation_risk, req.ip_trademark_risk,
                           req.supplier_concentration_risk, req.return_risk,
                           req.seasonality_cashflow_risk, req.review_manipulation_risk)

    blue_ocean = se.blue_ocean(market["components"]["entropy"], market["components"]["quality_gap"],
                                market["components"]["review_moat"], req.search_volume,
                                req.avg_price, truth["score"])

    opp = se.opportunity_score(market["score"], demand["score"], truth["score"], risk["score"],
                                proof=req.proof_score, stage=req.stage)

    compliance = None
    if req.category_key:
        certs = await db.get_cert_requirements(req.category_key)
        required = [c["cert_type"] for c in certs]
        compliance = se.compliance_veto(req.category_key, {c["category_key"] for c in certs} | {req.category_key},
                                         required, req.provided_certs, req.advisor_approved)

    gate = se.stage1_gate(opp, req.team_verdict,
                           compliance["compliance_review_required"] if compliance else False)

    result = {
        "market": market, "demand": demand, "truth": truth, "risk": risk,
        "blue_ocean": blue_ocean, "opportunity_score": opp,
        "compliance": compliance, "stage1_gate": gate,
    }

    await db.save_product_signals(req.keyword, req.marketplace,
                                   "market" if req.stage == 1 else "sourcing",
                                   {"market_score": market["score"], "demand_score": demand["score"],
                                    "truth_score": truth["score"], "risk_score": risk["score"],
                                    "proof_score": req.proof_score, "opportunity_score": opp,
                                    "is_blue_ocean": blue_ocean,
                                    "compliance_review_required": compliance["compliance_review_required"] if compliance else False})

    return result


# ---------------------------------------------------------------------------
# PROOF ASSETS (Aşama 2 — manuel kanıt yükleme/onay)
# ---------------------------------------------------------------------------
class ProofAssetRequest(BaseModel):
    keyword: str
    type: str    # bkz signal_engine.PROOF_POINTS anahtarları
    file_url: str | None = None
    competitor_id: str | None = None
    supplier_ref: str | None = None
    note: str | None = None
    category_is_regulated: bool = False


@app.post("/api/proof-assets")
async def add_proof_asset(req: ProofAssetRequest):
    if req.type not in se.PROOF_POINTS:
        raise HTTPException(400, f"Geçersiz proof type. Geçerli: {list(se.PROOF_POINTS)}")
    points = 0 if (req.type == "coa_lab_cert" and req.category_is_regulated) else se.PROOF_POINTS[req.type]
    asset_id = await db.add_proof_asset(req.keyword, req.type, points, req.file_url,
                                         req.competitor_id, req.supplier_ref, req.note)
    return {"id": asset_id, "points": points,
            "note": "Regüle kategoride COA puan değil veto kapısıdır — bkz /api/compliance-check" if points == 0 and req.type == "coa_lab_cert" else None}


@app.get("/api/proof-assets/{keyword}")
async def get_proof_assets(keyword: str):
    assets = await db.list_proof_assets(keyword)
    approved_types = [a["type"] for a in assets if a["status"] == "approved"]
    score = se.proof_signal(approved_types)
    return {"assets": assets, "proof_score": score}


class ProofApproveRequest(BaseModel):
    asset_id: int
    approved_by: str


@app.post("/api/proof-assets/approve")
async def approve_proof(req: ProofApproveRequest):
    await db.approve_proof_asset(req.asset_id, req.approved_by)
    return {"ok": True}


# ---------------------------------------------------------------------------
# COMPLIANCE / SERTİFİKA VETO
# ---------------------------------------------------------------------------
class ComplianceCheckRequest(BaseModel):
    category_key: str
    provided_certs: list[str] = []
    advisor_approved: bool = False


@app.post("/api/compliance-check")
async def compliance_check(req: ComplianceCheckRequest):
    certs = await db.get_cert_requirements(req.category_key)
    required = [c["cert_type"] for c in certs]
    return se.compliance_veto(req.category_key, {req.category_key} if certs else set(),
                               required, req.provided_certs, req.advisor_approved)


# ---------------------------------------------------------------------------
# QIPO — PORTFOLIO OPTIMIZER (CP-SAT, exact)
# ---------------------------------------------------------------------------
class CandidateIn(BaseModel):
    id: str
    keyword: str
    v: float
    cost: float
    category: str
    supplier: str


class PairPenaltyIn(BaseModel):
    id_a: str
    id_b: str
    penalty: float


class PortfolioSolveRequest(BaseModel):
    candidates: list[CandidateIn]
    budget: float
    k_cat: int = 2
    k_sup: int = 2
    pair_penalties: list[PairPenaltyIn] = []
    run_by: str | None = None
    generate_explanation: bool = False   # True ise ANTHROPIC_API_KEY ile arka planda gerekçe üretir


@app.post("/api/portfolio/solve")
async def portfolio_solve(req: PortfolioSolveRequest, background_tasks: BackgroundTasks):
    if not PORTFOLIO_AVAILABLE:
        raise HTTPException(501, "Portfolio özelliği bu deploy'da kapalı (ortools kurulu değil). "
                                  "requirements.txt'e ortools ekleyip yeniden deploy et.")
    candidates = [Candidate(**c.dict()) for c in req.candidates]
    penalties = [PairPenalty(**p.dict()) for p in req.pair_penalties]

    if not candidates:
        raise HTTPException(400, "En az bir aday gerekli")

    result = solve_portfolio(candidates, req.budget, req.k_cat, req.k_sup, penalties)
    run_id = await db.save_portfolio_run(req.budget, req.k_cat, req.k_sup, result, req.run_by)

    if req.generate_explanation and os.environ.get("ANTHROPIC_API_KEY"):
        selected_keywords = [c.keyword for c in candidates if c.id in result["selected"]]
        background_tasks.add_task(generate_portfolio_explanation, run_id, selected_keywords, result)
    else:
        await db.update_portfolio_explanation(
            run_id, "Gerekçe üretimi kapalı (ANTHROPIC_API_KEY tanımlı değil ya da istenmedi).", "failed")

    return {**result, "run_id": run_id}


async def generate_portfolio_explanation(run_id: int, selected_keywords: list[str], result: dict):
    """
    Doküman §3.4: sonuç senkron döner, gerekçe arka planda Anthropic API ile üretilir.
    Yalnızca ANTHROPIC_API_KEY tanımlıysa çalışır (kullanıcının kendi API key'i).
    """
    try:
        import httpx
        api_key = os.environ["ANTHROPIC_API_KEY"]
        prompt = (
            f"Şu keyword'ler bir private label portföyü için CP-SAT ile seçildi: "
            f"{', '.join(selected_keywords)}. Toplam maliyet: ${result['total_cost']}, "
            f"objektif değer: {result['objective_value']}. Bu seçimi 2-3 cümlede, "
            f"neden bu kombinasyonun (bütçe/kategori/tedarikçi dengesi açısından) "
            f"mantıklı olduğunu Türkçe açıkla."
        )
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-sonnet-4-6", "max_tokens": 300,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            data = resp.json()
            text = "".join(b.get("text", "") for b in data.get("content", []))
            await db.update_portfolio_explanation(run_id, text or "Gerekçe boş döndü.", "ready")
    except Exception as e:
        await db.update_portfolio_explanation(run_id, f"Gerekçe üretilemedi: {e}", "failed")


@app.get("/api/portfolio/{run_id}")
async def get_portfolio_run(run_id: int):
    run = await db.get_portfolio_run(run_id)
    if not run:
        raise HTTPException(404, "Portfolio run bulunamadı")
    return run


# ---------------------------------------------------------------------------
# BAYESIAN ÖĞRENME DÖNGÜSÜ
# ---------------------------------------------------------------------------
class LearningEventRequest(BaseModel):
    keyword: str
    event_type: str   # bkz bayesian.EVENT_UPDATES anahtarları
    opportunity_score_if_first: float | None = None  # ilk olay ise prior için gerekli
    source: str | None = None
    recorded_by: str | None = None


@app.post("/api/learning-event")
async def learning_event(req: LearningEventRequest):
    if not BAYESIAN_AVAILABLE:
        raise HTTPException(501, "Learning özelliği bu deploy'da kapalı (scipy kurulu değil). "
                                  "requirements.txt'e scipy ekleyip yeniden deploy et.")
    state = await db.get_latest_learning_state(req.keyword)
    if state:
        alpha, beta_val = state["alpha_after"], state["beta_after"]
    else:
        if req.opportunity_score_if_first is None:
            raise HTTPException(400, "İlk olay için opportunity_score_if_first gerekli (prior hesaplamak için)")
        prior = bys.initial_prior(req.opportunity_score_if_first)
        alpha, beta_val = prior["alpha"], prior["beta"]

    update = bys.apply_event(alpha, beta_val, req.event_type)
    p_result = bys.p_hat_with_interval(update["alpha"], update["beta"])

    await db.record_learning_event(
        req.keyword, req.event_type, update["alpha_delta"], update["beta_delta"],
        update["alpha"], update["beta"], p_result["p_hat"], req.source, req.recorded_by)

    return {**update, **p_result, "scale_gate_passed": bys.scale_gate(p_result["p_hat"])}


@app.get("/api/learning/{keyword}")
async def get_learning_state(keyword: str):
    if not BAYESIAN_AVAILABLE:
        raise HTTPException(501, "Learning özelliği bu deploy'da kapalı (scipy kurulu değil).")
    state = await db.get_latest_learning_state(keyword)
    if not state:
        return {"exists": False}
    p_result = bys.p_hat_with_interval(state["alpha_after"], state["beta_after"])
    return {"exists": True, "alpha": state["alpha_after"], "beta": state["beta_after"],
            **p_result, "scale_gate_passed": bys.scale_gate(p_result["p_hat"])}
