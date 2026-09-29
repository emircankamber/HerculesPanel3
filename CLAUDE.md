# CLAUDE.md — PL Pazar Paneli (SellerSprite MCP Panel)

Bu dosyayı Claude Code her oturumda otomatik okur. Buradaki bilgiler **gerçek
MCP çağrılarıyla test edilerek** bulundu — çoğu tahminle yazılsaydı yanlış
çıkardı (bkz. "Test metodolojisi" — aynı hatayı tekrarlama).

## Mimari (tek cümle)

Vanilla HTML/JS/CSS frontend + tek dosyalık FastAPI backend (`api/index.py`),
Vercel serverless'te çalışıyor, Neon Postgres (paylaşımlı/kalıcı veri),
SellerSprite MCP'ye backend'in kendi `mcp_client.py`'si üzerinden (Claude
aracı olmadan, doğrudan `mcp` Python SDK ile) bağlanıyor.

```
index.html/app.js  →  api/index.py (FastAPI)  →  mcp_client.py  →  SellerSprite MCP
                              ↓
                       database.py → Postgres (Neon) / SQLite (yerel)
```

## KRİTİK: SellerSprite MCP'nin gerçek davranışı (tahmin ETME, burada yazılı)

### 1. Parametre sarmalama — tool'a göre DEĞİŞİYOR
Çoğu tool parametreleri `{"request": {...}}` içine sarılı bekliyor:
`product_node`, `keyword_miner`, `market_research_statistics`,
`market_brand_concentration`, `market_price_distribution`,
`market_listing_date_distribution`, `market_product_demand_trend`,
`competitor_lookup`.

**AMA `keyword_research_trends` sarmalsız (düz parametre) bekliyor.** Bunu
hep-sarma varsayımıyla çağırmak sessizce boş sonuç döndürüyordu (uzun süre
"Search Volume Trend grafiği boş" hatasının kök sebebiydi). `mcp_client.py`'deki
`call_tool(..., wrap_in_request=False)` parametresi bunun için var.
**Yeni bir tool eklerken önce gerçek bir çağrıyla şemasını doğrula, sarmalı
olup olmadığını asla varsayma.**

### 2. `returnFields` parametresi GÜVENİLMEZ
`market_research_statistics` ve `competitor_lookup`'ta kullanılınca istenen
alanları `null` döndürüyor. **Hiçbir çağrıda kullanma** — tam objeyi çek,
Python tarafında filtrele.

### 3. `keyword` vs `keywordList` (keyword_miner) — FARKLI ŞEYLER
- `keyword`: GENİŞ eşleşme (ilişkili kelimeleri de döner) — genişletme
  tablosu ("Relevant Keywords") için doğru.
- `keywordList`: TAM eşleşme (yalnızca verdiğin kelimenin kendi verisi) —
  ana keyword'ün kendi metrikleri (PPC bloğu, ön değerlendirme) için doğru.
  Bunu karıştırmak "geniş eşleşmeden gelen yanlış bid/CVR" hatasına yol açar.

### 4. Kategori bulma: TAHMİN ETME, gerçek rakip verisini kullan
`product_node` + metin eşleştirme (`resolve_category_node`) **yedek yöntem**
— birden fazla makul kategori arasında yanlış seçim yapabiliyor (gerçek
örnekler: "mini magnetic tiles" → yanlışlıkla Tools&Home seçiliyordu, olması
gereken Toys&Games; "samsung water filter" → Appliances yerine yanlış
Tools&Home altkategori seçiliyordu).

**Birincil yöntem:** `competitor_lookup(keyword=..., matchType=3, size=10,
order=total_units desc)` — o keyword için GERÇEKTEN satan ürünlerin KENDİ
kategorisini kullan (`resolve_category_from_competitors`). `matchType=3`
(tam başlık eşleşme) uzun/spesifik keyword'lerde sıfır sonuç dönebilir —
o zaman `matchType=1` (kelime grubu) dene. İkisi de boş dönerse ancak o
zaman `product_node` tahminine düş.

### 5. Top Rakipler tablosu — başlık eşleşmesiyle ÇEKME
`matchType=3` ile rakip çekmek (kategori bulma amacıyla) ürünün orijinal
üreticisini (örn. Samsung'un kendi filtresi) DIŞLAR çünkü başlığında arama
kelimesi birebir geçmiyor olabilir ("SAMSUNG Genuine Filter for
Refrigerator HAF-QIN/EXP" başlığında "water" bile yok). Top Rakipler
tablosu için ayrı bir çağrı yap: `competitor_lookup(nodeIdPath=..., size=20,
order=total_units desc)` — kategorinin GERÇEK en çok satanları.

### 6. Alan adları — `data` genelde DOĞRUDAN liste, `.items` değil
`product_node`, `market_brand_concentration`, `market_price_distribution`,
`market_listing_date_distribution`, `keyword_research_trends`'in `data`
alanı **doğrudan bir liste**. `keyword_miner`'ın `data` alanı ise
`{"items": [...]}` şeklinde dict. `_extract_list()` yardımcı fonksiyonu
(`index.py`) her iki durumu da kapsar — yeni bir tool eklerken önce gerçek
yanıtı gör, varsayma.

Gerçek alan adları (varsayılanlar YANLIŞ çıktı):
- Marka payı: `totalRevenueRatio` (`share`/`percentage` DEĞİL)
- Dağılım tabloları: `label` + `unitsRatio` (`range`/`ratio` DEĞİL)
- Gross margin: `market_research_statistics.avgProfit` (zaten yüzde sayısı,
  örn. `68.19` = %68.19, /100 ile orana çevrilir)
- İade oranı: `market_product_demand_trend.data.returnRatio` (aynı şekilde
  zaten yüzde sayısı)
- Launch time bar etiketleri Çince geliyor (`"3年以上"` = "3+ yıl") —
  `app.js`'deki `LAUNCH_LABEL_TR` eşlemesi bunu çeviriyor.

### 7. `mcp` Python paketi sürümü — `>=1.9.4` şart
`mcp==1.2.0`'da Streamable HTTP transport (`streamable_http.py`) YOK, bu
sürüm `1.8.0`'dan itibaren eklendi. `requirements.txt`'te `mcp>=1.9.4,<2.0.0`
sabit — düşürme.

### 8. "Keyword Conversion Rate" — SellerSprite'ın web arayüzündeki rakamı
BİREBİR ÇEKEMİYORUZ. O sayfa muhtemelen ABA 3-tık dönüşüm payı metodolojisi
kullanıyor, ham `purchases/clicks`'ten farklı. Bunun yerine **kendi
hesapladığımız CVR**'yi kullanıyoruz: `click_cvr = purchases/clicks`,
`ACOS = bid/(click_cvr × avgPrice)` (bkz. `scoring.py::calc_keyword_ad_metrics`).
Panelde her yerde **"(hesaplanan)"** etiketiyle gösteriliyor — SellerSprite'ın
resmi sayısıyla karıştırılmasın diye. Bu bilinçli bir tasarım kararı, "bozuk"
değil. Değiştirmek istersen `aba_research_weekly/monthly/trend` tool'larını
gerçek bir keyword'le test et (henüz denenmedi).

## Vercel serverless'e özgü tuzaklar

- **`api/index.py`'nin en başında `sys.path.insert(0, ...)` şart** — Vercel'in
  Python runtime'ı `api/` klasörünü otomatik olarak import yoluna eklemiyor,
  kardeş modül importları (`database.py`, `mcp_client.py` vb.) bunsuz patlar.
- **`SELLERSPRITE_SECRET_KEY` okuması İSTEK ANINDA yapılmalı, import anında
  değil** — env var eksikse `os.environ["X"]` import anında çalışırsa TÜM
  uygulama (health check dahil) çöker. `mcp_client.py::_get_mcp_url()` bunu
  lazy yapıyor.
- **Env var değeri hem ham key hem tam URL olabilir** — kullanıcı
  `SELLERSPRITE_SECRET_KEY`'e SellerSprite'ın gösterdiği tam URL'yi
  yapıştırabilir. `_get_mcp_url()` ikisini de algılıyor (`http` ile
  başlıyorsa direkt kullan).
- **Postgres bağlantı env adı değişken** — Neon/Vercel entegrasyonu
  `DATABASE_URL`, `POSTGRES_URL`, ya da kullanıcının seçtiği herhangi bir
  "Custom Prefix" olabilir. `db_adapter.py::_discover_database_url()` önce
  bilinen isimleri dener, sonra TÜM env var'ları tarayıp `postgres://` ile
  başlayan ilk değeri kullanır.
- **Neon'un bağlantı URL'i `channel_binding` gibi asyncpg'nin anlamadığı
  parametreler içerir** — `_clean_pg_url()` bunları temizliyor, `sslmode`
  hariç.
- **Şema migrasyonu elle yapılmalı** — `CREATE TABLE IF NOT EXISTS` var olan
  tabloyu DEĞİŞTİRMEZ. Canlı veritabanı eski şemayla kurulmuşsa (örn.
  `market_decision`'a sonradan `user_id` eklendi), yeni kod eksik sütunu
  sorgulayınca çöker. `database.py::_add_column_if_missing()` +
  `_migrate_schema()` bunu `init_db()` içinde otomatik halleder — yeni bir
  sütun eklersen buraya da ekle.
- **Yakalanmayan hatalar düz metin döner, JSON değil** — Vercel'in runtime'ı
  FastAPI'yi atlayıp kendi "Internal Server Error" sayfasını gösteriyor,
  frontend bunu JSON sanıp parse edince anlaşılmaz hata veriyor.
  `index.py`'deki `@app.exception_handler(Exception)` bunu her zaman JSON'a
  çeviriyor — silme, hiçbir backend hatası artık düz metin dönmemeli.

## Veri politikası (kasıtlı kararlar)

- **Önbellek OKUMASI kaldırıldı** (kullanıcı isteği) — her `/api/analyze` ve
  `/api/analyze-asin` çağrısı HER ZAMAN canlı MCP verisi çeker, aynı keyword
  art arda aransa bile. `keyword_analysis` tablosu hâlâ yazılıyor (log/kayıt
  amaçlı) ama okunmuyor. `PAYLOAD_VERSION` mekanizması artık büyük ölçüde
  vestigial (okuma olmadığı için sürüm kontrolü tetiklenmiyor) — silinebilir
  ama zararsız, dursun.
- **Kararlar ve Geçmiş kullanıcıya özel** — `market_decision` ve
  `user_query_log` tabloları `user_id` ile izole. Ham SellerSprite verisi
  (`keyword_analysis`) paylaşımlı kalabilirdi ama artık önbellek okunmadığı
  için bu ayrımın önemi kalmadı.
- **Giriş sistemi "ilk kullanıcı kaydolunca kilitlenir"** — hiç kullanıcı
  yokken `auth_required=false`, panel açık. İlk `/api/auth/register`'dan
  sonra herkes (kendisi dahil) giriş yapmak zorunda. `require_auth()`
  dependency'si bunu kontrol ediyor — yeni bir endpoint eklersen bunu
  UNUTMA, MCP kotası yakan ya da veri yazan her endpoint'e ekle (bir
  denetimde 21 endpoint'in korumasız kaldığı bulunmuştu).

## Test metodolojisi — MCP'ye canlı erişimin yoksa böyle test et

Bu proje boyunca en pahalı hatalar **gerçek MCP yanıt şeklini görmeden
tahminle kod yazmaktan** çıktı (alan adları, sarmalama, liste/dict farkı hep
yanlış tahmin edildi, sonra gerçek çağrıyla düzeltildi). İki yol var:

1. **Gerçek MCP erişimin varsa** (Claude Code'a SellerSprite MCP bağlıysa):
   yeni bir tool/alan kullanmadan önce gerçek bir çağrı yap, `data` şeklini
   gözünle gör, sonra kodu yaz. Asla şemadan/isimden tahmin yürütme.

2. **Erişimin yoksa:** `mcp_client.py`'nin `ClientSession`'ını taklit eden
   bir stub yaz (bu konuşma boyunca hep bu kalıp kullanıldı):
   ```python
   class ClientSession:
       def __init__(self, *a, **k): pass
       async def __aenter__(self): return self
       async def __aexit__(self, *a): pass
       async def initialize(self): pass
       async def call_tool(self, name, args):
           # bu dosyadaki gerçek alan adlarıyla sahte yanıt üret
           ...
   ```
   `PYTHONPATH` ile gerçek `mcp` paketinin önüne koy, `SELLERSPRITE_SECRET_KEY=test`
   ile çalıştır. FastAPI `TestClient` ile uçtan uca test et — sadece syntax
   kontrolü YETERSİZ, gerçek değerleri elle hesaplayıp karşılaştır.

3. **Her önbellek/veri şekli değişikliğinde**: eğer bir alan formatı
   değişiyorsa, canlı veritabanında o alanın ESKİ halde kayıtlı olabileceğini
   unutma (bkz. şema migrasyonu tuzağı yukarıda).

## Bilinen eksikler / yarım kalan işler

- **Hercules v2.1/v3 modülleri backend'de var, frontend'i YOK:**
  `signal_engine.py` (Market/Demand/Truth/Risk/Proof, Opportunity Score, Blue
  Ocean rozeti), `bayesian.py` (öğrenme döngüsü), `portfolio.py` (QIPO,
  CP-SAT ile bütçe optimizasyonu — `ortools` gerektirir, Vercel deploy
  boyutu yüzünden `requirements.txt`'ten opsiyonel bırakıldı),
  `supplier_scoring.py`, `launch_control.py` (Discovery/Supplier
  Score/Creative Pipeline/Launch Control). API endpoint'leri çalışır durumda
  ve test edilmiş ama panelde hiçbir arayüzleri yok.
- **`signal_engine.py`'deki Demand/Truth sinyal ağırlıkları VARSAYIM** —
  orijinal Hercules dokümanı bu ikisi için iç ağırlık vermiyordu (Market ve
  Risk'inki net). Kodda `# VARSAYIM` yorumlarıyla işaretli, kalibrasyon
  gerektirir.
- **Discovery Engine'in Keepa/Google Trends filtreleri yok** — yalnızca
  `keyword_miner` ile tarama yapıyor, sahte fırsat eleme adımı eksik.
- **Kar analizindeki "Güçlü Yeni Marka" proxy'si küçük örneklemli** — top 10-20
  rakibin `availableDate`'ine bakıyor, pazarın TAMAMINI taramıyor.

## Geliştirme akışı önerisi

- `api/` altındaki her dosya bağımsız import edilebilir olmalı (Vercel tek
  dosyalık `api/index.py` giriş noktası bekliyor, kardeş dosyalar `sys.path`
  hilesiyle bulunuyor — yukarı bakın).
- Yerelde çalıştırmak için: `cd api && uvicorn index:app --reload` (SQLite
  fallback otomatik devreye girer, `DATABASE_URL` yoksa).
- Deploy: GitHub'a push → Vercel otomatik build eder (`vercel.json`'daki
  rewrite kuralı `/api/*`'ı `api/index.py`'a yönlendiriyor).
- Yeni bir SellerSprite tool'u eklerken sırayla: (1) gerçek çağrıyla şemayı
  doğrula, (2) `mcp_client.py::call_tool`'a doğru `wrap_in_request` değeriyle
  ekle, (3) stub ile uçtan uca test et, (4) gerçek deploy sonrası kullanıcıdan
  ekran görüntüsü/log iste, tahmin etme.
