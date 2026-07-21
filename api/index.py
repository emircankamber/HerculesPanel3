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
import time

# Vercel'in Python runtime'ı bu dosyayı importlib ile dosya-yolu üzerinden
# yüklüyor ve api/ klasörünü otomatik olarak sys.path'e eklemiyor — bu yüzden
# aşağıdaki kardeş modül importları (mcp_client, database, scoring vb.)
# eklemeden ModuleNotFoundError verir. Yerelde (api/ içinden çalıştırınca)
# sorun çıkmaz, sadece Vercel'in çalıştırma şeklinde ortaya çıkar.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import Response
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
import excel_export
import supplier_scoring as sup
import launch_control as lc

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
    await db.init_db_v3()
    await db.seed_cert_requirements_if_empty()


# ---------------------------------------------------------------------------
# Yardımcı: SellerSprite yanıtlarından liste çıkar (gerçek veriyle doğrulandı)
# ---------------------------------------------------------------------------
def _extract_list(resp: dict) -> list:
    """
    KRİTİK BULGU (gerçek MCP çağrılarıyla doğrulandı): market_brand_concentration,
    market_price_distribution, market_listing_date_distribution, product_node
    gibi tool'ların "data" alanı DOĞRUDAN BİR LİSTE — {"data": {"items": [...]}}
    değil. Önceki kod bunu varsaymadığı için brand/price/launch grafikleri hep
    boş geliyordu. Bu fonksiyon hem "data doğrudan liste" hem "data içinde
    items/list anahtarlı dict" hem de üst seviye "items" durumlarını kapsar.
    """
    d = resp.get("data")
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        items = d.get("items")
        if isinstance(items, list):
            return items
        lst = d.get("list")
        if isinstance(lst, list):
            return lst
    top_items = resp.get("items")
    return top_items if isinstance(top_items, list) else []


# ---------------------------------------------------------------------------
# Yardımcı: kategori node bul (market_* tool'ları için zorunlu)
# ---------------------------------------------------------------------------
async def resolve_category_from_competitors(seed_keyword: str, marketplace: str) -> tuple[dict | None, dict, list]:
    """
    BİRİNCİL YÖNTEM (gerçek veriyle doğrulandı — tahmin değil, gerçek ürün verisi):
    keyword'den kategori TAHMİN ETMEK yerine, o keyword için gerçekten satan
    üst rakip ürünlerin KENDİ kategorisini kullanıyoruz.

    KRİTİK DÜZELTME (gerçek veriyle bulundu — "samsung water filter for
    refrigerators" örneği): matchType=3 (tam başlık eşleşme) UZUN/spesifik
    keyword'lerde SIFIR sonuç dönebiliyor çünkü hiçbir gerçek ürün başlığı o
    tam kelime dizisini birebir içermiyor (test: bu keyword'de total=0).
    Bu durumda önceden direkt tahmin yöntemine düşülüyordu — YANLIŞTI, çünkü
    matchType=1 (kelime grubu eşleşme) ile aynı keyword'de 5 GERÇEK sonuç
    geldi, hepsi doğru kategoride (Appliances:...:Water Filters). Bu yüzden
    artık İKİ deneme yapılıyor: önce matchType=3 (en kesin), boş dönerse
    matchType=1 (hâlâ gerçek ürün verisi, biraz daha esnek). Yalnızca İKİSİ
    DE boş dönerse (çok nadir/niş keyword) tahmin yöntemine düşülür.

    Test kanıtı (kısa keyword): "samsung water filter" için matchType=3 ile
    3 farklı gerçek rakip (Waterspecialist, Waterdrop, ICEPURE) ÜÇÜ DE aynı
    doğru kategoriyi döndürdü.

    NOT: size=10 yapıldı çünkü bu ÇAĞRININ dönen item listesi aynı zamanda
    "Top Rakipler" panel bölümü için de kullanılıyor (analyze() içinde) —
    ayrı bir çağrı yapıp MCP kotasını artırmamak için tek çağrıdan hem
    kategori hem rakip listesi elde ediliyor.
    """
    from collections import Counter

    async def _try(match_type: int):
        result = await call_tool("competitor_lookup", {
            "keyword": seed_keyword, "marketplace": marketplace,
            "matchType": match_type, "size": 10,
            "order": {"field": "total_units", "desc": True},
        })
        items = result.get("data", {}).get("items", []) if isinstance(result.get("data"), dict) else []
        return items, result

    # 1) Önce en kesin: tam eşleşme
    items, result = await _try(3)
    if not items:
        # 2) Boşsa: kelime grubu eşleşme (hâlâ gerçek ürün verisi, tahmin değil)
        items, result = await _try(1)

    paths = [it.get("nodeIdPath") for it in items if it.get("nodeIdPath")]
    if not paths:
        return None, result, items
    most_common_path, _ = Counter(paths).most_common(1)[0]
    matching_item = next(it for it in items if it.get("nodeIdPath") == most_common_path)
    return {"nodeIdPath": most_common_path, "nodeLabelPath": matching_item.get("nodeLabelPath")}, result, items


async def resolve_category_node(seed_keyword: str, marketplace: str, preferred_departments: list[str] = None) -> tuple[dict | None, dict, list]:
    """
    YEDEK YÖNTEM — yalnızca resolve_category_from_competitors hiçbir gerçek
    rakip ürün bulamazsa (nadir/çok yeni/niş keyword'ler) devreye girer.
    Döndürür: (seçilen node ya da None, HAM tool yanıtı - debug için, top-3 aday).

    KRİTİK DÜZELTME (gerçek veriyle test edilerek bulundu): SellerSprite'ın
    gerçek alan adı "productCount" DEĞİL, "products". Ayrıca "en çok ürünü
    olan kategoriyi seç" yanlış bir sezgiydi — "water filter" aramasında en
    çok ürünlü kategori alakasız "Water Sports" çıkıyor. Bunun yerine
    keyword'deki kelimelerin kategori adıyla örtüşme sayısına göre en
    alakalı node'u seçiyoruz, eşitlikte ürün sayısı yüksek olanı tercih.

    İKİNCİ DÜZELTME (gerçek veriyle bulundu — "mini magnetic tiles" örneği):
    Salt metin eşleştirme yetersiz kalabiliyor çünkü Amazon'un kategori adı
    her zaman aranan kelimeyi içermeyebilir (örn. "magnetic tiles" oyuncakları
    Amazon'da "Magnetic Building" diye geçiyor, "tile" kelimesi hiç yok).
    Bu yüzden artık ÖNCE `preferred_departments` (keyword_miner'ın exact-match
    çağrısından gelen GERÇEK Amazon department sınıflandırması, örn.
    "Toys & Games") ile eşleşen adaylara filtreliyoruz, SONRA o alt kümede
    kelime-örtüşme + ürün sayısıyla en iyisini seçiyoruz. Bu, metin
    eşleştirmeden çok daha güvenilir çünkü Amazon'un kendi gerçek arama
    sonucu sınıflandırmasına dayanıyor.

    DÜRÜSTLÜK NOTU: preferred_departments'ın da (samsung water filter örneği
    ile görüldüğü gibi) her zaman doğru üst segmenti ("Appliances") içermeme
    riski var — bu yüzden BİRİNCİL yöntem artık gerçek rakip ürün verisi
    (resolve_category_from_competitors). Bu fonksiyon yalnızca o hiç sonuç
    bulamazsa çalışır. Top-3 aday panelde gösterilir, `category_override_node_id`
    ile her zaman manuel geçersiz kılınabilir.
    """
    result = await call_tool("product_node", {"keyword": seed_keyword, "marketplace": marketplace})
    nodes = result.get("data") or result.get("nodes") or []
    if isinstance(nodes, dict):
        nodes = nodes.get("list", [])
    if not nodes:
        return None, result, []

    kw_words = [w.rstrip("s") for w in seed_keyword.lower().split() if len(w) > 2]

    def relevance(n):
        label = (n.get("nodeLabelPath") or "").lower()
        return sum(1 for w in kw_words if w in label)

    # Department filtresi: Amazon'un GERÇEK sınıflandırmasına göre öncelik ver
    candidate_pool = nodes
    if preferred_departments:
        wanted = {d.strip().lower() for d in preferred_departments if d}
        dept_matched = [n for n in nodes if (n.get("nodeLabelPath") or "").split(":")[0].strip().lower() in wanted]
        if dept_matched:
            candidate_pool = dept_matched

    scored = sorted(candidate_pool, key=lambda n: (relevance(n), n.get("products", 0)), reverse=True)
    best = scored[0]
    top3 = [{"nodeIdPath": n.get("nodeIdPath"), "nodeLabelPath": n.get("nodeLabelPath"),
             "products": n.get("products"), "relevance": relevance(n)} for n in scored[:3]]
    return best, result, top3


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
    category_override_node_id: str | None = None  # belirsiz kategori seçimini manuel düzeltmek için


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    # 1) Önbellek kontrolü — ekip aynı keyword'ü tekrar sorgularsa MCP'ye gitme
    if not req.force_refresh:
        cached = await db.get_cached(req.keyword, req.marketplace)
        if cached:
            return {**cached, "source": "cache"}

    try:
        # 2) Ana keyword verisi — İKİ AYRI ÇAĞRI:
        #    (a) keywordList: TAM EŞLEŞME — sadece verdiğin keyword'ün kendi verisi
        #        (SellerSprite şema notu: "精准匹配,只会返回传入的关键词数据" = exact match,
        #        yalnızca verilen keyword'ün verisini döner, ilişkili kelime YOK).
        #        Ön değerlendirme/PPC bloğu için "phrase/broad değil, exact istiyoruz" gereksinimi bu.
        #    (b) keyword: GENİŞ EŞLEŞME — "Relevant Keywords" genişletme tablosu için
        #        (bilerek geniş: ilişkili/benzer kelimeleri de görmek istiyoruz).
        exact_kw_data = await call_tool("keyword_miner", {
            "keywordList": [req.keyword],
            "marketplace": req.marketplace,
        })
        kw_data = await call_tool("keyword_miner", {
            "keyword": req.keyword,
            "marketplace": req.marketplace,
            "minRelevancy": req.top_relevancy,
            "size": req.keyword_list_size,
            "order": {"field": "searches", "desc": True},
        })

        # 3) Kategori node'u bul (ya da manuel override kullan)
        #    BİRİNCİL YÖNTEM: gerçek rakip ürünlerin kendi kategorisi (tahmin değil).
        #    Yalnızca bu hiç sonuç bulamazsa department-filtreli tahmin yöntemine düşülür.
        #    Bu ÇAĞRI aynı zamanda "Top Rakipler" panel bölümünü de otomatik doldurur
        #    (ayrı bir MCP çağrısı yapmadan — kota tasarrufu).
        category_candidates = []
        product_node_raw = None
        preferred_departments = []
        node_from_competitors, competitor_category_raw, competitor_items = await resolve_category_from_competitors(req.keyword, req.marketplace)

        if req.category_override_node_id:
            node_id_path = req.category_override_node_id
            category_used_label = f"(manuel override: {node_id_path})"
        elif node_from_competitors:
            node_id_path = node_from_competitors["nodeIdPath"]
            category_used_label = f"{node_from_competitors['nodeLabelPath']} (gerçek rakip ürün verisinden)"
        else:
            # Yedek: gerçek rakip bulunamadı, department-filtreli tahmine düş
            exact_items_for_dept = exact_kw_data.get("data", {}).get("items", []) if isinstance(exact_kw_data.get("data"), dict) else []
            if exact_items_for_dept:
                preferred_departments = [d.get("label") for d in exact_items_for_dept[0].get("departments", []) if d.get("label")]
            node, product_node_raw, category_candidates = await resolve_category_node(
                req.keyword, req.marketplace, preferred_departments=preferred_departments)
            node_id_path = node.get("nodeIdPath") if node else None
            category_used_label = (node.get("nodeLabelPath") + " (tahmin — yedek yöntem)") if node else None

        # Top Rakipler tablosu için sadeleştirilmiş alanlar (panelde gösterilecek)
        top_competitors = [{
            "asin": it.get("asin"), "brand": it.get("brand"), "title": it.get("title"),
            "price": it.get("price") or it.get("averagePrice"),
            "units": it.get("units") or it.get("amzUnit"),
            "revenue": it.get("revenue") or it.get("amzSales"),
            "bsr": it.get("bsr"), "rating": it.get("rating"), "ratings": it.get("ratings"),
            "fulfillment": it.get("fulfillment"), "availableDate": it.get("availableDate"),
        } for it in (competitor_items or [])]

        # GÜÇLÜ YENİ MARKA (1 yıl) — gerçek veriden hesaplanan proxy:
        # Otomatik çekilen top 10 rakip (zaten en yüksek satış hacmine göre
        # sıralı gerçek ürünler) arasında, listeleme tarihi (availableDate,
        # gerçek Amazon verisi) son 12 ay içinde olan DİSTİNCT marka sayısı.
        # DÜRÜSTLÜK NOTU: Bu tüm pazarı değil, yalnızca top 10 rakibi tarar —
        # yani küçük örneklemli bir proxy'dir, pazarın TAMAMINDA kaç yeni
        # markanın güçlendiğinin kesin sayımı değildir. Yine de tahmin değil,
        # gerçek ASIN-bazlı listeleme tarihi verisine dayanır.
        one_year_ms = 365 * 24 * 3600 * 1000
        now_ms = time.time() * 1000
        recent_brands = {
            it.get("brand") for it in (competitor_items or [])
            if it.get("brand") and it.get("availableDate") and (now_ms - it["availableDate"]) <= one_year_ms
        }
        strong_new_brands_count = len(recent_brands)

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

        # 4) Keyword listesindeki her satır için hesaplanan reklam metrikleri (GENİŞ liste — tablo için)
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

        # 5) Ana hedef keyword'ün metrikleri — ARTIK exact_kw_data'DAN (tam eşleşme, phrase/broad değil)
        exact_items = exact_kw_data.get("data", {}).get("items", []) if isinstance(exact_kw_data.get("data"), dict) else []
        main_row = exact_items[0] if exact_items else None
        if main_row:
            main_row = {**main_row, **calc_keyword_ad_metrics(
                clicks=main_row.get("clicks", 0), purchases=main_row.get("purchases", 0),
                bid=main_row.get("bid"), avg_price=main_row.get("avgPrice"),
                impressions=main_row.get("impressions"), searches=main_row.get("searches"),
            )}
        main_acos = main_row["acos"] if main_row else None

        # Tabloda ana keyword'ün satırını da EXACT veriyle değiştir (broad değil) —
        # kullanıcı tabloda ve ön değerlendirmede tutarlı, tam eşleşmiş veri görsün.
        if main_row:
            replaced = False
            for i, r in enumerate(keyword_rows):
                if r.get("keyword", "").lower() == req.keyword.lower():
                    keyword_rows[i] = main_row
                    replaced = True
                    break
            if not replaced:
                keyword_rows.insert(0, main_row)

        # 6) Top brand share (brand_conc'tan) — GERÇEK ALAN ADI: totalRevenueRatio
        #    (brand_conc "data" doğrudan liste, "share"/"percentage" değil)
        brand_items = _extract_list(brand_conc)
        top_brand_share = max((b.get("totalRevenueRatio", 0) for b in brand_items), default=None) if brand_items else None

        # 7) Ön değerlendirme (kar analizi girdisi olmadan ilk taslak; kullanıcı kar
        #    analizine değer girince /api/profit ile net_margin güncellenir)
        stats_data = market_stats.get("data", market_stats)

        # GROSS MARGIN — gerçek MCP çağrısıyla doğrulandı: market_research_statistics'in
        # "avgProfit" alanı, o KATEGORİDEKİ ürünlerin ortalama gross margin'i (örn. 68.19
        # şeklinde — zaten yüzde olarak, 0-1 oranı DEĞİL). Bu, senin spesifik ürününün marjı
        # değil, PAZAR ORTALAMASI — ön değerlendirmenin "bu pazar tipik olarak %65+ marj
        # destekliyor mu" sorusu için doğru kaynak zaten bu. Ürüne özgü gerçek marj için
        # kar analizi (COGS'a dayalı) kullanılmaya devam eder — o ayrı bir şey.
        raw_gross_margin = stats_data.get("avgProfit")
        gross_margin = (raw_gross_margin / 100 if raw_gross_margin > 1 else raw_gross_margin) if raw_gross_margin is not None else None

        assessment = pre_assessment(
            avg_price=stats_data.get("avgPrice"),
            gross_margin=gross_margin,
            acos=main_acos,
            top_brand_share=top_brand_share,
            strong_new_brands=strong_new_brands_count,  # top 10 rakip availableDate proxy'si (bkz. yukarıdaki not)
            net_margin=None,
        )

        payload = {
            "keyword": req.keyword,
            "marketplace": req.marketplace,
            "category_used": category_used_label,
            "category_candidates": category_candidates,
            "keyword_data_raw": kw_data,
            "keyword_rows": keyword_rows,
            "market_stats": stats_data,
            "brand_concentration": brand_items,
            "price_distribution": _extract_list(price_dist),
            "launch_distribution": _extract_list(launch_dist),
            "demand_trend": demand_trend,
            # GERÇEK PAZAR İADE ORANI — market_product_demand_trend'in "returnRatio"
            # alanından (gerçek MCP çağrısıyla doğrulandı: örn. 3.1668 = %3.1668,
            # yani ham değer zaten yüzde sayısı, /100 ile orana çevriliyor).
            # Kar analizi hesaplayıcısını gerçek veriyle doldurmak için kullanılır.
            "market_return_rate": (
                (demand_trend.get("data", {}) or {}).get("returnRatio") / 100
                if isinstance(demand_trend.get("data"), dict) and demand_trend.get("data", {}).get("returnRatio") is not None
                else None
            ),
            "top_competitors": top_competitors,  # otomatik çekildi (competitor_lookup, matchType=3)
            "pre_assessment": assessment,
            # GEÇİCİ DEBUG ALANI — gerçek MCP yanıt şeklini görmek için.
            # Veri şekli netleşince bu alan kaldırılacak.
            "_debug": {
                "node_id_path": node_id_path,
                "category_candidates_top3": category_candidates,
                "preferred_departments_used": preferred_departments,
                "competitor_category_raw": competitor_category_raw,
                "product_node_raw": product_node_raw,
                "keyword_miner_raw": kw_data,
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


@app.get("/api/decisions")
async def get_decisions():
    """Tüm kararlandırılmış keyword'leri Uygun/Sınırda/Elenmiş olarak gruplu döner."""
    return await db.list_decisions_grouped()


@app.get("/api/recent")
async def recent(limit: int = Query(50, le=200)):
    return await db.list_recent(limit)


def _safe_filename(keyword: str, suffix: str) -> str:
    safe = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in keyword).strip().replace(" ", "_")
    return f"{safe or 'analiz'}_{suffix}.xlsx"


@app.post("/api/export/report")
async def export_report(payload: dict):
    """
    Panelde gösterilen tam analiz verisini (/api/analyze'ın döndürdüğü `data`
    objesinin aynısı — frontend zaten elinde tutuyor, tekrar MCP çağırmaya
    gerek yok) tek sayfalık kapsamlı bir Excel raporuna çevirir.
    """
    try:
        xlsx_bytes = excel_export.build_report_xlsx(payload)
    except Exception as e:
        raise HTTPException(500, f"Excel oluşturulamadı: {e}")
    filename = _safe_filename(payload.get("keyword", "analiz"), "rapor")
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class ExportKeywordsRequest(BaseModel):
    keyword: str
    keyword_rows: list[dict]


@app.post("/api/export/keywords")
async def export_keywords(req: ExportKeywordsRequest):
    """Sadece Relevant Keywords tablosunu ayrı bir Excel dosyası olarak üretir."""
    try:
        xlsx_bytes = excel_export.build_keywords_xlsx(req.keyword_rows, req.keyword)
    except Exception as e:
        raise HTTPException(500, f"Excel oluşturulamadı: {e}")
    filename = _safe_filename(req.keyword, "keywords")
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


# ---------------------------------------------------------------------------
# HERCULES v3 — SUPPLIER SCORE (§3A)
# ---------------------------------------------------------------------------
class SupplierScoreRequest(BaseModel):
    supplier_name: str
    keyword: str | None = None
    scored_by: str
    factory_verified: int = 0
    moq_fit: int = 0
    us_export: int = 0
    fba_knowledge: int = 0
    response_speed: int = 0
    video_willingness: int = 0
    cert_authenticity: int = 0
    sample_quality: int = 0
    price_stability: int = 0


@app.post("/api/supplier-score")
async def supplier_score(req: SupplierScoreRequest):
    result = sup.score_supplier(req.dict(exclude={"supplier_name", "keyword", "scored_by"}))
    supplier_id = await db.upsert_supplier(req.supplier_name)
    score_id = await db.save_supplier_score(
        supplier_id, req.scored_by, result["scores"], result["total_score"], result["blocked"])
    return {**result, "supplier_id": supplier_id, "score_id": score_id}


# ---------------------------------------------------------------------------
# HERCULES v3 — CREATIVE PIPELINE (§3B) — 9 parçalık kanban
# ---------------------------------------------------------------------------
CREATIVE_DELIVERABLES = {
    1: "Ham fabrika videosu", 2: "Numune açılış (unboxing) videosu",
    3: "Ölçü/spec doğrulama videosu", 4: "Hero image (ana görsel)",
    5: "6'lı görsel set (lifestyle+infografik)", 6: "30-45 sn Amazon ürün videosu",
    7: "10-15 sn reklam cut'ları (dikey)", 8: "UGC tarzı kullanım videosu",
    9: "Rakip görsel karşılaştırma matrisi",
}


class CreativeUpdateRequest(BaseModel):
    keyword: str
    deliverable_no: int
    status: str  # pending | shooting | approved | live
    owner: str | None = None
    due_date: str | None = None


@app.post("/api/creative-deliverables")
async def update_creative(req: CreativeUpdateRequest):
    if req.deliverable_no not in CREATIVE_DELIVERABLES:
        raise HTTPException(400, f"Geçersiz deliverable_no. 1-9 arası olmalı: {CREATIVE_DELIVERABLES}")
    await db.upsert_creative_deliverable(req.keyword, req.deliverable_no, req.status, req.owner, req.due_date)
    return {"ok": True, "deliverable": CREATIVE_DELIVERABLES[req.deliverable_no]}


@app.get("/api/creative-deliverables/{keyword}")
async def get_creative(keyword: str):
    existing = await db.list_creative_deliverables(keyword)
    existing_nos = {d["deliverable_no"] for d in existing}
    # Henüz hiç dokunulmamış teslimatları da "pending" olarak göster
    full_list = existing + [
        {"keyword": keyword, "deliverable_no": n, "status": "pending", "owner": None, "due_date": None}
        for n in CREATIVE_DELIVERABLES if n not in existing_nos
    ]
    full_list.sort(key=lambda d: d["deliverable_no"])
    for d in full_list:
        d["label"] = CREATIVE_DELIVERABLES[d["deliverable_no"]]
    launch_ready = all(
        d["status"] in ("approved", "live") for d in full_list if d["deliverable_no"] in (1, 2, 3, 4, 5)
    )
    return {"deliverables": full_list, "launch_ready": launch_ready,
            "launch_ready_note": "1-5 numaralı teslimatlar onaylanmadan lansman tarihi verilmez"}


# ---------------------------------------------------------------------------
# HERCULES v3 — LAUNCH CONTROL (§4)
# ---------------------------------------------------------------------------
class LaunchCheckpointRequest(BaseModel):
    keyword: str
    asin: str | None = None
    checkpoint_day: str  # "14" | "30" | "60" | "90" | "ongoing"
    ctr: float | None = None
    cvr: float | None = None
    acos: float | None = None
    net_margin: float | None = None
    review_avg: float | None = None
    review_count: int | None = None
    return_rate: float | None = None
    has_impressions: bool = True
    trend_flat_or_up: bool = False
    category_avg_return_rate: float = 0.03
    entered_by: str
    source: str = "manual"


@app.post("/api/launch-checkpoint")
async def launch_checkpoint(req: LaunchCheckpointRequest):
    metrics = req.dict()
    result = lc.evaluate_checkpoint(req.checkpoint_day, metrics)
    owner = lc.suggested_action_owner(result["verdict"], req.checkpoint_day)
    checkpoint_id = await db.save_launch_checkpoint(
        req.keyword, req.asin, req.checkpoint_day, metrics, result["verdict"], req.entered_by, req.source)
    return {**result, "assigned_to": owner, "checkpoint_id": checkpoint_id}


@app.get("/api/launch-checkpoints/{keyword}")
async def get_launch_checkpoints(keyword: str):
    return await db.list_launch_checkpoints(keyword)


@app.get("/api/hit-rate")
async def hit_rate():
    """Ekibin gördüğü tek Bayesian-türevi metrik: geçmiş verdict dağılımı."""
    return await db.get_hit_rate()


# ---------------------------------------------------------------------------
# HERCULES v3 — DISCOVERY ENGINE (§1) — İSKELET
# ---------------------------------------------------------------------------
# NOT (dürüstlük): Keepa çapraz kontrolü ve Google Trends (pytrends) entegrasyonu
# HENÜZ YOK. Bu ortamda Keepa API erişimi/anahtarı yoktu, pytrends ayrı bir
# bağımlılık + Google'a ağ erişimi gerektiriyor (Vercel prod'da muhtemelen
# çalışır ama test edilmedi). Şimdilik yalnızca keyword_miner ile tarama
# yapılıyor; Keepa/Trends filtreleri sonraki bir adımda eklenmeli.
class DiscoveryRunRequest(BaseModel):
    lane: str  # keyword_cluster | sub_niche | new_product_radar | replacement_consumable | competitor_watch
    seed_keywords: list[str]
    marketplace: str = "US"


@app.post("/api/discovery/run")
async def discovery_run(req: DiscoveryRunRequest):
    run_id = await db.create_discovery_run(req.lane, {"seed_keywords": req.seed_keywords, "marketplace": req.marketplace})
    candidates = []
    for seed in req.seed_keywords:
        kw_data = await call_tool("keyword_miner", {
            "keyword": seed, "marketplace": req.marketplace, "minRelevancy": 50, "size": 10,
        })
        items = kw_data.get("data", {}).get("items", []) if isinstance(kw_data.get("data"), dict) else []
        for item in items:
            kw = item.get("keyword")
            if not kw:
                continue
            # Keepa/Trends filtreleri henüz yok — bu alanlar şimdilik None
            await db.add_discovery_candidate(run_id, kw, req.lane, keepa_flags=None, trends_score=None)
            candidates.append(kw)
    return {"run_id": run_id, "lane": req.lane, "candidates_found": len(candidates),
            "candidates": candidates[:30],
            "warning": "Keepa/Trends eleme filtreleri henüz entegre değil — tüm sonuçlar 'new' statüsünde, manuel gözden geçirme önerilir."}


@app.get("/api/discovery/candidates")
async def discovery_candidates(run_id: int | None = None, status: str | None = None):
    return await db.list_discovery_candidates(run_id, status)
