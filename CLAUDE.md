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
  OKUNMAZ/YAZILMAZ. `_ensure_staff_in_team()` her açılışta ekipsiz owner/admin/`OWNER_EMAILS`'i varsayılan
  (en eski) ekibe ekler. Yeni kayıt ekip dışı başlar; owner olarak kaydolan (ilk kullanıcı / `OWNER_EMAILS`)
  varsayılan ekibe girer (hiç ekip yoksa "Genel" oluşturulur).
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
  - Owner menü rozeti: `/api/auth/status.new_outsiders_7d` (son 7 günde kaydolan ekip dışı; owner değilse null).
- **Eğitim ataması** `training_lessons.assign_mode` = `all` (tüm ekipler) | `teams` (`training_lesson_teams`,
  DİNAMİK: ekibe sonradan katılan görür, çıkan görmez) | `users` (`training_assignments`). Eski istemci
  `assign_all` gönderirse mod ondan türetilir. Ekip dışı hiçbir ders görmez/tamamlayamaz; tamamlama kayıtları
  üyelik değişince SİLİNMEZ. İlerleme `GET /api/training/progress?team_id=` (atananlar dinamik hesaplanır).
- **Kalıcı silme:** `POST /api/users/{id}/delete {confirm_email}` (onay e-postası tutmazsa 422; kendi hesabı,
  kalıcı owner, son owner → 409). `db.delete_user_completely`: oturumlar, eşikler, geçmiş, kararlar, kendi kontrol
  listeleri (madde + olaylarıyla), eğitim tamamlama/atamaları, ekip üyelikleri ve hesap SİLİNİR; yer tutucu YOK.
  Başkalarının kayıtlarındaki referanslar silinmez, NULL'lanır (`EMAIL_REF_COLUMNS`, `ID_REF_COLUMNS` — eklediği
  dersler, oluşturduğu davetler — ve `checklist_events.details_json`) → panelde "—". **Kişi e-postası/id'si yazan
  yeni bir sütun eklersen `EMAIL_REF_COLUMNS` / `ID_REF_COLUMNS` / `USER_ID_TABLES`'a da ekle.** Aynı e-posta sonra
  yeni (ekip dışı, member) hesap olarak kaydolabilir.

## Ön öneri kuralı (Uygun / Sınırda / Elenmiş)

- **TEK kaynak:** `scoring.py` → `UYGUN_MAX_NEGATIVE`, `ELIMINATE_AT`, `verdict_for()`,
  `verdict_rule()`. Sayıları başka hiçbir yere (JS, HTML metni, doküman) yazma.
- Sunucu kuralı analiz yanıtında `pre_assessment.rule` ({uygun_max_negative, eliminate_at, text})
  ve `GET /api/verdict-rule`'da gönderir. Panelin canlı yeniden hesaplaması (kâr hesaplayıcı →
  Kriter 06 → ön öneri), sade dille özet, rozet ve açıklama metinleri (panel + Ayarlar) bunu kullanır.
  `n/a` kriterler olumsuz sayılmaz.
- Geçmiş kayıtlardaki (`user_query_log.verdict`, `keyword_analysis.verdict`) ön öneriler
  kaydedildikleri andaki kurala göredir; yeniden hesaplanmaz.

## Kriter 03 — ACOS (ilk 5 keyword, ağırlıklı, hesaplanan)

- **Yalnızca SUNUCUDA:** `scoring.weighted_top_acos()` → `ACOS = Σ(bid × clicks) ÷ Σ(purchases × fiyat)`
  (5 keyword'e birlikte reklam verilse toplam harcama ÷ toplam satış). Hem `/api/analyze` hem
  `/api/analyze-asin`; sonuç `pre_assessment.acos_detail` (value, count, keywords[{keyword, rank, bid,
  clicks, purchases, price, spend, sales, acos}], total_spend, total_sales, no_sales, skipped).
- **Hangi 5:** keyword modunda `relevancy` en yüksek 5 (exact ana satır geniş satırın yerine geçerken
  relevancy korunur), ASIN modunda `trafficPercentage` en yüksek 5. **Fiyat:** keyword modunda her
  keyword'ün kendi `avgPrice`'ı, ASIN modunda ürünün kendi fiyatı.
- **Eksik veri:** bid/clicks/purchases/fiyat'tan biri yoksa keyword atlanır, sıradaki alınır. Satışı 0
  olan keyword harcamaya eklenir. <5 geçerli → olanlarla hesaplanır, kartta "N keyword". Hiç yoksa
  "Veri Yok" (n/a). Harcama var ama toplam satış 0 → değer yok, kriter OLUMSUZ.
- Kriter etiketi (`label`) iç anahtar olarak "ACOS" kaldı (eşikler, özet, eski kayıtlar); kartta ve Excel'de
  gösterilen ad "ACOS (ilk 5 keyword, ağırlıklı, hesaplanan)". Kâr hesaplayıcının ACOS ön değeri bu sayı.
- Keyword tablolarındaki satır bazlı ACOS (madde 8'deki `calc_keyword_ad_metrics`) ve **Lansman
  Raporu** (skill gereği ana keyword + kendi fiyatımız) bundan ETKİLENMEZ.

## Kâr analizi (Ürün Analizi → Kâr sekmesi)

- **Referral ORAN olarak tutulur** (varsayılan %15, düzenlenebilir); dolar = oran × satış fiyatı,
  fiyat değişince yeniden hesaplanır ve `/api/profit`'e DOLAR olarak gider (backend imzası aynı).
  Excel Kâr Analizi'nde "Referral Oranı (%)" girdisi + `=oran*fiyat` canlı formülü
  (`profit_analysis.inputs.ref_rate`; eski payload'larda oran $/fiyat'tan türetilir).
- **Analiz öncesi maliyet** (arama kutusu altındaki "Maliyet gir (opsiyonel)": COGS, FBA, Genel
  gider %): yalnızca DOLU alanlar Kâr bölümüne yazılır, Kriter 06 ve ön öneri ilk açılışta buna
  göre gelir, "Analiz öncesi girilen maliyetler kullanıldı" notu çıkar. Boşsa eski varsayılanlar
  (6.00 / 5.50 / %1). Değerler tarayıcıda (`localStorage: pl_pre_cost`) kalır, "Temizle" siler;
  sunucuya gitmez. Not: kayıtlı ön öneri (`user_query_log.verdict`) sunucunun maliyetsiz önerisidir.

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

