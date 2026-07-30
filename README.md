# SellerSprite Private Label Pazar Analiz Paneli — Vercel Sürümü

Bu klasör Vercel için yeniden düzenlendi: frontend (index.html/app.js/styles.css)
ve backend (api/index.py — FastAPI) **tek Vercel projesinde, aynı domain'de**
birlikte servis edilir. `/api/*` istekleri otomatik olarak Python fonksiyonuna
yönlenir (bkz. `vercel.json`), geri kalan her şey statik dosya olarak sunulur.

## ÖNEMLİ — Vercel'in iki test-aşaması kısıtı

1. **SQLite kalıcı DEĞİL.** Vercel'in Python fonksiyonları sunucusuz çalışır;
   yazılabilir tek yer `/tmp` ve bu dizin her "soğuk başlangıç"ta sıfırlanabilir.
   Yani 24 saatlik önbellek ve "geçmiş" listesi test sırasında beklenmedik
   şekilde boşalabilir. **Bu normal ve bilinen bir durum** — cPanel'e (kalıcı
   sunucu) geçince ortadan kalkar. Şimdilik test amaçlı kabul edilebilir.

2. **Zaman aşımı riski.** Bir keyword analizi ~9-10 sıralı MCP çağrısı yapıyor.
   `vercel.json`'da `maxDuration: 60` saniyeye ayarlandı, ama **Hobby (ücretsiz)
   planda Vercel bunu gerçekte daha düşük bir sınırla sınırlayabilir** (plana
   göre değişir — Vercel dashboard'da Functions sekmesinden gerçek limiti
   görebilirsin). `/api/analyze` timeout hatası verirse, iki çözüm yolu var:
   (a) `Pro` plana geçmek, (b) `main.py`'deki market_* çağrılarını (zaten
   `asyncio.gather` ile paralel) `keyword_miner`/`product_node` ile de
   paralelleştirmek — bu ikinci seçenek ücretsiz kalır, gerekirse birlikte
   yaparız.

## Vercel'e deploy adımları

1. Bu klasörü (`.git` dahil) GitHub'a yükle (manuel web arayüzünden veya
   `git push`) — repo kökünde `vercel.json`, `api/`, `index.html` görünmeli.
2. [vercel.com](https://vercel.com) → **Add New → Project** → GitHub repo'nu seç.
3. **Framework Preset:** "Other" (otomatik algılanmazsa).
4. **Root Directory:** repo kökü (bu klasörün kendisi — alt klasöre GİRME).
5. **Environment Variables** → `SELLERSPRITE_SECRET_KEY` = gerçek key'in.
6. **Deploy.**
7. Deploy bitince Vercel bir URL verir (örn. `https://sellersprite-panel.vercel.app`).
   Frontend zaten aynı domain'den `/api/...`'a istek attığı için (`app.js`'de
   `API_BASE = ""`) **başka hiçbir ayar değiştirmene gerek yok.**

## İlk canlı test

Vercel URL'ini aç, arama kutusuna bir keyword yaz (örn. "samsung water filter
for refrigerators"), **Analiz Et**'e bas. Backend'in gerçek SellerSprite MCP'ye
bağlanmayı ilk kez deneyeceği an burası. Hata alırsan Vercel dashboard'da
**Deployments → (son deploy) → Functions → Logs**'tan hata mesajını kopyala,
birlikte bakarız.

## Sonra cPanel'e taşıma

Test bittiğinde: frontend dosyaları (index.html/app.js/styles.css) doğrudan
cPanel'e taşınabilir (statik, değişiklik gerekmez — `API_BASE`'i o zaman
backend'in yeni adresine göre güncellersin). Backend içinse cPanel'in Python
uygulama desteği (varsa) ya da ayrı bir küçük VPS/Railway gerekir — cPanel'in
çoğu paylaşımlı planı uzun süreli Python arka plan servisi çalıştırmaya uygun
değildir; bu noktaya gelince birlikte değerlendiririz.

---

*Backend modülleri (Hercules Signal Engine v2.1, Bayesian öğrenme, QIPO) ve
bunların test durumu için orijinal README'ye bakılabilir — bu dosya yalnızca
Vercel'e özgü deploy talimatlarını içerir.*

---

## PAYLAŞIMLI KULLANIM — Postgres kurulumu (ZORUNLU)

**Kritik:** `DATABASE_URL` tanımlamazsanız veriler Vercel'in geçici `/tmp`
klasöründeki SQLite'ta tutulur. Bu durumda:
- Kayıtlar rastgele silinir (her soğuk başlangıçta)
- Ekip üyeleri **farklı veri görür** (her sunucusuz örneğin kendi dosyası var)

Panel bu durumda üstte sarı bir uyarı bandı gösterir. Paylaşımlı ve kalıcı
kullanım için Postgres şart:

### Adımlar
1. Vercel projenizde **Storage → Create Database → Postgres** (ücretsiz katman)
   — ya da [neon.tech](https://neon.tech) / [supabase.com](https://supabase.com)
   üzerinden ücretsiz bir Postgres açın.
2. Bağlantı adresini (`postgres://...` ile başlayan) kopyalayın.
3. Vercel → **Settings → Environment Variables** → `DATABASE_URL` olarak ekleyin.
4. **Redeploy** edin (env değişkeni eklemek otomatik redeploy tetiklemez).

Tablolar ilk açılışta otomatik oluşur. Sarı uyarı bandı kaybolduğunda
paylaşımlı depolama aktif demektir.

## GİRİŞ SİSTEMİ

- **İlk kurulumda kimlik doğrulama kapalıdır** (kolay başlangıç için).
- İlk kullanıcı "Yeni Hesap Oluştur" ile kaydolduğu anda panel **otomatik
  kilitlenir** — sonraki tüm erişimler giriş ister.
- Ekip üyeleri kendi e-posta/şifreleriyle kaydolur ama **hepsi aynı ortak
  veriyi görür** (kararlar ve geçmiş paylaşımlıdır, kişiye özel değildir).
- Şifreler PBKDF2-SHA256 (120.000 iterasyon) + rastgele salt ile saklanır,
  düz metin tutulmaz.
- Oturum token'ı tarayıcının localStorage'ında tutulur.

**Neden gerekli:** Vercel URL'iniz herkese açıktır. Giriş olmadan yabancılar
paneli kullanıp SellerSprite kotanızı harcayabilir.

## SİLME İŞLEMLERİ

- **Tekil silme:** Kararlar ve Geçmiş listelerinde her kaydın yanındaki `×`
- **Toplu silme:** Her iki sayfanın başlığındaki "Tümünü Temizle"
- Tüm silme işlemleri giriş yapmayı gerektirir (yetkisiz silme engellenir)

## ASIN İLE ANALİZ (Reverse ASIN)

Arama kutusuna keyword yerine bir **ASIN** (örn. `B071NFVVNG`) yazarsanız panel
otomatik olarak reverse ASIN moduna geçer (`B0` + 8 karakter formatı algılanır):

1. `competitor_lookup(asins=[ASIN])` → ürün detayı + **gerçek kategori**
2. `traffic_keyword` → ASIN'in trafik aldığı keyword'ler (organik sıra, reklam
   sırası, trafik payı, AC rozeti, bid, CVR)
3. `market_*` → ürünün **kendi** kategorisiyle pazar analizi

**Avantajı:** Kategori tahmin edilmez — ürünün kendi browse-node'u kullanılır.
Keyword modundaki kategori belirsizliği bu modda tamamen ortadan kalkar.

**Fark:** ACOS hesabında keyword ortalama fiyatı yerine **ürünün kendi fiyatı**
kullanılır (bu spesifik ürün analiz edildiği için daha doğru). "Relevancy"
sütunu bu modda **trafik payı yüzdesi**ni gösterir.

## KİŞİYE ÖZEL HESAPLAR (v2 — mimari değişiklik)

**Önemli değişiklik:** Kararlar ve Geçmiş artık **paylaşımlı değil, tamamen
kullanıcıya özel**. Her ekip üyesi kendi hesabıyla giriş yapar ve yalnızca
kendi aradığı keyword'leri ve verdiği kararları görür — başkasının kararını
göremez, silemez.

**Ama tekrarlı arama serbest:** Aynı keyword'ü/ASIN'i birden fazla kullanıcı
bağımsız olarak arayabilir. Ham SellerSprite verisi (`keyword_analysis`
tablosu) hâlâ **paylaşımlı önbellek** olarak kalır — yani biri bir keyword'ü
sorguladıysa, bir başkası aynısını sorguladığında SellerSprite'a tekrar
gidilmez (kota tasarrufu), ama bu arama **o kullanıcının kendi Geçmiş'ine**
ayrı bir kayıt olarak düşer. Yani veri kaynağı paylaşımlı, "kim ne baktı ve
ne karar verdi" bilgisi kişiye özel.

**Giriş:** E-posta formatı zorunlu değil — basit bir kullanıcı adı da olur.
Bir kez kaydolduktan sonra hep aynı kullanıcı adı/e-posta + şifre ile giriş
yapılır. Şifreler PBKDF2-SHA256 ile saklanır.

**Veritabanı tabloları (bu mimari için):**
- `keyword_analysis` — paylaşımlı SellerSprite veri önbelleği (24 saat TTL)
- `user_query_log` — kullanıcıya özel "Geçmiş" kaydı
- `market_decision` — kullanıcıya özel "Kararlar" (user_id NOT NULL)
- `users` / `sessions` — kimlik doğrulama
