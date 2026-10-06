"""
YETKİNLİK FORMU — 15 bölümün TEK kaynağı (şema + doğrulama + tamamlanma + yetenek haritası alanları).

- Panel formu bu şemadan çizer (`GET /api/competency/schema`); başka yerde bölüm/madde listesi YAZMA.
- Cevaplar düz bir sözlük: alan anahtarı -> değer. 04–13 arası bölümlerdeki tüm seviyeler (1–5)
  tek bir `levels` sözlüğünde tutulur: {madde_anahtarı: seviye}. Yetenek haritası bu sözlüğü okur.
- `clean_answers()` istemciden geleni şemaya göre süzer: bilinmeyen anahtar atılır, metinler kırpılır,
  seçenekler listede olmalı, seviyeler 1–5 tam sayı. Saf modül, veritabanı/MCP yok.
"""

LEVELS = {1: "Bilmiyorum", 2: "Temel", 3: "Orta", 4: "İyi", 5: "İleri"}
CEFR = ["A1", "A2", "B1", "B2", "C1", "C2", "Ana dil"]
LANG_SKILLS = [
    {"key": "reading", "label": "Mesleki Okuma & Analiz"},
    {"key": "writing", "label": "İş / E-posta Yazışması"},
    {"key": "speaking", "label": "Tedarikçi Görüşmesi (Konuşma)"},
    {"key": "terminology", "label": "Teknik Terminoloji Hakimiyeti"},
]
MAX_LANGUAGES = 6
TEXT_MAX = 160
TEXTAREA_MAX = 2000
LIST_ITEM_MAX = 200


def _r(key, label, desc=""):
    return {"key": key, "label": label, "desc": desc}


SECTIONS = [
    {"no": 1, "key": "general", "title": "Genel Profil & Yakın Hissedilen Uzmanlık Alanları", "fields": [
        {"key": "position", "type": "text", "label": "Mevcut Resmi / Şirket İçi Pozisyonunuz",
         "placeholder": "örn. Kıdemli PL Araştırmacı"},
        {"key": "work_model", "type": "single", "label": "Haftalık Ağırlıklı Çalışma Modeli",
         "options": ["Ofis", "Hibrit", "Tam Uzaktan"]},
        {"key": "focus_areas", "type": "multi", "label": "Bugün kendinizi en yakın, yetkin ve üretken hissettiğiniz operasyonel alanlar",
         "options": ["PL Pazar & Niş Araştırması", "Veri Analitiği & Karlılık", "Ürün Geliştirme / Donanım",
                     "Yapay Zeka & Otomasyon (LLM / Python)", "3D Modelleme / CAD", "UI/UX & Görsel Tasarım",
                     "Rakip Tersine Mühendislik", "PPC / Reklam Yönetimi", "Tedarikçi İletişimi (Global)"]},
    ]},
    {"no": 2, "key": "style", "title": "Çalışma Tarzı & Problem Çözme Refleksleri", "fields": [
        {"key": "work_style", "type": "single", "label": "Kişisel Çalışma Stilinizi En Çok Hangisi Tanımlar?", "options": [
            "Analitik & Veri Odaklı (Sayılar konuşsun, hipotez test edilsin)",
            "Hızlı Çıktı & Prototipçi (Mükemmeliyetçilikten önce çalışan versiyon)",
            "Derin Odak & Kusursuz Detaycı (Sıfır hata, estetik ve sağlam zemin)",
            "Stratejist & Bağlayıcı (Büyük resim, iş akışları ve delegasyon)"]},
        {"key": "first_step", "type": "single", "label": "Yeni ve Belirsiz Bir Görev Geldiğinde İlk Adımınız?", "options": [
            "Hemen dokümantasyon, pazar verisi ve örnek vaka taraması yaparım",
            "Hemen AI destekli hızlı bir yol haritası ve taslak üretip ekiple paylaşırım",
            "Görev yöneticisiyle kritik KPI ve beklenti netleştirmesi toplantısı isterim",
            "Kendi başıma 1-2 gün prototip üzerinde denemeler yaparak öğrenirim"]},
        {"key": "energizers", "type": "textarea", "label": "Gününüzde Enerjinizi En Çok Artıran İşler Nelerdir?",
         "placeholder": "örn. Karmaşık bir pazar tablosundan kimsenin fark etmediği bir fırsat metriği çıkarmak…"},
        {"key": "drainers", "type": "textarea", "label": "Enerjinizi En Çok Sömüren / Tüketen Görevler?",
         "placeholder": "örn. Sürekli aynı manuel veri girişini yapmak, belirsiz toplantılar…"},
    ]},
    {"no": 3, "key": "languages", "title": "Yabancı Dil Yetkinlik Kırılımı",
     "intro": "Genel sınav skorları yerine iş yapma esnasındaki akıcılığınızı değerlendiriniz.", "fields": [
        {"key": "languages", "type": "languages", "label": "Diller"},
    ]},
    {"no": 4, "key": "ecom", "title": "E-Ticaret & Amazon Envanter Becerileri", "fields": [
        {"key": "ecom", "type": "ratings", "items": [
            _r("ecom_research", "Amazon Ürün Araştırması & Niş Pazar Doğrulama", "BSR trendleri, ciro konsantrasyonu, giriş bariyerleri analizi"),
            _r("ecom_review_mining", "Rakip İnceleme & Olumsuz Yorum Madenciliği", "Müşteri hayal kırıklıklarını toplayıp ürün geliştirme brifingine çevirme"),
            _r("ecom_tools", "Helium 10 / SellerSprite / SmartScout Araç Seti", "Cerebro, Black Box, Brand Analytics ve tersine ASIN sorgusu"),
            _r("ecom_ppc", "Amazon PPC & Sponsorlu Reklam Algoritması", "ACOS / TACOS optimizasyonu, negatif anahtar kelimeler ve hedefleme"),
            _r("ecom_seller_central", "Seller Central Operasyonları & FBA Lojistik Akışı", "Listing oluşturma, envanter gönderi planları, vaka (case) çözümleri"),
        ]}]},
    {"no": 5, "key": "product", "title": "Ürün Geliştirme, İmalat & Kalite Kontrol", "fields": [
        {"key": "product", "type": "ratings", "items": [
            _r("prod_techpack", "Teknik Spesifikasyon (Tech-Pack) Yazımı", "Fabrikaya verilecek toleranslar, materyal kodu (ABS, SUS304 vb.) dokümantasyonu"),
            _r("prod_sample_test", "Numune Test Metodolojisi & Stres Testi", "Gelen prototip numunenin düşme, yıpranma, fonksiyonel sınırlarını test etme"),
            _r("prod_mold_cnc", "Enjeksiyon Kalıp & CNC İmalat Bilgisi", "Et kalınlığı, açı payı (draft angle), kalıp maliyeti ve hatası sezme"),
            _r("prod_packaging", "Ambalaj & Kutu İçi Yerleşim (CBM)", "Koli ebatları, Amazon tier sınırları (Small standard / Oversize) optimizasyonu"),
        ]}]},
    {"no": 6, "key": "cad", "title": "3D / CAD & Teknik Modelleme", "fields": [
        {"key": "cad", "type": "ratings", "items": [
            _r("cad_fusion", "Fusion 360 / SolidWorks"), _r("cad_render", "Blender / KeyShot Render"),
            _r("cad_drawing", "Teknik Resim Okuma & Tolerans"), _r("cad_ref3d", "Görsel Referanstan 3D Taslak"),
        ]}]},
    {"no": 7, "key": "design", "title": "Tasarım & Kreatif Üretim", "fields": [
        {"key": "design", "type": "ratings", "items": [
            _r("des_figma", "Figma (UI/UX & Şablonlar)"), _r("des_photoshop", "Photoshop / Illustrator"),
            _r("des_aplus", "Amazon A+ Content & İnfografik Kurgusu"), _r("des_mockup", "Ürün Mockup & Yaşam Alanı (Lifestyle)"),
        ]}]},
    {"no": 8, "key": "web", "title": "Web, Yazılım & BT Desteği", "fields": [
        {"key": "web", "type": "ratings", "items": [
            _r("web_python", "Python (Veri Kazıma & Scraper)"), _r("web_sql", "SQL / Veritabanı Sorguları"),
            _r("web_shopify", "Shopify / Webflow Mağaza Kurulumu"), _r("web_frontend", "HTML, CSS & Modern JS"),
        ]}]},
    {"no": 9, "key": "ai", "title": "Yapay Zeka, LLM & Otomasyon", "fields": [
        {"key": "ai", "type": "ratings", "items": [
            _r("ai_prompting", "Claude / ChatGPT İleri Promptlama"), _r("ai_coding", "Claude Code & Cursor ile Kodlama"),
            _r("ai_image", "Midjourney / Flux ile Ürün Görseli"), _r("ai_automation", "Make / Zapier / N8n İş Akışı Kurma"),
        ]}]},
    {"no": 10, "key": "data", "title": "Veri, Excel & Birim İktisat Finansı", "fields": [
        {"key": "data", "type": "ratings", "items": [
            _r("data_excel", "XLOOKUP, INDEX/MATCH, Dinamik Diziler"),
            _r("data_unit_economics", "Birim Karlılık, Net Marj & ROI Hesaplama"),
            _r("data_breakeven", "Başabaş (Break-Even) & Fiyat Elastikiyeti"),
        ]}]},
    {"no": 11, "key": "marketing", "title": "Pazarlama, Reklam & Büyüme", "fields": [
        {"key": "marketing", "type": "ratings", "items": [
            _r("mkt_ppc", "Amazon PPC Stratejisi & Kampanyalar"), _r("mkt_social", "Meta / TikTok Video Reklam Yaratıcılığı"),
            _r("mkt_email", "Klaviyo / E-posta Pazarlama & Sadakat"),
        ]}]},
    {"no": 12, "key": "supplier", "title": "Tedarikçi Ağı & Çin Fabrika Yönetimi", "fields": [
        {"key": "supplier", "type": "ratings", "items": [
            _r("sup_sourcing", "1688 / Alibaba Doğrudan Tedarikçi Bulma"),
            _r("sup_negotiation", "Pazarlık, MOQ İndirimi & Ödeme Şartları"),
            _r("sup_logistics", "FOB, EXW, DDP ve Deniz/Hava Navlun Hesabı"),
        ]}]},
    {"no": 13, "key": "ops", "title": "Süreç Dokümantasyonu & Operasyon", "fields": [
        {"key": "ops", "type": "ratings", "items": [
            _r("ops_sop", "SOP (Standart Operasyon Prosedürü) Yazma"), _r("ops_pm", "Notion / ClickUp / Jira Proje Akışı"),
            _r("ops_escalation", "Kriz Anında Hızlı Eskalasyon & Çözüm"),
        ]}]},
    {"no": 14, "key": "nature", "title": "İşin Doğası: Sevilen ve Zorlanılan Alanlar", "fields": [
        {"key": "loved_tasks", "type": "list", "max_items": 5, "label": "Yaparken Zamanı Unuttuğunuz En Keyifli 3-5 İş"},
        {"key": "struggle_tasks", "type": "list", "max_items": 5, "label": "Sizi En Çok Tıkayan veya Destek Gerektiren İşler"},
    ]},
    {"no": 15, "key": "positioning", "title": "Şirket İçi Öz Konumlandırma & Gizli Beceriler", "fields": [
        {"key": "hidden_skills", "type": "textarea", "label": "Şirketin şu an yeterince bilmediği veya henüz işinizde kullanmadığınız gizli süper güçleriniz"},
        {"key": "more_next_6m", "type": "text", "label": "Önümüzdeki 6 Ayda DAHA ÇOK Yapmak İstediğim"},
        {"key": "less_next_6m", "type": "text", "label": "Önümüzdeki 6 Ayda DAHA AZ Yapmak İstediğim"},
    ]},
]

# Yetenek haritasının alanları: 04–13 bölümlerindeki tüm seviyeli maddeler (sırasıyla).
SKILL_FIELDS = [{**it, "section_no": s["no"], "section_key": s["key"], "section_title": s["title"]}
                for s in SECTIONS for f in s["fields"] if f["type"] == "ratings" for it in f["items"]]
SKILL_KEYS = {f["key"] for f in SKILL_FIELDS}


def schema() -> dict:
    return {"sections": SECTIONS, "levels": LEVELS, "cefr": CEFR, "lang_skills": LANG_SKILLS,
            "max_languages": MAX_LANGUAGES, "limits": {"text": TEXT_MAX, "textarea": TEXTAREA_MAX, "list_item": LIST_ITEM_MAX}}


def _txt(v, limit: int) -> str:
    if not isinstance(v, str):
        return ""
    v = "".join(ch for ch in v if ch in "\n\t" or ord(ch) >= 32)   # kontrol karakterlerini at
    return v.strip()[:limit]


def _level(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and int(v) == v and 1 <= int(v) <= 5:
        return int(v)
    return None


def clean_answers(raw) -> dict:
    """İstemciden gelen cevapları şemaya göre süzer (bilinmeyen alanlar atılır)."""
    raw = raw if isinstance(raw, dict) else {}
    out: dict = {}
    for s in SECTIONS:
        for f in s["fields"]:
            k, t = f["key"], f["type"]
            v = raw.get(k)
            if t == "text":
                out[k] = _txt(v, TEXT_MAX)
            elif t == "textarea":
                out[k] = _txt(v, TEXTAREA_MAX)
            elif t == "single":
                out[k] = v if isinstance(v, str) and v in f["options"] else ""
            elif t == "multi":
                vals = v if isinstance(v, list) else []
                out[k] = [o for o in f["options"] if o in vals]
            elif t == "list":
                vals = v if isinstance(v, list) else []
                out[k] = [x for x in (_txt(i, LIST_ITEM_MAX) for i in vals[: f["max_items"]]) if x]
            elif t == "languages":
                langs = []
                for e in (v if isinstance(v, list) else [])[:MAX_LANGUAGES]:
                    if not isinstance(e, dict):
                        continue
                    name = _txt(e.get("name"), 40)
                    if not name:
                        continue
                    r = e.get("ratings") if isinstance(e.get("ratings"), dict) else {}
                    langs.append({"name": name, "cefr": e.get("cefr") if e.get("cefr") in CEFR else "",
                                  "note": _txt(e.get("note"), TEXT_MAX),
                                  "ratings": {sk["key"]: _level(r.get(sk["key"])) for sk in LANG_SKILLS}})
                out[k] = langs
    lv = raw.get("levels") if isinstance(raw.get("levels"), dict) else {}
    out["levels"] = {key: _level(lv.get(key)) for key in (f["key"] for f in SKILL_FIELDS)}
    out["levels"] = {k: v for k, v in out["levels"].items() if v is not None}
    return out


def _field_done(f: dict, a: dict) -> bool:
    t, v = f["type"], a.get(f["key"])
    if t in ("text", "textarea", "single"):
        return bool(v)
    if t in ("multi", "list"):
        return bool(v)
    if t == "ratings":
        return all((a.get("levels") or {}).get(it["key"]) for it in f["items"])
    if t == "languages":
        return any(e.get("name") and all(e["ratings"].get(sk["key"]) for sk in LANG_SKILLS) for e in v or [])
    return False


def progress(answers: dict) -> dict:
    """Bir bölüm, içindeki TÜM alanlar cevaplanmışsa tamamdır (seviyeli bölümde tüm maddeler puanlanmış)."""
    a = answers or {}
    done = [all(_field_done(f, a) for f in s["fields"]) for s in SECTIONS]
    return {"done": sum(done), "total": len(SECTIONS), "sections": {s["key"]: d for s, d in zip(SECTIONS, done)}}
