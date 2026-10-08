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

### 7. Paket sürümleri — `requirements.txt` SABİT (==), aralık YAZMA
`mcp==1.2.0`'da Streamable HTTP transport (`streamable_http.py`) YOK (1.8.0'dan itibaren var) — `mcp` en az 1.9.4.
**Vercel her deploy'da paketleri sıfırdan kurar.** Eskiden `mcp>=1.9.4,<2.0.0` ve `pydantic>=2.10.1,<3.0.0` aralık
olarak yazılıydı; pydantic 2.14.0 çıkınca `mcp 1.12.4` import anında çöktü (`ImportError: eval_type_backport`),
uygulama hiç başlamadı ve TÜM uçlar açıklamasız 500 döndü (giriş bile yapılamadı) — kodda hiçbir değişiklik
yokken, bir sonraki deploy'la. Artık `mcp==1.12.4`, `pydantic==2.13.5`, `pydantic-settings`, `sse-starlette`
sabit. Yükseltirken temiz ortamda (`uv venv` + `uv pip install -r requirements.txt`) uygulamayı import edip test et.
- Açılışta veritabanı hazırlanamazsa (yanlış DATABASE_URL, Neon erişilemez) uygulama artık ÇÖKMEZ:
  `index._ensure_db` hatayı saklar, her /api isteğinde yeniden dener, başarısızsa okunabilir JSON 503 döner.

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
- **ŞEMA SÜRÜMÜ — her şema/migrasyon değişikliğinde `database.SCHEMA_VERSION`'ı ARTIR.** Açılışta
  `init_db()` yalnızca TEK sorgu çalıştırır (`schema_version` tablosu); sürüm güncelse ve `OWNER_EMAILS`
  özeti (sha256) değişmediyse hiçbir CREATE/ALTER/UPDATE/tohum çalışmaz. Değilse tüm migrasyonlar
  (`_SCHEMAS` + `forum.SCHEMAS` + `_migrate_schema` + sertifika tohumu) TEK bağlantıda
  (`db_adapter.single_connection()`) çalışır ve sürüm yazılır. Yeni tablo, sütun, indeks, veri düzeltmesi
  ya da tohum ekleyip sürümü artırmazsan canlı veritabanında migrasyon HİÇ çalışmaz. Sonuç: veritabanına
  elle yapılan bir değişikliği (ör. kalıcı owner'ın rolünü DB'de düşürmek) artık bir sonraki açılış
  "düzeltmez" — sürüm artınca ya da OWNER_EMAILS değişince düzeltilir (yetki zaten sunucuda e-postadan owner).
- **Dosya adı başlıkları (Content-Disposition) latin-1 ile kodlanır** — Türkçe keyword'ü
  doğrudan `filename="..."` içine yazmak ş/ğ/ı'da `UnicodeEncodeError` → 500 veriyordu (tüm Excel
  export'ları). Dosya döndüren HER uçta `index.py::_content_disposition()` kullan: ASCII yedek
  (ş→s, ğ→g, ı→i, İ→I, ç→c, ö→o, ü→u, kalan ASCII dışı → _) + RFC 5987 `filename*=UTF-8''...`.
  Frontend `downloadBlob` adı istemcide kurar ve Türkçe harfleri korur. (Test notu: Chromium
  `C` yerelinde ASCII dışı indirme adlarını "download"a çevirir — Playwright'ı `LC_ALL=C.UTF-8` ile çalıştır.)
- **Excel'de formül enjeksiyonu** — openpyxl "=" ile başlayan HER metni formül yazar. Tüm Excel
  üreticileri sonunda `excel_export._neutralize_user_formulas(wb, kasıtlı_formüller)` çağırır:
  kasıtlı olmayan formüller metne (`data_type='s'`) çevrilir, "=,+,-,@" ile başlayan metinlere
  `quotePrefix` verilir. Yeni bir Excel üreticisi eklersen bunu ÇAĞIR; kasıtlı formülleri
  (Kâr Analizi gibi) `allowed` kümesine ekle. Raporlar sayfası artık CSV değil `POST /api/export/reports`.
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
- **İSTİSNA — Ekip Aktivitesi (yalnızca owner, Ekip Yönetimi → Aktivite):** `GET /api/team/activity` (`require_owner`;
  admin/member 403; `team_id` filtresi) ŞU AN en az bir ekipte olan kullanıcıların `user_query_log` ve `market_decision` kayıtlarını
  kişi/tarih/karar/keyword filtresi ve kişi başına özetle döner (özet varsayılan TÜM ZAMANLAR;
  since/until verilirse özet de listeler de yalnızca o aralık). SALT OKUNUR: bu
  bölüm için yazma ucu yok; karar/geçmiş silme uçları zaten yalnızca isteği yapanın KENDİ
  `user_id`'siyle çalışır. Kararın "ön öneri"si = aynı kişinin aynı keyword/pazar için karar anına
  kadarki son sorgusunun verdict'i. Panelde kayda tıklamak `runAnalysis` ile CANLI analiz başlatır
  (ASIN kayıtlarındaki `ASIN:` öneki ayıklanır). Giriş ekranı metni bunu kullanıcıya söylüyor.
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


## Trendler & Fırsatlar — gerçek MCP kaynağı bulundu (aba_research_weekly/monthly)

`/api/discovery/trending` endpoint'i `aba_research_weekly`/`aba_research_monthly`
tool'larını kullanıyor — gerçek MCP çağrısıyla doğrulandı. `searchModel`
parametresi (1-6) pazar tipini seçiyor: 1=Popüler, 2=Anormal Hareketli,
3=Sürekli Büyüyen, **4=Hızlı Yükselen** (Stitch tasarımındaki "Breakout
Nişler" kartının gerçek karşılığı), 5=Potansiyel, 6=Uzun Kuyruk.

Gerçek alan adları: `data.items` (dict içinde liste, keyword_miner gibi),
her item'da `searches` (arama HACMİ — hacim için tek doğru alan),
`searchRankGrowthRate` (0-1 oran; arama SIRALAMASININ yükselme oranı, hacim
büyümesi DEĞİL — ör. 0.909 ≈ 11. sıradan 1. sıraya çıkış; panelde "Sıralama
İvmesi" olarak etiketleniyor, asla "hacim büyümesi" deme),
`w4RankGrowthRate`/`w12RankGrowthRate` (aynı sıralama ivmesinin 4/12 haftalık
karşılaştırması),
`top3Brands`, `top3AsinDtoList` (görsel URL + CTR/CVR ile).

**Oran birimleri (gerçek MCP verisiyle doğrulandı):** `purchaseRate`,
`top3AsinDtoList[].clickRate` ve `top3AsinDtoList[].conversionRate` (backend'de
`purchase_rate`, `click_rate`, `conversion_rate`) HER ZAMAN 0-1 oran. Kanıt:
`purchaseRate = purchases / searches` (17881 / 2518485 = 0.0071 = %0.71).
Frontend'de büyüklüğe bakan tahmin mantığı YOK — daima ×100 ile yüzdeye
çevrilir (`app.js::fmtRate`). Küçük değerler (%0.71 gibi) doğrudur, "yanlış
birim" sanıp düzeltmeye çalışma.

**Kategori yasağı & filtreler (gerçek MCP çağrılarıyla doğrulandı — `api/trends.py`):**
- `departments` İSTEK parametresi görünen adı DEĞİL Amazon arama takma adını ister ("Toys & Games" → 0 sonuç,
  "toys-and-games" → çalışır); yanıttaki `departments` ise görünen ad. Takma adlar pazara göre değişir (UK'de
  "toys") → yalnızca US listesi (`trends.US_DEPARTMENTS`, 22 kategori) doğrulandı; UK/CA'da kategori filtresi
  taranan sayfalarda panelde (görünen adla) uygulanır (`category_mode="panel"`).
- `includeKeywords`/`excludeKeywords` çalışır. `minSearches`/`maxSearches`, `maxWordCount`, `minConversionRate`,
  `maxMonopolyClickRate` SESSİZCE YOK SAYILIR → tüm sayısal filtreler sunucuda (`trends.passes_filters`). Eski
  "Min. arama hacmi" filtresi bu yüzden hiç çalışmıyordu. Sayfa başına en fazla 40 kayıt; yasak/filtre sonrası
  sonuç azalırsa sonraki sayfa çekilir (en fazla `MAX_SCAN_PAGES`=5 MCP çağrısı). `order` parametresi de etkisiz.
- `clickShareRate`/`cvsShareRate` = ilk 3 ASIN'in clickRate/conversionRate TOPLAMI (0-1).
- **Yasak:** `banned_categories` (name_key = küçük harfli görünen ad). `POST/DELETE /api/trends/banned-categories`
  yalnızca owner (admin/member 403); `GET /api/trends/categories` herkese liste + `can_manage`. Keyword'ün
  kategorilerinden HERHANGİ BİRİ yasaklıysa SUNUCUDA atılır (kategorisiz keyword etkilenmez); yasaklı kategori
  filtre olarak istenirse 403. Panel owner'a kart etiketlerinde yasakla düğmesi gösterir.

**Kullanılmayan (bilerek):** `google_trend` tool'u da var (Amazon dışı,
Google arama trendini veriyor) ama henüz backend'e bağlanmadı —
"Mevsimsellik Riski" kartı için kullanılabilir, ihtiyaç olursa test edilip
eklenmeli. **TikTok/sosyal medya viral katsayısı için hiçbir gerçek MCP
kaynağı YOK** — Stitch tasarımındaki "Viral Dönüşüm Katsayısı" kartı bu
yüzden backend'e hiç bağlanmamalı, sahte veri olur.


## Roller & Eğitim / Görevler (owner / admin / member)

- **Rol kaynağı:** `users.role` sütunu (`_migrate_schema` içinde `_add_column_if_missing`
  ile eklenir — canlı Postgres'te users tablosu zaten vardı). Owner **atanabilir** bir
  roldür, birden fazla owner olabilir; yetki her istekte bu sütundan okunur
  (`database.py::get_user_role`). Migrasyon idempotent: boş/geçersiz rol → member;
  yalnızca HİÇ owner yoksa en küçük id'li kullanıcı owner yapılır (mevcut owner'lara
  dokunmaz). Hiç kullanıcı yokken ilk kayıt olan owner olur.
- **Yetki SUNUCUDA:** `index.py` → `require_user` (gerçek hesap şart; auth kapalıyken
  401), `require_staff` (owner/admin), `require_owner`. Arayüzde gizlemek yalnızca kolaylık.
  - Member yalnızca kendisine atanan dersleri görür (bkz. "Eğitim ataması" — tüm ekipler / ekiplerinden biri /
    kişisel; ekip dışı hiçbirini) ve yalnızca KENDİ tamamlamasını değiştirir:
    `/api/training/lessons/{id}/complete` gövdesinde user_id YOK, oturumdan alınır;
    atanmamış derse 404.
  - Rolleri yalnızca owner'lar değiştirir (`/api/users/{id}/role`: owner|admin|member;
    başka bir owner'ı ve kendini düşürebilir). **Kilitlenme koruması:** son kalan owner
    düşürülemez → 409 (`set_user_role` → `LastOwnerError`; koşul UPDATE içinde de var ve
    sonrasında 0 owner kalırsa değişiklik geri alınır).
- **Kalıcı owner (`OWNER_EMAILS`):** Vercel ortam değişkeni, virgülle ayrılmış e-postalar
  (küçük harfe çevrilip boşlukları temizlenir; istek anında okunur — `database.permanent_owner_emails`).
  E-postaları KODA YAZMA (repo herkese açık). Listedekiler `_migrate_schema`'da owner yapılır, bu
  e-postayla yeni kayıt doğrudan owner olur, `get_user_role` onları DB'deki değerden bağımsız owner
  sayar. Kimse (kendileri dahil) düşüremez → 409 (`PermanentOwnerError`); `/api/users` `permanent`
  bayrağı döner, rol ekranında seçim kilitli + "kalıcı owner" etiketi. Değişken yoksa hiçbir şey değişmez.
- **Video linkleri:** yalnızca https. YouTube ID'si sunucuda (`youtube_video_id`) ve
  istemcide (`YT_ID_RE`) 11 karakter `[A-Za-z0-9_-]` olarak doğrulanır, yalnızca bilinen
  YouTube host'larında; gömme `youtube-nocookie.com/embed/{id}`. Diğer linkler yeni sekmede
  (`rel="noopener noreferrer"`).
- Tablolar: `training_lessons`, `training_assignments`, `training_completions`
  (`completed_at` zaman damgası). Ders silinince atama/tamamlamalar da silinir.

## Çoklu ekip, Ekip Yönetimi & kalıcı hesap silme (yalnızca owner)

- **Model:** `teams` (ad, created_at) + `team_members` (team_id, user_id, added_at). Bir kişi birden fazla ekipte
  olabilir; HİÇBİR ekipte olmayan = **ekip dışı** (paneli normal kullanır: analiz, karar, kendi geçmişi/listeleri;
  ekip özelliklerinde görünmez). Roller (owner/admin/member) GENEL, ekibe bağlı değil.
- **Migrasyon (bir kez):** `schema_flags.teams_seeded` işareti ÖNCE yazılır (eşzamanlı sunucusuz örneklerde tek
  kurulum; ekipler sonradan silinse de yeniden kurulmaz), "Genel" oluşturulur, mevcut TÜM kullanıcılar eklenir.
  Önceki tekli `users.in_team` sütunu varsa dönüştürülür (1/NULL → Genel, 0 → ekip dışı); sütun kaldı ama artık
  OKUNMAZ/YAZILMAZ. `_ensure_staff_in_team()` migrasyon her çalıştığında (şema sürümü artınca ya da
  `OWNER_EMAILS` değişince — bkz. "Şema sürümü") ekipsiz owner/admin/`OWNER_EMAILS`'i varsayılan
  (en eski) ekibe ekler. Yeni kayıt ekip dışı başlar; owner olarak kaydolan (ilk kullanıcı / `OWNER_EMAILS`)
  varsayılan ekibe girer (hiç ekip yoksa "Genel" oluşturulur).
- **Hiyerarşi (iki seviye):** `teams.parent_id` (`_add_column_if_missing`; NULL = ana ekip; mevcut ekipler ana ekip
  kaldı, üyelik değişmedi). Alt ekibin altına ekip açılamaz (409); birden fazla ana ekip olabilir. **Kalıtım
  hesaplanır, satır yazılmaz:** `team_members` yalnızca DOĞRUDAN üyelik; alt ekip üyesi ana ekibin de üyesi sayılır
  (`db._effective`, `list_users().team_ids` = etkin, `direct_team_ids` = doğrudan). `team_member_ids(ana)` = doğrudan +
  tüm alt ekiplerin üyeleri, `team_member_ids(alt)` = yalnızca kendisi → Aktivite, ilerleme, Yetenek Haritası
  filtreleri buradan. Ana ekibe atanan ders alt ekip üyelerine de görünür (`_LESSON_VISIBLE_SQL` `mt.parent_id`),
  alt ekibe atanan yalnızca o alt ekibe. `DELETE /api/users/{id}/teams/{team}`: ana ekipten çıkarma o ana ekibin TÜM
  alt ekiplerinden de çıkarır (`removed` döner; panel onayda alt ekipleri listeler); `POST …/teams/{team}` ekler.
  (`POST /api/users/{id}/teams {team_ids}` doğrudan kümeyi eşitler, zincirleme çıkarma yapmaz.) Taşıma
  `PUT /api/teams/{id} {parent_id}` (gönderilirse; null = ana ekip yap): alt ekipleri olan ana ekip taşınamaz, hedef
  ana ekip olmalı, kendi altına olmaz (409; koşul UPDATE'te de). Alt ekipleri olan ana ekip silinemez (409). Ad
  benzersizliği aynı düzeyde (kardeşler arasında). Etiket `"Ana › Alt"` (`list_teams().label`, filtre/seçim listeleri
  `_team_opts`). Davet alt ekip için de üretilir; davetle kaydolan o alt ekibe (dolayısıyla ana ekibe) katılır.
  Owner/admin koruması etkin üyelikle: yalnızca alt ekipteki admin ana ekipten çıkarılamaz (409).
- **Ekip yalnızca şunları kapsar** (filtre SUNUCUDA, `db.team_member_ids(team_id=None)` = en az bir ekipte olanlar):
  Aktivite (arama/karar/özet; `team_id` filtresi, varsayılan tüm ekipler; tüm ekiplerden çıkarılanınki görünmez,
  geri eklenince kayıtlar silinmediği için geri gelir), eğitim, owner/admin'in BAŞKALARININ kontrol listelerini
  görmesi (sahibi hiçbir ekipte değilse 404; kişi kendi listesini her zaman görür).
- **Rol kuralları:** owner/admin yalnızca en az bir ekipte olana (`TeamRuleError` → 409); owner/admin (ve kalıcı owner)
  SON ekibinden çıkarılamaz → 409. Koşullar UPDATE'lerde de var (`EXISTS team_members`) + sonradan denetim/geri alma.
- **Ekip Yönetimi sayfası** (menü "Ekip Yönetimi", yalnızca owner; admin/member tüm uçlarda 403):
  - Ekipler: `GET/POST /api/teams`, `PUT/DELETE /api/teams/{id}`. Ad boşluklardan temizlenir, ≤60, büyük/küçük harf
    duyarsız benzersiz (422). Silme kullanıcı ve verilerine DOKUNMAZ; yalnızca üyelikler, `training_lesson_teams`
    ve ekibin davetleri silinir. Bir owner/admin'in TEK ekibiyse 409 (`delete_blockers`). Yalnızca o ekibe atanmış
    dersler `exclusive_lessons` olarak döner → onay penceresinde uyarı.
  - Üyeler: `POST /api/users/{id}/teams {team_ids}` kümeyi eşitler (boş = ekip dışı). Rol değişikliği (`/role`) bu
    sekmeye taşındı (Eğitim sayfasında artık yok). "Ekip dışı" filtresi en yeniden eskiye; son 7 günde kaydolana "yeni".
  - Davetler: `POST /api/invites {team_id, multi_use, days=7 (1–90)}` düz kodu YALNIZCA bir kez döner; sunucuda
    `sha256` (`team_invites.code_hash`). `GET /api/invites` aktifleri (iptal/süre/kullanım hakkı), `POST
    /api/invites/{id}/revoke`. Kayıt: `POST /api/auth/register {invite}` → `redeem_invite` tek `UPDATE … RETURNING`
    ile kullanımı artırır (tek kullanımlık iki kez kullanılamaz; `db_adapter.execute_fetch` COMMIT eder — `fetch_all`
    etmez). Geçersiz/süresi dolmuş/iptal davet kaydı ENGELLEMEZ → ekip dışı (`invite.status="invalid"`). Davet hesap
    oluşturulduktan SONRA harcanır. Panel `?invite=` kodunu sessionStorage'a alıp adres çubuğundan siler.
  - Aktivite: eski "Ekip Aktivitesi" (`GET /api/team/activity`) + `team_id` filtresi; özet ekip adlarıyla.
    Listeler SUNUCUDAN SAYFALI (`q_page`/`d_page`, `TEAM_PAGE_SIZE`=50; `totals` ve özet tüm filtrelenmiş kümeden,
    aralık dışı sayfa son sayfaya çekilir). Ön öneri yalnızca o sayfadaki kararlar için hesaplanır.
  - Yetenek Haritası: bkz. "Ad Soyad, Profil & Yetkinlik Formu".
  - Owner menü rozeti: `/api/auth/status.new_outsiders_7d` (son 7 günde kaydolan ekip dışı; owner değilse null).
- **Eğitim ataması** `training_lessons.assign_mode` = `all` (tüm ekipler) | `teams` (`training_lesson_teams`,
  DİNAMİK: ekibe sonradan katılan görür, çıkan görmez) | `users` (`training_assignments`). Eski istemci
  `assign_all` gönderirse mod ondan türetilir. Ekip dışı hiçbir ders görmez/tamamlayamaz; tamamlama kayıtları
  üyelik değişince SİLİNMEZ. İlerleme `GET /api/training/progress?team_id=` (atananlar dinamik hesaplanır).
- **Kalıcı silme:** `POST /api/users/{id}/delete {confirm_email}` (onay e-postası tutmazsa 422; kendi hesabı,
  kalıcı owner, son owner → 409). `db.delete_user_completely`: oturumlar, eşikler, geçmiş, kararlar, kendi kontrol
  listeleri (madde + olaylarıyla), eğitim tamamlama/atamaları, ekip üyelikleri, yetkinlik formu ve hesap SİLİNİR;
  yer tutucu YOK.
  Başkalarının kayıtlarındaki referanslar silinmez, NULL'lanır (`EMAIL_REF_COLUMNS`, `ID_REF_COLUMNS` — eklediği
  dersler, oluşturduğu davetler — ve `checklist_events.details_json`) → panelde "—". **Kişi e-postası/id'si yazan
  yeni bir sütun eklersen `EMAIL_REF_COLUMNS` / `ID_REF_COLUMNS` / `USER_ID_TABLES`'a da ekle.** Aynı e-posta sonra
  yeni (ekip dışı, member) hesap olarak kaydolabilir.

## Ad Soyad, Profil & Yetkinlik Formu

- **Ad soyad zorunlu:** kayıtta `first_name`/`last_name` (boşluk temizlenir, 1–60, yoksa 422). `users`'a
  `first_name, last_name, username, title, phone` sütunları `_add_column_if_missing` ile; `username` için
  `ux_users_username` (LOWER) benzersiz indeksi. **Kapı SUNUCUDA:** `require_auth` adı/soyadı olmayan (eski) hesaba
  428 verir; yalnızca profil uçları (`require_session` → `require_profile_user`) muaf. Panel 428'de ve
  `auth/status.needs_name`'de ad soyad ekranını açar (kapatılamaz; çıkış yapılabilir).
- **E-posta yerine "Ad Soyad" her yerde:** `db.display_name()`. Kayıtlarda e-postayla anılan kişiler (kontrol listesi
  işaretleyen/onaylayan/olaylar, şablonu güncelleyen, liste sahibi `owner_name`) çıktıda `index._person()` ile ada
  çevrilir — e-posta ASLA dönmez (kişi yoksa/adı yoksa "—"). E-posta yalnızca OWNER ekranlarında ikincil bilgi:
  `/api/users` ve `/api/training/progress` owner olmayana `email` göndermez; Aktivite/Ekip Yönetimi'nde ad + e-posta.
  Veritabanında e-posta sütunları (checked_by vb.) aynen kalır; dönüşüm yalnızca çıktıda.
- **Profil** (`GET/PUT /api/profile`, herkes yalnızca KENDİSİ): ad, soyad, kullanıcı adı (3–30, `[a-z0-9._-]`, küçük
  harfe çevrilir, büyük/küçük harf duyarsız benzersiz → 409; forumda görünecek, GİRİŞİ DEĞİŞTİRMEZ — giriş e-postayla),
  unvan (≤80), telefon (opsiyonel). PUT yalnızca gönderilen alanları değiştirir. Departman serbest metni YOK → kişinin
  ekipleri gösterilir. Fotoğraf yok, baş harflerden avatar (`app.js::initials`). **TC kimlik, doğum tarihi, adres,
  medeni durum, sağlık gibi kişisel veri alanları bilinçli olarak YOK — ekleme.** Hesap & Güvenlik:
  `POST /api/profile/password` (mevcut şifre yanlışsa 403; değişince bu oturum dışındakiler kapanır).
- **Yetkinlik formu** — şema TEK kaynak `api/competency.py` (15 bölüm; `GET /api/competency/schema`, panel buradan
  çizer). Cevaplar düz sözlük; 04–13 bölümlerinin seviyeleri tek `levels` sözlüğünde (madde → 1–5). `clean_answers()`
  bilinmeyeni atar, seçenek/seviye doğrular, metni kırpar. Tablo `competency_forms` (user_id PK, answers_json, status
  draft|submitted, updated_at, submitted_at). `PUT /api/competency/me` = otomatik taslak (panel ~0,9 sn debounce),
  `POST /api/competency/me/submit` = "gönderildi" + tarih; onay akışı YOK, gönderdikten sonra da düzenlenebilir
  (durum gönderildi kalır, panel "gönderimden sonra düzenlendi" der). Bölüm "tamam" = içindeki tüm alanlar dolu.
  **Görünürlük SUNUCUDA:** `GET /api/competency/users/{id}` yalnızca kişinin kendisi ya da OWNER; admin/üye 403
  (var olmayan id'de bile 403 — varlık sızmaz; owner'a 404). Hesap silinince form da silinir (`USER_ID_TABLES`).
  Tasarımdaki radar/skor/"Yönetici Notu"/rol eşleşmesi kartı uydurma veri olacağı için YOK.
- **Yetenek Haritası** (Ekip Yönetimi sekmesi, `GET /api/skills/map`, `require_owner`): kişi × 37 madde (04–13).
  Filtreler: `team` (all = en az bir ekipte | none = ekip dışı | ekip id), `field` (madde anahtarı | `s:<bölüm>` | boş),
  `min_level` (madde seçiliyse o maddede; değilse gösterilen maddelerden EN AZ BİRİNDE ≥). Haritada yalnızca formunu
  GÖNDERENLER; göndermeyenler (hiç başlamamış / taslak) `pending` listesinde. Kişiye tıklayınca salt okunur tam form.

## Topluluk & Forum (`api/forum.py` + `/api/forum/*`)

- **MCP çağrısı YOK.** Giriş yapmış herkes (ekip dışı dahil, ad soyad kapısından sonra — `require_user`) okur,
  başlık açar, cevap yazar. Başlık türleri: Soru / Tartışma / Bülten (herkes açabilir). Son 3 bülten şeritte.
- **Görünürlük SUNUCUDA:** `visibility` = `public` (varsayılan) | `team`. Hiçbir ekipte olmayan kullanıcı `team`
  başlıkları HİÇBİR yerde göremez: liste/arama/etiket/kategori sayıları/istatistik/bülten şeridi/katkıcılar
  (SQL'de süzülür, `forum.visible_threads`) ve doğrudan link + cevap/oy/kayıt/çözüm uçları (`index._visible_thread`
  / `_visible_reply` → 404, var olmayanla aynı yanıt). Ekip dışı `team` başlık açamaz/düzenleyemez (403). Tüm
  ekiplerden çıkarılan kişi kendi `team` başlığını da göremez. Yeni bir forum ucu eklersen bu yardımcıları KULLAN.
- **Yetkiler:** yazar kendi başlık/cevabını düzenler ve siler (owner dahil başkası METNİ düzenleyemez); owner/admin
  her başlığı ve cevabı siler, sabitler, kilitler. Kilitli başlığa cevap → 423 (koşul INSERT'in içinde). Çözüm:
  başlık sahibi ya da owner/admin işaretler/kaldırır (bültende 409; cevap başka başlığınsa 422); çözüm cevabı
  silinirse "Çözüldü" kalkar. Kategoriler: varsayılan 6 + "Genel" bir kez tohumlanır (`schema_flags.forum_seeded`),
  ekleme/adlandırma/silme yalnızca owner (içinde başlık olan kategori silinmez → 409).
- **Faydalı:** `forum_votes` birincil anahtarı (tür, id, kişi) → kişi başına bir; ikinci oy 409, kendi içeriğine 403,
  DELETE geri alır. **Görüntülenme** = başlığı açan BENZERSİZ kişi (`forum_views`). **Kaydet** = "Takip Edilen
  Başlıklar" (`forum_saves`). Sekmeler: Tümü (sabitler üstte, son etkinliğe göre), Çözülenler, Sıcak (son 7 günde
  cevap + faydalı oyu sayısına göre, yalnızca etkileşimi olanlar), Bültenler; ayrıca kayıtlılar, kategori, etiket, arama
  (başlık + metin + etiket, Türkçe harf duyarsız).
- **Gerçek veri:** istatistikte yalnızca toplam ve çözülen başlık; "Haftanın Katkıcıları" = son 7 gündeki cevap ve
  çözüm seçilen cevap sayısı (puan yok); popüler etiketler gerçek sayım. Tasarımdaki aktif satıcı/çözüm oranı/yanıt
  süresi, canlı yayın kartı, "Detay Raporu"/CPC kutuları uydurma olacağı için YOK.
- **Metin DÜZ METİN:** sunucu ham saklar (kontrol karakterleri atılır, uzunluk sınırları). Panel `app.js::forumText`
  her parçayı `esc()` ile kaçırır, yalnızca `http/https` URL'leri `<a target=_blank rel="noopener noreferrer nofollow">`
  yapar (`javascript:` vb. düz metin kalır), satır sonları `white-space: pre-wrap`. Dosya yükleme yok. Etiketler
  `[\w.+-]` (≤5, ≤30 karakter; `#` atılır, boşluk → tire, harf duyarsız tekilleştirme).
- **Paylaşım sınırı** (`forum.POST_LIMITS`/`THREAD_LIMITS`): başlık + cevap 1 dakikada 5, 1 saatte 40; yeni başlık
  10 dakikada 3 → 429. Düzenleme sınırlanmaz.
- Doğrudan link `#forum/<id>`. Hesap silinince kişinin başlıkları (cevaplarıyla), cevapları, oy/kayıt/görüntülemeleri
  silinir (`forum.delete_user_content`). Forum küçük ölçek varsayar: liste görünür başlıkların tamamını çekip
  Python'da süzer/sayfalar (sayfa 10).

## Ön öneri kuralı (Uygun / Sınırda / Elenmiş)

- **TEK kaynak:** `scoring.py` → `UYGUN_MAX_NEGATIVE`, `ELIMINATE_AT`, `verdict_for()`,
  `verdict_rule()`. Sayıları başka hiçbir yere (JS, HTML metni, doküman) yazma.
- Sunucu kuralı analiz yanıtında `pre_assessment.rule` ({uygun_max_negative, eliminate_at, text})
  ve `GET /api/verdict-rule`'da gönderir. Panelin canlı yeniden hesaplaması (kâr hesaplayıcı →
  Kriter 06 → ön öneri), sade dille özet, rozet ve açıklama metinleri (panel + Ayarlar) bunu kullanır.
  `n/a` kriterler olumsuz sayılmaz.
- Geçmiş kayıtlardaki (`user_query_log.verdict`, `keyword_analysis.verdict`) ön öneriler
  kaydedildikleri andaki kurala göredir; yeniden hesaplanmaz.

## Kriter 03 — ACOS (ilgili ilk 20 keyword, ağırlıklı, hesaplanan)

- **Yalnızca SUNUCUDA:** `scoring.weighted_top_acos()` → `ACOS = Σ(bid × clicks) ÷ Σ(purchases × fiyat)`
  (20 keyword'e birlikte reklam verilse toplam harcama ÷ toplam satış; tık/satışı çok olan doğal olarak ağır basar).
  Hem `/api/analyze` hem `/api/analyze-asin`; sonuç `pre_assessment.acos_detail` (value, count, n, keywords[{keyword,
  rank, bid, clicks, purchases, price, spend, sales, acos}], total_spend, total_sales, no_sales, skipped, keyword
  modunda `bands` = yakın (ilgililik ≥75) / orta (50–75) alt ACOS'ları — BİLGİ AMAÇLI, ön öneriye girmez).
- **HAVUZ — tabloyla aynı değil:** `keyword_miner(keyword, minRelevancy=50)` SellerSprite'tan `order=searches desc`
  ile gelir; tablo hacme göre ilk `keyword_list_size`(20) satırı gösterir (panel bunları kendi içinde relevancy'ye
  göre dizer — bu yalnızca GÖRÜNÜM sırası). Eskiden ACOS da bu hacme göre kesilmiş 20'den seçiliyordu → ilgili ama
  hacmi düşük keyword'ler hiç girmiyordu. Artık aynı çağrı `size=ACOS_POOL_SIZE`(100) çekilir (EK MCP ÇAĞRISI YOK),
  ACOS havuzun tamamından relevancy'si en yüksek 20 ile hesaplanır. ("samsung water filter" gerçek verisi: eski ilk 5
  %43,9; hacim-20 havuzundan ilk 20 %32,6; ilgili havuzdan ilk 20 %44,3.) `minRelevancy` gerçek çağrıyla doğrulandı
  (306 → 62). ASIN modunda `traffic_keyword(size=100)` → `trafficPercentage` en yüksek 20; trafik payı 0 olan
  keyword (ürüne trafik getirmiyor) sayılmaz (genel kural: sıralama değeri ≤0/None olan satır "ilgili" değil).
- Skill'deki (amazon-kw-segmenter) anlamsal gruplama bilinçli olarak YOK: gruplar LLM/web yorumuyla kuruluyor,
  sunucuda güvenilir kuralla ayrılamaz; grup ağırlıkları (ör. 70/20/10) keyfi olurdu. İlgililik puanı veri tabanlı.
- **Fiyat:** keyword modunda her keyword'ün kendi `avgPrice`'ı, ASIN modunda ürünün kendi fiyatı.
- **Eksik veri:** bid/clicks/purchases/fiyat'tan biri yoksa keyword atlanır, sıradaki alınır. Satışı 0
  olan keyword harcamaya eklenir. <20 geçerli → olanlarla hesaplanır, kartta "N keyword". Hiç yoksa
  "Veri Yok" (n/a). Harcama var ama toplam satış 0 → değer yok, kriter OLUMSUZ.
- Kriter etiketi (`label`) iç anahtar olarak "ACOS" kaldı (eşikler, özet, eski kayıtlar); kartta ve Excel'de
  gösterilen ad "ACOS (ilgili ilk 20 keyword, ağırlıklı, hesaplanan)". Kâr hesaplayıcının ACOS ön değeri bu sayı.
- Keyword tablolarındaki satır bazlı ACOS (madde 8'deki `calc_keyword_ad_metrics`) ve **Lansman
  Raporu** (skill gereği ana keyword + kendi fiyatımız) bundan ETKİLENMEZ.
- `returnFields` `traffic_keyword`'te de BOZUK (null döndü) — kullanma (bkz. madde 2).

## Kâr analizi (Ürün Analizi → Kâr sekmesi)

- **Referral ORAN olarak tutulur** (varsayılan %15, düzenlenebilir); dolar = oran × satış fiyatı,
  fiyat değişince yeniden hesaplanır ve `/api/profit`'e DOLAR olarak gider (backend imzası aynı).
  Excel Kâr Analizi'nde "Referral Oranı (%)" girdisi + `=oran*fiyat` canlı formülü
  (`profit_analysis.inputs.ref_rate`; eski payload'larda oran $/fiyat'tan türetilir).
- **Analiz öncesi maliyet** (arama kutusu altındaki "Maliyet gir (opsiyonel)": COGS, FBA, Genel
  gider %): yalnızca DOLU alanlar Kâr bölümüne yazılır, Kriter 06 ve ön öneri ilk açılışta buna
  göre gelir, "Analiz öncesi girilen maliyetler kullanıldı" notu çıkar. Boşsa eski varsayılanlar
  (6.00 / 5.50 / %1). Değerler tarayıcıda (`localStorage: pl_pre_cost`) kalır, "Temizle" siler; analiz
  isteğinde `pre_cost {cogs, fba, gen}` olarak (yalnızca dolu alanlar) sunucuya gider.
- **Kâr hesaplayıcısının başlangıç değerleri TEK KAYNAK: SUNUCU** (`scoring.initial_profit_inputs` +
  `net_margin_from_inputs`, `PROFIT_DEFAULTS`). Sunucu Kriter 06'yı (Net Kâr Marjı) bu değerlerle hesaplar ve
  yanıtta `profit_inputs` (+ `sources`: market|pre_cost|default) döner; panel alanları bu sayılarla
  (yeniden yuvarlamadan) doldurur. **Hata geçmişi:** eskiden sunucu `net_margin=None` (n/a, sayılmaz) ile
  önerip KAYDEDİYOR, panel ise açılışta varsayılan maliyetlerle Kriter 06'yı ekliyordu → panelde "Sınırda",
  Geçmiş/Ana Sayfa'da "Uygun" (gerçek örnek: "samsung water filter"). Kâr hesaplayıcısında maliyet sonradan
  değiştirilirse paneldeki öneri değişir, kayıt DEĞİŞMEZ — panel bunu `.verdict-saved-note` ile söyler.

## Logo (`assets/hercullogo.svg`)

Dosyayı DEĞİŞTİRME/optimize etme. Kırpma `#svgView(viewBox(...))` ile yapılıyor.
**Tuzak:** "Intelligent" satırı (v3; eskiden "Inteligente") `filterUnits="userSpaceOnUse"` ve bölgesi belirtilmemiş
(varsayılan -%10/%120) bir gölge filtresi kullanıyor; viewBox ~361 birimden kısa ya da
~819 birimden darsa satır HİÇ çizilmiyor. Kenar çubuğunda viewBox bu boyutta tutulup alt
satır kapsayıcının `overflow:hidden`'ı ile gizleniyor. Favicon: `assets/hercul-icon.svg`
(yalnızca ikon şekilleri, orijinal değerlerle).

## Araştırma Kontrol Listesi (`api/checklist.py` + `/api/checklists*`)

- **Snapshot SUNUCUDA alınır:** `POST /api/checklists {analysis_key, marketplace}` →
  `keyword_analysis` log kaydındaki son payload okunur (keyword modunda anahtar =
  `req.keyword`, ASIN modunda `ASIN:{asin}`). MCP çağrısı YOK; istemci değer
  göndermez (member otomatik maddeleri sahte değerle geçiremesin diye). Liste,
  oluşturulduğu andaki snapshot + şablon + eşiklerin KOPYASINI taşır; analiz ya da
  şablon sonradan değişse de mevcut liste değişmez.
- **Liste başlatma iki yoldan:** (1) Ürün Analizi sonucundaki "Kontrol Listesi Başlat", (2) Kontrol Listesi
  sayfasındaki "Yeni Ürün Listesi" → kişinin KENDİ analiz geçmişinden (`GET /api/recent`, anahtar+pazar başına en
  son) seçim; ikisi de aynı `POST /api/checklists`. Yeniden analiz/MCP çağrısı YOK. Aynı ürün için kişinin açık
  listesi varsa panel onay ister (sunucu ikinci listeyi engellemez).
- **6 otomatik madde** (`checklist.evaluate_auto`, elle işaretlenemez — 400):
  arama > 40.000 (yalnızca ana keyword'ün exact satırı; ASIN modunda "Veri yok"),
  ilk 10 rakip ciro toplamı > $500.000, ort. yorum < 800, yeni marka ≥ 3
  (pre_assessment'taki "Güçlü Yeni Marka" değeri), ilk 3 marka payı < %65,
  ort. fiyat $25–$70 (iki uç dahil). Veri yoksa `no_data` → "Veri yok", geçti sayılmaz.
- **Onay & kilit:** owner/admin kilitler; manuel/özel maddelerin HEPSİ işaretli olmalı (409).
  Geçmeyen ya da "Veri yok" olan otomatik madde varsa `POST /lock {reason}` ile **gerekçe
  zorunlu** (boşluklar temizlendikten sonra ≥5 karakter, yoksa 422). Gerekçe, onaylayan ve
  zaman `checklists.approval_reason/locked_by/locked_at`'a, geçilen maddeler
  `overridden_json`'a yazılır; bu maddeler `auto_override=true` ("gerekçeyle geçildi") olarak
  döner, `auto_status` gerçek değerini (fail/no_data) korur. (ASIN listelerinde arama hacmi
  maddesi hep "Veri yok" — gerekçesiz onay hiç mümkün olmazdı.)
  Kilitli listede her değişiklik 423 (owner dahil); kural her UPDATE/DELETE'in içinde
  (`AND status='open'`). Görünürlük: member yalnızca kendi listeleri (başkasınınkine 404).
- **Kilit açma:** `POST /api/checklists/{id}/unlock {reason}` — YALNIZCA owner (`require_owner`,
  admin/member 403), gerekçe zorunlu (422), kilitli olmayan liste 409. Açılınca override'lar
  sıfırlanır (tekrar onayda yeniden gerekçe gerekir). Geçmiş `checklist_events` tablosunda
  (kind lock|unlock, by_email, at, reason, details_json — unlock olayı önceki onayın
  kişi/zaman/gerekçesini de saklar); kilit açılsa da kayıtlar silinmez, detayda `events` olarak döner.
  `approval_reason`/`overridden_json` sütunları `_migrate_schema`'da eklenir.
- **Şablon:** tek kayıt (`checklist_template`, yoksa `DEFAULT_TEMPLATE`), yalnızca owner
  düzenler. 5 aşama ve otomatik maddeler sabit; başlıklar, manuel maddeler ve eşikler
  değişir. "Patent, Marka & Hukuk" (`legal`) aşamasındaki açık maddeler kritik sayılır.

## Lansman Raporu (`api/launch_report.py` + `POST /api/launch-report`)

- Kaynak: kullanıcının `amazon-urun-lansman-raporu` skill'i. Hesaplama metodolojisi BİREBİR
  (formüller ve işlem sırası dahil) — skill'in orijinal Python kodu test oracle'ı olarak çalıştırılıp
  her rakam `==` ile karşılaştırıldı (1/2 varyasyon orijinalle, 4 varyasyon orijinalle doğrulanmış
  N-varyasyonlu transliterasyonla). Formülü "sadeleştirmek" ya da işlem sırasını değiştirmek
  kuruşluk farklar çıkarır — değiştirirsen oracle testini yeniden çalıştır.
- Saf modül, MCP çağrısı YOK, endpoint `require_auth`. weasyprint YOK (Vercel'de sistem
  kütüphaneleri yok): sunucu HTML döner, panel Blob URL ile yeni sekmede açar, tarayıcıdan
  "PDF olarak kaydet". Sayfa numarası `@page @bottom-center` (Chromium 131+ destekler).
  Rapor sayfası kendi CSP'sini taşır (yalnızca nonce'lu tek script: yazdır butonu); tüm form
  metinleri `esc()`, @page içeriği `css_str()` ile kaçırılır.
- Kurallar (skill): TOPLAM MALİYET yalnızca COGS; Vine ek gideri (FBA+kayıt), Reklam, CC ayrı
  gider satırları; ACOS = kampanya reklamı / Bölüm 4 brüt gelir; kampanya günü yalnızca iç hesapta
  (metinde YOK — "~60 gün sonra" cümlesi Amazon'un komisyon ödeme süresidir, kampanya günü değil);
  "optimal"/"%100" yok; bölüm sırası 1-10; reklam ve Vine TEK varyasyonda.
- Veri farkları: SellerSprite keyword verisi AYLIK → Bölüm 2 "Aylık Satış". "Önerilen ACOS" =
  Bid ÷ (purchases/clicks × reklam varyasyonunun KENDİ fiyatı) — panelin pazar-ortalama-fiyatlı
  ACOS'u kopyalanmaz. Ürün Analizi'nden açılınca exact keyword satırı (purchases, clicks, bid,
  avgPrice) ve `market_return_rate` önceden dolar.
- Şablondan bilinçli sapmalar (rakamları etkilemez): tek senaryolu CC tablosunda başlık/sütun
  sayısı düzeltildi; kullanıcının girdiği oranlar (referral, CC, iade) tam sayı değilse ondalıkla yazılır (%6.4, şablon %6 derdi) — Bölüm 7 ACOS değerleri ve hedef ACOS cümlesi şablondaki gibi TAM SAYI; Bölüm 2'ye
  "türetilmiştir" dipnotu (skill metni bunu istiyor); 1 varyasyonda sepet ağırlığı satırı yok;
  sepet ağırlığı girilmezse sevkiyat adedi payı kullanılır.

## Performans (davranış aynı, yalnızca hız)

- **Postgres bağlantı havuzu** (`db_adapter.py`): ilk kullanımda kurulur, en fazla 5 bağlantı
  (`POOL_MAX_SIZE`), `statement_cache_size=0` (Neon PgBouncer — hazırlanmış ifade önbelleği orada hata verir;
  KALDIRMA). Bırakışta asyncpg'nin sıfırlama sorgusu yok (`_reset_conn`: yalnızca yarım işlem varsa ROLLBACK) —
  bu yüzden **oturum durumu kullanma** (SET, LISTEN, advisory kilit, imleç): havuzdaki bir sonraki isteğe taşınır.
  Olay döngüsü değişirse havuz yeniden kurulur. Sunucunun kapattığı boştaki bağlantıda (sorgu gönderilmeden
  gelen hata) bir kez taze bağlantıyla denenir (`_is_dead_connection`). SQLite yolu değişmedi.
  Tek bağlantıya bağlı çalışması gereken iş için `async with db_adapter.single_connection():`.
- **Oturum:** `db.has_users()` — kullanıcı olduğu bir kez görülünce bellekte (COUNT her istekte değil; güvenli
  çünkü kendi hesabını ve son owner'ı silmek yasak). `db.get_session` TEK JOIN'le oturum + ad/soyad + rol
  (`get_user_role` ile aynı kural: OWNER_EMAILS → owner, geçersiz → member, hesap yoksa None) + `in_team`
  getirir; `require_session` bunu isteğe özel `index._AUTH_CTX`'e koyar, `require_user` ve `_forum_viewer`
  ayrı sorgu yapmaz (`_auth_state(user_id)`). Yeni bir yetki bağımlılığı yazarsan bunu kullan.
- **Uzayabilen listeler sunucudan sayfalı:** Ekip Aktivitesi (yukarıda), Raporlar ve Forum (zaten sayfa 10).
  **Raporlar** `GET /api/reports?status=all|decided|pending&q=&range=all|N&market=&page=` — eskiden panelin
  yaptığı birleştirme (kararlar + son `REPORTS_RECENT_LIMIT`=200 sorgu, (keyword küçük harf, pazar) başına tek
  kayıt, en son karar/sorgu), filtreler, KPI'lar (yalnızca aralık + pazar filtresiyle) ve sayfalama
  (`REPORTS_PAGE_SIZE`=20) sunucuda. Excel `POST /api/export/reports {filters}` satırları sunucuda üretir
  (tüm sayfalar; eski `rows` yolu duruyor). Eski panelle aynı veride sıra, alanlar, KPI ve Excel birebir doğrulandı.
  Filtre `status`: all | decided | pending | uygun | sinirda | elenmis | conflict (karar ≠ ön öneri, `item.conflict`);
  `sort`: new | old | az. KPI kartları ve Karar Dağılımı satırları tıklanınca bu filtreyi kurar (2. tık kaldırır).
  Satırdan doğrudan karar verilir/değiştirilir (`POST /api/decision`, MCP YOK; "Ön öneriyi onayla" kısayolu) ve
  kayıtlı analizden kontrol listesi açılır. `decided_by` çıktıda AD SOYAD (`_person`).
- **Karar/sorgu anahtarı tek:** `index._analysis_key()` — ASIN analizlerinde sorgu "ASIN:B0.." ile, eski panelde
  karar "B0.. — başlık" ile kaydediliyordu → Raporlar'da aynı ürün İKİ satırdı. Panel artık kararı `ckAnalysisKey`
  ile gönderir, sunucu `/api/decision`'da ve Raporlar birleştirmesinde eski biçimi "ASIN:B0.."ya çevirir.
  `runAnalysis` "ASIN:" önekini ayıklar (eskiden Raporlar'daki "Yeniden Analiz Et" ASIN'i keyword sanıyordu).
  `/api/decision` karar değerini doğrular (Uygun|Sınırda|Elenmiş, 422).
- **Geçmişten silme kararı da siler:** `POST /api/history/delete` → `db.delete_decisions_for` (kişinin KENDİ
  kararları; ASIN'de eski "B0.. — başlık" biçimi dahil); toplu silme ("Tümünü Temizle") YOK — kullanıcı isteğiyle kaldırıldı, geçmiş ve kararlar
  yalnızca TEK TEK silinir (uçları da yok, geri ekleme). Böylece ürün
  Raporlar'dan ve Ana Sayfa'nın Uygun/Sınırda/Elenmiş sayılarından da kalkar. (Kararlar sayfasından karar silmek
  geçmişe dokunmaz.)

## Tailwind — derlenmiş CSS (CDN YOK)

- `cdn.tailwindcss.com` kaldırıldı; panel repodaki küçültülmüş `tailwind.css`'i yükler (`index.html`'de
  `styles.css`'ten SONRA — eski CDN stilleri de en sona eklendiği için sıralama aynı kaldı). Yapılandırma
  `tailwind/tailwind.config.js` (renk/font token'ları), giriş `tailwind/input.css`.
- **Yeni bir Tailwind sınıfı eklediğinde (index.html ya da app.js) CSS'i yeniden üret ve commit'le:**
  `./scripts/build-css.sh` (`npx tailwindcss@3.4.17 ... --minify`). Üretmezsen sınıf CSS'te olmaz, sessizce görünmez.
- Tarama yalnızca TAM yazılmış sınıfları görür. `bg-${renk}` gibi parça birleştirmeyle sınıf kurma; gerekiyorsa
  olası tüm sonuçları `tailwind.config.js` → `safelist`'e ekle. (Şu an `${...}` ile kurulan sınıfların hepsi
  styles.css'teki özel sınıflar ya da ternary içinde tam literaller — safelist boş.)
