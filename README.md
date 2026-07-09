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
