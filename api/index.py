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
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote

# Vercel'in Python runtime'ı bu dosyayı importlib ile dosya-yolu üzerinden
# yüklüyor ve api/ klasörünü otomatik olarak sys.path'e eklemiyor — bu yüzden
# aşağıdaki kardeş modül importları (mcp_client, database, scoring vb.)
# eklemeden ModuleNotFoundError verir. Yerelde (api/ içinden çalıştırınca)
# sorun çıkmaz, sadece Vercel'in çalıştırma şeklinde ortaya çıkar.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
import contextvars
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Depends, Header
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from mcp_client import call_tool
from scoring import calc_keyword_ad_metrics, calc_profit, pre_assessment, DEFAULT_THRESHOLDS, verdict_rule, weighted_top_acos
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
import checklist as ckl
import launch_report as lr
import competency as comp
import forum
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


@app.exception_handler(Exception)
async def _all_exceptions_as_json(request, exc: Exception):
    """
    KRİTİK: Vercel'in kendi runtime'ı, yakalanmamış bir hatada FastAPI'yi
    atlayıp DÜZ METİN ("Internal Server Error") döndürüyor — frontend bunu
    JSON sanıp parse etmeye çalışınca anlaşılmaz bir hata çıkıyor
    ("Unexpected token 'I'..."). Bu handler HER hatayı JSON'a çevirir, böylece
    hata her zaman okunabilir kalır.
    """
    return Response(
        content=json.dumps({"detail": f"Sunucu hatası: {exc}"}),
        status_code=500,
        media_type="application/json",
    )


@app.on_event("startup")
async def startup():
    # Şema sürümü güncelse tek sorgu; değilse tüm migrasyonlar (+ sertifika tohumu) tek bağlantıda.
    await db.init_db()


# İsteğe özel (her istek kendi görevinde/bağlamında çalışır): require_session'ın getirdiği rol + ekip bilgisi.
_AUTH_CTX: contextvars.ContextVar = contextvars.ContextVar("auth_ctx", default=None)


async def require_session(authorization: str | None = Header(default=None)) -> dict:
    """
    Oturum kontrolü — ad soyad kapısı OLMADAN. Yalnızca kişinin ad soyadını girebilmesi için gereken
    uçlarda (profil) kullanılır; diğer tüm uçlar `require_auth`.
    Oturum + rol + ekip bilgisi tek sorguda gelir (db.get_session); rol ve ekip isteğe özel bağlama
    (`_AUTH_CTX`) konur, require_user / _forum_viewer aynı istekte yeniden sorgulamaz.
    """
    if not await db.has_users():
        return {"email": "(auth kapalı — henüz kullanıcı yok)", "auth_disabled": True}
    token = (authorization or "").replace("Bearer ", "").strip()
    session = await db.get_session(token)
    if not session:
        raise HTTPException(401, "Oturum geçersiz veya süresi dolmuş — lütfen giriş yapın")
    _AUTH_CTX.set({"user_id": session["user_id"], "role": session["role"], "in_team": session["in_team"]})
    return {"email": session["email"], "user_id": session["user_id"], "token": token,
            "needs_name": not ((session.get("first_name") or "").strip() and (session.get("last_name") or "").strip())}


NAME_REQUIRED_DETAIL = "Devam etmek için önce adını ve soyadını girmen gerekiyor"


async def require_auth(authorization: str | None = Header(default=None)) -> dict:
    """
    Korumalı endpoint'ler için oturum kontrolü.
    Hiç kullanıcı kayıtlı değilse kimlik doğrulama DEVRE DIŞI (ilk kurulum kolaylığı) —
    ilk kullanıcı kaydolduğu anda tüm korumalı uçlar otomatik kilitlenir.
    """
    if not await db.has_users():
        return {"email": "(auth kapalı — henüz kullanıcı yok)", "auth_disabled": True}
    user = await require_session(authorization)
    # AD SOYAD KAPISI (sunucuda): adı/soyadı olmayan (eski) hesap, profil uçları dışında hiçbir şey yapamaz.
    if user.pop("needs_name", False):
        raise HTTPException(428, NAME_REQUIRED_DETAIL)
    return user


def _auth_state(user_id) -> dict | None:
    """require_session'ın BU istekte oturum sorgusuyla birlikte getirdiği rol/ekip bilgisi (yoksa None)."""
    st = _AUTH_CTX.get()
    return st if st and st.get("user_id") == user_id else None


async def require_user(user: dict = Depends(require_auth)) -> dict:
    """
    Gerçek (kayıtlı) bir kullanıcı + SUNUCU tarafında hesaplanan rolü.
    Auth kapalıyken (hiç kullanıcı yok) eğitim/rol uçları kullanılamaz: atanacak
    kimse yok ve tamamlama bir kişiye bağlanmak zorunda.
    """
    if user.get("auth_disabled") or not user.get("user_id"):
        raise HTTPException(401, "Bu bölüm için kayıtlı bir hesapla giriş yapın")
    st = _auth_state(user["user_id"])
    role = st["role"] if st else await db.get_user_role(user["user_id"])
    if not role:
        raise HTTPException(401, "Kullanıcı bulunamadı — lütfen tekrar giriş yapın")
    return {**user, "role": role}


async def require_staff(user: dict = Depends(require_user)) -> dict:
    """Owner veya admin."""
    if user["role"] not in ("owner", "admin"):
        raise HTTPException(403, "Bu işlem için yönetici yetkisi gerekli")
    return user


async def require_owner(user: dict = Depends(require_user)) -> dict:
    if user["role"] != "owner":
        raise HTTPException(403, "Bu işlemi yalnızca owner yapabilir")
    return user


# ---------------------------------------------------------------------------
# Yardımcı: SellerSprite yanıtlarından liste çıkar (gerçek veriyle doğrulandı)
# ---------------------------------------------------------------------------
def _parse_keyword_trend(resp: dict, last_n: int = 12) -> list:
    """
    keyword_research_trends'in gerçek yanıt şekli: "data" DOĞRUDAN bir liste,
    her ay için {"time": "2026年07月", "search": 19813, "purchase": 1921, ...}.
    "time" alanı Çince format — "2026-07" gibi okunabilir hale çeviriyoruz.
    Grafik kalabalık olmasın diye son `last_n` ay döndürülür (veri zaten
    kronolojik sıralı geliyor).
    """
    items = resp.get("data") if isinstance(resp.get("data"), list) else []
    parsed = []
    for it in items:
        m = re.match(r"(\d{4})年(\d{1,2})月", it.get("time", ""))
        month_label = f"{m.group(1)}-{int(m.group(2)):02d}" if m else it.get("time")
        parsed.append({
            "month": month_label,
            "search_volume": it.get("search"),
            "purchases": it.get("purchase"),
        })
    return parsed[-last_n:] if parsed else []


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
async def analyze(req: AnalyzeRequest, user: dict = Depends(require_auth)):
    uid = user.get("user_id", 0)
    # ÖNBELLEK OKUMASI KALDIRILDI (kullanıcı isteği): her arama artık HER ZAMAN
    # canlı MCP verisi çekiyor, son 24 saatte aynı keyword sorgulanmış olsa bile.
    # Eskiden 24 saatlik paylaşımlı önbellek kota tasarrufu sağlıyordu ama bu,
    # verinin bazen saatler önceki durumu yansıtması riskini taşıyordu — kullanıcı
    # bunun yerine her zaman en güncel veriyi görmeyi tercih etti.
    # (save_analysis çağrısı hâlâ duruyor — artık önbellek değil, sadece kayıt/log
    # amaçlı; okunmuyor.)

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

        # TOP RAKİPLER — KRİTİK DÜZELTME:
        # Eskiden kategori tespiti için yapılan matchType=3 (birebir başlık eşleşme)
        # çağrısının sonucu rakip listesi olarak da kullanılıyordu. Sorun: bu, ürün
        # BAŞLIĞINDA aranan kelimeyi birebir arıyor — Samsung'un kendi ürününün
        # başlığı "SAMSUNG Genuine Filter for Refrigerator HAF-QIN/EXP" olduğu için
        # "samsung water filter" aramasında ORİJİNAL ÜRÜN listeye HİÇ GİRMİYORDU;
        # sadece başlığında o kelime dizisi geçen compatible markalar geliyordu.
        # Çözüm: kategori belli olduktan sonra o kategorinin GERÇEK EN ÇOK SATANLARINI
        # ayrı bir çağrıyla çekiyoruz (nodeIdPath + satış adedine göre sıralı, 20 ürün).
        top_competitor_items = competitor_items or []
        if node_id_path:
            best_sellers_raw = await call_tool("competitor_lookup", {
                "marketplace": req.marketplace, "nodeIdPath": node_id_path, "size": 20,
                "order": {"field": "total_units", "desc": True}, "variation": "N",
            })
            bs_items = best_sellers_raw.get("data", {}).get("items", []) if isinstance(best_sellers_raw.get("data"), dict) else []
            if bs_items:
                top_competitor_items = bs_items

        top_competitors = [{
            "asin": it.get("asin"), "brand": it.get("brand"), "title": it.get("title"),
            "price": it.get("price") or it.get("averagePrice"),
            "units": it.get("units") or it.get("amzUnit"),
            "revenue": it.get("revenue") or it.get("amzSales"),
            "bsr": it.get("bsr"), "rating": it.get("rating"), "ratings": it.get("ratings"),
            "fulfillment": it.get("fulfillment"), "availableDate": it.get("availableDate"),
        } for it in top_competitor_items]

        # GÜÇLÜ YENİ MARKA (1 yıl) — gerçek veriden hesaplanan proxy:
        # Kategorinin en çok satan ürünleri arasında, listeleme tarihi
        # (availableDate, gerçek Amazon verisi) son 12 ay içinde olan DİSTİNCT
        # marka sayısı.
        # DÜRÜSTLÜK NOTU: Bu tüm pazarı değil, yalnızca en çok satan ~20 ürünü
        # tarar — küçük örneklemli bir göstergedir, pazarın TAMAMINDA kaç yeni
        # markanın güçlendiğinin kesin sayımı değildir. Yine de tahmin değil,
        # gerçek ASIN-bazlı listeleme tarihi verisine dayanır.
        one_year_ms = 365 * 24 * 3600 * 1000
        now_ms = time.time() * 1000
        recent_brands = {
            it.get("brand") for it in top_competitor_items
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

        # KEYWORD'ÜN KENDİ ARAMA HACMİ TRENDİ — KRİTİK DÜZELTME:
        # Panel eskiden "Trafik Trendi" grafiğinde market_product_demand_trend'in
        # glanceViews (KATEGORİ genelindeki milyonlarca sayfa görüntülenmesi)
        # alanını gösteriyordu — bu keyword'ün arama hacmiyle karışabiliyordu ve
        # sayılar (4-5 milyon) yanıltıcıydı. Gerçek MCP çağrısıyla doğrulandı:
        # keyword_research_trends bu KEYWORD'ün kendi aylık arama hacmini
        # (search alanı) veriyor — asıl istenen bu. Ayrı, doğru bir alan olarak
        # ekleniyor; demand_trend (return_rate için) DEĞİŞMEDİ, ayrıca duruyor.
        kw_trend_raw = await call_tool("keyword_research_trends", {
            "keyword": req.keyword, "marketplace": req.marketplace,
        }, wrap_in_request=False)
        search_volume_trend = _parse_keyword_trend(kw_trend_raw)

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
        # Tabloda ana keyword'ün satırını da EXACT veriyle değiştir (broad değil) —
        # kullanıcı tabloda ve ön değerlendirmede tutarlı, tam eşleşmiş veri görsün.
        if main_row:
            replaced = False
            for i, r in enumerate(keyword_rows):
                if r.get("keyword", "").lower() == req.keyword.lower():
                    # Exact satır geniş satırın yerine geçer; exact yanıtta olmayan alanlar
                    # (ör. relevancy — Kriter 03'ün sıralaması buna dayanır) geniş satırdan korunur.
                    keyword_rows[i] = {**r, **main_row}
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

        # Kullanıcının Ayarlar'da özelleştirdiği eşikler (varsa) — yoksa
        # pre_assessment zaten DEFAULT_THRESHOLDS'a düşer (bkz. scoring.py).
        user_thresholds = await db.get_user_thresholds(uid)

        # Kriter 03: relevancy'si en yüksek 5 keyword'ün ağırlıklı ACOS'u (her keyword kendi avgPrice'ıyla)
        acos_detail = weighted_top_acos(keyword_rows, "relevancy", lambda r: r.get("avgPrice"))
        assessment = pre_assessment(
            avg_price=stats_data.get("avgPrice"),
            gross_margin=gross_margin,
            acos=acos_detail["value"],
            acos_detail=acos_detail,
            top_brand_share=top_brand_share,
            strong_new_brands=strong_new_brands_count,  # top 10 rakip availableDate proxy'si (bkz. yukarıdaki not)
            net_margin=None,
            thresholds=user_thresholds,
        )

        payload = {
            "keyword": req.keyword,
            "marketplace": req.marketplace,
            "category_used": category_used_label,
            "fetched_at_iso": datetime.now(timezone.utc).isoformat(),  # her arama gerçekten canlı mı doğrulamak için
            "category_candidates": category_candidates,
            "keyword_data_raw": kw_data,
            "keyword_rows": keyword_rows,
            "market_stats": stats_data,
            "brand_concentration": brand_items,
            "price_distribution": _extract_list(price_dist),
            "launch_distribution": _extract_list(launch_dist),
            "demand_trend": demand_trend,
            "search_volume_trend": search_volume_trend,  # keyword'ün KENDİ arama hacmi (bkz. _parse_keyword_trend)
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
        }

        await db.save_analysis(req.keyword, req.marketplace, payload, req.requested_by)
        await db.log_user_query(uid, req.keyword, req.marketplace, assessment.get("verdict"))
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
async def competitors(req: CompetitorsRequest, user: dict = Depends(require_auth)):
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
async def profit(req: ProfitRequest, user: dict = Depends(require_auth)):
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
async def save_decision(req: DecisionRequest, user: dict = Depends(require_auth)):
    uid = user.get("user_id", 0)
    await db.save_decision(uid, req.keyword, req.marketplace, req.decision, req.note, user.get("email", req.decided_by))
    return {"ok": True}


@app.get("/api/decisions")
async def get_decisions(user: dict = Depends(require_auth)):
    """Yalnızca BU KULLANICININ kararlarını Uygun/Sınırda/Elenmiş olarak gruplu döner."""
    return await db.list_decisions_grouped(user.get("user_id", 0))


@app.get("/api/recent")
async def recent(limit: int = Query(50, le=200), user: dict = Depends(require_auth)):
    """Yalnızca BU KULLANICININ sorguladığı keyword'lerin geçmişi — herkese özel."""
    return await db.list_recent(user.get("user_id", 0), limit)



def _safe_filename(keyword: str, suffix: str) -> str:
    safe = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in keyword).strip().replace(" ", "_")
    return f"{safe or 'analiz'}_{suffix}.xlsx"


_TR_ASCII = str.maketrans({"ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ı": "i", "İ": "I",
                           "ç": "c", "Ç": "C", "ö": "o", "Ö": "O", "ü": "u", "Ü": "U"})


def _content_disposition(filename: str) -> str:
    """
    RFC 5987/6266: Türkçe (ASCII dışı) dosya adları için.
    Eskiden ad doğrudan filename="..." içine yazılıyordu; Starlette başlıkları latin-1
    kodladığı için ş/ğ/ı içeren keyword'lerde (örn. "şemsiye") export uçları 500 veriyordu.
    filename  = ASCII yedek (ş→s, ğ→g, ı→i, İ→I, ç→c, ö→o, ü→u; kalan ASCII dışı → _)
    filename* = UTF-8 yüzde kodlu asıl ad (modern tarayıcılar bunu kullanır)
    """
    fallback = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_"
                       for c in filename.translate(_TR_ASCII))
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@app.post("/api/export/report")
async def export_report(payload: dict, user: dict = Depends(require_auth)):
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
        headers={"Content-Disposition": _content_disposition(filename)},
    )


class ExportKeywordsRequest(BaseModel):
    keyword: str
    keyword_rows: list[dict]
    note: str | None = Field(None, max_length=300)   # panel filtre notu -> Excel başlığına


@app.post("/api/export/keywords")
async def export_keywords(req: ExportKeywordsRequest, user: dict = Depends(require_auth)):
    """Sadece Relevant Keywords tablosunu ayrı bir Excel dosyası olarak üretir."""
    try:
        xlsx_bytes = excel_export.build_keywords_xlsx(req.keyword_rows, req.keyword, req.note)
    except Exception as e:
        raise HTTPException(500, f"Excel oluşturulamadı: {e}")
    filename = _safe_filename(req.keyword, "keywords")
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


class ReportRow(BaseModel):
    keyword: str = Field("", max_length=500)
    marketplace: str = Field("", max_length=10)
    decision: str | None = Field(None, max_length=40)
    verdict: str | None = Field(None, max_length=40)
    note: str | None = Field(None, max_length=5000)
    decided_at: int | None = None
    queried_at: int | None = None
    decided_by: str | None = Field(None, max_length=300)


# ---------------------------------------------------------------------------
# RAPORLAR — kullanıcının kendi kararları + son 200 sorgusu, (keyword, pazar) başına tek kayıt.
# Eskiden panel /api/decisions + /api/recent?limit=200'ü çekip istemcide birleştiriyordu; birleştirme,
# filtre, KPI ve sayfalama artık burada (aynı kurallar), panel yalnızca bir sayfayı alır.
# ---------------------------------------------------------------------------
REPORTS_PAGE_SIZE = 20
REPORTS_RECENT_LIMIT = 200
REPORT_STATUSES = ("all", "decided", "pending")


async def _report_items(user_id) -> list[dict]:
    grouped = await db.list_decisions_grouped(user_id)
    recent = await db.list_recent(user_id, REPORTS_RECENT_LIMIT)
    items: dict = {}
    key = lambda kw, m: f"{str(kw).lower()}|{m}"
    for decision, rows in (grouped or {}).items():
        for it in rows or []:
            items[key(it["keyword"], it["marketplace"])] = {
                "keyword": it["keyword"], "marketplace": it["marketplace"], "decision": decision,
                "note": it.get("note") or "", "decided_at": it.get("decided_at"), "decided_by": it.get("decided_by") or "",
                "verdict": None, "queried_at": None}
    for r in recent:
        cur = items.get(key(r["keyword"], r["marketplace"]))
        if cur:
            if not cur["queried_at"] or (r["fetched_at"] or 0) > cur["queried_at"]:
                cur["queried_at"], cur["verdict"] = r["fetched_at"], r["verdict"]
        else:
            items[key(r["keyword"], r["marketplace"])] = {
                "keyword": r["keyword"], "marketplace": r["marketplace"], "decision": None, "note": "",
                "decided_at": None, "decided_by": "", "verdict": r["verdict"], "queried_at": r["fetched_at"]}
    return sorted(items.values(), key=_report_ts, reverse=True)


def _report_ts(it) -> int:
    return max(it.get("decided_at") or 0, it.get("queried_at") or 0)


def _report_filter(items: list, status: str, q: str, range_days: int | None, market: str, now: float):
    """(liste filtresi uygulanmış kayıtlar, KPI kümesi = yalnızca aralık + pazar filtresi)."""
    in_range = [it for it in items
                if (range_days is None or now - _report_ts(it) <= range_days * 86400)
                and (market == "all" or it["marketplace"] == market)]
    needle = (q or "").strip().lower()
    out = []
    for it in in_range:
        if status == "decided" and not it["decision"]:
            continue
        if status == "pending" and it["decision"]:
            continue
        if needle and needle not in f"{it['keyword']} {it['note']}".lower():
            continue
        out.append(it)
    return out, in_range


def _report_range(range_: str) -> int | None:
    if range_ in ("", "all"):
        return None
    if not range_.isdigit() or not 1 <= int(range_) <= 3650:
        raise HTTPException(422, "Geçersiz tarih aralığı")
    return int(range_)


@app.get("/api/reports")
async def reports(status: str = Query("all"), q: str = Query("", max_length=200), range: str = Query("all", max_length=8),
                  market: str = Query("all", max_length=10), page: int = Query(1, ge=1),
                  user: dict = Depends(require_auth)):
    """Raporlar sayfası: filtrelenmiş liste SAYFALI; KPI'lar (aralık + pazar filtresiyle) tüm kümeden."""
    if status not in REPORT_STATUSES:
        raise HTTPException(422, "Geçersiz durum filtresi")
    items = await _report_items(user.get("user_id", 0))
    now = time.time()
    rows, in_range = _report_filter(items, status, q, _report_range(range), market, now)
    pg, page_rows = _page_slice(rows, page, REPORTS_PAGE_SIZE)
    count = lambda d: sum(1 for it in in_range if it["decision"] == d)
    return {"items": page_rows, "total": len(rows), "all_count": len(items),
            "page": pg["page"], "pages": pg["pages"], "page_size": REPORTS_PAGE_SIZE,
            "kpi": {"total": len(in_range), "week": sum(1 for it in in_range if now - _report_ts(it) <= 7 * 86400),
                    "uygun": count("Uygun"), "sinirda": count("Sınırda"), "elenmis": count("Elenmiş"),
                    "undecided": sum(1 for it in in_range if not it["decision"])}}


class ReportFilters(BaseModel):
    status: str = "all"
    q: str = Field("", max_length=200)
    range: str = Field("all", max_length=8)
    market: str = Field("all", max_length=10)


class ExportReportsRequest(BaseModel):
    rows: list[ReportRow] = Field(default_factory=list, max_length=10000)
    # Verilirse satırlar SUNUCUDA (Raporlar sayfasının filtreleriyle, tüm sayfalar) üretilir; `rows` yok sayılır.
    filters: ReportFilters | None = None
    tz_offset_min: int = Field(0, ge=-900, le=900)
    filename: str = Field("pl_pazar_raporlari", max_length=120)


@app.post("/api/export/reports")
async def export_reports(req: ExportReportsRequest, user: dict = Depends(require_auth)):
    """Raporlar sayfası listesini (eski CSV ile aynı sütunlar) Excel olarak döner. Metinler formül değil METİN yazılır."""
    if req.filters is not None:
        f = req.filters
        if f.status not in REPORT_STATUSES:
            raise HTTPException(422, "Geçersiz durum filtresi")
        rows, _ = _report_filter(await _report_items(user.get("user_id", 0)), f.status, f.q,
                                 _report_range(f.range), f.market, time.time())
        rows = [{"keyword": it["keyword"] or "", "marketplace": it["marketplace"] or "", "decision": it["decision"] or "",
                 "verdict": it["verdict"] or "", "note": it["note"] or "", "decided_at": it["decided_at"] or None,
                 "queried_at": it["queried_at"] or None, "decided_by": it["decided_by"] or ""} for it in rows]
    else:
        rows = [r.model_dump() for r in req.rows]
    try:
        xlsx_bytes = excel_export.build_reports_xlsx(rows, req.tz_offset_min)
    except Exception as e:
        raise HTTPException(500, f"Excel oluşturulamadı: {e}")
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(f"{req.filename}.xlsx")},
    )


@app.get("/api/verdict-rule")
async def get_verdict_rule(user: dict = Depends(require_auth)):
    """Ön öneri kuralı (tek kaynak: scoring.py) — panelin açıklama metinleri bunu kullanır."""
    return verdict_rule()


@app.get("/api/thresholds")
async def thresholds(user: dict = Depends(require_auth)):
    """
    DEFAULT_THRESHOLDS + kullanıcının özelleştirdiği alanlar (varsa) birleşik
    döner. auth kapalıyken (henüz kullanıcı yok) user_id=0 için kayıt
    olmayacağı için saf DEFAULT_THRESHOLDS döner — davranış değişmez.
    """
    uid = user.get("user_id", 0)
    overrides = await db.get_user_thresholds(uid)
    return {**DEFAULT_THRESHOLDS, **overrides}


class ThresholdUpdateRequest(BaseModel):
    # Sınırlar app.js::THRESHOLD_FIELDS min/max ile BİREBİR aynı (tek kaynak
    # tutarlılığı) — biri değişirse diğeri de değişmeli. Yüzde alanları formda
    # 0-100 gösterilir ama buraya 0-1 oran olarak gelir (ACOS formda 0-150).
    # Aralık dışı değer -> FastAPI otomatik 422.
    min_avg_price: float | None = Field(None, ge=0, le=100)          # $0-100
    min_gross_margin: float | None = Field(None, ge=0, le=1)         # %0-100
    max_acos: float | None = Field(None, ge=0, le=1.5)               # %0-150
    max_brand_share: float | None = Field(None, ge=0, le=1)          # %0-100
    min_strong_new_brands: int | None = Field(None, ge=0, le=20)     # 0-20 marka, tam sayı
    min_net_margin: float | None = Field(None, ge=0, le=1)           # %0-100


@app.put("/api/thresholds")
async def update_thresholds(req: ThresholdUpdateRequest, user: dict = Depends(require_auth)):
    """
    Kısmi güncelleme — yalnızca gönderilen (None olmayan) alanlar değişir.
    Auth kapalıyken (auth_disabled) user_id=0 altında kaydedilir; ilk
    kullanıcı kaydolduğunda bu "global" satırın miras alınması beklenmez —
    her kullanıcı kendi eşiklerini yeniden ayarlamalı (bilinçli basit tutuldu).
    """
    uid = user.get("user_id", 0)
    payload = req.dict(exclude_none=True)
    if not payload:
        raise HTTPException(400, "Güncellenecek en az bir alan gerekli")
    await db.save_user_thresholds(uid, payload)
    merged = {**DEFAULT_THRESHOLDS, **await db.get_user_thresholds(uid)}
    return merged


@app.post("/api/thresholds/reset")
async def reset_thresholds(user: dict = Depends(require_auth)):
    """Tüm özelleştirmeleri siler, kullanıcı fabrika eşiklerine döner."""
    uid = user.get("user_id", 0)
    await db.reset_user_thresholds(uid)
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
async def compute_signals(req: SignalsRequest, user: dict = Depends(require_auth)):
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
async def add_proof_asset(req: ProofAssetRequest, user: dict = Depends(require_auth)):
    if req.type not in se.PROOF_POINTS:
        raise HTTPException(400, f"Geçersiz proof type. Geçerli: {list(se.PROOF_POINTS)}")
    points = 0 if (req.type == "coa_lab_cert" and req.category_is_regulated) else se.PROOF_POINTS[req.type]
    asset_id = await db.add_proof_asset(req.keyword, req.type, points, req.file_url,
                                         req.competitor_id, req.supplier_ref, req.note)
    return {"id": asset_id, "points": points,
            "note": "Regüle kategoride COA puan değil veto kapısıdır — bkz /api/compliance-check" if points == 0 and req.type == "coa_lab_cert" else None}


@app.get("/api/proof-assets/{keyword}")
async def get_proof_assets(keyword: str, user: dict = Depends(require_auth)):
    assets = await db.list_proof_assets(keyword)
    approved_types = [a["type"] for a in assets if a["status"] == "approved"]
    score = se.proof_signal(approved_types)
    return {"assets": assets, "proof_score": score}


class ProofApproveRequest(BaseModel):
    asset_id: int
    approved_by: str


@app.post("/api/proof-assets/approve")
async def approve_proof(req: ProofApproveRequest, user: dict = Depends(require_auth)):
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
async def compliance_check(req: ComplianceCheckRequest, user: dict = Depends(require_auth)):
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
async def portfolio_solve(req: PortfolioSolveRequest, background_tasks: BackgroundTasks, user: dict = Depends(require_auth)):
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
async def get_portfolio_run(run_id: int, user: dict = Depends(require_auth)):
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
async def learning_event(req: LearningEventRequest, user: dict = Depends(require_auth)):
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
async def get_learning_state(keyword: str, user: dict = Depends(require_auth)):
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
async def supplier_score(req: SupplierScoreRequest, user: dict = Depends(require_auth)):
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
async def update_creative(req: CreativeUpdateRequest, user: dict = Depends(require_auth)):
    if req.deliverable_no not in CREATIVE_DELIVERABLES:
        raise HTTPException(400, f"Geçersiz deliverable_no. 1-9 arası olmalı: {CREATIVE_DELIVERABLES}")
    await db.upsert_creative_deliverable(req.keyword, req.deliverable_no, req.status, req.owner, req.due_date)
    return {"ok": True, "deliverable": CREATIVE_DELIVERABLES[req.deliverable_no]}


@app.get("/api/creative-deliverables/{keyword}")
async def get_creative(keyword: str, user: dict = Depends(require_auth)):
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
async def launch_checkpoint(req: LaunchCheckpointRequest, user: dict = Depends(require_auth)):
    metrics = req.dict()
    result = lc.evaluate_checkpoint(req.checkpoint_day, metrics)
    owner = lc.suggested_action_owner(result["verdict"], req.checkpoint_day)
    checkpoint_id = await db.save_launch_checkpoint(
        req.keyword, req.asin, req.checkpoint_day, metrics, result["verdict"], req.entered_by, req.source)
    return {**result, "assigned_to": owner, "checkpoint_id": checkpoint_id}


@app.get("/api/launch-checkpoints/{keyword}")
async def get_launch_checkpoints(keyword: str, user: dict = Depends(require_auth)):
    return await db.list_launch_checkpoints(keyword)


@app.get("/api/hit-rate")
async def hit_rate(user: dict = Depends(require_auth)):
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
async def discovery_run(req: DiscoveryRunRequest, user: dict = Depends(require_auth)):
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
async def discovery_candidates(run_id: int | None = None, status: str | None = None, user: dict = Depends(require_auth)):
    return await db.list_discovery_candidates(run_id, status)


# ---------------------------------------------------------------------------
# KİMLİK DOĞRULAMA (basit e-posta + şifre)
# ---------------------------------------------------------------------------


class AuthRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(AuthRequest):
    invite: str | None = Field(None, max_length=200)   # davet bağlantısındaki kod (?invite=...)
    first_name: str = Field("", max_length=200)
    last_name: str = Field("", max_length=200)


_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clean_name_part(v: str | None, label: str) -> str:
    v = " ".join(_CTRL_RE.sub(" ", v or "").split())
    if not v:
        raise HTTPException(422, f"{label} zorunlu")
    if len(v) > 60:
        raise HTTPException(422, f"{label} en fazla 60 karakter olabilir")
    return v


@app.post("/api/auth/register")
async def auth_register(req: RegisterRequest):
    """Davet kodu geçerliyse kişi o ekibe katılır; geçersiz/süresi dolmuş/iptal edilmiş davet kaydı
    engellemez — kişi ekip dışı başlar (yanıtta invite.status = "invalid")."""
    if len(req.password) < 6:
        raise HTTPException(400, "Şifre en az 6 karakter olmalı")
    first, last = _clean_name_part(req.first_name, "Ad"), _clean_name_part(req.last_name, "Soyad")
    try:
        user = await db.create_user(req.email, req.password, (req.invite or "").strip() or None, first, last)
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = await db.create_session(user)
    return {"token": token, "email": user["email"], "invite": user.get("invite")}


@app.post("/api/auth/login")
async def auth_login(req: AuthRequest):
    user = await db.verify_user(req.email, req.password)
    if not user:
        raise HTTPException(401, "E-posta veya şifre hatalı")
    token = await db.create_session(user)
    return {"token": token, "email": user["email"]}


@app.post("/api/auth/logout")
async def auth_logout(authorization: str | None = Header(default=None)):
    token = (authorization or "").replace("Bearer ", "").strip()
    await db.delete_session(token)
    return {"ok": True}


@app.get("/api/auth/status")
async def auth_status(authorization: str | None = Header(default=None)):
    """Frontend açılışta çağırır: giriş gerekli mi, kullanıcı kim, veri paylaşımlı mı."""
    has_users = await db.has_users()
    token = (authorization or "").replace("Bearer ", "").strip()
    session = await db.get_session(token) if token else None
    role = session["role"] if session else None
    return {
        "auth_required": has_users,
        "has_users": has_users,
        "logged_in": bool(session),
        "email": session["email"] if session else None,
        "name": db.display_name(session) if session else None,
        # Ad soyad girilmemiş eski hesap: panel ad soyad ekranını gösterir (sunucu diğer uçlarda 428 verir)
        "needs_name": bool(session) and not ((session.get("first_name") or "").strip()
                                             and (session.get("last_name") or "").strip()),
        "user_id": session["user_id"] if session and role else None,
        "role": role,  # "owner" | "admin" | "member" | None — yetki kontrolü yine sunucuda yapılır
        "storage": db.storage_info(),
        # Owner menü rozeti: son 7 günde kaydolan, hiçbir ekipte olmayan hesaplar
        "new_outsiders_7d": await db.new_outsider_count(7) if role == "owner" else None,
    }


# ---------------------------------------------------------------------------
# SİLME İŞLEMLERİ
# ---------------------------------------------------------------------------
class DeleteKeywordRequest(BaseModel):
    keyword: str
    marketplace: str = "US"


@app.post("/api/decisions/delete")
async def delete_decision_ep(req: DeleteKeywordRequest, user: dict = Depends(require_auth)):
    await db.delete_decision(user.get("user_id", 0), req.keyword, req.marketplace)
    return {"ok": True, "deleted_by": user["email"]}


@app.post("/api/history/delete")
async def delete_history_ep(req: DeleteKeywordRequest, user: dict = Depends(require_auth)):
    await db.delete_analysis(user.get("user_id", 0), req.keyword, req.marketplace)
    return {"ok": True, "deleted_by": user["email"]}


@app.post("/api/decisions/clear")
async def clear_decisions_ep(user: dict = Depends(require_auth)):
    await db.clear_all_decisions(user.get("user_id", 0))
    return {"ok": True}


@app.post("/api/history/clear")
async def clear_history_ep(user: dict = Depends(require_auth)):
    await db.clear_all_analyses(user.get("user_id", 0))
    return {"ok": True}



# ---------------------------------------------------------------------------
# ASIN ANALİZİ (Reverse ASIN) — keyword yerine ASIN ile aynı raporu üretir
# ---------------------------------------------------------------------------
class AnalyzeAsinRequest(BaseModel):
    asin: str
    marketplace: str = "US"
    keyword_list_size: int = 20
    force_refresh: bool = False


@app.post("/api/analyze-asin")
async def analyze_asin(req: AnalyzeAsinRequest, user: dict = Depends(require_auth)):
    """
    ASIN girildiğinde:
      1. competitor_lookup(asins=[ASIN]) -> ürün detayı + GERÇEK kategori (nodeIdPath)
         (kategori TAHMİN EDİLMEZ — ürünün kendi browse-node'u kullanılır)
      2. traffic_keyword (reverse ASIN)  -> bu ASIN'in trafik aldığı keyword listesi
      3. market_* çağrıları              -> ürünün kendi kategorisiyle pazar analizi
    Çıktı, /api/analyze ile AYNI şekildedir; frontend aynı paneli yeniden kullanır.
    """
    asin = req.asin.strip().upper()
    cache_key = f"ASIN:{asin}"
    uid = user.get("user_id", 0)
    # ÖNBELLEK OKUMASI KALDIRILDI (bkz. /api/analyze'daki aynı not) — her ASIN
    # sorgusu artık her zaman canlı MCP verisi çekiyor.

    try:
        # 1) Ürün detayı + gerçek kategori
        detail_raw = await call_tool("competitor_lookup", {
            "asins": [asin], "marketplace": req.marketplace, "variation": "N",
        })
        detail_items = detail_raw.get("data", {}).get("items", []) if isinstance(detail_raw.get("data"), dict) else []
        if not detail_items:
            raise HTTPException(404, f"{asin} için ürün verisi bulunamadı — ASIN'i ve pazarı kontrol edin.")
        product = detail_items[0]
        node_id_path = product.get("nodeIdPath")
        category_used_label = (product.get("nodeLabelPath") or "") + " (ASIN'in kendi kategorisi)"
        asin_price = product.get("price") or product.get("averagePrice")

        # 2) Reverse ASIN — trafik keyword'leri
        rev_raw = await call_tool("traffic_keyword", {
            "asin": asin, "marketplace": req.marketplace, "size": req.keyword_list_size,
            "order": {"field": "searches", "desc": True},
        })
        rev_items = rev_raw.get("data", {}).get("items", []) if isinstance(rev_raw.get("data"), dict) else []

        keyword_rows = []
        for item in rev_items:
            # ACOS için ürünün KENDİ fiyatını kullanıyoruz — reverse ASIN'de bu
            # keyword ortalamasından daha doğru, çünkü bu spesifik ürünü analiz ediyoruz.
            metrics = calc_keyword_ad_metrics(
                clicks=item.get("clicks", 0), purchases=item.get("purchases", 0),
                bid=item.get("bid"), avg_price=asin_price,
                impressions=item.get("impressions"), searches=item.get("searches"),
            )
            badges = item.get("badges") or []
            rank_pos = (item.get("rankPosition") or {}).get("position")
            keyword_rows.append({
                **item, **metrics,
                "avgPrice": asin_price,
                "relevancy": round((item.get("trafficPercentage") or 0) * 100, 1),  # trafik payı %
                "organic_rank": rank_pos,
                "ad_rank": (item.get("adPosition") or {}).get("position"),
                "is_organic": "naturalSearching" in badges,
                "is_ad": "ads" in badges,
                "is_amazon_choice": "amazonChoice" in badges,
            })

        # Ana satır = en yüksek trafik payına sahip keyword
        main_row = max(keyword_rows, key=lambda r: r.get("trafficPercentage") or 0, default=None)
        # Kriter 03: trafik payı en yüksek 5 keyword'ün ağırlıklı ACOS'u (fiyat = ürünün kendi fiyatı)
        acos_detail = weighted_top_acos(keyword_rows, "trafficPercentage", lambda r: asin_price)

        # 3) Pazar analizi (ürünün kendi kategorisiyle)
        market_stats = brand_conc = price_dist = launch_dist = demand_trend = {}
        if node_id_path:
            market_stats, brand_conc, price_dist, launch_dist, demand_trend = await asyncio.gather(
                call_tool("market_research_statistics", {"marketplace": req.marketplace, "nodeIdPath": node_id_path, "topN": 10}),
                call_tool("market_brand_concentration", {"marketplace": req.marketplace, "nodeIdPath": node_id_path, "topN": 10}),
                call_tool("market_price_distribution", {"marketplace": req.marketplace, "nodeIdPath": node_id_path, "topN": 10}),
                call_tool("market_listing_date_distribution", {"marketplace": req.marketplace, "nodeIdPath": node_id_path, "topN": 10}),
                call_tool("market_product_demand_trend", {"marketplace": req.marketplace, "nodeIdPath": node_id_path, "topN": 10}),
            )

        # ASIN'in EN ÇOK trafik aldığı kelimenin arama hacmi trendi (bkz. keyword
        # akışındaki aynı isimli notta anlatılan gerekçe — kategori geneli değil,
        # bu ürünün asıl aranma kanalının kendi arama hacmi).
        search_volume_trend = []
        if main_row and main_row.get("keyword"):
            kw_trend_raw = await call_tool("keyword_research_trends", {
                "keyword": main_row["keyword"], "marketplace": req.marketplace,
            }, wrap_in_request=False)
            search_volume_trend = _parse_keyword_trend(kw_trend_raw)

        # 4) Aynı kategorideki top rakipler (bu ASIN hariç)
        comp_raw = await call_tool("competitor_lookup", {
            "marketplace": req.marketplace, "nodeIdPath": node_id_path, "size": 20,
            "order": {"field": "total_units", "desc": True}, "variation": "N",
        }) if node_id_path else {}
        comp_items = comp_raw.get("data", {}).get("items", []) if isinstance(comp_raw.get("data"), dict) else []
        top_competitors = [{
            "asin": it.get("asin"), "brand": it.get("brand"), "title": it.get("title"),
            "price": it.get("price") or it.get("averagePrice"),
            "units": it.get("units") or it.get("amzUnit"),
            "revenue": it.get("revenue") or it.get("amzSales"),
            "bsr": it.get("bsr"), "rating": it.get("rating"), "ratings": it.get("ratings"),
            "fulfillment": it.get("fulfillment"), "availableDate": it.get("availableDate"),
        } for it in comp_items]

        one_year_ms = 365 * 24 * 3600 * 1000
        now_ms = time.time() * 1000
        strong_new_brands_count = len({
            it.get("brand") for it in comp_items
            if it.get("brand") and it.get("availableDate") and (now_ms - it["availableDate"]) <= one_year_ms
        })

        brand_items = _extract_list(brand_conc)
        top_brand_share = max((b.get("totalRevenueRatio", 0) for b in brand_items), default=None) if brand_items else None
        stats_data = market_stats.get("data", market_stats)
        raw_gm = stats_data.get("avgProfit")
        gross_margin = (raw_gm / 100 if raw_gm and raw_gm > 1 else raw_gm) if raw_gm is not None else None

        user_thresholds = await db.get_user_thresholds(uid)
        assessment = pre_assessment(
            avg_price=stats_data.get("avgPrice"), gross_margin=gross_margin,
            acos=acos_detail["value"], acos_detail=acos_detail,
            top_brand_share=top_brand_share, strong_new_brands=strong_new_brands_count, net_margin=None,
            thresholds=user_thresholds)

        payload = {
            "keyword": f"{asin} — {(product.get('title') or '')[:60]}",
            "asin": asin,
            "marketplace": req.marketplace,
            "analysis_mode": "asin",
            "asin_info": {
                "asin": asin, "title": product.get("title"), "brand": product.get("brand"),
                "price": asin_price, "units": product.get("units"), "revenue": product.get("revenue"),
                "bsr": product.get("bsr"), "rating": product.get("rating"),
                "ratings": product.get("ratings"), "fulfillment": product.get("fulfillment"),
                "availableDate": product.get("availableDate"),
                "total_traffic_keywords": rev_raw.get("data", {}).get("total") if isinstance(rev_raw.get("data"), dict) else None,
            },
            "category_used": category_used_label,
            "fetched_at_iso": datetime.now(timezone.utc).isoformat(),  # her arama gerçekten canlı mı doğrulamak için
            "category_candidates": [],
            "keyword_rows": keyword_rows,
            "market_stats": stats_data,
            "brand_concentration": brand_items,
            "price_distribution": _extract_list(price_dist),
            "launch_distribution": _extract_list(launch_dist),
            "demand_trend": demand_trend,
            "search_volume_trend": search_volume_trend,
            "market_return_rate": (
                (demand_trend.get("data", {}) or {}).get("returnRatio") / 100
                if isinstance(demand_trend.get("data"), dict) and demand_trend.get("data", {}).get("returnRatio") is not None
                else None),
            "top_competitors": top_competitors,
            "pre_assessment": assessment,
        }

        await db.save_analysis(cache_key, req.marketplace, payload, user.get("email"))
        await db.log_user_query(uid, cache_key, req.marketplace, assessment.get("verdict"))
        return {**payload, "source": "live"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"SellerSprite MCP hatası: {e}")


# ---------------------------------------------------------------------------
# TRENDLER & FIRSATLAR — aba_research_weekly/monthly (gerçek MCP verisiyle
# doğrulandı) üzerine kurulu keşif endpoint'i
# ---------------------------------------------------------------------------
SEARCH_MODEL_LABELS = {
    1: "Popüler Pazar", 2: "Anormal Hareketli", 3: "Sürekli Büyüyen",
    4: "Hızlı Yükselen", 5: "Potansiyel", 6: "Uzun Kuyruk",
}


class TrendingRequest(BaseModel):
    marketplace: str = "US"
    search_model: int = 4  # varsayılan: hızlı yükselen (Breakout Nişler)
    granularity: str = "weekly"  # "weekly" | "monthly"
    departments: list[str] = []
    min_searches: int | None = None
    size: int = 20


@app.post("/api/discovery/trending")
async def discovery_trending(req: TrendingRequest, user: dict = Depends(require_auth)):
    """
    Trendler & Fırsatlar sayfası için: aba_research_weekly/monthly'den
    yükselen/anormal/potansiyel keyword'leri çeker. Gerçek MCP çağrısıyla
    doğrulanmış alan adları kullanılıyor — DÜRÜSTLÜK NOTU: Google Trends'i
    (mcp__Seller_Sprite__google_trend) ya da sosyal medya/TikTok viral
    katsayısını burada KULLANMIYORUZ — bu ikincisi için gerçek bir MCP
    kaynağı bulunamadı (Stitch tasarımındaki "Viral Dönüşüm Katsayısı"
    kartının backend karşılığı yok, eklenmemeli).
    """
    if req.search_model not in SEARCH_MODEL_LABELS:
        raise HTTPException(400, f"search_model 1-6 arası olmalı: {SEARCH_MODEL_LABELS}")
    tool = "aba_research_weekly" if req.granularity == "weekly" else "aba_research_monthly"

    args = {
        "marketplace": req.marketplace, "searchModel": req.search_model,
        "size": req.size, "order": {"field": "searches_growth", "desc": True},
    }
    if req.departments:
        args["departments"] = req.departments
    if req.min_searches:
        args["minSearches"] = req.min_searches

    raw = await call_tool(tool, args)
    items = raw.get("data", {}).get("items", []) if isinstance(raw.get("data"), dict) else []

    results = [{
        "keyword": it.get("keyword"),
        "departments": it.get("departments", []),
        "searches": it.get("searches"),
        "search_rank": it.get("searchRank"),
        "growth_rate": it.get("searchRankGrowthRate"),  # 0-1 oran: arama SIRALAMASI yükselme oranı (0.909 = 11. sıradan 1. sıraya), hacim büyümesi DEĞİL
        "growth_4w": it.get("w4RankGrowthRate"),
        "growth_12w": it.get("w12RankGrowthRate"),
        "purchases": it.get("purchases"),
        "purchase_rate": it.get("purchaseRate"),
        "bid": it.get("bid"), "bid_min": it.get("bidMin"), "bid_max": it.get("bidMax"),
        "top3_brands": [b for b in (it.get("top3Brands") or []) if b],
        "top3_asins": [{
            "asin": a.get("asin"), "image_url": a.get("imageUrl"),
            "click_rate": a.get("clickRate"), "conversion_rate": a.get("conversionRate"),
        } for a in (it.get("top3AsinDtoList") or [])],
    } for it in items]

    return {
        "search_model": req.search_model,
        "search_model_label": SEARCH_MODEL_LABELS[req.search_model],
        "granularity": req.granularity,
        "total": raw.get("data", {}).get("total") if isinstance(raw.get("data"), dict) else None,
        "results": results,
    }



# ---------------------------------------------------------------------------
# ROLLER & EĞİTİM / GÖREVLER
# Yetki kuralları SUNUCUDA: member yalnızca kendisine atanan dersleri görür ve
# yalnızca KENDİ tamamlamasını değiştirir (user_id her zaman oturumdan gelir).
# Owner/admin ders yönetir ve ilerlemeyi görür; rolleri yalnızca owner'lar değiştirir
# (birden fazla owner olabilir); son kalan owner düşürülemez (409).
# ---------------------------------------------------------------------------
_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}


def _validate_video_url(url: str | None) -> str | None:
    """Boş -> None. Aksi halde yalnızca https, host zorunlu, makul uzunluk."""
    from urllib.parse import urlsplit
    if url is None or not url.strip():
        return None
    url = url.strip()
    if len(url) > 500:
        raise HTTPException(422, "Video linki en fazla 500 karakter olabilir")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise HTTPException(422, "Video linki https:// ile başlamalı")
    if any(c in url for c in ' "<>\\\n\r\t'):
        raise HTTPException(422, "Video linki geçersiz karakter içeriyor")
    return url


def youtube_video_id(url: str | None) -> str | None:
    """Yalnızca bilinen YouTube host'larında ve 11 karakterlik geçerli ID'de döner; aksi halde None."""
    from urllib.parse import urlsplit, parse_qs
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    host = parts.hostname.lower()
    vid = None
    if host == "youtu.be":
        vid = parts.path.lstrip("/").split("/")[0]
    elif host in _YT_HOSTS:
        if parts.path == "/watch":
            vid = (parse_qs(parts.query).get("v") or [None])[0]
        else:
            m = re.match(r"^/(?:embed|shorts|live|v)/([^/]+)/?$", parts.path)
            vid = m.group(1) if m else None
    return vid if vid and _YT_ID_RE.match(vid) else None


def _lesson_out(row: dict, completed_at: int | None = None, staff: bool = False) -> dict:
    out = {
        "id": row["id"], "title": row["title"], "description": row.get("description") or "",
        "video_url": row.get("video_url"), "youtube_id": youtube_video_id(row.get("video_url")),
        "sort_order": row.get("sort_order", 0), "due_date": row.get("due_date"),
        "completed": completed_at is not None, "completed_at": completed_at,
    }
    if staff:
        out["assign_mode"] = row.get("assign_mode") or ("all" if row.get("assign_all") else "users")
        out["assign_all"] = out["assign_mode"] == "all"
        out["assignee_ids"] = row.get("assignee_ids", [])
        out["team_ids"] = row.get("team_ids", [])
        out["created_at"] = row.get("created_at")
        out["updated_at"] = row.get("updated_at")
    return out


class LessonIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=5000)
    video_url: str | None = Field(None, max_length=500)
    sort_order: int = Field(0, ge=0, le=10000)
    due_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    assign_all: bool = True
    # all = tüm ekipler | teams = seçilen ekip(ler) (DİNAMİK) | users = seçilen kişiler.
    # Verilmezse eski istemciler için assign_all'dan türetilir.
    assign_mode: str | None = Field(None, pattern=r"^(all|teams|users)$")
    team_ids: list[int] = Field(default_factory=list, max_length=200)
    assignee_ids: list[int] = Field(default_factory=list, max_length=500)


async def _clean_lesson(req: LessonIn) -> dict:
    data = req.model_dump()
    data["title"] = data["title"].strip()
    if not data["title"]:
        raise HTTPException(422, "Başlık boş olamaz")
    data["video_url"] = _validate_video_url(data.get("video_url"))
    if data.get("due_date"):
        try:
            datetime.strptime(data["due_date"], "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, "Son tarih geçerli bir tarih olmalı (YYYY-AA-GG)")
    else:
        data["due_date"] = None
    mode = data["assign_mode"] or ("all" if data["assign_all"] else "users")
    data["assign_mode"], data["assign_all"] = mode, mode == "all"
    if mode == "users":
        valid_ids = await db.team_member_ids()   # eğitim yalnızca ekip üyelerine atanır
        unknown = sorted(set(data["assignee_ids"]) - valid_ids)
        if unknown:
            raise HTTPException(422, f"Ekip üyesi olmayan ya da bilinmeyen kullanıcı id: {unknown}")
        if not data["assignee_ids"]:
            raise HTTPException(422, "Seçilen kişilere atama için en az bir kişi seçin")
    else:
        data["assignee_ids"] = []
    if mode == "teams":
        valid_teams = {t["id"] for t in await db.list_teams()}
        unknown = sorted(set(data["team_ids"]) - valid_teams)
        if unknown:
            raise HTTPException(422, f"Bilinmeyen ekip id: {unknown}")
        if not data["team_ids"]:
            raise HTTPException(422, "Ekip ataması için en az bir ekip seçin")
    else:
        data["team_ids"] = []
    return data


@app.get("/api/training/lessons")
async def training_lessons(user: dict = Depends(require_user)):
    """Member: yalnızca kendisine atananlar. Owner/admin: tüm dersler + atama bilgisi. Hepsinde kendi tamamlaması."""
    mine = await db.completions_for_user(user["user_id"])
    staff = user["role"] in ("owner", "admin")
    assigned = await db.list_lessons_for_user(user["user_id"])
    my_lessons = [_lesson_out(r, mine.get(r["id"])) for r in assigned]
    resp = {"role": user["role"], "my_lessons": my_lessons,
            "my_progress": {"completed": sum(1 for l in my_lessons if l["completed"]), "total": len(my_lessons)}}
    if staff:
        resp["all_lessons"] = [_lesson_out(r, mine.get(r["id"]), staff=True) for r in await db.list_lessons_all()]
    return resp


@app.post("/api/training/lessons")
async def training_create(req: LessonIn, user: dict = Depends(require_staff)):
    data = await _clean_lesson(req)
    lesson_id = await db.create_lesson(data, user["user_id"])
    return {"ok": True, "id": lesson_id}


@app.put("/api/training/lessons/{lesson_id}")
async def training_update(lesson_id: int, req: LessonIn, user: dict = Depends(require_staff)):
    if not await db.get_lesson(lesson_id):
        raise HTTPException(404, "Ders bulunamadı")
    await db.update_lesson(lesson_id, await _clean_lesson(req))
    return {"ok": True, "id": lesson_id}


@app.delete("/api/training/lessons/{lesson_id}")
async def training_delete(lesson_id: int, user: dict = Depends(require_staff)):
    if not await db.get_lesson(lesson_id):
        raise HTTPException(404, "Ders bulunamadı")
    await db.delete_lesson(lesson_id)
    return {"ok": True}


class CompletionIn(BaseModel):
    completed: bool


@app.post("/api/training/lessons/{lesson_id}/complete")
async def training_complete(lesson_id: int, req: CompletionIn, user: dict = Depends(require_user)):
    """Yalnızca kendi tamamlaması: gövdede user_id YOK, oturumdaki kullanıcı kullanılır."""
    if not await db.is_lesson_assigned(lesson_id, user["user_id"]):
        raise HTTPException(404, "Ders bulunamadı ya da size atanmamış")
    await db.set_completion(lesson_id, user["user_id"], req.completed)
    done = await db.completions_for_user(user["user_id"])
    return {"ok": True, "completed": lesson_id in done, "completed_at": done.get(lesson_id)}


@app.get("/api/training/progress")
async def training_progress(team_id: int | None = Query(None, ge=1), user: dict = Depends(require_staff)):
    """Kişi × ders tamamlama tablosu (yalnızca owner/admin). Yalnızca ŞU AN en az bir ekipte olanlar;
    team_id verilirse yalnızca o ekibin üyeleri. Atananlar DİNAMİK hesaplanır (ekibe atanan ders,
    kişinin şu anki ekiplerine göre). Tamamlama kayıtları silinmez; kişi geri eklenince yine görünür."""
    if team_id is not None and not await db.get_team(team_id):
        raise HTTPException(404, "Ekip bulunamadı")
    members = await db.team_member_ids(team_id)
    users = [u for u in await db.list_users() if u["id"] in members]
    if user["role"] != "owner":
        users = [{k: v for k, v in u.items() if k != "email"} for u in users]
    shown = [u["id"] for u in users]
    team_of = {u["id"]: set(u["team_ids"]) for u in users}

    def assignees(l):
        if l["assign_mode"] == "all":
            return shown
        if l["assign_mode"] == "teams":
            return [i for i in shown if team_of[i] & set(l["team_ids"])]
        return [i for i in shown if i in set(l["assignee_ids"])]

    lessons = await db.list_lessons_all()
    return {
        "team_id": team_id, "teams": _team_opts(await db.list_teams()),
        "users": users,
        "lessons": [{"id": l["id"], "title": l["title"], "sort_order": l["sort_order"], "due_date": l.get("due_date"),
                     "assign_mode": l["assign_mode"], "assignee_ids": assignees(l)} for l in lessons],
        "completions": [c for c in await db.all_completions() if c["user_id"] in members],
    }


@app.get("/api/users")
async def users_list(user: dict = Depends(require_staff)):
    """Atama ve rol yönetimi için kullanıcı listesi (owner/admin). Ekip adları da döner: admin ders atarken
    ekip seçebilsin (ekip YÖNETİMİ uçları yalnızca owner)."""
    users = await db.list_users()
    if user["role"] != "owner":   # e-posta yalnızca owner ekranlarında (ikincil bilgi)
        users = [{k: v for k, v in u.items() if k != "email"} for u in users]
    return {"users": users, "me": user["user_id"], "my_role": user["role"],
            "teams": _team_opts(await db.list_teams())}


class RoleIn(BaseModel):
    role: str = Field(..., pattern=r"^(owner|admin|member)$")


@app.post("/api/users/{target_id}/role")
async def users_set_role(target_id: int, req: RoleIn, user: dict = Depends(require_owner)):
    """
    Yalnızca owner'lar (rol her istekte DB'den okunur). Herkesi owner/admin/member
    yapabilir, başka bir owner'ı (ve kendini) düşürebilir — ama son kalan owner
    ve OWNER_EMAILS'teki kalıcı owner'lar düşürülemez (409).
    """
    if await db.get_user_role(target_id) is None:
        raise HTTPException(404, "Kullanıcı bulunamadı")
    try:
        await db.set_user_role(target_id, req.role)
    except db.PermanentOwnerError as e:
        raise HTTPException(409, str(e))
    except db.LastOwnerError as e:
        raise HTTPException(409, str(e))
    except db.TeamRuleError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "id": target_id, "role": await db.get_user_role(target_id),
            "self_changed": target_id == user["user_id"]}


class UserTeamsIn(BaseModel):
    team_ids: list[int] = Field(default_factory=list, max_length=200)


@app.post("/api/users/{target_id}/teams")
async def users_set_teams(target_id: int, req: UserTeamsIn, user: dict = Depends(require_owner)):
    """
    Kişinin ekiplerini verilen kümeye eşitler (çoklu ekle/çıkar; yalnızca owner). Boş küme = ekip dışı.
    Owner/admin son ekibinden çıkarılamaz (409) — önce rolü düşürülmeli. Bilinmeyen ekip 422.
    """
    try:
        await db.set_user_teams(target_id, req.team_ids)
    except LookupError:
        raise HTTPException(404, "Kullanıcı bulunamadı")
    except db.TeamRuleError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "id": target_id, "team_ids": await db.user_team_ids(target_id)}


# ---------------------------------------------------------------------------
# TOPLULUK & FORUM — giriş yapmış herkes (ekip dışı dahil). MCP çağrısı YOK.
# "Sadece ekip üyeleri" başlıkları hiçbir ekipte olmayan kişiye HİÇBİR uçta görünmez (liste, arama,
# bülten, istatistik, doğrudan link, cevap/oy/kayıt) -> 404. Kural her uçta `_visible_thread`'de.
# ---------------------------------------------------------------------------
async def _forum_viewer(user: dict) -> dict:
    st = _auth_state(user["user_id"])
    return {"id": user["user_id"], "role": user["role"], "staff": user["role"] in ("owner", "admin"),
            "can_see_team": st["in_team"] if st else await db.is_in_team(user["user_id"])}


def _forum_author(row: dict, uid_key: str = "user_id") -> dict:
    return {"id": row.get(uid_key), "name": db.display_name(row) or "—", "title": row.get("author_title") or ""}


def _excerpt(text: str, n: int = 280) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _thread_out(t: dict, cats: dict, v: dict, full: bool = False) -> dict:
    sol = t.get("solution")
    out = {"id": t["id"], "title": t["title"], "excerpt": _excerpt(t["body"]), "kind": t["kind"],
           "kind_label": forum.KINDS.get(t["kind"], t["kind"]), "visibility": t["visibility"],
           "category": {"id": t.get("category_id"), "name": cats.get(t.get("category_id"), "—")},
           "tags": t.get("tags") or [], "author": _forum_author(t),
           "created_at": t["created_at"], "last_activity_at": t["last_activity_at"], "edited_at": t.get("edited_at"),
           "reply_count": t.get("reply_count", 0), "views": t.get("views", 0), "useful": t.get("useful", 0),
           "voted": bool(t.get("voted")), "saved": bool(t.get("saved")),
           "pinned": bool(t["pinned"]), "locked": bool(t["locked"]), "solved": bool(t.get("solution_reply_id")),
           "solution": {"id": sol["id"], "author": _forum_author(sol), "excerpt": _excerpt(sol["body"], 320)} if sol else None,
           "is_mine": t["user_id"] == v["id"], "can_edit": t["user_id"] == v["id"],
           "can_delete": t["user_id"] == v["id"] or v["staff"], "can_moderate": v["staff"]}
    if full:
        out["body"] = t["body"]
    return out


async def _visible_thread(tid: int, v: dict) -> dict:
    t = await forum.get_thread(tid)
    if not forum.visible(t, v["can_see_team"]):
        raise HTTPException(404, "Başlık bulunamadı")   # kısıtlı başlığın varlığı da sızmaz
    return t


async def _visible_reply(rid: int, v: dict) -> tuple[dict, dict]:
    r = await forum.get_reply(rid)
    if not r:
        raise HTTPException(404, "Cevap bulunamadı")
    t = await forum.get_thread(r["thread_id"])
    if not forum.visible(t, v["can_see_team"]):
        raise HTTPException(404, "Cevap bulunamadı")
    return r, t


async def _all_visible(v: dict) -> tuple[list[dict], dict]:
    cats = {c["id"]: c["name"] for c in await forum.list_categories()}
    return await forum.visible_threads(v["can_see_team"], v["id"]), cats


def _default_order(t: dict):
    return (-int(bool(t["pinned"])), -t["last_activity_at"], -t["id"])


@app.get("/api/forum/meta")
async def forum_meta(user: dict = Depends(require_user)):
    """Kategoriler (görünür başlık sayılarıyla), gerçek istatistikler (toplam / çözülen başlık), son 3 bülten,
    son 7 günün katkıcıları (cevap + çözüm sayısı) ve popüler etiketler — hepsi YALNIZCA görünür başlıklardan."""
    v = await _forum_viewer(user)
    threads, cats = await _all_visible(v)
    per_cat: dict = {}
    tag_count: dict = {}
    for t in threads:
        per_cat[t.get("category_id")] = per_cat.get(t.get("category_id"), 0) + 1
        for tag in t["tags"]:
            k = tag.casefold()
            cur = tag_count.setdefault(k, {"tag": tag, "count": 0})
            cur["count"] += 1
    bulletins = sorted([t for t in threads if t["kind"] == "bulletin"], key=lambda t: (-t["created_at"], -t["id"]))[:3]
    since = int(time.time()) - forum.HOT_DAYS * 86400
    contrib = await forum.contributors_since(since, {t["id"] for t in threads})
    contrib.sort(key=lambda c: (-(c["replies"] + c["solutions"]), -c["solutions"], db.display_name(c["user"]).casefold()))
    return {
        "categories": [{"id": c["id"], "name": c["name"], "sort_order": c["sort_order"], "count": per_cat.get(c["id"], 0)}
                       for c in await forum.list_categories()],
        "stats": {"total": len(threads), "solved": sum(1 for t in threads if t.get("solution_reply_id"))},
        "bulletins": [_thread_out(t, cats, v) for t in bulletins],
        "contributors": [{"author": _forum_author(c["user"]), "replies": c["replies"], "solutions": c["solutions"]}
                         for c in contrib[:5]],
        "tags": sorted(tag_count.values(), key=lambda x: (-x["count"], x["tag"].casefold()))[:10],
        "kinds": forum.KINDS, "can_post_team": v["can_see_team"], "is_staff": v["staff"], "is_owner": v["role"] == "owner",
        "limits": {"title_min": forum.TITLE_MIN, "title_max": forum.TITLE_MAX, "body_max": forum.BODY_MAX,
                   "reply_max": forum.REPLY_MAX, "max_tags": forum.MAX_TAGS, "tag_max": forum.TAG_MAX},
    }


@app.get("/api/forum/threads")
async def forum_threads(tab: str = Query("all", pattern=r"^(all|solved|hot|bulletins|saved|mine)$"),
                        category: int | None = Query(None, ge=1), tag: str | None = Query(None, max_length=40),
                        q: str | None = Query(None, max_length=200), page: int = Query(1, ge=1, le=10000),
                        user: dict = Depends(require_user)):
    v = await _forum_viewer(user)
    threads, cats = await _all_visible(v)
    if category:
        threads = [t for t in threads if t.get("category_id") == category]
    if tag:
        k = tag.strip().lstrip("#").casefold()
        threads = [t for t in threads if any(x.casefold() == k for x in t["tags"])]
    if q and q.strip():
        needle = _tr_fold(q.strip())
        threads = [t for t in threads if needle in _tr_fold(" ".join([t["title"], t["body"], " ".join(t["tags"])]))]
    if tab == "solved":
        threads = [t for t in threads if t.get("solution_reply_id")]
    elif tab == "bulletins":
        threads = [t for t in threads if t["kind"] == "bulletin"]
    elif tab == "saved":
        threads = [t for t in threads if t["saved"]]
    elif tab == "mine":
        threads = [t for t in threads if t["user_id"] == v["id"]]
    if tab == "hot":
        score = await forum.interactions_since(int(time.time()) - forum.HOT_DAYS * 86400)
        threads = [t for t in threads if score.get(t["id"], 0) > 0]
        threads.sort(key=lambda t: (-score[t["id"]], -t["last_activity_at"], -t["id"]))
    else:
        threads.sort(key=_default_order)
    total = len(threads)
    pages = max(1, -(-total // forum.PAGE_SIZE))
    page = min(page, pages)
    chunk = threads[(page - 1) * forum.PAGE_SIZE: page * forum.PAGE_SIZE]
    return {"items": [_thread_out(t, cats, v) for t in chunk], "total": total, "page": page, "pages": pages,
            "page_size": forum.PAGE_SIZE}


@app.get("/api/forum/threads/{tid}")
async def forum_thread(tid: int, user: dict = Depends(require_user)):
    v = await _forum_viewer(user)
    await _visible_thread(tid, v)
    await forum.record_view(tid, v["id"])
    threads, cats = await _all_visible(v)
    t = next(x for x in threads if x["id"] == tid)
    replies = await forum.replies_of(tid, v["id"])
    out = _thread_out(t, cats, v, full=True)
    out.update({
        "can_reply": not t["locked"], "can_mark_solution": t["kind"] != "bulletin" and (t["user_id"] == v["id"] or v["staff"]),
        "replies": [{"id": r["id"], "body": r["body"], "author": _forum_author(r), "created_at": r["created_at"],
                     "edited_at": r.get("edited_at"), "useful": r["useful"], "voted": r["voted"],
                     "is_solution": r["id"] == t.get("solution_reply_id"), "is_mine": r["user_id"] == v["id"],
                     "can_edit": r["user_id"] == v["id"], "can_delete": r["user_id"] == v["id"] or v["staff"]}
                    for r in replies],
    })
    return out


class ThreadIn(BaseModel):
    title: str = Field(..., max_length=1000)
    body: str = Field(..., max_length=50_000)
    kind: str = Field("question", pattern=r"^(question|discussion|bulletin)$")
    category_id: int = Field(..., ge=1)
    visibility: str = Field("public", pattern=r"^(public|team)$")
    tags: list[str] = Field(default_factory=list, max_length=50)


async def _clean_thread(req: ThreadIn, v: dict) -> dict:
    try:
        d = {"title": forum.clean_text(req.title, "Başlık", forum.TITLE_MIN, forum.TITLE_MAX, single_line=True),
             "body": forum.clean_text(req.body, "Metin", 1, forum.BODY_MAX),
             "tags": forum.clean_tags(req.tags), "kind": req.kind, "visibility": req.visibility}
    except forum.ForumError as e:
        raise HTTPException(422, str(e))
    if not await forum.get_category(req.category_id):
        raise HTTPException(422, "Geçersiz kategori")
    d["category_id"] = req.category_id
    if d["visibility"] == "team" and not v["can_see_team"]:
        raise HTTPException(403, "\"Sadece ekip üyeleri\" başlığını yalnızca bir ekibe üye olanlar açabilir")
    return d


@app.post("/api/forum/threads")
async def forum_create(req: ThreadIn, user: dict = Depends(require_user)):
    v = await _forum_viewer(user)
    d = await _clean_thread(req, v)
    msg = await forum.rate_limited(v["id"], new_thread=True)
    if msg:
        raise HTTPException(429, msg)
    tid = await forum.create_thread(v["id"], d)
    return {"ok": True, "id": tid}


@app.put("/api/forum/threads/{tid}")
async def forum_update(tid: int, req: ThreadIn, user: dict = Depends(require_user)):
    """Yalnızca yazar düzenler (owner/admin silebilir, sabitler, kilitler ama başkasının metnini değiştirmez)."""
    v = await _forum_viewer(user)
    t = await _visible_thread(tid, v)
    if t["user_id"] != v["id"]:
        raise HTTPException(403, "Yalnızca yazar düzenleyebilir")
    await forum.update_thread(tid, await _clean_thread(req, v))
    return {"ok": True, "id": tid}


@app.delete("/api/forum/threads/{tid}")
async def forum_delete(tid: int, user: dict = Depends(require_user)):
    v = await _forum_viewer(user)
    t = await _visible_thread(tid, v)
    if t["user_id"] != v["id"] and not v["staff"]:
        raise HTTPException(403, "Yalnızca yazar ya da owner/admin silebilir")
    await forum.delete_thread(tid)
    return {"ok": True}


class FlagIn(BaseModel):
    value: bool


@app.post("/api/forum/threads/{tid}/pin")
async def forum_pin(tid: int, req: FlagIn, user: dict = Depends(require_staff)):
    v = await _forum_viewer(user)
    await _visible_thread(tid, v)
    await forum.set_flag(tid, "pinned", req.value)
    return {"ok": True, "pinned": req.value}


@app.post("/api/forum/threads/{tid}/lock")
async def forum_lock(tid: int, req: FlagIn, user: dict = Depends(require_staff)):
    v = await _forum_viewer(user)
    await _visible_thread(tid, v)
    await forum.set_flag(tid, "locked", req.value)
    return {"ok": True, "locked": req.value}


class ReplyIn(BaseModel):
    body: str = Field(..., max_length=50_000)


def _clean_reply(body: str) -> str:
    try:
        return forum.clean_text(body, "Cevap", 1, forum.REPLY_MAX)
    except forum.ForumError as e:
        raise HTTPException(422, str(e))


@app.post("/api/forum/threads/{tid}/replies")
async def forum_reply(tid: int, req: ReplyIn, user: dict = Depends(require_user)):
    v = await _forum_viewer(user)
    t = await _visible_thread(tid, v)
    body = _clean_reply(req.body)
    if t["locked"]:
        raise HTTPException(423, "Başlık kilitli — cevap yazılamaz")
    msg = await forum.rate_limited(v["id"], new_thread=False)
    if msg:
        raise HTTPException(429, msg)
    rid = await forum.add_reply(tid, v["id"], body)
    if rid is None:   # bu arada kilitlendi
        raise HTTPException(423, "Başlık kilitli — cevap yazılamaz")
    return {"ok": True, "id": rid}


@app.put("/api/forum/replies/{rid}")
async def forum_reply_update(rid: int, req: ReplyIn, user: dict = Depends(require_user)):
    v = await _forum_viewer(user)
    r, _ = await _visible_reply(rid, v)
    if r["user_id"] != v["id"]:
        raise HTTPException(403, "Yalnızca yazar düzenleyebilir")
    await forum.update_reply(rid, _clean_reply(req.body))
    return {"ok": True}


@app.delete("/api/forum/replies/{rid}")
async def forum_reply_delete(rid: int, user: dict = Depends(require_user)):
    v = await _forum_viewer(user)
    r, _ = await _visible_reply(rid, v)
    if r["user_id"] != v["id"] and not v["staff"]:
        raise HTTPException(403, "Yalnızca yazar ya da owner/admin silebilir")
    await forum.delete_reply(rid)
    return {"ok": True}


class SolutionIn(BaseModel):
    reply_id: int | None = None


@app.post("/api/forum/threads/{tid}/solution")
async def forum_solution(tid: int, req: SolutionIn, user: dict = Depends(require_user)):
    """Başlık sahibi ya da owner/admin bir cevabı "Çözüm" işaretler (reply_id=null kaldırır). Bültende çözüm olmaz."""
    v = await _forum_viewer(user)
    t = await _visible_thread(tid, v)
    if t["user_id"] != v["id"] and not v["staff"]:
        raise HTTPException(403, "Çözümü yalnızca başlık sahibi ya da owner/admin işaretleyebilir")
    if t["kind"] == "bulletin":
        raise HTTPException(409, "Bülten başlıklarında çözüm işaretlenmez")
    if req.reply_id is not None:
        r = await forum.get_reply(req.reply_id)
        if not r or r["thread_id"] != tid:
            raise HTTPException(422, "Cevap bu başlığa ait değil")
    await forum.set_solution(tid, req.reply_id)
    return {"ok": True, "solution_reply_id": req.reply_id}


async def _vote(target: str, target_id: int, v: dict, add: bool):
    if target == "thread":
        owner_id = (await _visible_thread(target_id, v))["user_id"]
    else:
        owner_id = (await _visible_reply(target_id, v))[0]["user_id"]
    if not add:
        await forum.remove_vote(target, target_id, v["id"])
        return {"ok": True, "voted": False}
    if owner_id == v["id"]:
        raise HTTPException(403, "Kendi içeriğine \"Faydalı\" veremezsin")
    if not await forum.add_vote(target, target_id, v["id"]):
        raise HTTPException(409, "Bu içerik için zaten \"Faydalı\" verdin")
    return {"ok": True, "voted": True}


@app.post("/api/forum/threads/{tid}/vote")
async def forum_vote_thread(tid: int, user: dict = Depends(require_user)):
    return await _vote("thread", tid, await _forum_viewer(user), True)


@app.delete("/api/forum/threads/{tid}/vote")
async def forum_unvote_thread(tid: int, user: dict = Depends(require_user)):
    return await _vote("thread", tid, await _forum_viewer(user), False)


@app.post("/api/forum/replies/{rid}/vote")
async def forum_vote_reply(rid: int, user: dict = Depends(require_user)):
    return await _vote("reply", rid, await _forum_viewer(user), True)


@app.delete("/api/forum/replies/{rid}/vote")
async def forum_unvote_reply(rid: int, user: dict = Depends(require_user)):
    return await _vote("reply", rid, await _forum_viewer(user), False)


@app.post("/api/forum/threads/{tid}/save")
async def forum_save(tid: int, user: dict = Depends(require_user)):
    v = await _forum_viewer(user)
    await _visible_thread(tid, v)
    await forum.set_saved(tid, v["id"], True)
    return {"ok": True, "saved": True}


@app.delete("/api/forum/threads/{tid}/save")
async def forum_unsave(tid: int, user: dict = Depends(require_user)):
    v = await _forum_viewer(user)
    await _visible_thread(tid, v)
    await forum.set_saved(tid, v["id"], False)
    return {"ok": True, "saved": False}


class CategoryIn(BaseModel):
    name: str | None = Field(None, max_length=200)
    sort_order: int | None = Field(None, ge=0, le=100000)


@app.post("/api/forum/categories")
async def forum_cat_create(req: CategoryIn, user: dict = Depends(require_owner)):
    try:
        return {"ok": True, "id": await forum.create_category(req.name or "")}
    except forum.ForumError as e:
        raise HTTPException(422, str(e))


@app.put("/api/forum/categories/{cid}")
async def forum_cat_update(cid: int, req: CategoryIn, user: dict = Depends(require_owner)):
    if not await forum.get_category(cid):
        raise HTTPException(404, "Kategori bulunamadı")
    try:
        await forum.update_category(cid, req.name, req.sort_order)
    except forum.ForumError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


@app.delete("/api/forum/categories/{cid}")
async def forum_cat_delete(cid: int, user: dict = Depends(require_owner)):
    if not await forum.get_category(cid):
        raise HTTPException(404, "Kategori bulunamadı")
    if not await forum.delete_category(cid):
        raise HTTPException(409, "İçinde başlık olan kategori silinemez — önce başlıkları taşıyın")
    return {"ok": True}


# ---------------------------------------------------------------------------
# PROFİL (herkes, YALNIZCA kendi profili) — ad soyad kapısından muaf (require_session)
# ---------------------------------------------------------------------------
async def require_profile_user(user: dict = Depends(require_session)) -> dict:
    if user.get("auth_disabled") or not user.get("user_id"):
        raise HTTPException(401, "Bu bölüm için kayıtlı bir hesapla giriş yapın")
    st = _auth_state(user["user_id"])
    role = st["role"] if st else await db.get_user_role(user["user_id"])
    if not role:
        raise HTTPException(401, "Kullanıcı bulunamadı — lütfen tekrar giriş yapın")
    return {**user, "role": role}


USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,28})[a-z0-9]$")
PHONE_RE = re.compile(r"^[0-9+()\-. ]{5,30}$")


class ProfileIn(BaseModel):
    first_name: str | None = Field(None, max_length=200)
    last_name: str | None = Field(None, max_length=200)
    username: str | None = Field(None, max_length=200)
    title: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=200)


@app.get("/api/profile")
async def profile_get(user: dict = Depends(require_profile_user)):
    prof = await db.get_profile(user["user_id"])
    form = await db.get_competency(user["user_id"])
    return {**prof, "role": user["role"], "needs_name": not (prof["first_name"] and prof["last_name"]),
            "competency": _form_meta(form)}


@app.put("/api/profile")
async def profile_put(req: ProfileIn, user: dict = Depends(require_profile_user)):
    """Yalnızca gönderilen alanlar değişir. Ad/soyad boş bırakılamaz; kullanıcı adı girişi DEĞİŞTİRMEZ
    (giriş e-postayla), benzersizdir (409). Doğum tarihi, kimlik no gibi kişisel veriler bilinçli olarak YOK."""
    fields: dict = {}
    sent = req.model_fields_set
    if "first_name" in sent:
        fields["first_name"] = _clean_name_part(req.first_name, "Ad")
    if "last_name" in sent:
        fields["last_name"] = _clean_name_part(req.last_name, "Soyad")
    if "username" in sent:
        u = (req.username or "").strip().lower()
        if u and not USERNAME_RE.match(u):
            raise HTTPException(422, "Kullanıcı adı 3–30 karakter olmalı; küçük harf, rakam, nokta, alt çizgi ve tire kullanılabilir")
        fields["username"] = u
    if "title" in sent:
        t = " ".join(_CTRL_RE.sub(" ", req.title or "").split())
        if len(t) > 80:
            raise HTTPException(422, "Unvan en fazla 80 karakter olabilir")
        fields["title"] = t
    if "phone" in sent:
        ph = " ".join((req.phone or "").split())
        if ph and not PHONE_RE.match(ph):
            raise HTTPException(422, "Telefon yalnızca rakam, boşluk, +, -, ( ) içerebilir (5–30 karakter)")
        fields["phone"] = ph
    try:
        await db.update_profile(user["user_id"], fields)
    except db.UsernameTakenError as e:
        raise HTTPException(409, str(e))
    return await profile_get(user)


class PasswordIn(BaseModel):
    current_password: str = Field(..., max_length=200)
    new_password: str = Field(..., max_length=200)


@app.post("/api/profile/password")
async def profile_password(req: PasswordIn, user: dict = Depends(require_profile_user)):
    """Hesap & Güvenlik: mevcut şifre doğrulanır; değişince bu oturum dışındaki tüm oturumlar kapanır."""
    if len(req.new_password) < 6:
        raise HTTPException(422, "Yeni şifre en az 6 karakter olmalı")
    if not await db.change_password(user["user_id"], req.current_password, req.new_password, user["token"]):
        raise HTTPException(403, "Mevcut şifre hatalı")
    return {"ok": True}


# ---------------------------------------------------------------------------
# YETKİNLİK FORMU — yalnızca kişinin KENDİSİ ve OWNER'lar görür (admin/diğer üyeler 403)
# ---------------------------------------------------------------------------
def _form_meta(form: dict | None) -> dict:
    prog = comp.progress((form or {}).get("answers") or {})
    return {"status": (form or {}).get("status") or "none", "updated_at": (form or {}).get("updated_at"),
            "submitted_at": (form or {}).get("submitted_at"), "progress": prog}


def _form_out(form: dict | None) -> dict:
    return {**_form_meta(form), "answers": comp.clean_answers((form or {}).get("answers") or {})}


@app.get("/api/competency/schema")
async def competency_schema(user: dict = Depends(require_user)):
    return comp.schema()


class CompetencyIn(BaseModel):
    answers: dict


@app.get("/api/competency/me")
async def competency_me(user: dict = Depends(require_user)):
    return _form_out(await db.get_competency(user["user_id"]))


@app.put("/api/competency/me")
async def competency_save(req: CompetencyIn, user: dict = Depends(require_user)):
    """Taslak kaydı (otomatik kaydetme). Cevaplar şemaya göre süzülür; durum değişmez."""
    if len(json.dumps(req.answers, ensure_ascii=False)) > 100_000:
        raise HTTPException(413, "Form çok büyük")
    await db.save_competency(user["user_id"], comp.clean_answers(req.answers))
    return _form_out(await db.get_competency(user["user_id"]))


@app.post("/api/competency/me/submit")
async def competency_submit(user: dict = Depends(require_user)):
    """Durumu "gönderildi" yapar (tarihiyle). Onay akışı YOK; gönderdikten sonra da düzenlenebilir."""
    if not await db.get_competency(user["user_id"]):
        raise HTTPException(409, "Göndermeden önce formu doldurun")
    await db.submit_competency(user["user_id"])
    return _form_out(await db.get_competency(user["user_id"]))


@app.get("/api/competency/users/{target_id}")
async def competency_of(target_id: int, user: dict = Depends(require_user)):
    """Kişinin kendisi ya da OWNER. Admin ve diğer üyeler 403 (var olup olmadığı da sızmaz)."""
    if target_id != user["user_id"] and user["role"] != "owner":
        raise HTTPException(403, "Bu formu yalnızca kişinin kendisi ve owner'lar görebilir")
    prof = await db.get_profile(target_id)
    if not prof:
        raise HTTPException(404, "Kullanıcı bulunamadı")
    return {**_form_out(await db.get_competency(target_id)),
            "user": {"id": prof["id"], "name": prof["name"], "email": prof["email"] if user["role"] == "owner" else None,
                     "title": prof["title"], "username": prof["username"], "teams": prof["teams"]}}


@app.get("/api/skills/map")
async def skills_map(team: str = Query("all", max_length=20), field: str = Query("", max_length=60),
                     min_level: int | None = Query(None, ge=1, le=5), user: dict = Depends(require_owner)):
    """
    YETENEK HARİTASI (yalnızca owner): kişi × yetkinlik maddesi (04–13 bölümleri, seviye 1–5).
    team: "all" (en az bir ekipte olanlar) | "none" (ekip dışı) | ekip id. field: "" (tüm maddeler) |
    "s:<bölüm anahtarı>" (o bölümün maddeleri) | madde anahtarı. min_level: madde seçiliyse o maddede,
    değilse gösterilen maddelerden EN AZ BİRİNDE seviye >= min_level olan kişiler.
    Haritada yalnızca formunu GÖNDERMİŞ kişiler; göndermeyenler (hiç başlamamış / taslak) ayrı listede.
    """
    all_users = await db.list_users()
    if team == "all":
        scope = [u for u in all_users if u["in_team"]]
    elif team == "none":
        scope = [u for u in all_users if not u["in_team"]]
    elif team.isdigit():
        if not await db.get_team(int(team)):
            raise HTTPException(404, "Ekip bulunamadı")
        scope = [u for u in all_users if int(team) in u["team_ids"]]
    else:
        raise HTTPException(422, "Geçersiz ekip filtresi")
    if not field:
        fields = comp.SKILL_FIELDS
    elif field.startswith("s:"):
        fields = [f for f in comp.SKILL_FIELDS if f["section_key"] == field[2:]]
    else:
        fields = [f for f in comp.SKILL_FIELDS if f["key"] == field]
    if not fields:
        raise HTTPException(422, "Bilinmeyen yetkinlik alanı")
    keys = [f["key"] for f in fields]
    team_list = await db.list_teams()
    teams = {t["id"]: t["label"] for t in team_list}
    forms = await db.all_competency()
    rows, pending = [], []
    for u in scope:
        f = forms.get(u["id"])
        person = {"id": u["id"], "name": u["name"] or u["email"], "email": u["email"],
                  "teams": [teams[t] for t in u["direct_team_ids"] if t in teams]}
        if not f or f.get("status") != "submitted":
            prog = comp.progress((f or {}).get("answers") or {})
            pending.append({**person, "status": (f or {}).get("status") or "none", "progress": prog,
                            "updated_at": (f or {}).get("updated_at")})
            continue
        levels = comp.clean_answers(f["answers"])["levels"]
        lv = {k: levels.get(k) for k in keys}
        if min_level is not None and not any((v or 0) >= min_level for v in lv.values()):
            continue
        rows.append({**person, "levels": lv, "submitted_at": f.get("submitted_at")})
    if len(keys) == 1:
        rows.sort(key=lambda r: (-(r["levels"][keys[0]] or 0), r["name"].casefold()))
    else:
        rows.sort(key=lambda r: r["name"].casefold())
    pending.sort(key=lambda r: r["name"].casefold())
    return {"fields": [{k: f[k] for k in ("key", "label", "section_no", "section_key", "section_title")} for f in fields],
            "sections": [{"key": s["key"], "no": s["no"], "title": s["title"]} for s in comp.SECTIONS
                         if any(f["type"] == "ratings" for f in s["fields"])],
            "all_fields": [{k: f[k] for k in ("key", "label", "section_key")} for f in comp.SKILL_FIELDS],
            "teams": _team_opts(team_list),
            "rows": rows, "pending": pending, "levels": comp.LEVELS,
            "filters": {"team": team, "field": field, "min_level": min_level}}


@app.post("/api/users/{target_id}/teams/{team_id}")
async def users_add_team(target_id: int, team_id: int, user: dict = Depends(require_owner)):
    """Tek ekibe ekler (alt ekibe eklenen ana ekibin de üyesi sayılır — ayrıca eklemeye gerek yok)."""
    try:
        await db.add_user_to_team(target_id, team_id)
    except LookupError:
        raise HTTPException(404, "Kullanıcı bulunamadı")
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "team_ids": await db.user_team_ids(target_id)}


@app.delete("/api/users/{target_id}/teams/{team_id}")
async def users_remove_team(target_id: int, team_id: int, user: dict = Depends(require_owner)):
    """Ekipten çıkarır; ANA ekipten çıkarılan kişi o ana ekibin tüm alt ekiplerinden de çıkar (removed listesi).
    Owner/admin son ekibinden çıkarılamaz (409)."""
    try:
        removed = await db.remove_user_from_team(target_id, team_id)
    except LookupError:
        raise HTTPException(404, "Kullanıcı bulunamadı")
    except db.TeamRuleError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "removed": removed, "team_ids": await db.user_team_ids(target_id)}


# ---------------------------------------------------------------------------
# EKİP YÖNETİMİ (yalnızca owner): ekipler, davetler
# ---------------------------------------------------------------------------
class TeamNameIn(BaseModel):
    name: str = Field(..., max_length=200)
    parent_id: int | None = Field(None, ge=1)   # verilirse alt ekip olarak oluşturulur


class TeamUpdateIn(BaseModel):
    name: str | None = Field(None, max_length=200)
    parent_id: int | None = Field(None, ge=1)   # GÖNDERİLİRSE taşır (null = ana ekip yap)


def _team_opts(teams: list[dict]) -> list[dict]:
    """Filtre/seçim listeleri için ağaç sıralı ekipler: label = "Ana › Alt"."""
    return [{"id": t["id"], "name": t["label"], "short": t["name"], "parent_id": t.get("parent_id")} for t in teams]


async def _team_or_404(team_id: int) -> dict:
    team = await db.get_team(team_id)
    if not team:
        raise HTTPException(404, "Ekip bulunamadı")
    return team


@app.get("/api/teams")
async def teams_list(user: dict = Depends(require_owner)):
    teams = await db.list_teams()
    for t in teams:
        # silme onayı için: yalnızca bu ekibe atanmış dersler ve ekip silinince ekipsiz kalacak owner/admin'ler
        t["exclusive_lessons"] = await db.lessons_only_for_team(t["id"])
        t["delete_blockers"] = await db.staff_only_in_team(t["id"])
    return {"teams": teams, "outsiders": len([u for u in await db.list_users() if not u["in_team"]])}


@app.post("/api/teams")
async def teams_create(req: TeamNameIn, user: dict = Depends(require_owner)):
    """Ana ekip ya da (parent_id ile) alt ekip. Yalnızca iki seviye: alt ekibin altına ekip açılamaz (409)."""
    try:
        tid = await db.create_team(req.name, req.parent_id)
    except db.TeamNameError as e:
        raise HTTPException(422, str(e))
    except db.TeamRuleError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "id": tid}


@app.put("/api/teams/{team_id}")
async def teams_update(team_id: int, req: TeamUpdateIn, user: dict = Depends(require_owner)):
    """Yeniden adlandır ve/veya taşı. parent_id alanı gönderilirse taşınır (null = ana ekip yap). Alt ekipleri olan
    ana ekip başka ekibin altına taşınamaz; hedef bir alt ekip olamaz (409)."""
    await _team_or_404(team_id)
    move = "parent_id" in req.model_fields_set
    try:
        await db.update_team(team_id, name=req.name, move=move, parent_id=req.parent_id)
    except db.TeamNameError as e:
        raise HTTPException(422, str(e))
    except db.TeamRuleError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "id": team_id}


@app.delete("/api/teams/{team_id}")
async def teams_delete(team_id: int, user: dict = Depends(require_owner)):
    """Kullanıcılar ve verileri SİLİNMEZ; yalnızca üyelikler, ders-ekip atamaları ve ekibin davetleri
    kalkar. Alt ekipleri olan ana ekip ya da bir owner/admin'in tek ekibi silinemez (409)."""
    await _team_or_404(team_id)
    try:
        await db.delete_team(team_id)
    except db.TeamRuleError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


class InviteIn(BaseModel):
    team_id: int = Field(..., ge=1)
    multi_use: bool = False
    days: int = Field(7, ge=1, le=90)


@app.get("/api/invites")
async def invites_list(user: dict = Depends(require_owner)):
    """Aktif (iptal edilmemiş, süresi dolmamış, kullanım hakkı kalan) davetler. Kod saklanmaz/dönmez."""
    return {"invites": await db.list_active_invites()}


@app.post("/api/invites")
async def invites_create(req: InviteIn, user: dict = Depends(require_owner)):
    """Belirli bir EKİP için davet. Düz kod YALNIZCA bu yanıtta döner; sunucuda sha256 saklanır."""
    await _team_or_404(req.team_id)
    inv = await db.create_invite(req.team_id, user["user_id"], req.multi_use, req.days)
    return {"ok": True, **inv}


@app.post("/api/invites/{invite_id}/revoke")
async def invites_revoke(invite_id: int, user: dict = Depends(require_owner)):
    if not await db.revoke_invite(invite_id):
        raise HTTPException(404, "Davet bulunamadı ya da zaten iptal edilmiş")
    return {"ok": True}


class DeleteUserIn(BaseModel):
    confirm_email: str = Field(..., max_length=320)


@app.post("/api/users/{target_id}/delete")
async def users_delete(target_id: int, req: DeleteUserIn, user: dict = Depends(require_owner)):
    """
    Hesabı KALICI olarak siler (yalnızca owner, geri alınamaz): hesap, oturumlar, eşikler, arama geçmişi,
    kararlar, kendi kontrol listeleri, eğitim tamamlamaları. Başkalarının kayıtlarındaki referanslar
    silinmez, kişi alanı boşaltılır (panelde "—"). Onay için hesabın e-postası yazılmalı (422).
    Kendi hesabı, kalıcı owner (OWNER_EMAILS) ve son owner silinemez (409).
    """
    target = next((u for u in await db.list_users() if u["id"] == target_id), None)
    if not target:
        raise HTTPException(404, "Kullanıcı bulunamadı")
    if (req.confirm_email or "").strip().lower() != (target["email"] or "").strip().lower():
        raise HTTPException(422, "Onay için hesabın e-posta adresini aynen yazın")
    if target_id == user["user_id"]:
        raise HTTPException(409, "Kendi hesabınızı silemezsiniz")
    if target["permanent"]:
        raise HTTPException(409, "Kalıcı owner (OWNER_EMAILS) silinemez")
    if target["role"] == "owner" and await db.owner_count() <= 1:
        raise HTTPException(409, "Son kalan owner silinemez — önce başka birini owner yapın")
    result = await db.delete_user_completely(target_id)
    return {"ok": True, "id": target_id, "deleted_checklists": result["deleted_checklists"]}



# ---------------------------------------------------------------------------
# ARAŞTIRMA KONTROL LİSTESİ
# - Liste, son kaydedilen analizin (keyword_analysis log kaydı) ANLIK KOPYASIYLA
#   oluşturulur; sonradan MCP çağrısı yapılmaz. İstemcinin gönderdiği değerlere
#   güvenilmez: otomatik maddeler sunucudaki snapshot'tan değerlendirilir.
# - Görünürlük: member yalnızca kendi listeleri (başkasınınkine 404), owner/admin tümü.
# - Kilitli liste sunucuda değiştirilemez (423). Kilitleme: owner/admin, tüm maddeler tamamsa.
# ---------------------------------------------------------------------------
async def _effective_template() -> tuple[dict, dict | None]:
    rec = await db.get_checklist_template()
    if rec:
        try:
            return ckl.validate_template(rec["template"]), rec
        except ckl.TemplateError:
            pass  # bozuk kayıt -> varsayılana düş
    return ckl.default_template(), None


def _person(names: dict | None, email: str | None) -> str | None:
    """Kayıtta e-postayla anılan kişinin "Ad Soyad"ı. Kişi yoksa/adı girilmemişse "—" (e-posta asla dönmez);
    alan zaten boşsa (silinmiş hesap) None -> panelde "—"."""
    if not email:
        return None
    return (names or {}).get(email.strip().lower()) or "—"


def _template_out(tpl: dict, rec: dict | None, names: dict | None = None) -> dict:
    th = tpl["thresholds"]
    stages = [{**s, "items": [{**it, "text": ckl.auto_text(it["auto"], th)} if it.get("auto") else it for it in s["items"]]}
              for s in tpl["stages"]]
    return {"template": {**tpl, "stages": stages}, "is_default": rec is None,
            "updated_at": rec["updated_at"] if rec else None, "updated_by": _person(names, rec["updated_by"]) if rec else None,
            "threshold_limits": {k: {"min": lo, "max": hi, "integer": is_int} for k, (lo, hi, is_int) in ckl.THRESHOLD_LIMITS.items()}}


def _checklist_out(cl: dict, items: list[dict], user: dict, detail: bool = True, events: list | None = None,
                   names: dict | None = None) -> dict:
    staff = user["role"] in ("owner", "admin")
    is_open = cl["status"] == "open"
    values = (cl.get("snapshot") or {}).get("values") or {}
    th = cl.get("thresholds") or ckl.DEFAULT_THRESHOLDS
    critical_stages = {s["key"] for s in cl.get("stages") or [] if s.get("critical")}
    # Gerekçeyle geçilen otomatik maddeler yalnızca KİLİTLİ listede geçerli (kilit açılınca sıfırlanır)
    overridden = set() if is_open else {o.get("item_id") for o in cl.get("overridden") or []}
    out_items, done, critical_open, manual_open, auto_unpassed = [], 0, 0, 0, []
    for it in items:
        o = {"id": it["id"], "stage_key": it["stage_key"], "kind": it["kind"], "text": it["text"],
             "checked_by": _person(names, it.get("checked_by")), "checked_at": it.get("checked_at"), "note": it.get("note") or "",
             "created_by": _person(names, it.get("created_by"))}
        if it["kind"] == "auto":
            ev = ckl.evaluate_auto(it.get("auto_key"), values, th)
            ov = ev["status"] != "pass" and it["id"] in overridden
            o.update({"auto_key": it.get("auto_key"), "auto_status": ev["status"], "auto_display": ev["display"],
                      "auto_value": ev["value"], "auto_override": ov,
                      "checked": ev["status"] == "pass" or ov, "checked_by": None, "checked_at": None})
            if ev["status"] != "pass":
                auto_unpassed.append({"item_id": it["id"], "auto_key": it.get("auto_key"), "text": it["text"],
                                      "status": ev["status"], "display": ev["display"]})
        else:
            o["checked"] = bool(it.get("checked"))
            manual_open += not o["checked"]
        done += o["checked"]
        if not o["checked"] and it["stage_key"] in critical_stages:
            critical_open += 1
        out_items.append(o)
    total = len(out_items)
    complete = total > 0 and done == total
    manual_complete = total > 0 and manual_open == 0
    can_edit = is_open and (staff or cl["user_id"] == user["user_id"])
    out = {
        "id": cl["id"], "title": cl.get("title"), "analysis_key": cl["analysis_key"], "marketplace": cl["marketplace"],
        "owner_name": _person(names, cl.get("user_email")), "is_mine": cl["user_id"] == user["user_id"],
        "status": cl["status"], "locked_by": _person(names, cl.get("locked_by")), "locked_at": cl.get("locked_at"),
        "approval_reason": None if is_open else cl.get("approval_reason"),
        "overridden_count": 0 if is_open else len(cl.get("overridden") or []),
        "analysis_fetched_at": (cl.get("snapshot") or {}).get("fetched_at"),
        "created_at": cl["created_at"], "updated_at": cl["updated_at"],
        "progress": {"done": done, "total": total, "critical_open": critical_open, "complete": complete,
                     "manual_open": manual_open, "manual_complete": manual_complete},
        "can_edit": can_edit, "can_delete": can_edit,
        # Manuel/özel maddeler tamamsa onaylanabilir; geçmeyen/veri olmayan otomatik madde varsa gerekçe zorunlu
        "can_lock": staff and is_open and manual_complete,
        "needs_reason": is_open and bool(auto_unpassed),
        "auto_unpassed": auto_unpassed if is_open else [],
        "can_unlock": user["role"] == "owner" and not is_open,
    }
    if detail:
        snap = cl.get("snapshot") or {}
        out.update({"stages": cl.get("stages") or [], "thresholds": th, "items": out_items,
                    "snapshot": {k: snap.get(k) for k in ("keyword", "marketplace", "analysis_mode", "fetched_at", "category")}
                    | {"values": values},
                    "events": [{"kind": e["kind"], "by": _person(names, e.get("by_email")), "at": e["at"], "reason": e.get("reason"),
                                "overridden": [{"text": x.get("text"), "status": x.get("status"), "display": x.get("display")}
                                               for x in ((e.get("details") or {}).get("overridden") or [])]}
                               for e in events or []]})
    return out


async def _get_checklist_for(cid: int, user: dict) -> dict:
    cl = await db.get_checklist(cid)
    # owner/admin başkalarının listelerini yalnızca liste sahibi ŞU AN ekipteyse görür
    visible = cl and (cl["user_id"] == user["user_id"]
                      or (user["role"] in ("owner", "admin") and await db.is_in_team(cl["user_id"])))
    if not visible:
        raise HTTPException(404, "Kontrol listesi bulunamadı")  # member başkasının listesinin varlığını bile göremez
    return cl


async def _get_editable(cid: int, user: dict) -> dict:
    cl = await _get_checklist_for(cid, user)
    if cl["status"] != "open":
        raise HTTPException(423, "Liste onaylanıp kilitlendi — değiştirilemez")
    return cl


@app.get("/api/checklists/template")
async def checklist_template_get(user: dict = Depends(require_user)):
    tpl, rec = await _effective_template()
    return _template_out(tpl, rec, await db.email_name_map())


@app.put("/api/checklists/template")
async def checklist_template_put(payload: dict, user: dict = Depends(require_owner)):
    """Yalnızca owner. Aşamalar ve otomatik maddeler sabit; başlıklar, manuel maddeler ve eşikler düzenlenir.
    Mevcut listeler etkilenmez (her liste oluşturulduğu andaki şablonun kopyasını taşır)."""
    try:
        tpl = ckl.validate_template(payload.get("template") if isinstance(payload, dict) else None)
    except ckl.TemplateError as e:
        raise HTTPException(422, str(e))
    await db.save_checklist_template(tpl, user["email"])
    tpl, rec = await _effective_template()
    return _template_out(tpl, rec, await db.email_name_map())


@app.post("/api/checklists/template/reset")
async def checklist_template_reset(user: dict = Depends(require_owner)):
    await db.reset_checklist_template()
    tpl, rec = await _effective_template()
    return _template_out(tpl, rec, await db.email_name_map())


class ChecklistCreate(BaseModel):
    analysis_key: str = Field(..., min_length=1, max_length=300)
    marketplace: str = Field(..., pattern=r"^[A-Z]{2}$")


@app.post("/api/checklists")
async def checklist_create(req: ChecklistCreate, user: dict = Depends(require_user)):
    rec = await db.get_analysis_record(req.analysis_key, req.marketplace)
    if not rec:
        raise HTTPException(404, "Bu ürün için kayıtlı analiz bulunamadı — önce Ürün Analizi'ni çalıştırın")
    payload = {**rec["payload"], "_analysis_key": req.analysis_key}
    snapshot = ckl.extract_snapshot(payload, rec["fetched_at"])
    tpl, _ = await _effective_template()
    th = tpl["thresholds"]
    asin_title = ((payload.get("asin_info") or {}).get("title") or "") if payload.get("analysis_mode") == "asin" else ""
    title = (asin_title or str(payload.get("keyword") or req.analysis_key)).strip()[:200]
    items, order = [], 0
    for st in tpl["stages"]:
        for it in st["items"]:
            order += 1
            if it.get("auto"):
                items.append({"stage_key": st["key"], "item_order": order, "kind": "auto", "auto_key": it["auto"],
                              "text": ckl.auto_text(it["auto"], th)})
            else:
                items.append({"stage_key": st["key"], "item_order": order, "kind": "manual", "text": it["text"]})
    stages = [{k: s[k] for k in ("key", "title", "subtitle", "critical")} for s in tpl["stages"]]
    cid = await db.create_checklist(user["user_id"], user["email"], req.analysis_key, req.marketplace, title,
                                    snapshot, stages, th, items)
    return await _checklist_detail(cid, user)


@app.get("/api/checklists")
async def checklist_list(user: dict = Depends(require_user)):
    staff = user["role"] in ("owner", "admin")
    lists = await db.list_checklists(None if staff else user["user_id"])
    if staff:   # başkalarının listeleri yalnızca sahibi ekipteyse
        team = await db.team_member_ids()
        lists = [c for c in lists if c["user_id"] == user["user_id"] or c["user_id"] in team]
    items = await db.items_for_checklists([c["id"] for c in lists])
    names = await db.email_name_map()
    return {"role": user["role"], "checklists": [_checklist_out(c, items.get(c["id"], []), user, detail=False, names=names) for c in lists]}


@app.get("/api/checklists/{cid}")
async def checklist_get(cid: int, user: dict = Depends(require_user)):
    await _get_checklist_for(cid, user)
    return await _checklist_detail(cid, user)


class ChecklistItemUpdate(BaseModel):
    checked: bool
    note: str | None = Field(None, max_length=1000)


@app.post("/api/checklists/{cid}/items/{item_id}")
async def checklist_item_update(cid: int, item_id: int, req: ChecklistItemUpdate, user: dict = Depends(require_user)):
    """Yalnızca manuel/özel maddeler; otomatik maddeler snapshot'tan hesaplanır, elle işaretlenemez."""
    await _get_editable(cid, user)
    items = {i["id"]: i for i in await db.checklist_items(cid)}
    it = items.get(item_id)
    if not it:
        raise HTTPException(404, "Madde bulunamadı")
    if it["kind"] == "auto":
        raise HTTPException(400, "Otomatik maddeler analiz verisinden hesaplanır, elle işaretlenemez")
    note = (req.note or "").strip() or None
    if not await db.set_item_state(cid, item_id, req.checked, note, user["email"]):
        raise HTTPException(423, "Liste onaylanıp kilitlendi — değiştirilemez")
    return await _checklist_detail(cid, user)


class ChecklistCustomItem(BaseModel):
    stage_key: str = Field(..., pattern=r"^(market|competition|defects|legal|costs)$")
    text: str = Field(..., min_length=1, max_length=300)


@app.post("/api/checklists/{cid}/items")
async def checklist_item_add(cid: int, req: ChecklistCustomItem, user: dict = Depends(require_user)):
    await _get_editable(cid, user)
    text = req.text.strip()
    if not text:
        raise HTTPException(422, "Madde metni boş olamaz")
    if await db.add_custom_item(cid, req.stage_key, text, user["email"]) is None:
        raise HTTPException(423, "Liste onaylanıp kilitlendi — değiştirilemez")
    return await _checklist_detail(cid, user)


@app.delete("/api/checklists/{cid}/items/{item_id}")
async def checklist_item_delete(cid: int, item_id: int, user: dict = Depends(require_user)):
    """Yalnızca özel (kullanıcının eklediği) maddeler silinebilir."""
    await _get_editable(cid, user)
    it = next((i for i in await db.checklist_items(cid) if i["id"] == item_id), None)
    if not it:
        raise HTTPException(404, "Madde bulunamadı")
    if it["kind"] != "custom":
        raise HTTPException(400, "Yalnızca özel eklenen maddeler silinebilir")
    if not await db.delete_custom_item(cid, item_id):
        raise HTTPException(423, "Liste onaylanıp kilitlendi — değiştirilemez")
    return await _checklist_detail(cid, user)


class ChecklistReasonIn(BaseModel):
    reason: str | None = Field(None, max_length=1000)


CK_REASON_MIN = 5


def _clean_reason(reason: str | None) -> str:
    return " ".join((reason or "").split())


async def _checklist_detail(cid: int, user: dict) -> dict:
    return _checklist_out(await db.get_checklist(cid), await db.checklist_items(cid), user,
                          events=await db.checklist_events(cid), names=await db.email_name_map())


@app.post("/api/checklists/{cid}/lock")
async def checklist_lock(cid: int, body: ChecklistReasonIn | None = None, user: dict = Depends(require_staff)):
    """Owner/admin onaylayıp kilitler. Manuel/özel maddelerin hepsi işaretli olmalı. Geçmeyen ya da
    "Veri yok" olan otomatik madde varsa gerekçe ZORUNLU; gerekçe, onaylayan ve zaman saklanır."""
    cl = await _get_editable(cid, user)
    out = _checklist_out(cl, await db.checklist_items(cid), user)
    p = out["progress"]
    if not p["manual_complete"]:
        raise HTTPException(409, f"Manuel maddeler tamamlanmadan onaylanamaz ({p['manual_open']} açık madde)")
    reason = _clean_reason(body.reason if body else None)
    if out["needs_reason"] and len(reason) < CK_REASON_MIN:
        raise HTTPException(422, f"{len(out['auto_unpassed'])} otomatik madde geçmedi ya da veri yok — "
                                 f"onay için gerekçe zorunlu (en az {CK_REASON_MIN} karakter)")
    overridden = out["auto_unpassed"]
    locked_at = await db.lock_checklist(cid, user["email"], reason or None, overridden)
    if not locked_at:
        raise HTTPException(423, "Liste zaten kilitli")
    # Yarış kontrolü: kilitleme anında bir manuel madde geri alındıysa / madde eklendiyse kilidi kaldır
    after = _checklist_out(await db.get_checklist(cid), await db.checklist_items(cid), user)
    if not after["progress"]["complete"]:
        await db.revert_lock(cid, locked_at)
        raise HTTPException(409, "Kilitleme sırasında bir madde değişti — tekrar deneyin")
    await db.record_lock_event(cid, user["email"], locked_at, reason or None, overridden)
    return await _checklist_detail(cid, user)


@app.post("/api/checklists/{cid}/unlock")
async def checklist_unlock(cid: int, body: ChecklistReasonIn | None = None, user: dict = Depends(require_owner)):
    """Yalnızca owner, gerekçe yazarak kilitli listeyi açar. Kim/ne zaman/neden ve önceki onay olaya yazılır."""
    cl = await _get_checklist_for(cid, user)
    if cl["status"] != "locked":
        raise HTTPException(409, "Liste kilitli değil")
    reason = _clean_reason(body.reason if body else None)
    if len(reason) < CK_REASON_MIN:
        raise HTTPException(422, f"Kilidi açmak için gerekçe zorunlu (en az {CK_REASON_MIN} karakter)")
    if not await db.unlock_checklist(cid, user["email"], reason):
        raise HTTPException(409, "Liste aynı anda değişti — tekrar deneyin")
    return await _checklist_detail(cid, user)


@app.delete("/api/checklists/{cid}")
async def checklist_delete(cid: int, user: dict = Depends(require_user)):
    await _get_editable(cid, user)
    if not await db.delete_checklist(cid):
        raise HTTPException(423, "Liste onaylanıp kilitlendi — silinemez")
    return {"ok": True}


# ---------------------------------------------------------------------------
# LANSMAN RAPORU (amazon-urun-lansman-raporu skill metodolojisi — api/launch_report.py)
# MCP çağrısı YOK; tüm girdiler formdan gelir. Rapor HTML döner, panel yeni sekmede
# açar ve tarayıcıdan "PDF olarak kaydet" ile yazdırılır (weasyprint Vercel'de yok).
# ---------------------------------------------------------------------------
class LRVariation(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    price: float = Field(..., gt=0, le=100000)
    cogs: float = Field(..., ge=0, le=100000)
    fba: float = Field(..., ge=0, le=10000)
    units: int = Field(..., ge=0, le=1_000_000)
    share: float | None = Field(None, ge=0, le=100)   # sepet ağırlığı (%); boşsa sevkiyat payı


class LRSecondVine(BaseModel):
    name: str = Field("Kanada (CA)", min_length=1, max_length=60)
    total: int = Field(..., ge=1, le=100_000)
    enrolled: int | None = Field(None, ge=1, le=100_000)
    from_main_batch: bool = False


class LRKeyword(BaseModel):
    monthly_purchases: float | None = Field(None, ge=0)
    monthly_clicks: float | None = Field(None, ge=0)
    bid: float | None = Field(None, ge=0, le=1000)
    market_avg_price: float | None = Field(None, ge=0, le=100000)


class LaunchReportIn(BaseModel):
    product_name: str = Field(..., min_length=1, max_length=120)
    main_keyword: str = Field(..., min_length=1, max_length=200)
    variations: list[LRVariation] = Field(..., min_length=1, max_length=lr.MAX_VARIATIONS)
    ads_index: int = Field(0, ge=0, lt=lr.MAX_VARIATIONS)
    referral_pct: float = Field(15, ge=0, lt=100)
    budget_mode: str = Field("acos", pattern=r"^(daily|acos)$")
    daily_budget: float | None = Field(None, gt=0, le=1_000_000)
    target_acos_pct: float | None = Field(11, gt=0, le=100)
    us_vine_units: int = Field(..., ge=1, le=100_000)
    second_vine: LRSecondVine | None = None
    vine_fee: float = Field(200, ge=0, le=100_000)
    return_pct: float = Field(5, ge=0, lt=100)
    cc_pct: float = Field(15, ge=0, lt=100)
    cc_mode: str = Field("scenarios", pattern=r"^(expected|scenarios)$")
    cc_expected: int | None = Field(None, ge=0, le=1_000_000)
    cc_low: int = Field(30, ge=0, le=1_000_000)
    cc_mid: int = Field(80, ge=0, le=1_000_000)
    campaign_days: int = Field(60, ge=1, le=365)
    keyword: LRKeyword = Field(default_factory=LRKeyword)


@app.post("/api/launch-report")
async def launch_report(req: LaunchReportIn, user: dict = Depends(require_auth)):
    try:
        html, _, _ = lr.generate(req.model_dump())
    except lr.ReportError as e:
        raise HTTPException(422, str(e))
    return Response(content=html, media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------------------
# EKİP AKTİVİTESİ — yalnızca owner (admin/member 403). SALT OKUNUR: yazma ucu yok;
# karar silme/temizleme uçları zaten yalnızca isteği yapanın KENDİ user_id'siyle çalışır.
# MCP çağrısı yok; kayda tıklamak panelde runAnalysis ile CANLI yeni analiz başlatır.
# ---------------------------------------------------------------------------
TEAM_DECISIONS = ("Uygun", "Sınırda", "Elenmiş")
TEAM_PAGE_SIZE = 50   # Ekip Aktivitesi listeleri sunucudan sayfalı gelir


def _page_slice(rows: list, page: int, size: int):
    """(sayfa bilgisi, o sayfanın satırları). Sayfa aralık dışındaysa son sayfaya çekilir."""
    pages = max(1, -(-len(rows) // size))
    page = min(max(1, page), pages)
    return {"page": page, "pages": pages}, rows[(page - 1) * size: page * size]


def _tr_fold(s: str) -> str:
    return (s or "").replace("I", "ı").replace("İ", "i").lower()


def _verdict_for(index: dict, uid, kw, mp, at):
    """Kararın ön önerisi: aynı kişinin aynı keyword/pazar için karar anına kadarki SON sorgusunun önerisi
    (karardan önce sorgu yoksa en son sorgusununki)."""
    rows = index.get((uid, kw, mp)) or []
    before = [r for r in rows if r[0] <= at]
    pick = (before or rows)[-1] if rows else None
    return pick[1] if pick else None


@app.get("/api/team/activity")
async def team_activity(user_id: int | None = Query(None, ge=0), team_id: int | None = Query(None, ge=1),
                        since: int | None = Query(None, ge=0), until: int | None = Query(None, ge=0),
                        decision: str | None = Query(None), q: str | None = Query(None, max_length=200),
                        q_page: int = Query(1, ge=1), d_page: int = Query(1, ge=1),
                        user: dict = Depends(require_owner)):
    if decision and decision not in TEAM_DECISIONS:
        raise HTTPException(422, "Geçersiz karar filtresi")
    # Yalnızca ŞU AN ekipte olanlar: ekip dışı / çıkarılmış kişilerin (ve oturumsuz kullanımın) kayıtları
    # hiç görünmez; tekrar ekibe eklenince kayıtları silinmediği için geri gelir.
    # team_id verilirse yalnızca o ekibin üyeleri; yoksa "Tüm ekipler" = en az bir ekipte olan herkes.
    if team_id is not None:
        await _team_or_404(team_id)
    members = await db.team_member_ids(team_id)
    users = [u for u in await db.list_users() if u["id"] in members]
    emails = {u["id"]: u["email"] for u in users}
    names = {u["id"]: u["name"] for u in users}   # panelde "Ad Soyad"; e-posta yalnızca ikincil bilgi (owner ekranı)
    team = set(emails)
    teams = await db.list_teams()
    team_names = {t["id"]: t["label"] for t in teams}
    queries = [r for r in await db.team_queries(user_id, since, until) if r["user_id"] in team]
    decisions = [r for r in await db.team_decisions(user_id, since, until, decision) if r["user_id"] in team]
    if q and q.strip():
        needle = _tr_fold(q.strip())
        queries = [r for r in queries if needle in _tr_fold(r["keyword"])]
        decisions = [r for r in decisions if needle in _tr_fold(r["keyword"])]

    # SAYFALAMA (sunucuda): listeler sayfa sayfa döner; toplamlar/özet tüm filtrelenmiş kümeden.
    q_pg, q_rows = _page_slice(queries, q_page, TEAM_PAGE_SIZE)
    d_pg, d_rows = _page_slice(decisions, d_page, TEAM_PAGE_SIZE)

    # Ön öneri yalnızca bu sayfadaki kararlar için (onların keyword'lerinin sorgu geçmişinden) hesaplanır.
    vindex: dict = {}
    for r in await db.team_verdict_log([r["keyword"] for r in d_rows]):
        vindex.setdefault((r["user_id"], r["keyword"], r["marketplace"]), []).append((r["queried_at"], r["verdict"]))

    def who(uid, email):
        return names.get(uid) or emails.get(uid) or "—"

    q_out = [{"id": r["id"], "user_id": r["user_id"], "user": who(r["user_id"], r.get("email")), "email": emails.get(r["user_id"]),
              "keyword": r["keyword"], "marketplace": r["marketplace"], "at": r["queried_at"],
              "verdict": r.get("verdict")} for r in q_rows]
    d_out = [{"id": r["id"], "user_id": r["user_id"], "user": who(r["user_id"], r.get("email")), "email": emails.get(r["user_id"]),
              "keyword": r["keyword"], "marketplace": r["marketplace"], "at": r["decided_at"],
              "decision": r["decision"], "note": r.get("note") or "",
              "verdict": _verdict_for(vindex, r["user_id"], r["keyword"], r["marketplace"], r["decided_at"])}
             for r in d_rows]

    # Özet: seçili tarih aralığı (since/until), aralık yoksa TÜM ZAMANLAR. Kişi/karar/arama filtreleri özete uygulanmaz.
    counts = await db.team_counts(since, until)
    summary = [{"user_id": u["id"], "user": u["name"] or u["email"], "email": u["email"], "role": u["role"],
                "teams": [team_names[t] for t in u["direct_team_ids"] if t in team_names],
                "queries": counts["queries"].get(u["id"], 0), "decisions": counts["decisions"].get(u["id"], 0)}
               for u in users]

    return {"range": {"since": since, "until": until}, "team_id": team_id,
            "teams": _team_opts(teams),
            "users": [{"id": u["id"], "name": u["name"] or u["email"], "email": u["email"], "role": u["role"]} for u in users],
            "summary": summary,
            "queries": q_out, "decisions": d_out,
            "totals": {"queries": len(queries), "decisions": len(decisions)},
            "page_size": TEAM_PAGE_SIZE, "pages": {"queries": q_pg, "decisions": d_pg}}

