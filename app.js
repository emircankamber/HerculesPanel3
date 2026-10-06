// SellerSprite PL Panel — frontend
// Backend'i aynı origin'den servis ediyorsan boş bırak; ayrı deploy ettiysen
// tam URL yaz (örn. "https://pl-panel-api.up.railway.app").
const API_BASE = "";

// ---------------------------------------------------------------------------
// Kimlik doğrulama & token yönetimi
// ---------------------------------------------------------------------------
const TOKEN_KEY = "pl_panel_token";
function getToken() { return localStorage.getItem(TOKEN_KEY) || ""; }
function setToken(t) { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); }

/** Her isteğe otomatik Authorization ekler; 401 alırsa giriş ekranını açar. */
async function apiFetch(url, options = {}) {
  const opts = { ...options, headers: { ...(options.headers || {}) } };
  const token = getToken();
  if (token) opts.headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(url, opts);
  if (res.status === 401) {
    setToken("");
    showLogin("Oturumunuz sona erdi — lütfen tekrar giriş yapın.");
  }
  if (res.status === 428) showNameGate();   // ad soyad girilmemiş eski hesap (kapı sunucuda)
  return res;
}

let authRequiredGlobal = false;
// Oturumdaki kullanıcı (yalnızca GÖRÜNÜM için; tüm yetki kontrolleri sunucuda yapılır)
let currentUser = { role: null, user_id: null, email: null, name: null };

// --- Ad soyad kapısı: adı/soyadı olmayan (eski) hesap girmeden panele devam edemez (sunucu 428 verir).
function showNameGate() {
  const o = document.getElementById("name-overlay");
  if (!o || o.style.display === "flex") return;
  hideLogin();
  o.style.display = "flex";
  setTimeout(() => document.getElementById("ng-first")?.focus(), 0);
}
/** "Ad Soyad" -> "AS" (avatar) */
function initials(name) {
  const p = String(name || "").trim().split(/\s+/).filter(Boolean);
  return ((p[0] || "?")[0] + (p.length > 1 ? p[p.length - 1][0] : "")).toLocaleUpperCase("tr-TR");
}

// --- Davet bağlantısı (?invite=KOD): kod yalnızca bu sekmede tutulur, adres çubuğundan hemen silinir.
const INVITE_KEY = "pl_invite";
function pendingInvite() { try { return sessionStorage.getItem(INVITE_KEY) || ""; } catch { return ""; } }
function clearInvite() { try { sessionStorage.removeItem(INVITE_KEY); } catch { /* yok say */ } }
(function captureInvite() {
  try {
    const u = new URL(location.href);
    const code = (u.searchParams.get("invite") || "").trim();
    if (!code) return;
    try { sessionStorage.setItem(INVITE_KEY, code.slice(0, 200)); } catch { /* depolama yoksa yalnızca bu sayfa */ window.__plInvite = code.slice(0, 200); }
    u.searchParams.delete("invite");
    history.replaceState(null, "", u.pathname + (u.search || "") + u.hash);
  } catch { /* yok say */ }
})();
function inviteCode() { return pendingInvite() || window.__plInvite || ""; }

function showNotice(text, kind = "") {
  const el = document.getElementById("app-notice");
  if (!el) return;
  document.getElementById("app-notice-text").textContent = text;
  el.className = `app-notice ${kind}`.trim();
  el.style.display = "flex";
}
function setTeamBadge(n) {
  const b = document.getElementById("nav-team-badge");
  if (b) { b.textContent = n > 99 ? "99+" : String(n || ""); b.style.display = n ? "" : "none"; }
  const t = document.getElementById("tg-new-badge");
  if (t) { t.textContent = b ? b.textContent : String(n || ""); t.style.display = n ? "" : "none"; }
}

function showLogin(errorMsg = "", dismissible = false) {
  const overlay = document.getElementById("login-overlay");
  if (overlay) overlay.style.display = "flex";
  const inv = document.getElementById("login-invite-note");
  if (inv) {
    inv.textContent = inviteCode() ? "Davet bağlantısıyla geldin: \"Yeni Hesap Oluştur\" ile kaydolursan davetin ekibine katılırsın. Bağlantının süresi dolmuşsa ya da iptal edildiyse hesabın yine oluşturulur ama ekip dışı başlarsın." : "";
    inv.style.display = inviteCode() ? "block" : "none";
    if (inviteCode()) document.getElementById("reg-names").style.display = "grid";   // davetle gelen büyük ihtimalle kaydolacak
  }
  const err = document.getElementById("login-error");
  if (err) err.textContent = errorMsg;
  const dismissBtn = document.getElementById("dismiss-login-btn");
  if (dismissBtn) dismissBtn.style.display = dismissible ? "block" : "none";
}
function hideLogin() {
  const overlay = document.getElementById("login-overlay");
  if (overlay) overlay.style.display = "none";
}

async function checkAuthStatus() {
  try {
    const res = await apiFetch(`${API_BASE}/api/auth/status`);
    const s = await res.json();
    authRequiredGlobal = !!s.auth_required;
    currentUser = { role: s.role || null, user_id: s.user_id || null, email: s.email || null, name: s.name || null };
    const profNav = document.querySelector('.nav-btn[data-view="profile"]');
    if (profNav) profNav.style.display = s.logged_in && s.role ? "" : "none";
    if (s.logged_in && s.needs_name) showNameGate();
    // Ekip Aktivitesi menüsü yalnızca owner'a görünür (yetki yine sunucuda: diğerleri 403)
    const teamNav = document.querySelector('.nav-btn[data-view="team"]');
    if (teamNav) teamNav.style.display = currentUser.role === "owner" ? "" : "none";
    setTeamBadge(currentUser.role === "owner" ? s.new_outsiders_7d : 0);
    if (currentUser.role !== "owner" && $("#view-team")?.classList.contains("active")) showView("home");

    // Paylaşımlı depolama uyarısı
    const warnEl = document.getElementById("storage-warning");
    if (warnEl && s.storage && s.storage.warning) {
      warnEl.textContent = "⚠ " + s.storage.warning;
      warnEl.style.display = "block";
    } else if (warnEl) {
      warnEl.style.display = "none";
    }

    const chip = document.getElementById("user-chip");
    const logoutBtn = document.getElementById("logout-btn");
    const openLoginBtn = document.getElementById("open-login-btn");
    if (s.auth_required && !s.logged_in) {
      showLogin();
    } else if (!s.logged_in && inviteCode()) {
      showLogin("", true);   // davet bağlantısı: kayıt ekranını göster (auth henüz kapalı olsa da)
    } else {
      hideLogin();
      if (chip) chip.textContent = s.name || (s.logged_in ? "" : (s.auth_required ? "" : "auth kapalı — henüz kullanıcı yok"));
      if (logoutBtn) logoutBtn.style.display = s.logged_in ? "inline-block" : "none";
    }
    // Giriş yapılmamışsa (auth zorunlu olsun olmasın) manuel giriş/kayıt butonu görünsün
    if (openLoginBtn) openLoginBtn.style.display = s.logged_in ? "none" : "inline-block";
    if (s.logged_in && inviteCode()) {
      clearInvite(); window.__plInvite = "";
      showNotice("Davet bağlantıları yeni kayıtlar içindir — zaten bir hesabın var. Bir ekibe eklenmek için panel yöneticisine (owner) başvur.");
    }
    return s;
  } catch {
    // Backend erişilemezse giriş ekranını zorla açma — kullanıcı en azından hatayı görsün
  }
}

async function doAuth(endpoint) {
  const email = document.getElementById("login-email").value.trim();
  const password = document.getElementById("login-password").value;
  const err = document.getElementById("login-error");
  if (!email || !password) { err.textContent = "E-posta ve şifre gerekli."; return; }
  try {
    const payload = { email, password };
    if (endpoint === "register") {
      // Kayıtta ad ve soyad zorunlu (sunucu da 422 verir)
      const box = document.getElementById("reg-names");
      if (box.style.display === "none") { box.style.display = "grid"; err.textContent = "Yeni hesap için adını ve soyadını da gir."; document.getElementById("reg-first").focus(); return; }
      payload.first_name = document.getElementById("reg-first").value.trim();
      payload.last_name = document.getElementById("reg-last").value.trim();
      if (!payload.first_name || !payload.last_name) { err.textContent = "Ad ve soyad zorunlu."; return; }
      if (inviteCode()) payload.invite = inviteCode();
    }
    const res = await fetch(`${API_BASE}/api/auth/${endpoint}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json();
    if (!res.ok) { err.textContent = apiErrorText(body, res.status); return; }
    if (endpoint === "register" && body.invite) {
      clearInvite(); window.__plInvite = "";
      if (body.invite.status === "joined") showNotice(`Hoş geldin! "${body.invite.team}" ekibine katıldın.`);
      else showNotice("Davet bağlantısı geçersiz, süresi dolmuş ya da iptal edilmiş — hesabın oluşturuldu ama ekip dışı başladın. Bir ekibe eklenmek için panel yöneticisine başvur.", "warn");
    }
    setToken(body.token);
    hideLogin();
    await checkAuthStatus();
    if ($("#view-home")?.classList.contains("active")) loadHome();
    if ($("#view-training")?.classList.contains("active")) loadTraining();
  } catch (e) {
    err.textContent = `Bağlantı hatası: ${e.message}`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const loginBtn = document.getElementById("login-btn");
  const regBtn = document.getElementById("register-btn");
  const logoutBtn = document.getElementById("logout-btn");
  const openLoginBtn = document.getElementById("open-login-btn");
  const dismissLoginBtn = document.getElementById("dismiss-login-btn");
  if (loginBtn) loginBtn.addEventListener("click", () => doAuth("login"));
  if (regBtn) regBtn.addEventListener("click", () => doAuth("register"));
  if (openLoginBtn) openLoginBtn.addEventListener("click", () => showLogin("", !authRequiredGlobal));
  if (dismissLoginBtn) dismissLoginBtn.addEventListener("click", () => hideLogin());
  const ngForm = document.getElementById("name-gate-form");
  if (ngForm) ngForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const first = document.getElementById("ng-first").value.trim(), last = document.getElementById("ng-last").value.trim();
    const errEl = document.getElementById("ng-error");
    if (!first || !last) { errEl.textContent = "Ad ve soyad zorunlu."; return; }
    try {
      const r = await apiFetch(`${API_BASE}/api/profile`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ first_name: first, last_name: last }) });
      const b = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(apiErrorText(b, r.status));
      location.reload();   // kapı kalktı: tüm görünümleri baştan yükle
    } catch (err) { errEl.textContent = err.message; }
  });
  document.getElementById("ng-logout")?.addEventListener("click", async () => {
    await apiFetch(`${API_BASE}/api/auth/logout`, { method: "POST" }); setToken(""); location.reload();
  });
  const noticeClose = document.getElementById("app-notice-close");
  if (noticeClose) noticeClose.addEventListener("click", () => { document.getElementById("app-notice").style.display = "none"; });
  if (logoutBtn) logoutBtn.addEventListener("click", async () => {
    await apiFetch(`${API_BASE}/api/auth/logout`, { method: "POST" });
    setToken("");
    location.reload();
  });
  ["login-email", "login-password"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("keydown", e => { if (e.key === "Enter") doAuth("login"); });
  });

  // Toplu temizleme butonları
  const clearDec = document.getElementById("clear-decisions-btn");
  if (clearDec) clearDec.addEventListener("click", async () => {
    if (!confirm("TÜM pazar kararları kalıcı olarak silinecek. Emin misiniz?")) return;
    await apiFetch(`${API_BASE}/api/decisions/clear`, { method: "POST" });
    loadDecisions();
  });
  const clearHist = document.getElementById("clear-history-btn");
  if (clearHist) clearHist.addEventListener("click", async () => {
    if (!confirm("TÜM sorgu geçmişi kalıcı olarak silinecek. Emin misiniz?")) return;
    await apiFetch(`${API_BASE}/api/history/clear`, { method: "POST" });
    loadHistory();
  });

  // Ana Sayfa varsayılan görünüm: yalnızca DB okuyan uçlar (MCP çağrısı YOK).
  // Giriş zorunlu ve oturum yoksa 401 yerine önce girişi bekle.
  checkAuthStatus().then(s => {
    if (s && s.auth_required && !s.logged_in) return;
    if ($("#view-home")?.classList.contains("active")) loadHome();
  });
});


const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ---------------------------------------------------------------------------
// Navigasyon
// ---------------------------------------------------------------------------
function showView(view) {
  $$(".nav-btn").forEach(b => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach(v => v.classList.remove("active"));
  $(`#view-${view}`).classList.add("active");
  if (view === "history") loadHistory();
  if (view === "decisions") loadDecisions();
  if (view === "reports") loadReports();
  if (view === "home") loadHome();
  if (view === "settings") loadSettings();
  if (view === "training") loadTraining();
  if (view === "checklists") loadChecklists();
  if (view === "launch") lrEnsureVars();
  if (view === "team") loadTeamAdmin();
  if (view === "profile") loadProfile();
  if (view === "trends") updateTrendsStale();
  // Chart.js gizli (display:none) kapsayıcıda 0 boyutla çizer — veri
  // görünümleri yalnızca görünür olduklarında (yeniden) render edilir.
  if (view === "keywords") renderKeywordView();
  if (view === "competitors") renderCompetitorView();
  window.scrollTo({ top: 0 });
}
$$(".nav-btn").forEach(btn => btn.addEventListener("click", () => showView(btn.dataset.view)));
$$("[data-goto]").forEach(btn => btn.addEventListener("click", () => showView(btn.dataset.goto)));

/** Üst çubuktaki veri durumu chip'i ("Veri Hazır · Son çekim …") */
function setDataState(kind, text) {
  const chip = $("#data-state-chip");
  if (!chip) return;
  chip.className = `chip chip-dot ${kind} hidden sm:inline-flex`;
  chip.textContent = text;
}

// ---------------------------------------------------------------------------
// Arama formu
// ---------------------------------------------------------------------------
// Son başarılı analiz yanıtı — Keyword Araştırma ve Rakip Analizi görünümleri
// bu payload'u OLDUĞU GİBİ okur (ek MCP çağrısı ya da yeniden hesaplama yok).
let lastAnalysis = null;
let analysisBusy = false;

/** Tüm görünümlerdeki durum satırlarını aynı anda günceller */
function setAnalysisStatus(text, cls = "") {
  $$("#status-line, .analysis-status").forEach(el => {
    el.textContent = text;
    el.className = `${el.id === "status-line" ? "" : "analysis-status "}status-line mt-3 ${cls}`.trim();
  });
}

/**
 * Tek analiz giriş noktası: Ürün Analizi formu, üst arama çubuğu, Keyword
 * Araştırma formu ve "yeniden analiz" butonları hepsi bunu çağırır — backend
 * sözleşmesi (/api/analyze, /api/analyze-asin) öncekiyle birebir aynı.
 */
async function runAnalysis(keyword, marketplace, categoryOverride = "") {
  keyword = (keyword || "").trim();
  if (!keyword || analysisBusy) return;
  // Reverse ASIN kayıtları geçmişte "B0XXXXXXXX — başlık" olarak tutuluyor; yeniden
  // analizde yalnızca ASIN'i gönder (aksi halde başlık keyword olarak aranırdı).
  const asinPrefix = keyword.match(/^(B0[A-Z0-9]{8})\s+—\s/i);
  if (asinPrefix) keyword = asinPrefix[1];

  $("#kw-input").value = keyword;
  $("#market-input").value = marketplace;
  const kwvInput = $("#kwv-input");
  if (kwvInput) { kwvInput.value = keyword; $("#kwv-market").value = marketplace; }

  analysisBusy = true;
  $$("#search-btn, .analyze-submit").forEach(b => { b.disabled = true; });
  setDataState("warn", "Veri çekiliyor…");

  try {
    // ASIN mi keyword mü? (B0 + 8 alfanümerik = Amazon ASIN formatı)
    const isAsin = /^B0[A-Z0-9]{8}$/i.test(keyword);
    setAnalysisStatus(isAsin
      ? "Reverse ASIN yapılıyor — ürün, keyword'leri ve pazarı çekiliyor…"
      : "SellerSprite MCP'den veri çekiliyor… (9-10 çağrı, birkaç saniye sürebilir)", "loading");

    const res = isAsin
      ? await apiFetch(`${API_BASE}/api/analyze-asin`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ asin: keyword, marketplace }),
        })
      : await apiFetch(`${API_BASE}/api/analyze`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            keyword, marketplace,
            ...(categoryOverride ? { category_override_node_id: categoryOverride } : {}),
          }),
        });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    setAnalysisStatus(data.source === "cache"
      ? "önbellekten yüklendi (24 saat içinde daha önce çekilmiş)"
      : "canlı SellerSprite verisi yüklendi");
    lastAnalysis = data;
    kwvResetSort();  // yeni analiz: sıralama varsayılana (Relevancy / Traffic Share, azalan)
    renderPanel(data);
    renderDataViews();
    const t = data.fetched_at_iso ? new Date(data.fetched_at_iso) : new Date();
    setDataState("ok", `Veri Hazır · Son çekim: ${t.toLocaleTimeString("tr-TR")}`);
  } catch (err) {
    setAnalysisStatus(`Hata: ${err.message}`, "error");
    setDataState("bad", "Veri çekilemedi");
  } finally {
    analysisBusy = false;
    $$("#search-btn, .analyze-submit").forEach(b => { b.disabled = false; });
  }
}

$("#search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  runAnalysis($("#kw-input").value, $("#market-input").value, $("#category-override-input").value.trim());
});
$("#global-search-form")?.addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#global-search-input");
  const kw = input.value.trim();
  if (!kw) return;
  input.value = "";
  // Veri görünümlerinden birindeysen orada kal; değilse Ürün Analizi'ne geç
  const active = $(".view.active")?.id?.replace("view-", "");
  if (!["search", "keywords", "competitors"].includes(active)) showView("search");  // Ana Sayfa dahil diğerlerinden Ürün Analizi'ne geç
  runAnalysis(kw, $("#market-input").value);
});

// ---------------------------------------------------------------------------
// Panel render
// ---------------------------------------------------------------------------
function renderPanel(data) {
  const tpl = $("#tpl-panel").content.cloneNode(true);
  const root = tpl.querySelector(".panel");
  const isAsinMode = data.analysis_mode === "asin";

  // --- Başlık kartı: chip'ler, başlık, kategori breadcrumb ---
  const fetchedTime = data.fetched_at_iso ? new Date(data.fetched_at_iso).toLocaleTimeString("tr-TR") : null;
  const chips = [];
  if (isAsinMode && data.asin) chips.push(`<span class="chip">ASIN: ${esc(data.asin)}</span>`);
  chips.push(`<span class="chip">Pazar: ${esc(data.marketplace)}</span>`);
  chips.push(`<span class="chip chip-dot ok">Canlı SellerSprite MCP Verisi</span>`);
  if (fetchedTime) chips.push(`<span class="chip na">Çekilme: ${fetchedTime}</span>`);
  root.querySelector(".panel-chips").innerHTML = chips.join("");
  root.querySelector(".kw-title").textContent = (isAsinMode && data.asin_info?.title) || data.keyword;

  if (data.category_used) {
    // "A:B:C (gerçek rakip ürün verisinden)" -> breadcrumb + kaynak notu
    const m = String(data.category_used).match(/^(.*?)\s*(\([^()]*\))?\s*$/);
    const path = (m && m[1]) || "";
    const note = (m && m[2]) || "";
    const parts = path.split(/\s*[:›>]\s*/).filter(Boolean);
    root.querySelector(".kw-breadcrumb").innerHTML = parts.length
      ? `<span class="material-symbols-outlined" style="font-size:15px">category</span>` +
        parts.map(p => `<span>${esc(p)}</span>`).join(`<span class="material-symbols-outlined" style="font-size:14px">chevron_right</span>`)
      : "";
    root.querySelector(".kw-sub").innerHTML = note
      ? `<span class="inline-flex items-center gap-1 text-primary font-medium"><span class="material-symbols-outlined" style="font-size:15px">verified</span>Kategori kaynağı</span> ${esc(note.replace(/^\(|\)$/g, ""))}`
      : (!parts.length ? esc(data.category_used) : "");
  }

  // --- Ön öneri rozeti ---
  const pa = data.pre_assessment || {};
  const badge = root.querySelector(".verdict-badge");
  const verdictHint = root.querySelector(".verdict-hint");
  const VERDICT_CLASS = { "Uygun": "uygun", "Sınırda": "sinirda", "Elenmiş": "elenmis" };
  const VERDICT_HINT = { "Uygun": "İlk bakışta girilebilir", "Sınırda": "Yüksek dikkat gerektirir", "Elenmiş": "Zorlu pazar" };
  // Ön öneri kuralı SUNUCUDAN gelir (tek kaynak: scoring.py UYGUN_MAX_NEGATIVE / ELIMINATE_AT).
  // Kural yoksa (beklenmedik eski yanıt) panel yeniden hesaplamaz, sunucunun önerisini gösterir.
  const rule = pa.rule && Number.isFinite(pa.rule.uygun_max_negative) && Number.isFinite(pa.rule.eliminate_at) ? pa.rule : null;
  const verdictFor = (n) => !rule ? null : n <= rule.uygun_max_negative ? "Uygun" : n < rule.eliminate_at ? "Sınırda" : "Elenmiş";
  const NEG_CHIP = { "Uygun": "ok", "Sınırda": "warn", "Elenmiş": "bad" };
  function setVerdict(verdict, negCount) {
    badge.textContent = verdict || "—";
    badge.className = `verdict-badge ${VERDICT_CLASS[verdict] || "sinirda"}`;
    verdictHint.textContent = VERDICT_HINT[verdict] || "";
    root.querySelector(".neg-count").textContent = negCount ?? "—";
    root.querySelector(".neg-chip").className = `neg-chip chip ${NEG_CHIP[verdict] || "na"}`;
  }
  setVerdict(pa.verdict, pa.negative_count);
  root.querySelector(".neg-rule").textContent = rule ? ` · ${rule.eliminate_at}+ = Elenmiş` : "";
  root.querySelector(".verdict-rule-text").textContent = rule ? rule.text : "";

  // --- Sade dille özet: neden bu karar? ---
  function buildPlainSummary(criteria, verdict) {
    const negatives = criteria.filter(c => c.flag === "OLUMSUZ");
    const naCount = criteria.filter(c => c.flag === "n/a").length;
    const REASON = {
      "Ort. Satış Fiyatı": "ortalama fiyat düşük (kar marjı sıkışır)",
      "Gross Margin": "pazarın brüt kar marjı hedefin altında",
      "ACOS": "en ilgili 5 keyword'de reklam maliyeti yüksek",
      "En Büyük Marka Payı": "tek bir marka pazara hakim",
      "Güçlü Yeni Marka (1 yıl)": "son 1 yılda pazara girip tutunabilen marka çok az",
      "Net Kar Marjı (kar analizi)": "girdiğiniz maliyetlerle net kar marjı yetersiz",
    };
    let txt;
    if (!negatives.length) {
      txt = "Tüm kriterler olumlu. Bu pazar ilk bakışta girilebilir görünüyor.";
    } else {
      const reasons = negatives.map(c => REASON[c.label] || c.label).join("; ");
      const head = `${negatives.length} kriter olumsuz, genel değerlendirme ${verdict || "—"}`;
      txt = verdict === "Elenmiş" ? `${head} — bu pazar zorlu görünüyor: ${reasons}.`
        : verdict === "Uygun" ? `${head} (tek başına eleme sebebi değil): ${reasons}.`
        : `${head}: ${reasons}.`;
    }
    if (naCount) txt += ` ${naCount} kriter için veri yok.`;
    return `<div class="font-medium">${esc(txt)}</div><div class="text-xs text-secondary mt-1">Son karar sizindir — aşağıdaki kriter kartlarını ve verileri inceleyip Pazar Kararı'nı işaretleyin.</div>`;
  }
  const summaryEl = root.querySelector(".verdict-summary");

  // --- Ön değerlendirme: mevcut 6 kriter (scoring.py) — yalnızca görsel katman ---
  const critGrid = root.querySelector(".crit-grid");
  // KRİTİK DÜZELTME: eskiden büyüklüğe bakarak ("< 3 ise yüzdedir") tahmin
  // ediyordu — "Güçlü Yeni Marka" gibi düz SAYI kriterlerinde (örn. 2 marka)
  // bunu yanlışlıkla "200.0%" gösteriyordu (gerçek kullanıcı raporuyla bulundu).
  // Artık backend'in gönderdiği c.unit alanına göre kesin biçimlendiriyor.
  // Eski önbellek kayıtlarında (unit alanı eklenmeden önce kaydedilmiş) c.unit
  // boş gelir; bu durumda etiketten çıkarım yapıyoruz. Aksi halde "Ort. Satış
  // Fiyatı 35.48" -> "%3548" gibi absürt değerler çıkıyordu.
  const UNIT_BY_LABEL = {
    "Ort. Satış Fiyatı": "usd",
    "Güçlü Yeni Marka (1 yıl)": "count",
  };
  const resolveUnit = (c) => c.unit || UNIT_BY_LABEL[c.label] || "percent";
  const fmtCrit = (v, unit) => {
    if (v === null || v === undefined) return "n/a";
    if (typeof v !== "number") return v;
    if (unit === "count") return String(v);
    if (unit === "usd") return "$" + v.toFixed(2);
    return (v * 100).toFixed(1) + "%";  // "percent" (varsayılan)
  };
  const critContext = buildCriterionContext(data);
  const criteria = (pa.criteria || []).map(c => ({ ...c }));

  function renderCriterion(div, c, idx) {
    const unit = resolveUnit(c);
    const ctx = critContext[c.label] || {};
    const isNetMargin = c.label === "Net Kar Marjı (kar analizi)";
    const flagInfo = c.flag === "OK" ? ["ok", "Olumlu"]
      : c.flag === "OLUMSUZ" ? ["bad", "Olumsuz"]
      : ["na", isNetMargin ? "Veri Bekleniyor" : "Veri Yok"];
    const dirLabel = c.direction === ">=" ? "≥" : "≤";
    const hasVal = typeof c.value === "number";

    // Eşik çubuğu: yüzde kriterleri 0-100% ölçeğinde, diğerleri eşiğin 2 katına kadar
    const scaleMax = unit === "percent"
      ? Math.max(1, hasVal ? c.value : 0)
      : Math.max(c.threshold * 2, hasVal ? c.value * 1.1 : 0) || 1;
    const fillPct = hasVal ? Math.max(0, Math.min(100, (c.value / scaleMax) * 100)) : 0;
    const markerPct = Math.max(0, Math.min(100, (c.threshold / scaleMax) * 100));

    const valueHtml = hasVal
      ? `${fmtCrit(c.value, unit)}${ctx.suffix ? `<small>${ctx.suffix}</small>` : ""}`
      : `<span class="text-outline">—</span>`;
    const vizHtml = typeof ctx.viz === "function" ? ctx.viz(c) : (ctx.viz || "");

    div.className = "crit card";
    div.dataset.label = c.label;
    div.dataset.flag = c.flag;
    div.dataset.direction = c.direction;
    div.dataset.threshold = c.threshold;
    div.dataset.unit = unit;
    div.innerHTML = `
      <div class="crit-head">
        <div class="min-w-0">
          <div class="eyebrow">Kriter ${String(idx + 1).padStart(2, "0")}</div>
          <div class="crit-name">${esc(ctx.title || c.label)}</div>
        </div>
        <span class="crit-flag chip ${flagInfo[0]}">${flagInfo[1]}</span>
      </div>
      <div class="crit-body">
        <div class="min-w-0">
          <div class="crit-val">${valueHtml}</div>
          <div class="crit-sub">${ctx.sub || ""}</div>
        </div>
        <div class="crit-viz">${vizHtml}</div>
      </div>
      <div class="crit-track">
        <div class="crit-fill ${c.flag === "OK" ? "" : c.flag === "OLUMSUZ" ? "bad" : "na"}" style="width:${fillPct}%"></div>
        <div class="crit-marker" style="left:calc(${markerPct}% - 1px)" title="Eşik"></div>
      </div>
      <div class="crit-foot">
        <span>Eşik: ${dirLabel} ${fmtCrit(c.threshold, unit)}</span>
        <button type="button" class="crit-more">Detay Gör <span class="material-symbols-outlined" style="font-size:16px">expand_more</span></button>
      </div>
      <div class="crit-help">${esc(CRIT_HELP[c.label] || "")}${typeof ctx.detail === "function" ? ctx.detail(c) : ""}${isNetMargin
        ? ` <button type="button" class="crit-goto-profit crit-more">Kâr hesaplayıcıyı aç <span class="material-symbols-outlined" style="font-size:15px">arrow_forward</span></button>` : ""}</div>`;
    div.querySelector(".crit-more").addEventListener("click", () => div.classList.toggle("open"));
    const gotoProfit = div.querySelector(".crit-goto-profit");
    if (gotoProfit) gotoProfit.addEventListener("click", () => {
      activateTab("profit");
      root.querySelector('[data-pane="profit"]').scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  criteria.forEach((c, idx) => {
    const div = document.createElement("div");
    renderCriterion(div, c, idx);
    critGrid.appendChild(div);
  });
  if (summaryEl) summaryEl.innerHTML = buildPlainSummary(criteria, pa.verdict);

  // --- Ön değerlendirmeyi kar analizindeki net marjla güncelle (canlı) ---
  function updateNetMarginCriterion(marginValue) {
    const idx = criteria.findIndex(c => c.label === "Net Kar Marjı (kar analizi)");
    if (idx < 0) return;
    const c = criteria[idx];
    c.value = marginValue;
    c.flag = marginValue >= c.threshold ? "OK" : "OLUMSUZ";
    const card = [...critGrid.children].find(el => el.dataset.label === c.label);
    const wasOpen = card.classList.contains("open");
    renderCriterion(card, c, idx);
    if (wasOpen) card.classList.add("open");

    // Toplam olumsuz sayısını ve ön öneriyi SUNUCUNUN kuralıyla yeniden hesapla
    const negCount = criteria.filter(x => x.flag === "OLUMSUZ").length;
    const newVerdict = verdictFor(negCount) ?? pa.verdict;
    setVerdict(newVerdict, negCount);
    if (summaryEl) summaryEl.innerHTML = buildPlainSummary(criteria, newVerdict);
  }

  // --- Detay sekmeleri ---
  function activateTab(name) {
    root.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
    root.querySelectorAll(".tab-pane").forEach(p => p.classList.toggle("active", p.dataset.pane === name));
  }
  root.querySelectorAll(".tab-btn").forEach(b => b.addEventListener("click", () => activateTab(b.dataset.tab)));

  // ASIN modunda "İlgililik" sütunu aslında TRAFİK PAYI'nı gösteriyor — başlığı düzelt
  const relHeader = root.querySelector(".th-relevancy");
  if (relHeader && isAsinMode) {
    relHeader.textContent = "Traffic Share";
    relHeader.title = "Bu ürünün toplam trafiğinin yüzde kaçı bu kelimeden geliyor";
  }

  // --- ASIN bilgi bloğu (yalnızca reverse ASIN modunda) ---
  if (isAsinMode && data.asin_info) {
    const block = root.querySelector(".asin-info-block");
    const grid = root.querySelector(".asin-info-grid");
    if (block && grid) {
      block.style.display = "block";
      const a = data.asin_info;
      const cells = [
        ["ASIN", a.asin], ["Marka", a.brand], ["Fiyat", a.price != null ? "$" + Number(a.price).toFixed(2) : null],
        ["Aylık Satış", a.units != null ? fmtCompact(a.units) : null],
        ["Aylık Ciro", a.revenue != null ? "$" + fmtCompact(Math.round(a.revenue)) : null],
        ["BSR", a.bsr], ["Rating", a.rating], ["Review", a.ratings != null ? fmtCompact(a.ratings) : null],
        ["Fulfillment", a.fulfillment], ["Trafik KW Sayısı", a.total_traffic_keywords],
      ].filter(([, v]) => v !== null && v !== undefined);
      grid.innerHTML = cells.map(([l, v]) =>
        `<div class="stat-card"><div class="stat-label">${l}</div><div class="stat-value">${esc(v)}</div></div>`).join("");
    }
  }

  // --- Pazar özeti ---
  const stats = data.market_stats || {};
  const statGrid = root.querySelector(".stat-grid");
  const statEntries = [
    ["Ort. Fiyat", stats.avgPrice, "$", "Pazardaki ürünlerin ortalama satış fiyatı"],
    ["Ort. Rating", stats.avgRating, "", "Pazardaki ürünlerin ortalama yıldız puanı (5 üzerinden)"],
    ["Ort. Review", stats.avgRatings, "compact", "Ürün başına ortalama yorum sayısı. Yüksekse pazara girmek zor."],
    ["Toplam Marka", stats.brands, "", "Pazarda satış yapan toplam marka sayısı"],
    ["Ort. Satıcı", stats.avgSellers, "", "Bir listing'de ortalama kaç satıcı var. Yüksekse Buy Box rekabeti sert."],
    ["Yeni Ürün (12 ay)", stats.newProducts, "", "Son 12 ayda pazara giren ürün sayısı"],
    ["Yeni Ürün Oranı", stats.newProductProportion, "%mul100", "Yeni ürünlerin toplam içindeki payı. Yüksekse pazar hareketli."],
    ["İlk Listing", stats.firstShelfDate, "", "Bu pazardaki en eski ürünün listelenme tarihi. Eskiyse pazar oturmuş."],
  ];
  statEntries.forEach(([label, value, unit, help]) => {
    if (value === undefined || value === null) return;
    const div = document.createElement("div");
    div.className = "stat-card";
    if (help) div.title = help;
    let display = value;
    if (unit === "$") display = "$" + Number(value).toFixed(2);
    if (unit === "compact") display = fmtCompact(value);
    if (unit === "%mul100") {
      const v = Number(value);
      // GÜVENLİK: SellerSprite bazı alanları zaten yüzde (örn. 27.78) bazılarını
      // oran (0.2778) olarak dönebiliyor. Büyüklüğe göre otomatik algıla —
      // >1 ise zaten yüzdedir, tekrar 100'le çarpma (daha önce "%2778.0" hatası buradan geliyordu).
      display = (v > 1 ? v : v * 100).toFixed(1) + "%";
    }
    div.innerHTML = `<div class="stat-label">${label}</div><div class="stat-value">${esc(display)}</div>`;
    statGrid.appendChild(div);
  });

  // --- Grafikler ---
  requestAnimationFrame(() => {
    drawBrandChart(root.querySelector(".chart-brand"), data.brand_concentration || []);
    drawPriceChart(root.querySelector(".chart-price"), data.price_distribution || []);
    drawLaunchChart(root.querySelector(".chart-launch"), data.launch_distribution || []);

    // Search Volume Trend: ÖNCELİK grafik. Grafik çizilemezse (veri yok ya da
    // Chart.js hatası) en azından son 3 ayı YAZI olarak göster — kullanıcı
    // hiçbir durumda elini boş dönmesin.
    const trendCanvas = root.querySelector(".chart-trend");
    const trendFallback = root.querySelector(".chart-fallback-text");
    const svt = data.search_volume_trend || [];
    let chartOk = false;
    if (svt.length) {
      try {
        drawTrendChart(trendCanvas, svt);
        chartOk = true;
      } catch (e) {
        console.error("Search volume grafiği çizilemedi:", e);
      }
    }
    if (!chartOk) {
      trendCanvas.style.display = "none";
      if (trendFallback) {
        trendFallback.style.display = "block";
        if (svt.length) {
          const last3 = svt.slice(-3);
          trendFallback.innerHTML = "Son " + last3.length + " ay arama hacmi:<br>" +
            last3.map(m => `<b>${m.month}:</b> ${(m.search_volume ?? 0).toLocaleString("tr-TR")}`).join(" &nbsp;·&nbsp; ");
        } else {
          trendFallback.textContent = "Search volume verisi bu keyword için şu an mevcut değil.";
        }
      }
    }
  });

  // --- Relevant keywords tablosu (tarayıcıda sıralanır; her yeni analizde varsayılan: Relevancy/Traffic Share ↓) ---
  const tbody = root.querySelector(".kw-tbody");
  const kwSort = { ...KW_SORT_DEFAULT };
  const kwSortedRows = () => sortKeywordRows(data.keyword_rows || [], kwSort.key, kwSort.dir);
  const renderKwTable = () => {
    tbody.innerHTML = "";
    kwSortedRows().forEach(row => {
      const tr = document.createElement("tr");
      const acos = row.acos;
      const acosClass = acos == null ? "" : acos < 0.2 ? "acos-good" : acos < 0.5 ? "acos-mid" : "acos-bad";
      tr.innerHTML = `
      <td class="font-medium">${esc(row.keyword ?? "")}</td>
      <td title="${fmtNum(row.searches)}">${fmtCompact(row.searches)}</td>
      <td title="${fmtNum(row.clicks)}">${fmtCompact(row.clicks)}</td>
      <td title="${fmtNum(row.purchases)}">${fmtCompact(row.purchases)}</td>
      <td title="hesaplanan">${row.click_cvr != null ? (row.click_cvr * 100).toFixed(1) + "%" : "n/a"}</td>
      <td>${row.bid != null ? "$" + row.bid.toFixed(2) : "n/a"}</td>
      <td class="${acosClass}">${acos != null ? (acos * 100).toFixed(1) + "%" : "n/a"}</td>
      <td>${row.cpa != null ? "$" + row.cpa.toFixed(2) : "n/a"}</td>
      <td>${row.relevancy != null ? esc(row.relevancy) + (data.analysis_mode === "asin" ? "%" : "") : "n/a"}</td>`;
      tbody.appendChild(tr);
    });
    markSortedHeaders(root.querySelectorAll(".kw-table th[data-sort]"), kwSort);
  };
  bindSortHeaders(root.querySelectorAll(".kw-table th[data-sort]"), kwSort, renderKwTable);
  renderKwTable();

  // --- Kar analizi (canlı) ---
  const profitInputs = ["cogs", "sale", "fba", "ref", "acos", "ret", "gen"].map(k => root.querySelector(`.p-${k}`));

  // Gerçek pazar verisiyle önceden doldur (kullanıcı hâlâ istediği gibi değiştirebilir)
  const marketAvgPrice = data.market_stats?.avgPrice;
  if (marketAvgPrice) root.querySelector(".p-sale").value = marketAvgPrice.toFixed(2);
  // ACOS ön değeri = Kriter 03 (ilk 5 keyword ağırlıklı, sunucu hesabı); düzenlenebilir
  const acosCrit = (pa.criteria || []).find(c => c.label === "ACOS");
  const weightedAcos = typeof acosCrit?.value === "number" ? acosCrit.value : null;
  if (weightedAcos != null) root.querySelector(".p-acos").value = (weightedAcos * 100).toFixed(1);
  if (data.market_return_rate != null) root.querySelector(".p-ret").value = (data.market_return_rate * 100).toFixed(2);
  // Analiz öncesi girilen maliyetler (opsiyonel): yalnızca DOLU alanlar Kâr bölümüne yazılır
  const preCost = readPreCost();
  const preUsed = [];
  if (preCost.cogs != null) { root.querySelector(".p-cogs").value = preCost.cogs; preUsed.push(`COGS $${preCost.cogs.toFixed(2)}`); }
  if (preCost.fba != null) { root.querySelector(".p-fba").value = preCost.fba; preUsed.push(`FBA $${preCost.fba.toFixed(2)}`); }
  if (preCost.gen != null) { root.querySelector(".p-gen").value = preCost.gen; preUsed.push(`Genel gider %${preCost.gen}`); }
  const preNote = root.querySelector(".profit-precost-note");
  if (preNote && preUsed.length) {
    preNote.textContent = `✓ Analiz öncesi girilen maliyetler kullanıldı — ${preUsed.join(" · ")}.`;
    preNote.style.display = "";
  }
  // Kaynağını panelde belirt (şeffaflık — hangi değerler gerçek, hangileri hâlâ manuel varsayım)
  const profitSourceNote = root.querySelector(".profit-source-note");
  if (profitSourceNote) {
    const sources = [];
    if (marketAvgPrice) sources.push("Satış Fiyatı: pazar ortalaması");
    if (weightedAcos != null) sources.push(`ACOS: ilk ${pa.acos_detail?.count ?? 5} keyword ağırlıklı (hesaplanan)`);
    if (data.market_return_rate != null) sources.push("Return Rate: pazar ortalaması");
    const manual = ["COGS", "FBA"].filter((_, i) => (i === 0 ? preCost.cogs : preCost.fba) == null);
    profitSourceNote.textContent = sources.length
      ? `✓ Gerçek veriyle dolduruldu — ${sources.join(" · ")}.${manual.length ? ` ${manual.join("/")} hâlâ manuel girilmeli.` : ""} Referral oranı kategoriye göre değiştirilebilir.`
      : "";
  }
  let lastProfitResult = null;  // Excel export'ta kullanılacak
  const refUsdEl = root.querySelector(".p-ref-usd");
  const recalcProfit = async () => {
    const [cogs, sale, fba, refRate, acos, ret, gen] = profitInputs.map(i => parseFloat(i.value) || 0);
    // Referral ORAN olarak tutulur; dolar karşılığı = oran × satış fiyatı (fiyat değişince yeniden hesaplanır)
    const ref = refRate / 100 * sale;   // Excel'deki =oran×fiyat formülüyle aynı (yuvarlamasız)
    if (refUsdEl) refUsdEl.textContent = `= $${ref.toFixed(2)}`;
    try {
      const res = await apiFetch(`${API_BASE}/api/profit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cogs, sale_price: sale, fba_fee: fba, referral_fee: ref,
          acos: acos / 100, return_rate: ret / 100, overhead_rate: gen / 100,
        }),
      });
      const p = await res.json();
      lastProfitResult = { ...p, inputs: { cogs, sale, fba, ref, ref_rate: refRate, acos, ret, gen } };
      root.querySelector(".o-adv").textContent = "$" + p.ad_cost.toFixed(2);
      root.querySelector(".o-retc").textContent = "$" + p.return_cost.toFixed(2);
      root.querySelector(".o-tot").textContent = "$" + p.total_cost.toFixed(2);
      root.querySelector(".o-profit").textContent = "$" + p.unit_profit.toFixed(2);
      root.querySelector(".o-margin").textContent = (p.margin * 100).toFixed(1) + "%";
      root.querySelector(".o-roi").textContent = (p.roi * 100).toFixed(1) + "%";
      const profitEl = root.querySelector(".o-profit");
      profitEl.style.color = p.unit_profit < 0 ? "var(--red)" : "var(--text-primary)";
      if (sale > 0) updateNetMarginCriterion(p.margin);  // ön değerlendirmeyi canlı güncelle
    } catch { /* backend geçici erişilemezse sessiz geç */ }
  };
  profitInputs.forEach(i => i.addEventListener("input", recalcProfit));
  recalcProfit();

  // --- Top rakipler ---
  let competitorRows = [];  // signal hesaplamasında kullanılacak

  function renderCompetitorRows(rows) {
    const tbody = root.querySelector(".comp-tbody");
    tbody.innerHTML = "";
    if (!rows.length) {
      tbody.innerHTML = "<tr><td colspan='7' class='muted'>Otomatik rakip bulunamadı — ASIN'leri manuel gir.</td></tr>";
      return;
    }
    rows.forEach(r => {
      const tr = document.createElement("tr");
      const isNew = isWithinLastYear(r.availableDate);
      tr.innerHTML = `<td class="l">
          <div class="flex items-center gap-2"><span class="mono font-semibold">${esc(r.asin ?? "")}</span>${isNew ? '<span class="chip warn">Yeni Giriş</span>' : ""}</div>
          <div class="text-xs muted truncate max-w-[280px]" title="${esc(r.title ?? "")}">${esc(r.brand ?? "")}${r.title ? " · " + esc(r.title) : ""}</div>
        </td>
        <td>$${Number(r.price ?? 0).toFixed(2)}</td>
        <td title="${fmtNum(r.units)} adet">${fmtCompact(r.units)} <span class="muted text-xs">adet</span></td>
        <td class="font-semibold" title="$${fmtNum(Math.round(r.revenue || 0))}">$${fmtCompact(Math.round(r.revenue || 0))}</td>
        <td>${r.bsr != null ? '<span class="chip">#' + fmtNum(r.bsr) + "</span>" : "n/a"}</td>
        <td>${r.rating != null ? '<span class="inline-flex items-center gap-1"><span class="material-symbols-outlined text-tertiary-container" style="font-size:15px">star</span>' + r.rating + "</span>" : "n/a"}</td>
        <td title="${fmtNum(r.reviews ?? r.ratings)} yorum">${fmtCompact(r.reviews ?? r.ratings)}</td>`;
      tbody.appendChild(tr);
    });
  }

  // Otomatik gelen rakipleri (backend'in competitor_lookup çağrısından) hemen göster
  if (data.top_competitors && data.top_competitors.length) {
    competitorRows = data.top_competitors.map(i => ({
      asin: i.asin, brand: i.brand, price: i.price,
      units: i.units ?? 0, revenue: i.revenue ?? 0,
      bsr: i.bsr, rating: i.rating, reviews: i.ratings,
      title: i.title, availableDate: i.availableDate,
    }));
    renderCompetitorRows(competitorRows);
  } else {
    renderCompetitorRows([]);
  }

  root.querySelector(".competitor-fetch-btn").addEventListener("click", async () => {
    const asinsRaw = root.querySelector(".competitor-asins").value.trim();
    if (!asinsRaw) return;
    const asins = asinsRaw.split(",").map(s => s.trim()).filter(Boolean);
    const tbody = root.querySelector(".comp-tbody");
    tbody.innerHTML = "<tr><td colspan='7' class='muted'>yükleniyor…</td></tr>";
    try {
      const res = await apiFetch(`${API_BASE}/api/competitors`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asins, marketplace: data.marketplace }),
      });
      const result = await res.json();
      const items = result?.data?.items || result?.items || [];
      competitorRows = items.map(i => ({
        asin: i.asin, brand: i.brand, price: i.price ?? i.averagePrice,
        units: i.units ?? i.amzUnit ?? 0, revenue: i.revenue ?? i.amzSales ?? 0,
        bsr: i.bsr, rating: i.rating, reviews: i.ratings,
        title: i.title, availableDate: i.availableDate,
      }));
      renderCompetitorRows(competitorRows);
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan='7' class='muted'>Hata: ${esc(err.message)}</td></tr>`;
    }
  });

  // --- Hercules Signal Engine ---
  root.querySelector(".signal-compute-btn").addEventListener("click", async () => {
    const status = root.querySelector(".signal-status");
    status.textContent = "hesaplanıyor…";
    try {
      const payload = buildSignalsPayload(data, competitorRows, root);
      const res = await apiFetch(`${API_BASE}/api/signals`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${res.status}`); }
      const result = await res.json();
      status.textContent = competitorRows.length
        ? "rakip verisiyle hesaplandı"
        : "yaklaşık hesaplandı (rakip verisi çekilmedi — pazar ortalamaları kullanıldı)";
      renderSignalResults(root, result);
    } catch (err) {
      status.textContent = `Hata: ${err.message}`;
    }
  });

  // --- Proof Assets ---
  const refreshProofAssets = async () => {
    const res = await apiFetch(`${API_BASE}/api/proof-assets/${encodeURIComponent(data.keyword)}`);
    const result = await res.json();
    const list = root.querySelector(".proof-list");
    list.innerHTML = "";
    (result.assets || []).forEach(a => {
      const div = document.createElement("div");
      div.className = "proof-item";
      div.innerHTML = `<span>${esc(a.type)} <span class="muted">(${a.points}p)</span> ${a.note ? "· " + esc(a.note) : ""}</span>
        <span style="display:flex;align-items:center;gap:8px;">
          <span class="proof-item-status ${esc(a.status)}">${esc(a.status)}</span>
          ${a.status === "pending" ? `<button data-id="${a.id}">Onayla</button>` : ""}
        </span>`;
      const btn = div.querySelector("button");
      if (btn) btn.addEventListener("click", async () => {
        await apiFetch(`${API_BASE}/api/proof-assets/approve`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ asset_id: a.id, approved_by: "ekip" }),
        });
        refreshProofAssets();
      });
      list.appendChild(div);
    });
    root.querySelector(".proof-total-val").textContent = result.proof_score?.score ?? 0;
  };
  root.querySelector(".proof-add-btn").addEventListener("click", async () => {
    const type = root.querySelector(".proof-type-select").value;
    const note = root.querySelector(".proof-note").value;
    await apiFetch(`${API_BASE}/api/proof-assets`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keyword: data.keyword, type, note }),
    });
    root.querySelector(".proof-note").value = "";
    refreshProofAssets();
  });
  refreshProofAssets();

  // --- Pazar kararı kaydet ---
  root.querySelector(".decision-save").addEventListener("click", async () => {
    const decision = root.querySelector(".decision-select").value;
    const note = root.querySelector(".decision-note").value;
    if (!decision) return;
    await apiFetch(`${API_BASE}/api/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keyword: data.keyword, marketplace: data.marketplace, decision, note }),
    });
    root.querySelector(".decision-saved-msg").textContent = `✓ kaydedildi · ${new Date().toLocaleTimeString("tr-TR")}`;
  });

  // --- Excel export: tam rapor ---
  root.querySelector(".export-report-btn").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    const originalHtml = btn.innerHTML;
    btn.textContent = "hazırlanıyor…";
    try {
      const exportPayload = { ...data, profit_analysis: lastProfitResult };
      const res = await apiFetch(`${API_BASE}/api/export/report`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(exportPayload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await downloadBlob(res, `${data.keyword}_rapor.xlsx`);
    } catch (err) {
      alert(`Excel oluşturulamadı: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
  });

  // --- Excel export: sadece keyword tablosu ---
  root.querySelector(".export-keywords-btn").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    const originalHtml = btn.innerHTML;
    btn.textContent = "hazırlanıyor…";
    try {
      const res = await apiFetch(`${API_BASE}/api/export/keywords`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keyword: data.keyword, keyword_rows: kwSortedRows() }),  // ekrandaki sırayla
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await downloadBlob(res, `${data.keyword}_keywords.xlsx`);
    } catch (err) {
      alert(`Excel oluşturulamadı: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
  });

  // --- Kontrol listesi başlat (sunucu, kayıtlı analizin ANLIK KOPYASINI alır; MCP çağrısı yok) ---
  root.querySelector(".ck-start-btn").addEventListener("click", (e) => startChecklist(data, e.currentTarget));
  root.querySelector(".lr-start-btn").addEventListener("click", () => openLaunchReport(data));

  const container = $("#result-container");
  container.innerHTML = "";
  container.appendChild(tpl);

}

async function downloadBlob(response, filename) {
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  // Türkçe harfler korunur (sunucu da RFC 5987 filename* gönderiyor); yalnızca dosya sisteminde yasak karakterler _
  a.download = filename.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_");
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Büyük sayıları okunabilir kısaltır: 2385059 -> "2,4M", 13392 -> "13,4B" */
function fmtCompact(v) {
  if (v === null || v === undefined || isNaN(v)) return "n/a";
  const n = Number(v);
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1).replace(".", ",") + "M";
  if (Math.abs(n) >= 1e4) return (n / 1e3).toFixed(1).replace(".", ",") + "B";
  return n.toLocaleString("tr-TR");
}

/** Kriterlerin ne anlama geldiğini sade dille açıklar (tooltip için) */
const CRIT_HELP = {
  "Ort. Satış Fiyatı": "Pazardaki ürünlerin ortalama satış fiyatı. Düşük fiyatlı pazarlarda kar marjı sıkışır.",
  "Gross Margin": "Pazardaki ürünlerin ortalama brüt kar marjı. Yüksek olması, fiyatlandırma alanı olduğunu gösterir.",
  "ACOS": "En ilgili 5 keyword'e (ASIN modunda trafik payı en yüksek 5) birlikte reklam verilse oluşacak toplam harcamanın toplam satışa oranı: Σ(bid × tık) ÷ Σ(satış × fiyat). Yüksekse reklamla satmak pahalı demek.",
  "En Büyük Marka Payı": "Pazarın en büyük markasının ciro payı. Tek marka baskınsa girmek zordur.",
  "Güçlü Yeni Marka (1 yıl)": "Son 1 yılda pazara girip üst sıralara çıkabilmiş marka sayısı. Az ise pazar yeni girenlere kapalı demek.",
  "Net Kar Marjı (kar analizi)": "Aşağıdaki kar analizi hesaplayıcısına girdiğiniz maliyetlere göre hesaplanan net kar marjınız.",
};

/** innerHTML'e giden metinler için HTML kaçışı */
function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

/** availableDate (ms ya da tarih metni) son 12 ay içinde mi — backend'in "Güçlü Yeni Marka" proxy'siyle aynı pencere */
function isWithinLastYear(v) {
  if (v === null || v === undefined || v === "") return false;
  const t = typeof v === "number" ? v : Date.parse(v);
  if (!isFinite(t)) return false;
  return Date.now() - t <= 365 * 24 * 3600 * 1000;
}

/** Küçük halka gösterge (kriter kartları) */
function ringSvg(fraction, color) {
  const f = Math.max(0, Math.min(1, Number(fraction) || 0));
  const r = 20, c = 2 * Math.PI * r;
  return `<svg width="56" height="56" viewBox="0 0 56 56" aria-hidden="true">
    <circle cx="28" cy="28" r="${r}" fill="none" stroke="#e5eeff" stroke-width="6"/>
    <circle cx="28" cy="28" r="${r}" fill="none" stroke="${color}" stroke-width="6" stroke-linecap="round"
      stroke-dasharray="${(f * c).toFixed(2)} ${c.toFixed(2)}" transform="rotate(-90 28 28)"/>
    <text x="28" y="31.5" text-anchor="middle" font-size="10.5" font-weight="600" fill="#0b1c30" font-family="Inter,sans-serif">${Math.round(f * 100)}%</text>
  </svg>`;
}
const flagColor = (c) => c.flag === "OK" ? "#005c55" : c.flag === "OLUMSUZ" ? "#ba1a1a" : "#bdc9c6";

/**
 * Mevcut 6 kriterin (scoring.py) kartlarında gösterilecek bağlam + mini görsel.
 * YALNIZCA görsel katman: değer/eşik/flag backend'den gelir, burada yeniden
 * hesaplanmaz — yalnızca payload'daki mevcut alanlar (market_stats, dağılımlar,
 * keyword_rows, top_competitors) açıklama/mini grafik için okunur.
 */
function buildCriterionContext(data) {
  const stats = data.market_stats || {};
  const priceDist = data.price_distribution || [];
  const brands = data.brand_concentration || [];
  const comps = data.top_competitors || [];

  const topBrand = brands.reduce((best, b) =>
    (b.totalRevenueRatio ?? 0) > (best?.totalRevenueRatio ?? -1) ? b : best, null);

  return {
    "Ort. Satış Fiyatı": {
      title: "Ort. Satış Fiyatı",
      suffix: "/ ort.",
      sub: `Kategori ortalama fiyatı${stats.brands != null ? ` · ${fmtNum(stats.brands)} marka` : ""}`,
      viz: () => {
        const vals = priceDist.slice(0, 8).map(i => Number(i.unitsRatio ?? i.ratio ?? i.percentage ?? 0));
        if (!vals.length) return "";
        const max = Math.max(...vals) || 1;
        return `<div class="mini-bars" title="Fiyat dağılımı (satış adedi payı)">${vals.map(v =>
          `<span class="${v === max ? "hl" : ""}" style="height:${Math.max(8, (v / max) * 100)}%"></span>`).join("")}</div>`;
      },
    },
    "Gross Margin": {
      title: "Gross Margin",
      suffix: "brüt marj",
      sub: "Kategori ortalaması (avgProfit)",
      viz: (c) => typeof c.value === "number" ? ringSvg(c.value, flagColor(c)) : "",
    },
    // Kriter 03 — sunucu hesaplar (scoring.weighted_top_acos): Σ(bid×clicks) ÷ Σ(purchases×fiyat), ilk 5 keyword
    "ACOS": {
      title: "ACOS (ilk 5 keyword, ağırlıklı, hesaplanan)",
      sub: (() => {
        const d = data.pre_assessment?.acos_detail;
        if (!d || !d.count) return "";
        const base = `${d.count} keyword · toplam harcama ÷ toplam satış`;
        return d.no_sales ? `${base} · satış yok (harcama ${fmtUsd(d.total_spend)})` : base;
      })(),
      viz: (c) => typeof c.value === "number" && c.value <= 1 ? ringSvg(c.value, flagColor(c)) : "",   // %100 üstünde halka yanıltır
      detail: () => {
        const d = data.pre_assessment?.acos_detail;
        if (!d || !d.count) return "";
        const rankLbl = d.rank_field === "trafficPercentage" ? "Trafik payı" : "Relevancy";
        const rankFmt = (v) => v == null ? "—" : d.rank_field === "trafficPercentage" ? `%${(v * 100).toFixed(1)}` : esc(v);
        const rows = d.keywords.map(k => `<tr><td class="l"><span class="block min-w-[90px] max-w-[160px] whitespace-normal [overflow-wrap:anywhere]">${esc(k.keyword ?? "")}</span></td>
          <td class="font-semibold ${k.acos == null ? "text-error" : ""}">${k.acos == null ? "satış yok" : fmtPct(k.acos)}</td>
          <td>${fmtUsd(k.bid)}</td><td>${fmtNum(k.clicks)}</td><td>${fmtNum(k.purchases)}</td><td>${fmtUsd(k.price)}</td><td>${rankFmt(k.rank)}</td></tr>`).join("");
        return `<div class="acos-detail mt-2 overflow-x-auto">
          <div class="text-[11px] font-semibold text-on-surface">Kullanılan ${d.count} keyword${d.count < d.n ? ` (geçerli verisi olan ${d.count} keyword bulundu)` : ""}</div>
          <table class="data-table text-[11.5px] mt-1"><thead><tr><th class="l">Keyword</th><th>ACOS</th><th>Bid</th><th>Tık</th><th>Satış</th><th>Fiyat</th><th>${rankLbl}</th></tr></thead>
          <tbody>${rows}<tr class="font-semibold"><td class="l">Toplam</td><td>${d.value == null ? "—" : fmtPct(d.value)}</td>
          <td></td><td></td><td></td><td></td><td></td></tr></tbody></table>
          <div class="text-[11px] text-secondary mt-1">Harcama ${fmtUsd(d.total_spend)} ÷ satış ${fmtUsd(d.total_sales)}. Eksik verili keyword'ler atlanır.</div></div>`;
      },
    },
    "En Büyük Marka Payı": {
      title: "En Büyük Marka Payı",
      suffix: "tek marka",
      sub: topBrand?.brand ? `Lider: ${esc(topBrand.brand)}` : "Kategori ciro payı",
      viz: (c) => {
        if (typeof c.value !== "number") return "";
        const lead = Math.max(0, Math.min(100, c.value * 100));
        const col = flagColor(c);
        return `<div class="split-bar">
          <div class="labels"><span style="color:${col}">%${lead.toFixed(0)}<br><span class="font-normal">Lider</span></span>
          <span class="text-secondary text-right">%${(100 - lead).toFixed(0)}<br><span class="font-normal">Diğer</span></span></div>
          <div class="bar"><span style="width:${lead}%;background:${col}"></span></div>
        </div>`;
      },
    },
    "Güçlü Yeni Marka (1 yıl)": {
      title: "Güçlü Yeni Marka (1 yıl)",
      suffix: "marka",
      sub: comps.length
        ? `En çok satan ${comps.length} ürün içinde son 1 yılda listelenen farklı marka`
        : "Rakip verisi yok",
      viz: () => {
        if (!comps.length) return "";
        return `<div class="dot-grid" title="Dolu nokta = son 12 ayda listelenen ürün">${comps.slice(0, 20).map(it =>
          `<span class="${isWithinLastYear(it.availableDate) ? "on" : ""}"></span>`).join("")}</div>`;
      },
    },
    "Net Kar Marjı (kar analizi)": {
      title: "Net Kâr Marjı",
      sub: "Birim Maliyet & Kâr hesaplayıcısından (canlı)",
      viz: (c) => typeof c.value === "number" ? ringSvg(c.value, flagColor(c)) : "",
    },
  };
}

function fmtNum(v) {
  if (v == null) return "n/a";
  return Number(v).toLocaleString("tr-TR");
}

// ---------------------------------------------------------------------------
// Grafikler (Chart.js) — SellerSprite alan adları netleşince burada eşleştir
// ---------------------------------------------------------------------------
// Açık tema paleti (Editorial Intelligence token'ları)
const CHART_COLORS = ["#005c55", "#3f6fb0", "#a15600", "#0f9488", "#7a86a8", "#c98a4b", "#80d5cb", "#bec6e0", "#ba1a1a", "#6e7977"];
const CHART_GRID = "#e5eeff";
const CHART_TICK = "#565e74";
if (window.Chart) {
  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = CHART_TICK;
}

function baseOptions(extra = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: { y: { grid: { color: CHART_GRID }, border: { display: false }, ticks: { color: CHART_TICK } },
              x: { grid: { display: false }, ticks: { color: CHART_TICK } } },
    ...extra,
  };
}
const pctScales = () => ({
  y: { ticks: { callback: v => v + "%", color: CHART_TICK }, grid: { color: CHART_GRID }, border: { display: false } },
  x: { grid: { display: false }, ticks: { color: CHART_TICK } },
});

function drawBrandChart(canvas, items) {
  if (!items || !items.length) return;
  const labels = items.map(b => b.brand ?? b.name ?? "?");
  // GERÇEK ALAN ADI: totalRevenueRatio (gerçek MCP çağrısıyla doğrulandı) — share/percentage yok
  const values = items.map(b => {
    const raw = b.totalRevenueRatio ?? b.share ?? b.percentage ?? 0;
    return raw > 1 ? raw : raw * 100;
  });
  new Chart(canvas, {
    type: "doughnut",
    data: { labels, datasets: [{ data: values, backgroundColor: CHART_COLORS, borderColor: "#ffffff", borderWidth: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: "62%",
      plugins: { legend: { position: "right", labels: { color: CHART_TICK, boxWidth: 8, boxHeight: 8, usePointStyle: true, font: { size: 11 } } } } },
  });
}

function drawPriceChart(canvas, items) {
  // Backend artık düz liste gönderiyor (data.data?.items sarmalı YOK — gerçek yanıt "data" doğrudan liste)
  if (!items || !items.length) return;
  new Chart(canvas, {
    type: "bar",
    data: { labels: items.map(i => i.label ?? i.range), datasets: [{ data: items.map(i => (i.unitsRatio ?? i.ratio ?? i.percentage ?? 0) * 100), backgroundColor: "#0f766e", borderRadius: 4, maxBarThickness: 36 }] },
    options: baseOptions({ scales: pctScales() }),
  });
}

const LAUNCH_LABEL_TR = {
  "1个月": "≤1 ay", "半年": "~6 ay", "1年半": "~1.5 yıl",
  "2年半": "~2.5 yıl", "3年以上": "3+ yıl",
  "1个月以内": "≤1 ay", "3个月": "~3 ay", "6个月": "~6 ay",
  "1年": "~1 yıl", "2年": "~2 yıl", "3年": "~3 yıl",
};
function translateLaunchLabel(label) {
  return LAUNCH_LABEL_TR[label] || label;
}

function drawLaunchChart(canvas, items) {
  if (!items || !items.length) return;
  new Chart(canvas, {
    type: "bar",
    data: { labels: items.map(i => translateLaunchLabel(i.label ?? i.range)), datasets: [{ data: items.map(i => (i.unitsRatio ?? i.ratio ?? i.percentage ?? 0) * 100), backgroundColor: "#a15600", borderRadius: 4, maxBarThickness: 36 }] },
    options: baseOptions({ scales: pctScales() }),
  });
}

// KRİTİK DÜZELTME: eskiden demand_trend'in glanceViews'ı (KATEGORİ genelinde
// milyonlarca sayfa görüntülenmesi) gösteriliyordu — kullanıcı gerçek ekran
// görüntüsüyle "milyonluk trafik yok, bu keyword'ün search volume'u olmalı"
// diye işaret etti, haklıydı. Artık backend'in ayrıca gönderdiği
// search_volume_trend (keyword_research_trends'ten, keyword'ün KENDİ aylık
// arama hacmi) kullanılıyor — gerçek MCP çağrısıyla doğrulandı.
function drawTrendChart(canvas, items) {
  if (!items || !items.length) return;
  new Chart(canvas, {
    type: "line",
    data: { labels: items.map(i => i.month ?? ""), datasets: [{ data: items.map(i => i.search_volume ?? 0), borderColor: "#005c55", backgroundColor: "rgba(15,118,110,0.10)", fill: true, tension: 0.35, pointRadius: 2, borderWidth: 2 }] },
    options: baseOptions(),
  });
}

// ---------------------------------------------------------------------------
// Geçmiş görünümü
// ---------------------------------------------------------------------------
async function loadHistory() {
  const list = $("#history-list");
  list.innerHTML = "<p class='p-6 text-sm text-secondary'>yükleniyor…</p>";
  try {
    const res = await apiFetch(`${API_BASE}/api/recent`);
    const rows = await res.json();
    list.innerHTML = "";
    if (!rows.length) { list.innerHTML = "<p class='p-6 text-sm text-secondary'>Henüz hiç keyword analiz edilmemiş.</p>"; return; }
    rows.forEach(r => {
      const div = document.createElement("div");
      div.className = "hist-row";
      const date = new Date(r.fetched_at * 1000).toLocaleString("tr-TR");
      const vClass = { "Uygun": "ok", "Sınırda": "warn", "Elenmiş": "bad" }[r.verdict] || "na";
      div.innerHTML = `<span class="hist-kw">${esc(r.keyword)} <span class="hist-meta">(${esc(r.marketplace)})</span></span>
        <span style="display:flex;align-items:center;gap:10px;">
          <span class="chip ${vClass}">${esc(r.verdict ?? "—")}</span>
          <span class="hist-meta">${date}</span>
          <button class="card-delete-btn" title="Bu kaydı sil">&times;</button>
        </span>`;
      div.querySelector(".card-delete-btn").addEventListener("click", async (ev) => {
        ev.stopPropagation();
        if (!confirm(`"${r.keyword}" geçmiş kaydı silinsin mi?`)) return;
        await apiFetch(`${API_BASE}/api/history/delete`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ keyword: r.keyword, marketplace: r.marketplace }),
        });
        loadHistory();
      });
      div.addEventListener("click", () => {
        $("#kw-input").value = r.keyword;
        $("#market-input").value = r.marketplace;
        showView("search");
      });
      list.appendChild(div);
    });
  } catch {
    list.innerHTML = "<p class='p-6 text-sm text-error'>Geçmiş yüklenemedi (backend erişilebilir mi kontrol et).</p>";
  }
}

// ---------------------------------------------------------------------------
// Hercules Signal Engine — payload oluşturma ve sonuç render
// ---------------------------------------------------------------------------
function buildSignalsPayload(data, competitorRows, root) {
  const stats = data.market_stats || {};
  const mainRow = (data.keyword_rows || []).find(
    r => (r.keyword || "").toLowerCase() === data.keyword.toLowerCase()
  ) || (data.keyword_rows || [])[0] || {};

  // 3 aylık arama trendi — demand_trend'den yaklaşık türet (gerçek veri yoksa 0)
  const trendItems = data.demand_trend?.data?.items || data.demand_trend?.items || [];
  let svTrend = 0;
  if (trendItems.length >= 4) {
    const last = trendItems[trendItems.length - 1].glanceViews || 0;
    const prev3 = trendItems[trendItems.length - 4].glanceViews || 1;
    svTrend = (last - prev3) / prev3;
  }

  const brandItems = data.brand_concentration || [];
  const brandShares = brandItems.map(b => {
    const s = b.share ?? b.percentage ?? 0;
    return s > 1 ? s / 100 : s;
  });

  let asinRevenueShares, top10RatingsWeighted, top10ReviewCounts, reportedRevenue, units;
  if (competitorRows.length) {
    const totalRev = competitorRows.reduce((s, r) => s + (r.revenue || 0), 0) || 1;
    asinRevenueShares = competitorRows.map(r => (r.revenue || 0) / totalRev);
    top10RatingsWeighted = competitorRows.map(r => [r.rating || 4.0, (r.revenue || 0) / totalRev]);
    top10ReviewCounts = competitorRows.map(r => r.reviews || 0);
    reportedRevenue = competitorRows[0]?.revenue || stats.avgRevenue || 0;
    units = competitorRows[0]?.units || stats.avgUnits || 0;
  } else {
    // YAKLAŞIK: rakip verisi çekilmedi, pazar ortalamalarıyla kaba tahmin
    asinRevenueShares = brandShares.length ? brandShares : [1];
    top10RatingsWeighted = (brandShares.length ? brandShares : [1]).map(s => [stats.avgRating || 4.0, s]);
    top10ReviewCounts = [stats.avgRatings || 0];
    reportedRevenue = stats.avgRevenue || 0;
    units = stats.avgUnits || 0;
  }

  const certs = root.querySelector(".s-certs").value.split(",").map(s => s.trim()).filter(Boolean);

  return {
    keyword: data.keyword, marketplace: data.marketplace, stage: 1,
    brand_shares: brandShares.length ? brandShares : [1],
    asin_revenue_shares: asinRevenueShares,
    top10_ratings_weighted: top10RatingsWeighted,
    top10_review_counts: top10ReviewCounts,
    new_product_revenue_share: stats.newProductProportion ?? 0,
    search_volume: mainRow.searches || 0,
    sv_trend_pct_3m: svTrend,
    click_cvr: mainRow.click_cvr || 0,
    acos: mainRow.acos || 0,
    avg_price: stats.avgPrice || mainRow.avgPrice || 0,
    reported_revenue: reportedRevenue,
    units: units,
    regulation_risk: +root.querySelector(".s-reg").value,
    ip_trademark_risk: +root.querySelector(".s-ip").value,
    supplier_concentration_risk: +root.querySelector(".s-supc").value,
    return_risk: +root.querySelector(".s-ret").value,
    seasonality_cashflow_risk: +root.querySelector(".s-seas").value,
    review_manipulation_risk: +root.querySelector(".s-revm").value,
    category_key: root.querySelector(".s-category").value.trim() || null,
    provided_certs: certs,
    team_verdict: data.pre_assessment?.verdict || "Sınırda",
  };
}

function renderSignalResults(root, result) {
  root.querySelector(".signal-results").style.display = "block";
  const barsEl = root.querySelector(".signal-bars");
  barsEl.innerHTML = "";
  const signals = [
    ["Market", result.market.score, "#0f766e"],
    ["Demand", result.demand.score, "#3f6fb0"],
    ["Truth", result.truth.score, "#565e74"],
    ["Risk", result.risk.score, "#ba1a1a"],
  ];
  signals.forEach(([label, score, color]) => {
    const row = document.createElement("div");
    row.className = "sig-bar-row";
    row.innerHTML = `<div class="sig-bar-label">${label}</div>
      <div class="sig-bar-track"><div class="sig-bar-fill" style="width:${score}%;background:${color}"></div></div>
      <div class="sig-bar-val">${score.toFixed(1)}</div>`;
    barsEl.appendChild(row);
  });

  root.querySelector(".opp-score-val").textContent = result.opportunity_score.toFixed(1);

  const boBadge = root.querySelector(".blue-ocean-badge");
  boBadge.textContent = result.blue_ocean ? "Blue Ocean" : "Blue Ocean değil";
  boBadge.className = "blue-ocean-badge " + (result.blue_ocean ? "yes" : "no");

  const gateBadge = root.querySelector(".stage-gate-badge");
  gateBadge.textContent = result.stage1_gate.passed ? "Kapı: Geçti" : "Kapı: Bloklu";
  gateBadge.className = "stage-gate-badge " + (result.stage1_gate.passed ? "pass" : "fail");

  root.querySelector(".gate-reasons").textContent = result.stage1_gate.reasons.length
    ? "Sebep: " + result.stage1_gate.reasons.join(" · ")
    : result.stage1_gate.note || "";

  const compBanner = root.querySelector(".compliance-banner");
  if (result.compliance && result.compliance.compliance_review_required) {
    compBanner.style.display = "block";
    compBanner.textContent = `⚠ Uygunluk vetosu aktif — eksik belge: ${result.compliance.missing_certs.join(", ")}. Danışman onayı gerekli (CEO override yok).`;
  } else {
    compBanner.style.display = "none";
  }
}

// ---------------------------------------------------------------------------
// Pazar Kararları (Kanban görünümü)
// ---------------------------------------------------------------------------
const DECISION_COLS = { "Uygun": "uygun", "Sınırda": "sinirda", "Elenmiş": "elenmis" };

async function loadDecisions() {
  const summary = $("#decisions-summary");
  summary.textContent = "yükleniyor…";
  try {
    const res = await apiFetch(`${API_BASE}/api/decisions`);
    const grouped = await res.json();

    let total = 0;
    for (const [decision, slug] of Object.entries(DECISION_COLS)) {
      const items = grouped[decision] || [];
      total += items.length;
      $(`#count-${slug}`).textContent = items.length;
      const container = $(`#cards-${slug}`);
      container.innerHTML = "";
      if (!items.length) {
        container.innerHTML = `<div class="decision-empty">Henüz karar yok</div>`;
        continue;
      }
      // en yeni önce (backend zaten decided_at DESC döndürüyor)
      items.forEach(item => {
        const card = document.createElement("div");
        card.className = "decision-card";
        const date = new Date(item.decided_at * 1000).toLocaleDateString("tr-TR");
        card.innerHTML = `
          <div class="decision-card-top">
            <div class="decision-card-kw">${esc(item.keyword)}</div>
            <button class="card-delete-btn" title="Bu kararı sil">&times;</button>
          </div>
          <div class="decision-card-meta"><span>${esc(item.marketplace)}</span><span>${date}</span></div>
          ${item.note ? `<div class="decision-card-note">"${esc(item.note)}"</div>` : ""}
        `;
        card.querySelector(".card-delete-btn").addEventListener("click", async (ev) => {
          ev.stopPropagation();  // karta tıklama (analiz açma) tetiklenmesin
          if (!confirm(`"${item.keyword}" kararı silinsin mi?`)) return;
          await apiFetch(`${API_BASE}/api/decisions/delete`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ keyword: item.keyword, marketplace: item.marketplace }),
          });
          loadDecisions();
        });
        card.addEventListener("click", () => {
          // Sorgu sayfasına dön, keyword'ü doldur, otomatik tekrar analiz et
          showView("search");
          runAnalysis(item.keyword, item.marketplace);
        });
        container.appendChild(card);
      });
    }
    summary.textContent = `toplam ${total} kararlandırılmış keyword`;
  } catch (err) {
    summary.textContent = `Hata: ${err.message}`;
  }
}

// ===========================================================================
// Veri görünümleri: Keyword Araştırma · Rakip Analizi · Raporlar
// YALNIZCA görsel katman — son analiz payload'unu (lastAnalysis) ve mevcut
// /api/decisions + /api/recent uçlarını okur. Hiçbir metrik yeniden
// hesaplanmaz; sıralama/filtreleme/sayfalama tamamen istemci tarafında.
// ===========================================================================

/** Aynı canvas'a tekrar çizmeden önce eski Chart örneğini yok et */
function makeChart(canvas, config) {
  if (!canvas || !window.Chart) return null;
  const old = Chart.getChart(canvas);
  if (old) old.destroy();
  return new Chart(canvas, config);
}

/** Basit sayfalama çubuğu */
function renderPager(el, page, pages, onChange) {
  if (!el) return;
  el.innerHTML = "";
  if (pages <= 1) return;
  const mk = (label, target, { active = false, disabled = false, icon = false } = {}) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `pager-btn${active ? " active" : ""}`;
    b.disabled = disabled;
    b.innerHTML = icon ? `<span class="material-symbols-outlined" style="font-size:16px">${label}</span>` : String(label);
    b.setAttribute("aria-label", icon ? (label === "chevron_left" ? "Önceki sayfa" : "Sonraki sayfa") : `Sayfa ${label}`);
    if (!disabled && !active) b.addEventListener("click", () => onChange(target));
    el.appendChild(b);
  };
  mk("chevron_left", page - 1, { disabled: page <= 1, icon: true });
  const nums = new Set([1, pages, page - 1, page, page + 1].filter(n => n >= 1 && n <= pages));
  let prev = 0;
  [...nums].sort((a, b) => a - b).forEach(n => {
    if (n - prev > 1) { const gap = document.createElement("span"); gap.className = "pager-gap"; gap.textContent = "…"; el.appendChild(gap); }
    mk(n, n, { active: n === page });
    prev = n;
  });
  mk("chevron_right", page + 1, { disabled: page >= pages, icon: true });
}

/** Segment (sekme benzeri) buton grubunu tek seçimli yap */
function bindSegment(container, attr, onChange) {
  if (!container) return;
  container.querySelectorAll(`[data-${attr}]`).forEach(btn => btn.addEventListener("click", () => {
    container.querySelectorAll(`[data-${attr}]`).forEach(b => b.classList.toggle("active", b === btn));
    onChange(btn.dataset[attr]);
  }));
}

const ACOS_CLASS = (acos) => acos == null ? "" : acos < 0.2 ? "acos-good" : acos < 0.5 ? "acos-mid" : "acos-bad";
const fmtPct = (v, d = 1) => v == null || isNaN(v) ? "n/a" : (Number(v) * 100).toFixed(d) + "%";
const fmtUsd = (v, d = 2) => v == null || isNaN(v) ? "n/a" : "$" + Number(v).toFixed(d);
/** Puanı görüntü için 1 haneye yuvarla (değer değişmez, yalnızca biçim) */
const fmtRating = (v) => v == null || v === "" || isNaN(v) ? "n/a" : Number(v).toFixed(1);

function renderDataViews() {
  const active = $(".view.active")?.id;
  if (active === "view-keywords") renderKeywordView();
  if (active === "view-competitors") renderCompetitorView();
}

// ---------------------------------------------------------------------------
// Keyword Araştırma
// ---------------------------------------------------------------------------
// --- Keyword tablosu sıralaması (Ürün Analizi + Keyword Araştırma ortak; tamamen tarayıcıda, MCP yok) ---
// Varsayılan: Relevancy azalan (ASIN modunda aynı alan "Traffic Share" adıyla gösterilir).
const KW_SORT_DEFAULT = { key: "relevancy", dir: -1 };
const KW_TEXT_COLS = new Set(["keyword"]);
const KW_COLLATOR = new Intl.Collator("tr", { sensitivity: "base", numeric: true });

/** Sıralama değeri: Keyword -> metin; diğerleri SAYI ("1,234", "12%" gibi metinler de sayıya çevrilir). Boş/n/a -> null. */
function kwSortValue(row, key) {
  const v = row[key];
  if (KW_TEXT_COLS.has(key)) {
    const t = v == null ? "" : String(v).trim();
    return t === "" ? null : t;
  }
  if (v == null || v === "") return null;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  const t = String(v).replace(/[%,\s$]/g, "");
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** Kararlı sıralama; null değerler yön ne olursa olsun en altta, eşitlikte orijinal sıra korunur. */
function sortKeywordRows(rows, key, dir) {
  if (!key) return [...rows];
  return rows.map((r, i) => ({ r, i, v: kwSortValue(r, key) }))
    .sort((a, b) => {
      if (a.v == null || b.v == null) return a.v == null && b.v == null ? a.i - b.i : a.v == null ? 1 : -1;
      const c = KW_TEXT_COLS.has(key) ? KW_COLLATOR.compare(a.v, b.v) : a.v - b.v;
      return c ? c * dir : a.i - b.i;
    })
    .map(x => x.r);
}

/** Başlık tıklaması: yeni sütun -> sayısal azalan / Keyword A→Z; aynı sütun -> yön tersine. */
function bindSortHeaders(ths, state, rerender) {
  ths.forEach(th => {
    th.classList.add("sortable");
    th.setAttribute("role", "button");
    th.tabIndex = 0;
    const go = () => {
      const k = th.dataset.sort;
      if (state.key === k) state.dir *= -1;
      else { state.key = k; state.dir = KW_TEXT_COLS.has(k) ? 1 : -1; }
      rerender();
    };
    th.addEventListener("click", go);
    th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  });
}

function markSortedHeaders(ths, state) {
  ths.forEach(th => {
    const on = th.dataset.sort === state.key;
    th.classList.toggle("sorted", on);
    th.dataset.dir = on ? (state.dir > 0 ? "asc" : "desc") : "";
    th.setAttribute("aria-sort", on ? (state.dir > 0 ? "ascending" : "descending") : "none");
  });
}

const kwvState = { filter: "all", query: "", sortKey: KW_SORT_DEFAULT.key, sortDir: KW_SORT_DEFAULT.dir, page: 1, perPage: 10 };
function kwvResetSort() { kwvState.sortKey = KW_SORT_DEFAULT.key; kwvState.sortDir = KW_SORT_DEFAULT.dir; kwvState.page = 1; }
const kwvSortState = { get key() { return kwvState.sortKey; }, set key(v) { kwvState.sortKey = v; },
                       get dir() { return kwvState.sortDir; }, set dir(v) { kwvState.sortDir = v; } };

function kwvMainRow(data) {
  const rows = data.keyword_rows || [];
  return rows.find(r => (r.keyword || "").toLowerCase() === String(data.keyword || "").toLowerCase())
    // ASIN modunda ana satır = trafik payı en yüksek keyword (sunucudaki ana satır kuralı)
    || (data.analysis_mode === "asin" ? rows.reduce((best, r) => (r.trafficPercentage ?? -1) > (best?.trafficPercentage ?? -1) ? r : best, null) : null)
    || null;
}

/** Keyword Araştırma: filtrelere uyan TÜM satırlar, ekrandaki sırayla (sayfalamadan bağımsız). */
function kwvVisibleRows(data) {
  const minVol = parseFloat($("#kwv-minvol").value) || 0;
  const q = kwvState.query.toLowerCase();
  const rows = (data.keyword_rows || []).filter(r => {
    if (minVol && (r.searches ?? 0) < minVol) return false;
    if (q && !(r.keyword || "").toLowerCase().includes(q)) return false;
    if (kwvState.filter === "highvol" && (r.searches ?? 0) < 10000) return false;
    if (kwvState.filter === "lowacos" && !(r.acos != null && r.acos < 0.2)) return false;
    return true;
  });
  return sortKeywordRows(rows, kwvState.sortKey, kwvState.sortDir);
}

/** Aktif filtreler: Excel başlığı için Türkçe not ve dosya adı eki ({ title, file }); filtre yoksa null. */
function kwvFilterNote(shown, total) {
  const title = [], file = [];
  if (kwvState.filter === "highvol") { title.push("Yüksek Hacim"); file.push("yüksek-hacim"); }
  if (kwvState.filter === "lowacos") { title.push("Düşük ACOS"); file.push("düşük-acos"); }
  const minVol = parseFloat($("#kwv-minvol").value) || 0;
  if (minVol) { title.push(`min. hacim ${fmtNum(minVol)}`); file.push(`min-hacim-${Math.round(minVol)}`); }
  if (kwvState.query) {
    title.push(`arama: ${kwvState.query}`);
    file.push(`arama-${kwvState.query.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "").slice(0, 24) || "x"}`);
  }
  if (!title.length) return null;
  return { title: `filtreli: ${title.join(" · ")} (${shown}/${total} satır)`, file: `filtreli_${file.join("_")}` };
}

function renderKeywordView() {
  const data = lastAnalysis;
  $("#kwv-empty").classList.toggle("hidden", !!data);
  $("#kwv-content").classList.toggle("hidden", !data);
  $("#kwv-export").disabled = !data;
  if (!data) return;

  const rows = data.keyword_rows || [];
  const main = kwvMainRow(data);
  const isAsin = data.analysis_mode === "asin";

  $("#kwv-k-count").textContent = fmtNum(rows.length);
  $("#kwv-k-vol").textContent = main ? fmtCompact(main.searches) : "n/a";
  $("#kwv-k-vol-sub").textContent = main ? main.keyword : "Ana keyword satırı bulunamadı";
  $("#kwv-k-bid").textContent = main ? fmtUsd(main.bid) : "n/a";
  $("#kwv-k-cpa").textContent = main && main.cpa != null ? `CPA ${fmtUsd(main.cpa)} / satış` : "";
  $("#kwv-k-acos").textContent = main ? fmtPct(main.acos) : "n/a";
  const acosCls = ACOS_CLASS(main?.acos);
  $("#kwv-k-acos-chip").innerHTML = main?.acos == null ? ""
    : `<span class="chip ${acosCls === "acos-good" ? "ok" : acosCls === "acos-mid" ? "warn" : "bad"}">hesaplanan</span>`;
  $("#kwv-k-cvr").textContent = main && main.click_cvr != null ? `Click CVR ${fmtPct(main.click_cvr)} (hesaplanan)` : "";

  const relTh = $(".kwv-th-rel");
  relTh.textContent = isAsin ? "Traffic Share" : "Relevancy";
  relTh.title = isAsin ? "Bu ürünün toplam trafiğinin yüzde kaçı bu kelimeden geliyor" : "Aranan ürünle ilgililik (0-100)";

  renderKeywordTable();

  // Search volume trend (payload'daki search_volume_trend — keyword'ün kendi hacmi)
  const svt = data.search_volume_trend || [];
  const trendCanvas = $("#kwv-trend");
  const trendEmpty = $("#kwv-trend-empty");
  trendCanvas.style.display = svt.length ? "block" : "none";
  trendEmpty.style.display = svt.length ? "none" : "block";
  trendEmpty.textContent = "Search volume verisi bu keyword için şu an mevcut değil.";
  if (svt.length) {
    makeChart(trendCanvas, {
      type: "line",
      data: { labels: svt.map(i => i.month ?? ""), datasets: [{ data: svt.map(i => i.search_volume ?? 0), borderColor: "#005c55", backgroundColor: "rgba(15,118,110,0.10)", fill: true, tension: 0.35, pointRadius: 0, borderWidth: 2 }] },
      options: baseOptions({ scales: {
        y: { grid: { color: CHART_GRID }, border: { display: false }, ticks: { color: CHART_TICK, callback: v => fmtCompact(v) } },
        x: { grid: { display: false }, ticks: { color: CHART_TICK, maxTicksLimit: 6 } } } }),
    });
  } else {
    const old = window.Chart && Chart.getChart(trendCanvas); if (old) old.destroy();
  }

  // En çok satış getiren 5 keyword (purchases'a göre ham sıralama)
  const top = rows.filter(r => r.purchases != null).sort((a, b) => b.purchases - a.purchases).slice(0, 5);
  const maxP = top[0]?.purchases || 1;
  $("#kwv-top-purchases").innerHTML = top.length ? top.map(r => `
    <div>
      <div class="flex justify-between gap-3 text-[13px]"><span class="truncate font-medium" title="${esc(r.keyword)}">${esc(r.keyword)}</span><span class="tabular text-secondary shrink-0">${fmtCompact(r.purchases)}</span></div>
      <div class="h-1.5 rounded-full bg-surface-container mt-1.5"><div class="h-full rounded-full bg-primary-container" style="width:${(r.purchases / maxP) * 100}%"></div></div>
    </div>`).join("") : `<div class="text-xs text-secondary">Satış verisi yok.</div>`;
}

function renderKeywordTable() {
  const data = lastAnalysis;
  if (!data) return;
  const isAsin = data.analysis_mode === "asin";
  const main = kwvMainRow(data);
  const rows = kwvVisibleRows(data);
  markSortedHeaders($$("#kwv-table th[data-sort]"), kwvSortState);

  const total = rows.length;
  const pages = Math.max(1, Math.ceil(total / kwvState.perPage));
  kwvState.page = Math.min(kwvState.page, pages);
  const start = (kwvState.page - 1) * kwvState.perPage;
  const pageRows = rows.slice(start, start + kwvState.perPage);
  const maxVol = Math.max(1, ...(data.keyword_rows || []).map(r => r.searches || 0));

  $("#kwv-result-chip").textContent = `${fmtNum(total)} sonuç`;
  $("#kwv-range").textContent = total ? `${start + 1}–${start + pageRows.length} arası gösteriliyor (toplam ${fmtNum(total)})` : "";
  const tbody = $("#kwv-tbody");
  tbody.innerHTML = pageRows.length ? "" : `<tr><td colspan="10" class="muted !text-center !py-8">Filtreye uyan keyword yok.</td></tr>`;
  pageRows.forEach(row => {
    const isMain = main && row === main;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="l">
        <div class="flex items-center gap-2 min-w-0">
          <span class="material-symbols-outlined ${isMain ? "text-primary" : "text-outline"}" style="font-size:16px" title="${isMain ? "Ana keyword" : "İlişkili keyword"}">${isMain ? "verified" : "tag"}</span>
          <span class="font-medium whitespace-normal break-words min-w-[140px]">${esc(row.keyword ?? "")}</span>
        </div>
      </td>
      <td title="${fmtNum(row.searches)}">
        <div class="flex items-center justify-end gap-2"><span>${fmtCompact(row.searches)}</span>
          <span class="vol-bar"><span style="width:${((row.searches || 0) / maxVol) * 100}%"></span></span></div>
      </td>
      <td title="${fmtNum(row.clicks)}">${fmtCompact(row.clicks)}</td>
      <td title="${fmtNum(row.purchases)}">${fmtCompact(row.purchases)}</td>
      <td>${row.click_cvr != null ? fmtPct(row.click_cvr) : "n/a"}</td>
      <td>${row.bid != null ? fmtUsd(row.bid) : "n/a"}</td>
      <td class="${ACOS_CLASS(row.acos)}">${row.acos != null ? fmtPct(row.acos) : "n/a"}</td>
      <td>${row.cpa != null ? fmtUsd(row.cpa) : "n/a"}</td>
      <td>${row.relevancy != null ? esc(row.relevancy) + (isAsin ? "%" : "") : "n/a"}</td>
      <td class="!text-center"><button type="button" class="icon-btn" title="Bu keyword'ü analiz et (canlı MCP çağrısı)" aria-label="Bu keyword'ü analiz et"><span class="material-symbols-outlined">arrow_forward</span></button></td>`;
    tr.querySelector(".icon-btn").addEventListener("click", () => {
      runAnalysis(row.keyword, data.marketplace);
    });
    tbody.appendChild(tr);
  });
  renderPager($("#kwv-pager"), kwvState.page, pages, (p) => { kwvState.page = p; renderKeywordTable(); });
}

(function bindKeywordView() {
  $("#kwv-form").addEventListener("submit", (e) => {
    e.preventDefault();
    kwvState.page = 1;
    runAnalysis($("#kwv-input").value, $("#kwv-market").value);
  });
  $("#kwv-minvol").addEventListener("input", () => { kwvState.page = 1; renderKeywordTable(); });
  $("#kwv-search").addEventListener("input", (e) => { kwvState.query = e.target.value.trim(); kwvState.page = 1; renderKeywordTable(); });
  bindSegment($("#kwv-filters"), "filter", (f) => { kwvState.filter = f; kwvState.page = 1; renderKeywordTable(); });
  bindSortHeaders($$("#kwv-table th[data-sort]"), kwvSortState, () => { kwvState.page = 1; renderKeywordTable(); });
  // Excel: mevcut /api/export/keywords ucu (payload değişmedi)
  $("#kwv-export").addEventListener("click", async (e) => {
    const data = lastAnalysis;
    if (!data) return;
    const rows = kwvVisibleRows(data);
    if (!rows.length) { alert("Filtreye uyan keyword yok — indirilecek satır bulunmuyor."); return; }
    const note = kwvFilterNote(rows.length, (data.keyword_rows || []).length);
    const btn = e.currentTarget;
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = "hazırlanıyor…";
    try {
      const res = await apiFetch(`${API_BASE}/api/export/keywords`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        // yalnızca filtreye uyan satırlar, ekrandaki sırayla (sayfalamadan bağımsız)
        body: JSON.stringify({ keyword: data.keyword, keyword_rows: rows, note: note ? note.title : null }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await downloadBlob(res, `${data.keyword}_keywords${note ? `_${note.file}` : ""}.xlsx`);
    } catch (err) {
      alert(`Excel oluşturulamadı: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.innerHTML = originalHtml;
    }
  });
})();

// ---------------------------------------------------------------------------
// Rakip Analizi
// ---------------------------------------------------------------------------
const cvState = { scope: "all", bsr: false, fba: false, page: 1, perPage: 10 };
const AMAZON_DOMAIN = { US: "amazon.com", UK: "amazon.co.uk", CA: "amazon.ca" };

function fmtLaunch(v) {
  if (v === null || v === undefined || v === "") return "n/a";
  const t = typeof v === "number" ? v : Date.parse(v);
  if (!isFinite(t)) return esc(v);
  return new Date(t).toLocaleDateString("tr-TR", { month: "short", year: "numeric" });
}
function brandInitials(brand) {
  const words = String(brand || "?").trim().split(/\s+/).filter(Boolean);
  return ((words[0]?.[0] || "?") + (words[1]?.[0] || words[0]?.[1] || "")).toUpperCase();
}
function isFba(f) { return /FBA|AMZ/i.test(String(f || "")); }

function renderCompetitorView() {
  const data = lastAnalysis;
  $("#cv-empty").classList.toggle("hidden", !!data);
  const content = $("#cv-content");
  content.classList.toggle("hidden", !data);
  content.classList.toggle("flex", !!data);
  if (!data) return;

  const comps = data.top_competitors || [];
  const stats = data.market_stats || {};
  const title = data.analysis_mode === "asin" ? (data.asin || data.keyword) : data.keyword;
  $("#cv-market-name").textContent = `${title} (${data.marketplace})`;
  $("#cv-asin-count").textContent = `${comps.length} rakip ASIN`;

  const lead = comps[0];
  $("#cv-top-revenue").textContent = lead?.revenue != null ? "$" + fmtNum(Math.round(lead.revenue)) : "n/a";
  $("#cv-top-revenue-sub").textContent = lead ? `${lead.brand || "?"} · ${fmtCompact(lead.units)} adet/ay` : "Rakip verisi yok";

  // --- Marka ciro payı (brand_concentration — totalRevenueRatio) ---
  const brands = data.brand_concentration || [];
  const brandCanvas = $("#cv-brand-chart");
  if (brands.length) {
    makeChart(brandCanvas, {
      type: "doughnut",
      data: { labels: brands.map(b => b.brand ?? b.name ?? "?"),
        datasets: [{ data: brands.map(b => { const raw = b.totalRevenueRatio ?? 0; return raw > 1 ? raw : raw * 100; }),
          backgroundColor: CHART_COLORS, borderColor: "#ffffff", borderWidth: 2 }] },
      options: { responsive: true, maintainAspectRatio: false, cutout: "64%",
        plugins: { legend: { position: "right", labels: { color: CHART_TICK, boxWidth: 8, boxHeight: 8, usePointStyle: true, font: { size: 11 } } },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.label}: %${Number(ctx.raw).toFixed(1)}` } } } },
    });
  } else {
    const old = window.Chart && Chart.getChart(brandCanvas); if (old) old.destroy();
  }
  const brandCrit = (data.pre_assessment?.criteria || []).find(c => c.label === "En Büyük Marka Payı");
  $("#cv-brand-foot").innerHTML = brandCrit && brandCrit.value != null
    ? `<div class="flex items-center justify-between gap-2"><span>En büyük marka payı <b class="text-on-surface">${fmtPct(brandCrit.value)}</b> · eşik ≤ ${fmtPct(brandCrit.threshold, 0)}</span>
        <span class="chip ${brandCrit.flag === "OK" ? "ok" : brandCrit.flag === "OLUMSUZ" ? "bad" : "na"}">${brandCrit.flag === "OK" ? "Olumlu" : brandCrit.flag === "OLUMSUZ" ? "Olumsuz" : "Veri Yok"}</span></div>`
    : (brands.length ? "" : "Marka konsantrasyonu verisi yok.");

  // --- Fiyat vs. Puan (ham rakip verisi; nokta boyutu yalnızca görsel ölçek) ---
  const pts = comps.filter(c => c.price != null && c.rating != null);
  const maxUnits = Math.max(1, ...pts.map(c => c.units || 0));
  makeChart($("#cv-scatter-chart"), {
    type: "bubble",
    data: { datasets: [{
      data: pts.map(c => ({ x: Number(c.price), y: Number(c.rating), r: 4 + Math.sqrt((c.units || 0) / maxUnits) * 10, _c: c })),
      backgroundColor: "rgba(15,118,110,0.35)", borderColor: "#005c55", borderWidth: 1 }] },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => {
        const c = ctx.raw._c; return ` ${c.brand || c.asin}: $${Number(c.price).toFixed(2)} · ★${c.rating} · ${fmtCompact(c.units)} adet/ay`; } } } },
      scales: {
        x: { title: { display: true, text: "Fiyat ($)", color: CHART_TICK }, grid: { color: CHART_GRID }, ticks: { color: CHART_TICK, callback: v => "$" + v } },
        y: { title: { display: true, text: "Puan", color: CHART_TICK }, grid: { color: CHART_GRID }, border: { display: false }, ticks: { color: CHART_TICK } } } },
  });
  $("#cv-scatter-foot").innerHTML = `
    <div>Kategori ort. fiyat<br><b class="text-on-surface text-sm">${stats.avgPrice != null ? fmtUsd(stats.avgPrice) : "n/a"}</b></div>
    <div>Kategori ort. puan<br><b class="text-on-surface text-sm">${stats.avgRating != null ? fmtRating(stats.avgRating) : "n/a"}</b></div>`;

  // --- Rakip yorum sayıları ---
  const withReviews = comps.filter(c => c.ratings != null);
  makeChart($("#cv-review-chart"), {
    type: "bar",
    data: { labels: withReviews.map((c, i) => `#${i + 1}`),
      datasets: [{ data: withReviews.map(c => c.ratings), backgroundColor: "#a15600", borderRadius: 3, maxBarThickness: 18 }] },
    options: baseOptions({
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        title: (items) => { const c = withReviews[items[0].dataIndex]; return `${c.brand || ""} · ${c.asin || ""}`; },
        label: (ctx) => ` ${fmtNum(ctx.raw)} yorum` } } },
      scales: { y: { grid: { color: CHART_GRID }, border: { display: false }, ticks: { color: CHART_TICK, callback: v => fmtCompact(v) } },
                x: { grid: { display: false }, ticks: { color: CHART_TICK, maxTicksLimit: 10 } } } }),
  });
  $("#cv-review-foot").innerHTML = `
    <div>Kategori ort. yorum<br><b class="text-on-surface text-sm">${stats.avgRatings != null ? fmtCompact(stats.avgRatings) : "n/a"}</b></div>
    <div>Yeni ürün oranı (12 ay)<br><b class="text-on-surface text-sm">${stats.newProductProportion != null
      ? (Number(stats.newProductProportion) > 1 ? Number(stats.newProductProportion) : Number(stats.newProductProportion) * 100).toFixed(1) + "%" : "n/a"}</b></div>`;

  // --- Pazar liderleri (backend sırası: total_units desc) ---
  const LEADER_TAG = ["Satış lideri #1", "Satış #2", "Satış #3"];
  $("#cv-leaders").innerHTML = comps.slice(0, 3).map((c, i) => `
    <div class="card p-4 flex gap-3 min-w-0">
      <div class="w-12 h-12 shrink-0 rounded-lg bg-surface-container grid place-items-center font-display font-semibold text-primary">${esc(brandInitials(c.brand))}</div>
      <div class="min-w-0">
        <div class="eyebrow text-primary">${LEADER_TAG[i]}</div>
        <div class="font-semibold text-sm truncate mt-0.5">${esc(c.brand || "?")}</div>
        <div class="text-xs text-secondary line-clamp-2 mt-0.5" title="${esc(c.title || "")}">${esc(c.title || c.asin || "")}</div>
        <div class="flex flex-wrap gap-x-3 gap-y-1 text-xs mt-2 tabular">
          <span>${c.price != null ? fmtUsd(c.price) : "n/a"}</span>
          <span>${fmtCompact(c.units)} adet/ay</span>
          <span>★ ${fmtRating(c.rating)}</span>
          ${c.bsr != null ? `<span class="text-secondary">BSR #${fmtNum(c.bsr)}</span>` : ""}
        </div>
      </div>
    </div>`).join("") || `<div class="text-sm text-secondary">Rakip verisi yok.</div>`;

  renderCompetitorTable();
}

function renderCompetitorTable() {
  const data = lastAnalysis;
  if (!data) return;
  let rows = (data.top_competitors || []).map((c, i) => ({ ...c, _rank: i + 1 }));
  if (cvState.scope !== "all") rows = rows.slice(0, Number(cvState.scope));
  if (cvState.bsr) rows = rows.filter(c => c.bsr != null && c.bsr < 5000);
  if (cvState.fba) rows = rows.filter(c => isFba(c.fulfillment));

  const total = rows.length;
  const pages = Math.max(1, Math.ceil(total / cvState.perPage));
  cvState.page = Math.min(cvState.page, pages);
  const start = (cvState.page - 1) * cvState.perPage;
  const pageRows = rows.slice(start, start + cvState.perPage);
  const domain = AMAZON_DOMAIN[data.marketplace] || "amazon.com";

  $("#cv-range").textContent = total ? `${start + 1}–${start + pageRows.length} arası · toplam ${total} rakip gösteriliyor` : "";
  const tbody = $("#cv-tbody");
  tbody.innerHTML = pageRows.length ? "" : `<tr><td colspan="9" class="muted !text-center !py-8">Filtreye uyan rakip yok.</td></tr>`;
  pageRows.forEach(c => {
    const asinOk = /^[A-Z0-9]{10}$/i.test(String(c.asin || ""));
    const isNew = isWithinLastYear(c.availableDate);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="l">
        <div class="flex items-center gap-3 min-w-0">
          <span class="avatar">${esc(brandInitials(c.brand))}</span>
          <div class="min-w-0">
            <div class="font-semibold truncate max-w-[220px]" title="${esc(c.title || "")}">${esc(c.brand || "?")} <span class="muted font-normal text-xs">#${c._rank}</span></div>
            <div class="mono muted">${esc(c.asin || "")}</div>
          </div>
        </div>
      </td>
      <td class="l">${fmtLaunch(c.availableDate)}${isNew ? ' <span class="chip warn ml-1">Yeni</span>' : ""}</td>
      <td class="font-medium">${c.price != null ? fmtUsd(c.price) : "n/a"}</td>
      <td title="${fmtNum(c.units)} adet">${fmtCompact(c.units)} <span class="muted text-xs">ad</span></td>
      <td class="font-semibold text-primary" title="$${fmtNum(Math.round(c.revenue || 0))}">$${fmtCompact(Math.round(c.revenue || 0))}</td>
      <td><span class="inline-flex items-center gap-1"><span class="material-symbols-outlined text-tertiary-container" style="font-size:15px">star</span>${fmtRating(c.rating)}</span> <span class="muted text-xs">(${fmtCompact(c.ratings)})</span></td>
      <td>${c.bsr != null ? "#" + fmtNum(c.bsr) : "n/a"}</td>
      <td class="!text-center">${c.fulfillment ? `<span class="chip ${isFba(c.fulfillment) ? "ok" : "na"}">${esc(c.fulfillment)}</span>` : "n/a"}</td>
      <td class="!text-center">${asinOk
        ? `<a class="icon-btn" href="https://www.${domain}/dp/${encodeURIComponent(c.asin)}" target="_blank" rel="noopener noreferrer" title="Amazon'da aç" aria-label="Amazon'da aç"><span class="material-symbols-outlined">open_in_new</span></a>`
        : ""}</td>`;
    tbody.appendChild(tr);
  });
  renderPager($("#cv-pager"), cvState.page, pages, (p) => { cvState.page = p; renderCompetitorTable(); });
}

(function bindCompetitorView() {
  bindSegment($("#cv-scope"), "scope", (s) => { cvState.scope = s; cvState.page = 1; renderCompetitorTable(); });
  [["#cv-f-bsr", "bsr"], ["#cv-f-fba", "fba"]].forEach(([sel, key]) => {
    const btn = $(sel);
    btn.addEventListener("click", () => {
      cvState[key] = !cvState[key];
      btn.setAttribute("aria-pressed", String(cvState[key]));
      cvState.page = 1;
      renderCompetitorTable();
    });
  });
  // Tam rapor: Ürün Analizi panelindeki mevcut export butonunu kullan
  // (kar analizi sonucu da aynı şekilde eklensin diye aynı kod yolu).
  $("#cv-export").addEventListener("click", () => {
    const btn = $("#result-container .export-report-btn");
    if (btn) btn.click();
  });
})();

// ---------------------------------------------------------------------------
// Raporlar — /api/decisions + /api/recent (kullanıcıya özel)
// ---------------------------------------------------------------------------
const repState = { status: "all", query: "", range: "all", market: "all", items: [] };
const DECISION_CHIP = { "Uygun": "ok", "Sınırda": "warn", "Elenmiş": "bad" };

async function loadReports() {
  const list = $("#rep-list");
  list.innerHTML = `<p class="p-6 text-sm text-secondary">yükleniyor…</p>`;
  try {
    const [decRes, recRes] = await Promise.all([
      apiFetch(`${API_BASE}/api/decisions`),
      apiFetch(`${API_BASE}/api/recent?limit=200`),
    ]);
    if (!decRes.ok || !recRes.ok) throw new Error(`HTTP ${decRes.ok ? recRes.status : decRes.status}`);
    const grouped = await decRes.json();
    const recent = await recRes.json();

    // (keyword, pazar) başına tek kayıt: en son karar + en son sorgu
    const map = new Map();
    const keyOf = (kw, m) => `${String(kw).toLowerCase()}|${m}`;
    Object.entries(grouped || {}).forEach(([decision, items]) => (items || []).forEach(it => {
      map.set(keyOf(it.keyword, it.marketplace), {
        keyword: it.keyword, marketplace: it.marketplace, decision, note: it.note || "",
        decided_at: it.decided_at, decided_by: it.decided_by || "", verdict: null, queried_at: null,
      });
    }));
    (Array.isArray(recent) ? recent : []).forEach(r => {
      const k = keyOf(r.keyword, r.marketplace);
      const cur = map.get(k);
      if (cur) {
        if (!cur.queried_at || r.fetched_at > cur.queried_at) { cur.queried_at = r.fetched_at; cur.verdict = r.verdict; }
      } else {
        map.set(k, { keyword: r.keyword, marketplace: r.marketplace, decision: null, note: "",
          decided_at: null, decided_by: "", verdict: r.verdict, queried_at: r.fetched_at });
      }
    });
    repState.items = [...map.values()].sort((a, b) =>
      Math.max(b.decided_at || 0, b.queried_at || 0) - Math.max(a.decided_at || 0, a.queried_at || 0));
    renderReports();
  } catch (err) {
    list.innerHTML = `<p class="p-6 text-sm text-error">Raporlar yüklenemedi: ${esc(err.message)}</p>`;
  }
}

function repFiltered() {
  const now = Date.now() / 1000;
  const q = repState.query.toLowerCase();
  return repState.items.filter(it => {
    const ts = Math.max(it.decided_at || 0, it.queried_at || 0);
    if (repState.range !== "all" && now - ts > Number(repState.range) * 86400) return false;
    if (repState.market !== "all" && it.marketplace !== repState.market) return false;
    if (repState.status === "decided" && !it.decision) return false;
    if (repState.status === "pending" && it.decision) return false;
    if (q && !`${it.keyword} ${it.note}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

function renderReports() {
  const items = repFiltered();
  const nowS = Date.now() / 1000;
  const inRange = repState.items.filter(it => {
    const ts = Math.max(it.decided_at || 0, it.queried_at || 0);
    return (repState.range === "all" || nowS - ts <= Number(repState.range) * 86400)
      && (repState.market === "all" || it.marketplace === repState.market);
  });
  const count = (d) => inRange.filter(it => it.decision === d).length;
  const cU = count("Uygun"), cS = count("Sınırda"), cE = count("Elenmiş");
  const undecided = inRange.filter(it => !it.decision).length;
  const decidedTotal = cU + cS + cE;
  const total = inRange.length;
  const week = inRange.filter(it => nowS - Math.max(it.decided_at || 0, it.queried_at || 0) <= 7 * 86400).length;
  const pct = (n, d) => d ? Math.round((n / d) * 1000) / 10 : 0;

  $("#rep-k-total").textContent = fmtNum(total);
  $("#rep-k-week").textContent = week ? `+${week} bu hafta` : "";
  $("#rep-k-uygun").textContent = fmtNum(cU);
  $("#rep-k-uygun-pct").textContent = `%${pct(cU, decidedTotal)} kararların`;
  $("#rep-k-elenmis").textContent = fmtNum(cE);
  $("#rep-k-elenmis-pct").textContent = `%${pct(cE, decidedTotal)} kararların`;
  $("#rep-k-sinirda").textContent = fmtNum(cS);
  $("#rep-k-sinirda-sub").textContent = undecided ? `+${undecided} kararsız` : "";
  $("#rep-b-uygun").style.width = pct(cU, decidedTotal) + "%";
  $("#rep-b-elenmis").style.width = pct(cE, decidedTotal) + "%";
  $("#rep-b-sinirda").style.width = pct(cS, decidedTotal) + "%";

  // --- Karar dağılımı ---
  $("#rep-dist-total").textContent = fmtNum(decidedTotal);
  $("#rep-dist-range").textContent = repState.range === "all" ? "Tüm zamanlar" : `Son ${repState.range} gün`;
  const dist = [["Uygun", cU, "#005c55"], ["Sınırda", cS, "#a15600"], ["Elenmiş", cE, "#ba1a1a"]];
  makeChart($("#rep-dist-chart"), {
    type: "doughnut",
    data: { labels: dist.map(d => d[0]), datasets: [{ data: decidedTotal ? dist.map(d => d[1]) : [1],
      backgroundColor: decidedTotal ? dist.map(d => d[2]) : ["#e5eeff"], borderColor: "#ffffff", borderWidth: 3 }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: "74%",
      plugins: { legend: { display: false }, tooltip: { enabled: !!decidedTotal } } },
  });
  $("#rep-dist-legend").innerHTML = dist.map(([l, n, col]) => `
    <div class="flex items-center justify-between gap-2">
      <span class="flex items-center gap-2"><span class="w-2.5 h-2.5 rounded-full" style="background:${col}"></span>${l}</span>
      <span class="tabular font-medium">%${pct(n, decidedTotal)} (${n})</span>
    </div>`).join("");

  // --- Liste ---
  $("#rep-count").textContent = `${items.length} dosya listeleniyor`;
  const list = $("#rep-list");
  if (!items.length) {
    list.innerHTML = `<p class="p-6 text-sm text-secondary">${repState.items.length ? "Filtreye uyan kayıt yok." : "Henüz analiz ya da karar kaydı yok."}</p>`;
    return;
  }
  list.innerHTML = "";
  const fmtDate = (s) => s ? new Date(s * 1000).toLocaleString("tr-TR", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
  items.forEach(it => {
    const div = document.createElement("div");
    div.className = "rep-item";
    const statusChip = it.decision
      ? `<span class="chip ${DECISION_CHIP[it.decision] || "na"}">${esc(it.decision)} · ekip kararı</span>`
      : `<span class="chip na">İnceleniyor · karar yok</span>`;
    const verdictHtml = it.verdict
      ? `<span class="chip ${DECISION_CHIP[it.verdict] || "na"}">${esc(it.verdict)}</span>` : `<span class="text-secondary">—</span>`;
    div.innerHTML = `
      <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
        <div class="flex gap-3 min-w-0">
          <span class="w-10 h-10 shrink-0 rounded-lg bg-surface-container-low grid place-items-center text-primary"><span class="material-symbols-outlined">${/^B0[A-Z0-9]{8}/i.test(it.keyword) ? "inventory_2" : "manage_search"}</span></span>
          <div class="min-w-0">
            <div class="font-display font-semibold text-[16px] leading-snug break-words">${esc(it.keyword)}</div>
            <div class="flex flex-wrap items-center gap-2 text-xs text-secondary mt-1">
              <span class="chip chip-dot na">${esc(it.marketplace)} Pazarı</span>
              <span>${fmtDate(Math.max(it.decided_at || 0, it.queried_at || 0))}</span>
            </div>
          </div>
        </div>
        <div class="shrink-0">${statusChip}</div>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3 rounded-xl bg-surface-container-low p-3 mt-4 text-xs">
        <div><div class="text-secondary">Pazar kararı</div><div class="font-semibold text-sm mt-0.5">${esc(it.decision || "—")}</div></div>
        <div><div class="text-secondary">Ön öneri (algoritmik)</div><div class="mt-0.5">${verdictHtml}</div></div>
        <div><div class="text-secondary">Karar tarihi</div><div class="font-medium text-sm mt-0.5">${it.decided_at ? fmtDate(it.decided_at) : "—"}</div></div>
        <div><div class="text-secondary">Son sorgu</div><div class="font-medium text-sm mt-0.5">${it.queried_at ? fmtDate(it.queried_at) : "—"}</div></div>
      </div>
      ${it.note ? `<div class="text-[13px] text-on-surface-variant italic mt-3 break-words">"${esc(it.note)}"</div>` : ""}
      <div class="flex flex-wrap items-center justify-between gap-2 mt-3">
        <span class="text-xs text-secondary">${it.decided_by ? "Karar: " + esc(it.decided_by) : ""}</span>
        <button type="button" class="rep-open crit-more !text-[13px]">Yeniden Analiz Et <span class="material-symbols-outlined" style="font-size:16px">arrow_forward</span></button>
      </div>`;
    div.querySelector(".rep-open").addEventListener("click", () => {
      showView("search");
      runAnalysis(it.keyword, it.marketplace);
    });
    list.appendChild(div);
  });
}

(function bindReportsView() {
  $("#rep-search").addEventListener("input", (e) => { repState.query = e.target.value.trim(); renderReports(); });
  bindSegment($("#rep-status"), "status", (s) => { repState.status = s; renderReports(); });
  $("#rep-range").addEventListener("change", (e) => { repState.range = e.target.value; renderReports(); });
  $("#rep-market").addEventListener("change", (e) => { repState.market = e.target.value; renderReports(); });
  // Excel (.xlsx), eski CSV ile aynı sütunlar. Sunucu metinleri formül değil METİN yazar (formül enjeksiyonu koruması).
  $("#rep-xlsx").addEventListener("click", async (e) => {
    const btn = e.currentTarget, original = btn.innerHTML;
    const items = repFiltered();
    const name = `pl_pazar_raporlari_${new Date().toISOString().slice(0, 10)}`;
    btn.disabled = true; btn.textContent = "hazırlanıyor…";
    try {
      const res = await apiFetch(`${API_BASE}/api/export/reports`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: name, tz_offset_min: new Date().getTimezoneOffset(),
          rows: items.map(it => ({ keyword: it.keyword ?? "", marketplace: it.marketplace ?? "", decision: it.decision || "",
            verdict: it.verdict || "", note: it.note ?? "", decided_at: it.decided_at || null, queried_at: it.queried_at || null,
            decided_by: it.decided_by ?? "" })),
        }),
      });
      if (!res.ok) { const b = await res.json().catch(() => ({})); throw new Error(apiErrorText(b, res.status)); }
      await downloadBlob(res, `${name}.xlsx`);
    } catch (err) {
      alert(`Excel oluşturulamadı: ${err.message}`);
    } finally { btn.disabled = false; btn.innerHTML = original; }
  });
})();


// ---------------------------------------------------------------------------
// Ayarlar — /api/thresholds
// Kullanıcıya özel eşikler (api/index.py + api/database.py::user_thresholds):
//   GET   /api/thresholds        → DEFAULT_THRESHOLDS + kullanıcının override'ları
//   PUT   /api/thresholds        → kısmi güncelleme; yanıt birleşik eşik nesnesi
//   POST  /api/thresholds/reset  → override'ları siler; yanıt DEFAULT_THRESHOLDS
// Birim DEFAULT_THRESHOLDS ile aynı (oranlar 0-1). Backend aralık doğrulaması
// yapmıyor — aralık/tam sayı kontrolü burada, göndermeden önce yapılır.
// Kaydedilen eşikler SONRAKİ /api/analyze(-asin) çağrılarında pre_assessment'a
// geçer; açık analiz ekranı frontend'de yeniden puanlanmaz.
// ---------------------------------------------------------------------------
const THRESHOLD_FIELDS = [
  { key: "min_avg_price", label: "Ort. Satış Fiyatı", dir: "≥", unit: "usd", min: 0, max: 100, step: 0.5,
    help: "Pazar ortalama fiyatı bu değerin altındaysa kriter olumsuz." },
  { key: "min_gross_margin", label: "Gross Margin", dir: "≥", unit: "pct", min: 0, max: 100, step: 1,
    help: "Kategori ortalama brüt marjı (avgProfit) için alt sınır." },
  { key: "max_acos", label: "ACOS (hesaplanan)", dir: "≤", unit: "pct", min: 0, max: 150, step: 1,
    help: "Ana keyword'ün hesaplanan ACOS'u bu değeri aşarsa olumsuz." },
  { key: "max_brand_share", label: "En Büyük Marka Payı", dir: "≤", unit: "pct", min: 0, max: 100, step: 1,
    help: "Lider markanın ciro payı bu değeri aşarsa olumsuz." },
  { key: "min_strong_new_brands", label: "Güçlü Yeni Marka (1 yıl)", dir: "≥", unit: "count", min: 0, max: 20, step: 1,
    help: "Son 12 ayda en çok satanlara giren farklı marka sayısı için alt sınır." },
  { key: "min_net_margin", label: "Net Kâr Marjı", dir: "≥", unit: "pct", min: 0, max: 100, step: 1,
    help: "Kâr hesaplayıcısındaki net marj için alt sınır." },
];
const setState = { loaded: null, busy: false };

// Birim dönüşümü yalnızca gösterim içindir: oranlar formda yüzde olarak düzenlenir
const toDisplay = (f, v) => v == null || isNaN(v) ? "" : f.unit === "pct" ? +(Number(v) * 100).toFixed(2) : +Number(v).toFixed(2);
const fromDisplay = (f, v) => f.unit === "pct" ? +(v / 100).toFixed(4) : f.unit === "count" ? Math.round(v) : +v;
const fmtThreshold = (f, v) => v == null || isNaN(v) ? "—"
  : f.unit === "usd" ? "$" + Number(v).toFixed(2) : f.unit === "pct" ? "%" + toDisplay(f, v) : String(v);

function setBanner(kind, html) {
  const el = $("#set-banner");
  if (!kind) { el.style.display = "none"; el.innerHTML = ""; return; }
  const icon = { ok: "check_circle", warn: "info", bad: "error" }[kind];
  el.className = `set-banner ${kind} mt-6`;
  el.innerHTML = `<span class="material-symbols-outlined">${icon}</span><div>${html}</div>`;
  el.style.display = "flex";
}

function buildSettingsForm() {
  const form = $("#set-form");
  form.innerHTML = "";
  THRESHOLD_FIELDS.forEach((f, i) => {
    const wrap = document.createElement("div");
    wrap.className = "set-field";
    wrap.dataset.key = f.key;
    const prefix = f.unit === "usd" ? "$" : f.unit === "pct" ? "%" : "";
    const suffix = f.unit === "count" ? "marka" : "";
    const scale = (v) => f.unit === "usd" ? "$" + v : f.unit === "pct" ? "%" + v : String(v);
    wrap.innerHTML = `
      <div class="flex items-start justify-between gap-3">
        <label for="set-in-${f.key}" class="flex items-start gap-2 min-w-0">
          <span class="chip na shrink-0">${String(i + 1).padStart(2, "0")}</span>
          <span class="min-w-0"><span class="block font-semibold text-sm">${esc(f.label)} <span class="text-secondary font-normal">${f.dir}</span></span>
          <span class="block text-xs text-secondary mt-0.5">${esc(f.help)}</span></span>
        </label>
        <span class="set-num shrink-0">${prefix}<input type="number" id="set-in-${f.key}" class="field" min="${f.min}" max="${f.max}" step="${f.step}" inputmode="decimal">${suffix ? `<span class="text-xs font-normal text-secondary">${suffix}</span>` : ""}</span>
      </div>
      <input type="range" class="set-range" min="${f.min}" max="${f.max}" step="${f.step}" aria-label="${esc(f.label)} eşiği">
      <div class="set-scale"><span>${scale(f.min)}</span><span class="set-loaded"></span><span>${scale(f.max)}</span></div>
      <div class="set-err"></div>`;
    const num = wrap.querySelector('input[type="number"]');
    const range = wrap.querySelector('input[type="range"]');
    num.addEventListener("input", () => { if (num.value !== "") range.value = num.value; updateSettingsState(); });
    range.addEventListener("input", () => { num.value = range.value; updateSettingsState(); });
    form.appendChild(wrap);
  });
}

function fillSettingsForm(values) {
  THRESHOLD_FIELDS.forEach(f => {
    const wrap = $(`#set-form [data-key="${f.key}"]`);
    const v = toDisplay(f, values?.[f.key]);
    wrap.querySelector('input[type="number"]').value = v;
    wrap.querySelector('input[type="range"]').value = v === "" ? f.min : v;
    wrap.querySelector(".set-loaded").textContent = `Kayıtlı: ${fmtThreshold(f, values?.[f.key])}`;
  });
  updateSettingsState();
}

/** Formu doğrula; geçerliyse backend birimiyle 6 anahtarlı nesneyi döndür */
function readSettingsForm() {
  const out = {};
  let valid = true, dirty = 0;
  THRESHOLD_FIELDS.forEach(f => {
    const wrap = $(`#set-form [data-key="${f.key}"]`);
    const raw = wrap.querySelector('input[type="number"]').value.trim();
    const v = Number(raw);
    let err = "";
    if (raw === "" || !isFinite(v)) err = "Sayı girin.";
    else if (v < f.min || v > f.max) err = `${f.min}–${f.max} aralığında olmalı.`;
    else if (f.unit === "count" && !Number.isInteger(v)) err = "Tam sayı olmalı.";
    wrap.querySelector(".set-err").textContent = err;
    wrap.classList.toggle("invalid", !!err);
    if (err) { valid = false; wrap.classList.remove("dirty"); return; }
    out[f.key] = fromDisplay(f, v);
    const isDirty = setState.loaded && Math.abs(out[f.key] - Number(setState.loaded[f.key])) > 1e-9;
    wrap.classList.toggle("dirty", !!isDirty);
    if (isDirty) dirty++;
  });
  return { values: out, valid, dirty };
}

function updateSettingsState() {
  const { valid, dirty } = readSettingsForm();
  const ready = !!setState.loaded && !setState.busy;
  $("#set-save").disabled = !ready || !valid || !dirty;
  $("#set-revert").disabled = !ready || !dirty;
  $("#set-reset").disabled = !ready;
  const chip = $("#set-dirty-chip");
  if (!setState.loaded) { chip.className = "chip bad"; chip.textContent = "Yüklenemedi"; }
  else if (!valid) { chip.className = "chip bad"; chip.textContent = "Geçersiz değer var"; }
  else if (dirty) { chip.className = "chip warn"; chip.textContent = `${dirty} kaydedilmemiş değişiklik`; }
  else { chip.className = "chip ok"; chip.textContent = "Kayıtlı değerlerle aynı"; }
  $("#set-status").textContent = !setState.loaded ? "Eşikler okunamadı."
    : dirty ? `${dirty} eşik değişti — henüz kaydedilmedi.` : "Tüm eşikler kayıtlı değerlerle aynı.";
}

/** 6 anahtarın hepsi sayı mı? (backend yanıtını olduğu gibi kabul etmeden önce) */
function isThresholdObject(o) {
  return o && typeof o === "object" && THRESHOLD_FIELDS.every(f => typeof o[f.key] === "number" && isFinite(o[f.key]));
}

async function loadSettings() {
  if (!$("#set-form").children.length) buildSettingsForm();
  setBanner(null);
  $("#set-dirty-chip").className = "chip na";
  $("#set-dirty-chip").textContent = "Yükleniyor…";
  try {
    apiFetch(`${API_BASE}/api/verdict-rule`).then(r => r.ok ? r.json() : null).then(rule => {
      if (!rule) return;
      $("#set-rule-text").textContent = `${rule.text}. Bu kural backend'de sabittir, buradan değiştirilemez.`;
      $("#set-rule-chip").textContent = `${rule.eliminate_at}+ olumsuz → Elenmiş`;
    }).catch(() => {});
    const res = await apiFetch(`${API_BASE}/api/thresholds`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json();
    if (!isThresholdObject(body)) throw new Error("beklenmeyen yanıt biçimi");
    setState.loaded = body;
    fillSettingsForm(body);
  } catch (err) {
    setState.loaded = null;
    updateSettingsState();
    setBanner("bad", `Eşikler yüklenemedi: ${esc(err.message)}`);
  }
}

/** FastAPI hata gövdesini okunur metne çevir (400: string, 422: doğrulama listesi) */
function apiErrorText(data, status) {
  const d = data && data.detail;
  if (Array.isArray(d)) return d.map(x => `${(x.loc || []).slice(-1)[0] || "alan"}: ${x.msg || "geçersiz"}`).join(" · ");
  if (typeof d === "string" && d) return d;
  return `HTTP ${status}`;
}

/** PUT /api/thresholds ve POST /api/thresholds/reset — yanıt 6 anahtarlı eşik nesnesi */
async function thresholdWrite(method, url, body) {
  const res = await apiFetch(url, {
    method, headers: { "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) throw new Error("Oturum gerekli — giriş yapıp tekrar deneyin.");
  if (!res.ok) throw new Error(apiErrorText(data, res.status));
  if (!isThresholdObject(data)) throw new Error("Backend beklenmeyen bir yanıt döndürdü.");
  return data;
}

async function runSettingsAction(btn, fn) {
  setState.busy = true;
  updateSettingsState();
  const originalHtml = btn.innerHTML;
  btn.textContent = "gönderiliyor…";
  try {
    await fn();
  } catch (err) {
    setBanner("bad", `İşlem başarısız: ${esc(err.message)}<br><span class='text-xs'>Değerleriniz formda korundu.</span>`);
  } finally {
    btn.innerHTML = originalHtml;
    setState.busy = false;
    updateSettingsState();
  }
}

(function bindSettingsView() {
  $("#set-save").addEventListener("click", (e) => {
    const { values, valid, dirty } = readSettingsForm();
    if (!valid || !dirty) return;
    runSettingsAction(e.currentTarget, async () => {
      setState.loaded = await thresholdWrite("PUT", `${API_BASE}/api/thresholds`, values);
      fillSettingsForm(setState.loaded);
      setBanner("ok", "Eşikler kaydedildi. Bir sonraki analizde ön değerlendirme bu eşiklerle yapılır; açık analiz ekranı yeniden puanlanmaz.");
    });
  });
  $("#set-reset").addEventListener("click", (e) => {
    if (!confirm("6 eşik de varsayılan değerlere döndürülecek. Emin misiniz?")) return;
    runSettingsAction(e.currentTarget, async () => {
      setState.loaded = await thresholdWrite("POST", `${API_BASE}/api/thresholds/reset`);
      fillSettingsForm(setState.loaded);
      setBanner("ok", "Eşikler varsayılan değerlere sıfırlandı. Bir sonraki analizde varsayılanlar kullanılır.");
    });
  });
  $("#set-revert").addEventListener("click", () => {
    if (setState.loaded) fillSettingsForm(setState.loaded);
    setBanner(null);
  });
})();


// ---------------------------------------------------------------------------
// Trendler & Fırsatlar — POST /api/discovery/trending (aba_research_weekly/monthly)
// ALAN ANLAMI: growth_rate / growth_4w / growth_12w = searchRankGrowthRate,
// yani arama SIRALAMASININ yükselme oranı (0.909 ≈ 11. sıradan 1. sıraya).
// Arama hacmi büyümesi DEĞİLDİR → panelde "Sıralama İvmesi". Hacim = searches.
// Her tarama 1 MCP çağrısı: filtre değişince otomatik çekme YOK, yalnızca "Tara".
// ---------------------------------------------------------------------------
const SEARCH_MODEL_LABELS = { 1: "Popüler Pazar", 2: "Anormal Hareketli", 3: "Sürekli Büyüyen", 4: "Hızlı Yükselen", 5: "Potansiyel", 6: "Uzun Kuyruk" };
const trdState = { data: null, lastParams: null, busy: false, sort: "backend" };

/** Görsel URL'ine yalnızca https ise izin ver; aksi halde null */
function safeHttpsUrl(v) {
  if (typeof v !== "string" || !v) return null;
  try {
    const u = new URL(v);
    return u.protocol === "https:" ? u.href : null;
  } catch { return null; }
}

/** Sıralama ivmesi: 0-1 oran → "+%90.9" (negatifse sıralama düşmüş) */
function fmtRankAccel(v) {
  if (v == null || v === "" || isNaN(v)) return "n/a";
  const n = Number(v) * 100;
  return (n > 0 ? "+" : n < 0 ? "−" : "") + "%" + Math.abs(n).toFixed(1);
}
const rankAccelClass = (v) => v == null || isNaN(v) ? "text-secondary" : Number(v) > 0 ? "text-primary" : Number(v) < 0 ? "text-error" : "text-secondary";

/**
 * purchase_rate / click_rate / conversion_rate — gerçek MCP verisiyle doğrulandı:
 * HER ZAMAN 0-1 oran (ör. purchase_rate = purchases/searches = 17881/2518485 = 0.0071).
 * Büyüklüğe bakıp tahmin yapma; daima ×100 göster.
 */
function fmtRate(v) {
  if (v == null || v === "" || isNaN(v)) return "n/a";
  return "%" + (Number(v) * 100).toFixed(2);
}

function readTrendParams() {
  const minRaw = $("#trd-minsearch").value.trim();
  const min = minRaw === "" ? null : Math.max(0, Math.round(Number(minRaw)));
  return {
    marketplace: $("#trd-market").value,
    search_model: Number($("#trd-model").value),
    granularity: $("#trd-gran").value,
    ...(min ? { min_searches: min } : {}),
    size: Number($("#trd-size").value),
  };
}

function updateTrendsStale() {
  const stale = !!trdState.lastParams && JSON.stringify(readTrendParams()) !== JSON.stringify(trdState.lastParams);
  $("#trd-stale").style.display = stale ? "block" : "none";
}

async function scanTrends() {
  if (trdState.busy) return;
  const minRaw = $("#trd-minsearch").value.trim();
  const status = $("#trd-status");
  if (minRaw !== "" && (!isFinite(Number(minRaw)) || Number(minRaw) < 0)) {
    status.textContent = "Min. arama hacmi 0 veya daha büyük bir sayı olmalı.";
    status.className = "status-line error";
    return;
  }
  const params = readTrendParams();
  trdState.busy = true;
  $("#trd-scan").disabled = true;
  status.textContent = `${SEARCH_MODEL_LABELS[params.search_model]} keyword'ler taranıyor… (1 MCP çağrısı)`;
  status.className = "status-line loading";
  try {
    const res = await apiFetch(`${API_BASE}/api/discovery/trending`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(apiErrorText(data, res.status));
    if (!data || !Array.isArray(data.results)) throw new Error("Backend beklenmeyen bir yanıt döndürdü.");
    trdState.data = { ...data, _params: params, _at: new Date() };
    trdState.lastParams = params;
    status.textContent = `Tarama tamamlandı · ${data.results.length} keyword`;
    status.className = "status-line";
    renderTrends();
  } catch (err) {
    status.textContent = `Hata: ${err.message}`;
    status.className = "status-line error";
  } finally {
    trdState.busy = false;
    $("#trd-scan").disabled = false;
    updateTrendsStale();
  }
}

function renderTrends() {
  const d = trdState.data;
  $("#trd-empty").classList.toggle("hidden", !!d);
  $("#trd-content").classList.toggle("hidden", !d);
  if (!d) return;
  const p = d._params;
  $("#trd-summary-chip").textContent = `${d.search_model_label || SEARCH_MODEL_LABELS[p.search_model]} · ${d.results.length} aday`;
  $("#trd-meta").textContent = `${p.marketplace} · ${p.granularity === "monthly" ? "aylık" : "haftalık"}${d.total != null ? ` · toplam ${fmtNum(d.total)} eşleşme` : ""} · ${d._at.toLocaleTimeString("tr-TR")}`;

  let rows = d.results.map((r, i) => ({ ...r, _rank: i + 1 }));
  if (trdState.sort !== "backend") {
    const k = trdState.sort;
    rows.sort((a, b) => (b[k] ?? -Infinity) - (a[k] ?? -Infinity));
  }
  const list = $("#trd-list");
  list.innerHTML = rows.length ? "" : `<div class="card card-pad text-sm text-secondary lg:col-span-2">Bu filtrelerle sonuç bulunamadı. Min. arama hacmini düşürmeyi ya da başka bir pazar tipini deneyin.</div>`;
  rows.forEach(r => {
    const card = document.createElement("div");
    card.className = "card p-5 flex flex-col gap-4 min-w-0";
    const depts = (r.departments || []).slice(0, 3).map(x => `<span class="chip na">${esc(x)}</span>`).join("");
    const brands = (r.top3_brands || []).map(b => `<span class="chip">${esc(b)}</span>`).join("") || `<span class="text-xs text-secondary">—</span>`;
    const asins = (r.top3_asins || []).map(a => {
      const img = safeHttpsUrl(a.image_url);
      const tip = `${a.asin || ""} · CTR ${fmtRate(a.click_rate)} · CVR ${fmtRate(a.conversion_rate)}`;
      return `<div class="flex items-center gap-2 min-w-0" title="${esc(tip)}">
        ${img ? `<img src="${esc(img)}" alt="" loading="lazy" referrerpolicy="no-referrer" class="w-10 h-10 rounded-lg object-contain bg-surface-container-low border border-hairline shrink-0">`
              : `<span class="w-10 h-10 rounded-lg bg-surface-container-low grid place-items-center text-outline shrink-0"><span class="material-symbols-outlined">image</span></span>`}
        <div class="min-w-0 text-[11px] leading-4"><div class="mono font-semibold truncate">${esc(a.asin || "—")}</div><div class="text-secondary">CTR ${fmtRate(a.click_rate)} · CVR ${fmtRate(a.conversion_rate)}</div></div>
      </div>`;
    }).join("");
    const bidRange = r.bid_min != null && r.bid_max != null ? ` <span class="text-secondary font-normal text-xs">(${fmtUsd(r.bid_min)}–${fmtUsd(r.bid_max)})</span>` : "";
    card.innerHTML = `
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-1.5"><span class="chip ok">#${r._rank}</span>${depts}</div>
          <div class="font-display font-semibold text-[17px] leading-snug mt-2 break-words">${esc(r.keyword || "—")}</div>
        </div>
        <div class="text-right shrink-0" title="Arama sıralamasının yükselme oranı (searchRankGrowthRate). Arama hacmi büyümesi DEĞİLDİR.">
          <div class="eyebrow">Sıralama İvmesi</div>
          <div class="font-display text-[26px] font-medium tracking-tight tabular ${rankAccelClass(r.growth_rate)}">${fmtRankAccel(r.growth_rate)}</div>
          <div class="text-[11px] text-secondary tabular">4H <span class="${rankAccelClass(r.growth_4w)}">${fmtRankAccel(r.growth_4w)}</span> · 12H <span class="${rankAccelClass(r.growth_12w)}">${fmtRankAccel(r.growth_12w)}</span></div>
        </div>
      </div>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 rounded-xl bg-surface-container-low p-3 text-xs">
        <div><div class="text-secondary">Arama hacmi</div><div class="font-semibold text-sm tabular mt-0.5" title="${fmtNum(r.searches)}">${fmtCompact(r.searches)}</div></div>
        <div><div class="text-secondary">Arama sırası</div><div class="font-semibold text-sm tabular mt-0.5">${r.search_rank != null ? "#" + fmtNum(r.search_rank) : "n/a"}</div></div>
        <div><div class="text-secondary">Satış</div><div class="font-semibold text-sm tabular mt-0.5" title="${fmtNum(r.purchases)}">${fmtCompact(r.purchases)} <span class="font-normal text-secondary">(${fmtRate(r.purchase_rate)})</span></div></div>
        <div><div class="text-secondary">Bid</div><div class="font-semibold text-sm tabular mt-0.5">${r.bid != null ? fmtUsd(r.bid) : "n/a"}${bidRange}</div></div>
      </div>
      <div><div class="eyebrow mb-1.5">Tıklama payı en yüksek 3 marka</div><div class="flex flex-wrap gap-1.5">${brands}</div></div>
      ${asins ? `<div><div class="eyebrow mb-2">İlk 3 ASIN</div><div class="grid grid-cols-1 sm:grid-cols-3 gap-2">${asins}</div></div>` : ""}
      <div class="flex justify-end mt-auto"><button type="button" class="trd-analyze btn btn-outline btn-sm"><span class="material-symbols-outlined">monitoring</span>Analiz Et</button></div>`;
    card.querySelector(".trd-analyze").addEventListener("click", () => {
      showView("search");
      runAnalysis(r.keyword, p.marketplace);
    });
    list.appendChild(card);
  });
}

(function bindTrendsView() {
  $("#trd-form").addEventListener("submit", (e) => { e.preventDefault(); scanTrends(); });
  // Filtre değişikliği yalnızca "güncel değil" uyarısını açar — MCP çağrısı yapmaz
  ["#trd-model", "#trd-gran", "#trd-market", "#trd-minsearch", "#trd-size"].forEach(sel => {
    $(sel).addEventListener("input", updateTrendsStale);
    $(sel).addEventListener("change", updateTrendsStale);
  });
  $("#trd-sort").addEventListener("change", (e) => { trdState.sort = e.target.value; renderTrends(); });
})();


// ---------------------------------------------------------------------------
// Ana Sayfa — YALNIZCA veritabanından okur: GET /api/recent + GET /api/decisions.
// Açılışta hiçbir MCP çağrısı yapılmaz; trend taraması bile otomatik değildir
// (yalnızca Trendler sayfasına link). Tüm sayılar kayıtların sayımıdır.
// ---------------------------------------------------------------------------
const HOME_RECENT_LIMIT = 200;

function fmtAgo(tsSec) {
  if (!tsSec) return "—";
  const diff = Math.max(0, Date.now() / 1000 - tsSec);
  if (diff < 60) return "az önce";
  if (diff < 3600) return `${Math.floor(diff / 60)} dk önce`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} sa önce`;
  if (diff < 7 * 86400) return `${Math.floor(diff / 86400)} gün önce`;
  return new Date(tsSec * 1000).toLocaleDateString("tr-TR", { day: "2-digit", month: "short", year: "numeric" });
}
const fmtStamp = (tsSec) => tsSec ? new Date(tsSec * 1000).toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";

async function loadHome() {
  const status = $("#home-status");
  status.textContent = "yükleniyor…";
  status.className = "text-xs text-secondary";
  try {
    const [recRes, decRes] = await Promise.all([
      apiFetch(`${API_BASE}/api/recent?limit=${HOME_RECENT_LIMIT}`),
      apiFetch(`${API_BASE}/api/decisions`),
    ]);
    if (recRes.status === 401 || decRes.status === 401) throw new Error("Giriş gerekli.");
    const recBody = await recRes.json().catch(() => null);
    const decBody = await decRes.json().catch(() => null);
    if (!recRes.ok) throw new Error(apiErrorText(recBody, recRes.status));
    if (!decRes.ok) throw new Error(apiErrorText(decBody, decRes.status));
    renderHome(Array.isArray(recBody) ? recBody : [], decBody && typeof decBody === "object" ? decBody : {});
    status.textContent = `Güncellendi · ${new Date().toLocaleTimeString("tr-TR")}`;
  } catch (err) {
    status.textContent = `Veriler yüklenemedi: ${err.message}`;
    status.className = "text-xs text-error";
  }
}

function renderHome(recent, grouped) {
  const nowS = Date.now() / 1000;
  const keyOf = (kw, m) => `${String(kw).toLowerCase()}|${m}`;

  // --- Kararlar (backend: her keyword+pazar için EN SON karar) ---
  const decisions = [];
  ["Uygun", "Sınırda", "Elenmiş"].forEach(d => (grouped[d] || []).forEach(it => decisions.push({ ...it, decision: d })));
  const decMap = new Map(decisions.map(d => [keyOf(d.keyword, d.marketplace), d]));
  const cnt = (d) => (grouped[d] || []).length;
  const cU = cnt("Uygun"), cS = cnt("Sınırda"), cE = cnt("Elenmiş");
  const decTotal = cU + cS + cE;
  const pct = (n) => decTotal ? (n / decTotal) * 100 : 0;

  // --- Özet kartlar ---
  const capped = recent.length >= HOME_RECENT_LIMIT;
  $("#home-k-analyses").textContent = capped ? `${HOME_RECENT_LIMIT}+` : fmtNum(recent.length);
  const week = recent.filter(r => nowS - (r.fetched_at || 0) <= 7 * 86400).length;
  $("#home-k-week").textContent = week ? `+${week} bu hafta` : "bu hafta yok";
  const markets = new Set(recent.map(r => keyOf(r.keyword, r.marketplace)));
  $("#home-k-markets").textContent = `${fmtNum(markets.size)} farklı pazar${capped ? " (son " + HOME_RECENT_LIMIT + " sorgu)" : ""}`;
  [["uygun", cU], ["sinirda", cS], ["elenmis", cE]].forEach(([k, n]) => {
    $(`#home-k-${k}`).textContent = fmtNum(n);
    $(`#home-b-${k}`).style.width = pct(n) + "%";
    $(`#home-p-${k}`).textContent = decTotal ? `%${pct(n).toFixed(0)} kararların` : "henüz karar yok";
  });

  // --- Son incelenen pazarlar (recent zaten en yeni önce; keyword+pazar başına ilk kayıt) ---
  const seen = new Set();
  const latest = [];
  recent.forEach(r => {
    const k = keyOf(r.keyword, r.marketplace);
    if (seen.has(k)) return;
    seen.add(k);
    latest.push(r);
  });
  $("#home-markets-count").textContent = latest.length ? `${latest.length} pazar` : "";
  const list = $("#home-markets");
  list.innerHTML = latest.length ? "" : `<div class="p-6 text-sm text-secondary">Henüz analiz yok. <button type="button" class="crit-more" data-goto-inline="search">İlk analizi başlat →</button></div>`;
  latest.slice(0, 6).forEach(r => {
    const dec = decMap.get(keyOf(r.keyword, r.marketplace));
    const vChip = r.verdict ? `<span class="chip ${DECISION_CHIP[r.verdict] || "na"}">Ön öneri: ${esc(r.verdict)}</span>` : "";
    const dChip = dec ? `<span class="chip ${DECISION_CHIP[dec.decision] || "na"} chip-dot">Karar: ${esc(dec.decision)}</span>`
                      : `<span class="chip na">Karar yok</span>`;
    const row = document.createElement("button");
    row.type = "button";
    row.className = "home-market";
    row.title = "Yeniden analiz et (canlı MCP çağrısı)";
    row.innerHTML = `
      <span class="w-10 h-10 shrink-0 rounded-lg bg-surface-container-low grid place-items-center text-primary"><span class="material-symbols-outlined">${/^B0[A-Z0-9]{8}/i.test(r.keyword) ? "inventory_2" : "manage_search"}</span></span>
      <span class="min-w-0 flex-1 text-left">
        <span class="block font-semibold text-[14px] leading-snug break-words">${esc(r.keyword)}</span>
        <span class="block text-xs text-secondary mt-0.5">${esc(r.marketplace)} · ${fmtAgo(r.fetched_at)}</span>
        ${dec?.note ? `<span class="block text-xs text-on-surface-variant italic mt-1 truncate">"${esc(dec.note)}"</span>` : ""}
      </span>
      <span class="flex flex-col items-end gap-1 shrink-0">${dChip}${vChip}</span>`;
    row.addEventListener("click", () => { showView("search"); runAnalysis(r.keyword, r.marketplace); });
    list.appendChild(row);
  });
  list.querySelectorAll("[data-goto-inline]").forEach(b => b.addEventListener("click", () => showView(b.dataset.gotoInline)));

  // --- Son aktiviteler: yalnızca gerçek kayıt zaman damgaları ---
  const events = [
    ...recent.map(r => ({ ts: r.fetched_at, type: "analysis", keyword: r.keyword, marketplace: r.marketplace, verdict: r.verdict })),
    ...decisions.map(d => ({ ts: d.decided_at, type: "decision", keyword: d.keyword, marketplace: d.marketplace, decision: d.decision, note: d.note, by: d.decided_by })),
  ].filter(e => e.ts).sort((a, b) => b.ts - a.ts).slice(0, 12);
  const feed = $("#home-feed");
  feed.innerHTML = events.length ? events.map(e => e.type === "analysis" ? `
    <li class="home-event">
      <span class="home-event-dot bg-primary"></span>
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-x-2 text-[11px] text-secondary"><span class="font-semibold tracking-wide uppercase text-primary">Analiz</span><span title="${esc(fmtStamp(e.ts))}">${fmtStamp(e.ts)}</span></div>
        <div class="text-[13px] mt-0.5 break-words"><b>${esc(e.keyword)}</b> <span class="text-secondary">(${esc(e.marketplace)})</span> analiz edildi${e.verdict ? ` · ön öneri <span class="chip ${DECISION_CHIP[e.verdict] || "na"}">${esc(e.verdict)}</span>` : ""}</div>
      </div>
    </li>` : `
    <li class="home-event">
      <span class="home-event-dot" style="background:${e.decision === "Uygun" ? "var(--primary)" : e.decision === "Elenmiş" ? "var(--error)" : "var(--tertiary-container)"}"></span>
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-x-2 text-[11px] text-secondary"><span class="font-semibold tracking-wide uppercase text-tertiary">Karar</span><span>${fmtStamp(e.ts)}</span>${e.by ? `<span>· ${esc(e.by)}</span>` : ""}</div>
        <div class="text-[13px] mt-0.5 break-words"><b>${esc(e.keyword)}</b> <span class="text-secondary">(${esc(e.marketplace)})</span> → <span class="chip ${DECISION_CHIP[e.decision] || "na"}">${esc(e.decision)}</span></div>
        ${e.note ? `<div class="text-xs text-on-surface-variant italic mt-0.5 break-words">"${esc(e.note)}"</div>` : ""}
      </div>
    </li>`).join("") : `<li class="text-sm text-secondary p-2">Henüz aktivite yok.</li>`;
}

(function bindHomeView() {
  $("#home-refresh").addEventListener("click", loadHome);
})();


// ---------------------------------------------------------------------------
// Eğitim & Görevler
// Sunucu uçları: GET /api/training/lessons (herkes, kendi atamaları),
// POST/PUT/DELETE /api/training/lessons[/id] (owner/admin),
// POST /api/training/lessons/{id}/complete (yalnızca kendi kaydı),
// GET /api/training/progress + GET /api/users (owner/admin),
// POST /api/users/{id}/role (yalnızca owner). Arayüzdeki gizleme yalnızca kolaylık.
// ---------------------------------------------------------------------------
const YT_ID_RE = /^[A-Za-z0-9_-]{11}$/;
const ROLE_LABEL = { owner: "Owner", admin: "Admin", member: "Üye" };
const trnState = { data: null, users: [], progress: null, editing: null };

const isStaff = () => currentUser.role === "owner" || currentUser.role === "admin";
const todayISO = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const fmtDue = (iso) => { if (!iso) return ""; const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d).toLocaleDateString("tr-TR", { day: "2-digit", month: "short", year: "numeric" }); };

function dueChip(lesson) {
  if (!lesson.due_date) return "";
  const t = todayISO();
  if (!lesson.completed && lesson.due_date < t) return `<span class="chip bad">Gecikti · ${esc(fmtDue(lesson.due_date))}</span>`;
  return `<span class="chip ${lesson.completed ? "na" : "warn"}">Son tarih · ${esc(fmtDue(lesson.due_date))}</span>`;
}

/** Video alanı: YouTube (ID tekrar doğrulanır) -> nocookie gömme butonu; diğer https -> yeni sekme */
function lessonVideoHtml(l) {
  if (l.youtube_id && YT_ID_RE.test(l.youtube_id)) {
    return `<div class="trn-video-slot"><button type="button" class="trn-play btn btn-outline btn-sm" data-yt="${esc(l.youtube_id)}"><span class="material-symbols-outlined">play_circle</span>Videoyu oynat</button></div>`;
  }
  const url = safeHttpsUrl(l.video_url);
  if (url) {
    let host = ""; try { host = new URL(url).hostname.replace(/^www\./, ""); } catch {}
    return `<a class="btn btn-outline btn-sm" href="${esc(url)}" target="_blank" rel="noopener noreferrer"><span class="material-symbols-outlined">open_in_new</span>Videoyu aç${host ? ` <span class="text-secondary font-normal">(${esc(host)})</span>` : ""}</a>`;
  }
  return "";
}

function embedYouTube(slot, id) {
  if (!YT_ID_RE.test(id)) return;
  const wrap = document.createElement("div");
  wrap.className = "trn-video";
  const f = document.createElement("iframe");
  f.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?rel=0&autoplay=1`;
  f.title = "Ders videosu";
  f.loading = "lazy";
  f.referrerPolicy = "strict-origin-when-cross-origin";
  f.allow = "accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture; fullscreen";
  f.allowFullscreen = true;
  wrap.appendChild(f);
  slot.replaceChildren(wrap);
}

async function loadTraining() {
  const status = $("#trn-status");
  const guest = !currentUser.role;
  $("#trn-guest").style.display = guest ? "block" : "none";
  $("#trn-mine").style.display = guest ? "none" : "block";
  $("#trn-progress").style.display = guest ? "none" : "block";
  $("#trn-staff").style.display = !guest && isStaff() ? "flex" : "none";
  $("#trn-role-chip").textContent = guest ? "" : ROLE_LABEL[currentUser.role] || "";
  $("#trn-role-chip").style.display = guest ? "none" : "";
  if (guest) { status.textContent = ""; return; }

  status.textContent = "yükleniyor…"; status.className = "status-line loading mt-4";
  try {
    const reqs = [apiFetch(`${API_BASE}/api/training/lessons`)];
    const pt = trnState.progTeam ? `?team_id=${encodeURIComponent(trnState.progTeam)}` : "";
    if (isStaff()) reqs.push(apiFetch(`${API_BASE}/api/users`), apiFetch(`${API_BASE}/api/training/progress${pt}`));
    const resps = await Promise.all(reqs);
    const bodies = await Promise.all(resps.map(r => r.json().catch(() => ({}))));
    resps.forEach((r, i) => { if (!r.ok) throw new Error(apiErrorText(bodies[i], r.status)); });
    trnState.data = bodies[0];
    // Sunucunun döndürdüğü rol esastır (ör. rol bu arada değiştiyse)
    if (bodies[0].role && bodies[0].role !== currentUser.role) { currentUser.role = bodies[0].role; return loadTraining(); }
    if (isStaff()) { trnState.users = bodies[1].users || []; trnState.teams = bodies[1].teams || []; trnState.progress = bodies[2]; }
    renderTraining();
    status.textContent = ""; status.className = "status-line mt-4";
  } catch (err) {
    status.textContent = `Hata: ${err.message}`; status.className = "status-line error mt-4";
  }
}

function renderTraining() {
  const d = trnState.data;
  const prog = d.my_progress || { completed: 0, total: 0 };
  $("#trn-progress-done").textContent = prog.completed;
  $("#trn-progress-total").textContent = prog.total;
  $("#trn-progress-bar").style.width = prog.total ? (prog.completed / prog.total) * 100 + "%" : "0";
  $("#trn-mine-count").textContent = prog.total ? `${prog.completed}/${prog.total} tamamlandı` : "";

  // --- Derslerim ---
  const list = $("#trn-mine-list");
  list.innerHTML = d.my_lessons.length ? "" : `<div class="card card-pad text-sm text-secondary xl:col-span-2">Sana atanmış ders yok.</div>`;
  d.my_lessons.forEach((l, i) => {
    const card = document.createElement("article");
    card.className = "card p-5 flex flex-col gap-3 min-w-0";
    card.innerHTML = `
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-1.5"><span class="chip na">Ders ${i + 1}</span>${dueChip(l)}</div>
          <h4 class="font-display font-semibold text-[17px] leading-snug mt-2 break-words">${esc(l.title)}</h4>
        </div>
        ${l.completed ? `<span class="chip ok shrink-0">Tamamlandı</span>` : `<span class="chip warn shrink-0">Bekliyor</span>`}
      </div>
      ${l.description ? `<p class="trn-desc text-[13.5px] text-on-surface-variant leading-relaxed">${esc(l.description)}</p>` : ""}
      ${lessonVideoHtml(l)}
      <div class="flex flex-wrap items-center justify-between gap-2 mt-auto pt-2 border-t border-hairline">
        <span class="text-xs text-secondary">${l.completed ? `Tamamlanma: ${esc(fmtStamp(l.completed_at))}` : "Henüz tamamlanmadı"}</span>
        <button type="button" class="trn-toggle btn ${l.completed ? "btn-outline" : "btn-primary"} btn-sm">
          <span class="material-symbols-outlined">${l.completed ? "undo" : "task_alt"}</span>${l.completed ? "Geri al" : "Tamamladım"}</button>
      </div>`;
    const play = card.querySelector(".trn-play");
    if (play) play.addEventListener("click", () => embedYouTube(play.parentElement, play.dataset.yt));
    card.querySelector(".trn-toggle").addEventListener("click", async (e) => {
      const btn = e.currentTarget; btn.disabled = true;
      try {
        const r = await apiFetch(`${API_BASE}/api/training/lessons/${encodeURIComponent(l.id)}/complete`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ completed: !l.completed }),
        });
        const b = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(apiErrorText(b, r.status));
        await loadTraining();
      } catch (err) {
        btn.disabled = false;
        $("#trn-status").textContent = `Hata: ${err.message}`; $("#trn-status").className = "status-line error mt-4";
      }
    });
    list.appendChild(card);
  });

  if (!isStaff()) return;
  renderLessonAdmin();
  renderMatrix();
}

// --- Ders yönetimi (owner/admin) ---
function renderTeamPicker(selected = []) {
  const sel = new Set(selected);
  $("#trn-f-teams").innerHTML = (trnState.teams || []).map(t => `
    <label><input type="checkbox" value="${esc(t.id)}" ${sel.has(t.id) ? "checked" : ""}><span title="${esc(t.name)}">${esc(t.name)}</span></label>`).join("")
    || `<span class="text-xs text-secondary">Henüz ekip yok — Ekip Yönetimi'nden oluşturun.</span>`;
}
function trnShowAssignPick(mode) {
  $("#trn-f-teams").style.display = mode === "teams" ? "grid" : "none";
  $("#trn-f-users").style.display = mode === "some" ? "grid" : "none";
}
const TRN_MODE_RADIO = { all: "all", teams: "teams", users: "some" };
function renderUserPicker(selected = []) {
  const box = $("#trn-f-users");
  const sel = new Set(selected);
  // Eğitim yalnızca ekip üyelerine atanır (sunucu da ekip dışı id'yi 422 ile reddeder)
  box.innerHTML = trnState.users.filter(u => u.in_team).map(u => `
    <label><input type="checkbox" value="${u.id}" ${sel.has(u.id) ? "checked" : ""}><span title="${esc(u.name || "")}">${esc(u.name || "(ad girilmemiş)")}</span>
      <span class="chip na !text-[10px]">${esc(ROLE_LABEL[u.role] || u.role)}</span></label>`).join("") || `<span class="text-xs text-secondary">Ekip üyesi yok.</span>`;
}

function resetLessonForm() {
  trnState.editing = null;
  $("#trn-f-id").value = ""; $("#trn-f-title").value = ""; $("#trn-f-desc").value = ""; $("#trn-f-url").value = "";
  const maxOrder = Math.max(0, ...((trnState.data?.all_lessons) || []).map(l => l.sort_order || 0));
  $("#trn-f-order").value = maxOrder + 1; $("#trn-f-due").value = "";
  $$('input[name="trn-assign"]').forEach(r => { r.checked = r.value === "all"; });
  trnShowAssignPick("all");
  renderUserPicker([]); renderTeamPicker([]);
  $("#trn-form-mode").textContent = "Yeni ders"; $("#trn-form-mode").className = "chip na";
  $("#trn-f-cancel").style.display = "none";
}

function fillLessonForm(l) {
  trnState.editing = l.id;
  $("#trn-f-id").value = l.id; $("#trn-f-title").value = l.title; $("#trn-f-desc").value = l.description || "";
  $("#trn-f-url").value = l.video_url || ""; $("#trn-f-order").value = l.sort_order ?? 0; $("#trn-f-due").value = l.due_date || "";
  const radio = TRN_MODE_RADIO[l.assign_mode] || (l.assign_all ? "all" : "some");
  $$('input[name="trn-assign"]').forEach(r => { r.checked = r.value === radio; });
  trnShowAssignPick(radio);
  renderUserPicker(l.assignee_ids || []); renderTeamPicker(l.team_ids || []);
  $("#trn-form-mode").textContent = `Düzenleniyor: #${l.id}`; $("#trn-form-mode").className = "chip warn";
  $("#trn-f-cancel").style.display = "";
  $("#trn-form").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderLessonAdmin() {
  if (trnState.editing == null) resetLessonForm();
  else { const l = trnState.data.all_lessons.find(x => x.id === trnState.editing); l ? (renderUserPicker(l.assignee_ids), renderTeamPicker(l.team_ids || [])) : resetLessonForm(); }
  const tbody = $("#trn-lessons-tbody");
  const lessons = trnState.data.all_lessons || [];
  tbody.innerHTML = lessons.length ? "" : `<tr><td colspan="6" class="muted !text-center !py-6">Henüz ders yok — yukarıdan ekleyin.</td></tr>`;
  lessons.forEach(l => {
    const tr = document.createElement("tr");
    const video = l.youtube_id ? `<span class="chip ok">YouTube</span>` : safeHttpsUrl(l.video_url) ? `<span class="chip na">Link</span>` : `<span class="muted">—</span>`;
    const tname = new Map((trnState.teams || []).map(t => [t.id, t.name]));
    const assign = l.assign_mode === "teams"
      ? (l.team_ids || []).map(id => `<span class="chip na mr-1 mb-1 max-w-[160px] truncate" title="${esc(tname.get(id) || "")}">${esc(tname.get(id) || "?")}</span>`).join("") || `<span class="chip bad" title="Atanmış ekip kalmadı — ders kimseye görünmüyor">Ekip yok</span>`
      : l.assign_mode === "all" || l.assign_all ? `<span class="chip ok">Tüm ekipler</span>` : `<span class="chip warn">${l.assignee_ids.length} kişi</span>`;
    tr.innerHTML = `<td class="l tabular">${esc(l.sort_order)}</td>
      <td class="l"><div class="font-semibold whitespace-normal break-words min-w-[160px]">${esc(l.title)}</div></td>
      <td class="l">${video}</td><td class="l">${l.due_date ? esc(fmtDue(l.due_date)) : '<span class="muted">—</span>'}</td><td class="l"><div class="whitespace-normal min-w-[110px]">${assign}</div></td>
      <td class="!text-center whitespace-nowrap"><button type="button" class="icon-btn trn-edit" title="Düzenle" aria-label="Düzenle"><span class="material-symbols-outlined">edit</span></button>
        <button type="button" class="icon-btn trn-del !text-error" title="Sil" aria-label="Sil"><span class="material-symbols-outlined">delete</span></button></td>`;
    tr.querySelector(".trn-edit").addEventListener("click", () => fillLessonForm(l));
    tr.querySelector(".trn-del").addEventListener("click", async () => {
      if (!confirm(`"${l.title}" dersi ve tüm tamamlama kayıtları silinsin mi?`)) return;
      const r = await apiFetch(`${API_BASE}/api/training/lessons/${encodeURIComponent(l.id)}`, { method: "DELETE" });
      const b = await r.json().catch(() => ({}));
      if (!r.ok) { $("#trn-f-msg").textContent = `Hata: ${apiErrorText(b, r.status)}`; $("#trn-f-msg").className = "text-xs text-error"; return; }
      if (trnState.editing === l.id) trnState.editing = null;
      loadTraining();
    });
    tbody.appendChild(tr);
  });
}

async function saveLesson(e) {
  e.preventDefault();
  const msg = $("#trn-f-msg");
  const title = $("#trn-f-title").value.trim();
  const url = $("#trn-f-url").value.trim();
  const radio = $('input[name="trn-assign"]:checked').value;
  const mode = radio === "some" ? "users" : radio;
  const assignAll = mode === "all";
  const assignees = $$('#trn-f-users input:checked').map(i => Number(i.value));
  const teamIds = $$('#trn-f-teams input:checked').map(i => Number(i.value));
  const fail = (t) => { msg.textContent = t; msg.className = "text-xs text-error"; };
  if (!title) return fail("Başlık gerekli.");
  if (url && !safeHttpsUrl(url)) return fail("Video linki https:// ile başlamalı.");
  if (mode === "users" && !assignees.length) return fail("En az bir kişi seçin ya da 'Tüm ekipler'e atayın.");
  if (mode === "teams" && !teamIds.length) return fail("En az bir ekip seçin ya da 'Tüm ekipler'e atayın.");
  const body = {
    title, description: $("#trn-f-desc").value, video_url: url || null,
    sort_order: Math.max(0, Math.round(Number($("#trn-f-order").value) || 0)),
    due_date: $("#trn-f-due").value || null, assign_all: assignAll, assign_mode: mode,
    assignee_ids: mode === "users" ? assignees : [], team_ids: mode === "teams" ? teamIds : [],
  };
  const id = trnState.editing;
  $("#trn-f-save").disabled = true;
  try {
    const r = await apiFetch(`${API_BASE}/api/training/lessons${id ? "/" + encodeURIComponent(id) : ""}`, {
      method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(apiErrorText(b, r.status));
    trnState.editing = null;
    msg.textContent = id ? "Ders güncellendi." : "Ders eklendi."; msg.className = "text-xs text-primary";
    await loadTraining();
  } catch (err) { fail(`Kaydedilemedi: ${err.message}`); }
  finally { $("#trn-f-save").disabled = false; }
}

// --- Kişi × ders tablosu ---
function renderMatrix() {
  const pg = trnState.progress;
  const table = $("#trn-matrix");
  const tsel = $("#trn-prog-team");
  tsel.innerHTML = `<option value="">Tüm ekipler</option>` + (pg?.teams || []).map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
  tsel.value = (pg?.teams || []).some(t => String(t.id) === String(trnState.progTeam || "")) ? String(trnState.progTeam) : "";
  if (pg && !pg.users.length && pg.lessons.length) { table.innerHTML = `<tbody><tr><td class="muted !py-6">Bu ekipte kimse yok.</td></tr></tbody>`; return; }
  if (!pg || !pg.lessons.length) { table.innerHTML = `<tbody><tr><td class="muted !py-6">Henüz ders yok.</td></tr></tbody>`; return; }
  const done = new Map(pg.completions.map(c => [`${c.lesson_id}:${c.user_id}`, c.completed_at]));
  const t = todayISO();
  const head = `<thead><tr><th>Kişi</th><th>İlerleme</th>${pg.lessons.map(l => `<th class="lesson" title="${esc(l.title)}">${esc(l.title.length > 40 ? l.title.slice(0, 40) + "…" : l.title)}</th>`).join("")}</tr></thead>`;
  const rows = pg.users.map(u => {
    const assigned = pg.lessons.filter(l => l.assignee_ids.includes(u.id));
    const n = assigned.filter(l => done.has(`${l.id}:${u.id}`)).length;
    const cells = pg.lessons.map(l => {
      if (!l.assignee_ids.includes(u.id)) return `<td><span class="trn-cell na" title="Atanmadı">·</span></td>`;
      const at = done.get(`${l.id}:${u.id}`);
      if (at) return `<td><span class="trn-cell done" title="Tamamlandı: ${esc(fmtStamp(at))}">✓</span></td>`;
      const late = l.due_date && l.due_date < t;
      return `<td><span class="trn-cell ${late ? "late" : "todo"}" title="${late ? "Son tarih geçti" : "Atandı, tamamlanmadı"}">—</span></td>`;
    }).join("");
    return `<tr><td><div class="font-medium truncate max-w-[200px]" title="${esc(u.name || "")}">${esc(u.name || "(ad girilmemiş)")}</div><div class="text-[11px] text-secondary truncate max-w-[200px]">${esc(ROLE_LABEL[u.role] || u.role)}${u.email ? ` · ${esc(u.email)}` : ""}</div></td>
      <td class="tabular">${n}/${assigned.length}</td>${cells}</tr>`;
  }).join("");
  table.innerHTML = head + `<tbody>${rows}</tbody>`;
}

(function bindTrainingView() {
  $("#trn-form").addEventListener("submit", saveLesson);
  $("#trn-f-cancel").addEventListener("click", () => { trnState.editing = null; resetLessonForm(); $("#trn-f-msg").textContent = ""; });
  $$('input[name="trn-assign"]').forEach(r => r.addEventListener("change", () => trnShowAssignPick($('input[name="trn-assign"]:checked').value)));
  $("#trn-prog-team").addEventListener("change", (e) => { trnState.progTeam = e.target.value || null; loadTraining(); });
  $("#trn-login").addEventListener("click", () => showLogin("", !authRequiredGlobal));
})();


// ---------------------------------------------------------------------------
// Araştırma Kontrol Listesi
// POST /api/checklists {analysis_key, marketplace} — sunucu keyword_analysis kaydından
// snapshot alır (istemci değer göndermez). Otomatik maddeler sunucuda hesaplanır;
// görünürlük (member yalnızca kendi listeleri) ve kilit (423) sunucuda uygulanır.
// ---------------------------------------------------------------------------
const CK_STAGE_ICON = { market: "trending_up", competition: "shield", defects: "psychology", legal: "gavel", costs: "calculate" };
const ckState = { lists: [], current: null, template: null, tplDraft: null };

function ckAnalysisKey(data) {
  return data.analysis_mode === "asin" && data.asin ? `ASIN:${data.asin}` : data.keyword;
}

async function startChecklist(data, btn) {
  if (!currentUser.role) { showLogin("Kontrol listesi için kayıtlı bir hesapla giriş yapın.", !authRequiredGlobal); return; }
  const original = btn.innerHTML; btn.disabled = true; btn.textContent = "oluşturuluyor…";
  try {
    const r = await apiFetch(`${API_BASE}/api/checklists`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ analysis_key: ckAnalysisKey(data), marketplace: data.marketplace }),
    });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(apiErrorText(b, r.status));
    ckState.current = b;
    showView("checklists");
  } catch (err) {
    alert(`Kontrol listesi oluşturulamadı: ${err.message}`);
  } finally { btn.disabled = false; btn.innerHTML = original; }
}

async function ckApi(url, opts = {}) {
  const r = await apiFetch(`${API_BASE}${url}`, { headers: { "Content-Type": "application/json" }, ...opts });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(apiErrorText(b, r.status)); e.status = r.status; throw e; }
  return b;
}

function ckSetStatus(text, cls = "") { const el = $("#ck-status"); el.textContent = text; el.className = `status-line mt-4 ${cls}`.trim(); }

async function loadChecklists() {
  const guest = !currentUser.role;
  $("#ck-guest").style.display = guest ? "block" : "none";
  $("#ck-body").style.display = guest ? "none" : "grid";
  $("#ck-tpl-open").style.display = currentUser.role === "owner" ? "" : "none";
  if (guest) { $("#ck-tpl").style.display = "none"; ckSetStatus(""); return; }
  ckSetStatus("yükleniyor…", "loading");
  try {
    const b = await ckApi("/api/checklists");
    ckState.lists = b.checklists || [];
    const wanted = ckState.current?.id ?? ckState.lists[0]?.id;
    if (wanted != null && (!ckState.current || ckState.current.id !== wanted || !ckState.current.items)) {
      ckState.current = await ckApi(`/api/checklists/${encodeURIComponent(wanted)}`);
    } else if (wanted == null) ckState.current = null;
    renderChecklistList(); renderChecklistDetail(); ckSetStatus("");
  } catch (err) { ckSetStatus(`Hata: ${err.message}`, "error"); }
}

function ckProgressBar(p) {
  const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
  return { pct, html: `<div class="crit-track !mt-2"><div class="crit-fill" style="width:${pct}%"></div></div>` };
}

function renderChecklistList() {
  const box = $("#ck-list");
  $("#ck-list-count").textContent = ckState.lists.length ? `${ckState.lists.length} liste` : "";
  box.innerHTML = ckState.lists.length ? "" : `<div class="card card-pad text-sm text-secondary w-full">Henüz liste yok. Ürün Analizi ekranındaki <b>Kontrol Listesi Başlat</b> butonuyla oluştur.</div>`;
  ckState.lists.forEach(l => {
    const pb = ckProgressBar(l.progress);
    const b = document.createElement("button");
    b.type = "button";
    b.className = `card ck-card p-4 ${ckState.current?.id === l.id ? "active" : ""}`;
    b.innerHTML = `
      <div class="flex items-start justify-between gap-2">
        <div class="font-semibold text-sm leading-snug break-words min-w-0">${esc(l.title || l.analysis_key)}</div>
        <span class="flex gap-1 shrink-0">${l.status === "locked" && l.overridden_count ? '<span class="chip warn" title="Geçmeyen/verisi olmayan otomatik madde gerekçeyle geçildi">gerekçeli</span>' : ""}${l.status === "locked" ? '<span class="chip ok">Kilitli</span>' : '<span class="chip na">Açık</span>'}</span>
      </div>
      <div class="text-[11px] text-secondary mt-1 truncate">${esc(l.marketplace)} · analiz ${esc(fmtStamp(l.analysis_fetched_at))}${l.is_mine ? "" : ` · ${esc(l.owner_name || "—")}`}</div>
      <div class="flex items-center justify-between text-xs mt-2"><span class="tabular font-medium">${l.progress.done}/${l.progress.total} · %${pb.pct}</span>
        ${l.progress.critical_open ? `<span class="chip bad">${l.progress.critical_open} kritik</span>` : ""}</div>${pb.html}`;
    b.addEventListener("click", async () => {
      try { ckState.current = await ckApi(`/api/checklists/${encodeURIComponent(l.id)}`); renderChecklistList(); renderChecklistDetail(); }
      catch (err) { ckSetStatus(`Hata: ${err.message}`, "error"); }
    });
    box.appendChild(b);
  });
}

/** Mutasyon sonrası sunucunun döndürdüğü güncel listeyi uygula (423 = kilitlendi -> yeniden yükle) */
async function ckMutate(url, opts) {
  try {
    ckState.current = await ckApi(url, opts);
    const i = ckState.lists.findIndex(x => x.id === ckState.current.id);
    if (i >= 0) ckState.lists[i] = { ...ckState.lists[i], ...ckState.current };
    renderChecklistList(); renderChecklistDetail(); ckSetStatus("");
  } catch (err) {
    ckSetStatus(`Hata: ${err.message}`, "error");
    if (err.status === 423 || err.status === 404) loadChecklists();
  }
}

function renderChecklistDetail() {
  const box = $("#ck-detail");
  const c = ckState.current;
  if (!c || !c.items) { box.innerHTML = ""; return; }
  const pb = ckProgressBar(c.progress);
  const snap = c.snapshot || {};
  const locked = c.status === "locked";
  const staff = isStaff();
  const remaining = c.progress.total - c.progress.done;
  const reasonBox = (cls, ph) => `<textarea class="field ${cls} w-full !h-auto min-h-[76px] py-2 mt-2" maxlength="1000" rows="3" placeholder="${ph}"></textarea>
      <div class="ck-reason-msg text-xs text-error mt-1"></div>`;
  let approval;
  if (locked) {
    approval = `<div class="flex items-center gap-2 text-sm"><span class="material-symbols-outlined text-primary">lock</span><span>Onaylandı ve kilitlendi</span></div>
      <div class="text-xs text-secondary mt-1">${esc(c.locked_by || "—")} · ${esc(fmtStamp(c.locked_at))}</div>
      ${c.overridden_count ? `<div class="mt-3"><span class="chip warn">${c.overridden_count} otomatik madde gerekçeyle geçildi</span></div>` : ""}
      ${c.approval_reason ? `<div class="text-xs mt-2"><span class="text-secondary">Onay gerekçesi:</span> <span class="italic break-words">"${esc(c.approval_reason)}"</span></div>` : ""}`;
    if (c.can_unlock) approval += `<div class="border-t border-outline-variant/40 mt-4 pt-3">
      <div class="text-xs font-semibold">Kilidi aç (yalnızca owner)</div>
      ${reasonBox("ck-unlock-reason", "Kilidi neden açıyorsun? (zorunlu)")}
      <button type="button" class="ck-unlock btn btn-outline w-full mt-1"><span class="material-symbols-outlined">lock_open</span>Kilidi Aç</button></div>`;
  } else if (c.can_lock) {
    approval = c.needs_reason ? `<div class="text-xs text-on-surface-variant">Şu otomatik maddeler geçmedi ya da verisi yok. Onaylamak için <b>gerekçe zorunlu</b>; bu maddeler "gerekçeyle geçildi" olarak işaretlenir:</div>
      <ul class="text-xs mt-2 flex flex-col gap-1">${c.auto_unpassed.map(a => `<li class="flex flex-wrap gap-1.5 items-center"><span class="ck-badge ${esc(a.status)}">${esc(a.display)}</span><span class="break-words min-w-0">${esc(a.text)}</span></li>`).join("")}</ul>
      ${reasonBox("ck-lock-reason", "Onay gerekçesi (zorunlu)")}` : "";
    approval += `<button type="button" class="ck-lock btn btn-primary w-full ${c.needs_reason ? "mt-1" : ""}"><span class="material-symbols-outlined">lock</span>${c.needs_reason ? "Gerekçeyle Onayla &amp; Kilitle" : "Onayla &amp; Kilitle"}</button>
      <div class="text-xs text-secondary mt-2">Kilitlendikten sonra liste değiştirilemez; kilidi yalnızca owner gerekçeyle açabilir.</div>`;
  } else {
    const mo = c.progress.manual_open;
    approval = `<button type="button" class="btn btn-primary w-full" disabled><span class="material-symbols-outlined">lock</span>Onayla &amp; Kilitle</button>
      <div class="text-xs text-secondary mt-2">${!staff ? "Onay owner veya admin tarafından verilir." : ""}${mo ? ` ${mo} manuel madde tamamlanmadı.` : ""}</div>`;
  }
  const evLabel = { lock: ["lock", "Onaylandı & kilitlendi"], unlock: ["lock_open", "Kilit açıldı"] };
  const eventsHtml = (c.events || []).length ? `<div class="card card-pad"><h4 class="font-display font-semibold mb-3">Onay geçmişi</h4>
      <ol class="flex flex-col gap-3">${c.events.slice().reverse().map(e => {
        const [ico, label] = evLabel[e.kind] || ["history", e.kind];
        return `<li class="text-xs"><div class="flex items-center gap-1.5 font-semibold"><span class="material-symbols-outlined !text-[16px] ${e.kind === "unlock" ? "text-tertiary" : "text-primary"}">${ico}</span>${esc(label)}</div>
          <div class="text-secondary mt-0.5 break-words">${esc(e.by || "—")} · ${esc(fmtStamp(e.at))}</div>
          ${e.reason ? `<div class="italic mt-0.5 break-words">"${esc(e.reason)}"</div>` : ""}
          ${(e.overridden || []).length ? `<div class="text-secondary mt-0.5 break-words">Gerekçeyle geçilen: ${e.overridden.map(o => esc(o.text)).join("; ")}</div>` : ""}</li>`;
      }).join("")}</ol></div>` : "";

  const stagesHtml = (c.stages || []).map((st, si) => {
    const items = c.items.filter(i => i.stage_key === st.key);
    const done = items.filter(i => i.checked).length;
    const rows = items.map(it => ckItemHtml(it, c)).join("");
    return `<section class="card card-pad ck-stage ${st.critical ? "critical" : ""}">
      <div class="flex flex-wrap items-start justify-between gap-2">
        <div class="flex gap-3 min-w-0">
          <span class="w-9 h-9 shrink-0 rounded-lg ${st.critical ? "bg-error-container text-error" : "bg-surface-container-low text-primary"} grid place-items-center font-display font-semibold text-sm">${String(si + 1).padStart(2, "0")}</span>
          <div class="min-w-0"><h4 class="font-display font-semibold text-[16px] leading-snug break-words">${esc(st.title)}</h4>
            <p class="text-xs ${st.critical ? "text-error" : "text-secondary"} mt-0.5 break-words">${st.critical ? "Kritik aşama · " : ""}${esc(st.subtitle || "")}</p></div>
        </div>
        <span class="chip ${done === items.length && items.length ? "ok" : st.critical && done < items.length ? "bad" : "na"} shrink-0">${done} / ${items.length} tamamlandı</span>
      </div>
      <div class="flex flex-col gap-2 mt-4">${rows || '<div class="text-xs text-secondary">Bu aşamada madde yok.</div>'}</div>
    </section>`;
  }).join("");

  box.innerHTML = `
    <div class="card card-pad">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="eyebrow">Aktif odak</div>
          <h2 class="font-display text-xl font-semibold mt-1 break-words">${esc(c.title || c.analysis_key)}</h2>
          <div class="flex flex-wrap gap-1.5 mt-2"><span class="chip mono">${esc(c.analysis_key)}</span><span class="chip na">${esc(c.marketplace)}</span>
            ${locked ? '<span class="chip ok">Kilitli</span>' : '<span class="chip na">Açık</span>'}${c.is_mine ? "" : `<span class="chip warn">${esc(c.owner_name || "—")}</span>`}</div>
          <div class="ck-snap mt-3 inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-lg bg-surface-container-low px-3 py-2 text-xs">
            <span class="material-symbols-outlined !text-[16px] text-primary" aria-hidden="true">event</span>
            <span class="font-semibold">Snapshot: analiz tarihi ${esc(fmtStamp(snap.fetched_at))}</span>
            ${snap.fetched_at ? `<span class="text-secondary">(${esc(fmtAgo(snap.fetched_at))})</span>` : ""}</div>
          <div class="text-xs text-secondary mt-2">Liste oluşturma: ${esc(fmtStamp(c.created_at))}${snap.category ? ` · ${esc(snap.category)}` : ""} · değerler bu analiz anından kopyalandı, sonradan değişmez.</div>
        </div>
        ${c.can_delete ? '<button type="button" class="ck-delete btn btn-danger shrink-0"><span class="material-symbols-outlined">delete</span>Listeyi sil</button>' : ""}
      </div>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
      <div class="card p-5"><div class="eyebrow">Protokol ilerlemesi</div>
        <div class="mt-2 flex items-baseline gap-2"><span class="font-display text-[34px] font-medium tracking-tight tabular">%${pb.pct}</span>
          <span class="text-sm text-secondary tabular">${c.progress.done} / ${c.progress.total} doğrulandı · ${remaining} kalan</span></div>${pb.html}</div>
      <div class="card p-5 ${c.progress.critical_open ? "!bg-error-container/40" : ""}"><div class="eyebrow">Risk durumu</div>
        <div class="mt-2 font-display text-xl font-semibold ${c.progress.critical_open ? "text-error" : "text-primary"}">${c.progress.critical_open ? `${c.progress.critical_open} kritik kontrol açık` : "Kritik açık madde yok"}</div>
        <div class="text-xs text-secondary mt-1">Patent, marka &amp; hukuk aşamasındaki tamamlanmamış maddeler kritik sayılır.</div></div>
    </div>
    <div class="grid grid-cols-1 2xl:grid-cols-[minmax(0,1fr)_280px] gap-4 mt-4">
      <div class="flex flex-col gap-4 min-w-0">${stagesHtml}</div>
      <div class="flex flex-col gap-4 min-w-0">
        <div class="card card-pad"><h4 class="font-display font-semibold">Özel Kontrol Maddesi Ekle</h4>
          <p class="text-xs text-secondary mt-1">Bu ürüne özgü bir kontrol adımı ekle.</p>
          <form class="ck-add flex flex-col gap-2 mt-3">
            <input type="text" class="field ck-add-text" maxlength="300" placeholder="örn. FCC belgesi kontrolü" ${c.can_edit ? "" : "disabled"}>
            <select class="field ck-add-stage" ${c.can_edit ? "" : "disabled"}>${(c.stages || []).map((st, i) => `<option value="${esc(st.key)}">Aşama ${String(i + 1).padStart(2, "0")}: ${esc(st.title)}</option>`).join("")}</select>
            <button type="submit" class="btn btn-outline" ${c.can_edit ? "" : "disabled"}><span class="material-symbols-outlined">add_task</span>Ekle</button>
            <span class="ck-add-msg text-xs text-error"></span>
          </form></div>
        <div class="card card-pad"><h4 class="font-display font-semibold mb-3">Onay</h4>${approval}</div>
        ${eventsHtml}
      </div>
    </div>`;

  // --- olaylar ---
  box.querySelectorAll(".ck-item[data-id]").forEach(row => {
    const id = Number(row.dataset.id);
    const it = c.items.find(x => x.id === id);
    const cb = row.querySelector("input[type=checkbox]");
    const note = row.querySelector(".ck-note");
    if (cb) cb.addEventListener("change", () => ckMutate(`/api/checklists/${c.id}/items/${id}`, { method: "POST", body: JSON.stringify({ checked: cb.checked, note: note ? note.value : it.note }) }));
    const save = row.querySelector(".ck-note-save");
    if (save) save.addEventListener("click", () => ckMutate(`/api/checklists/${c.id}/items/${id}`, { method: "POST", body: JSON.stringify({ checked: it.checked, note: note.value }) }));
    const del = row.querySelector(".ck-del");
    if (del) del.addEventListener("click", () => { if (confirm("Bu özel madde silinsin mi?")) ckMutate(`/api/checklists/${c.id}/items/${id}`, { method: "DELETE" }); });
  });
  const form = box.querySelector(".ck-add");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = form.querySelector(".ck-add-text").value.trim();
    if (!text) { form.querySelector(".ck-add-msg").textContent = "Madde metni gerekli."; return; }
    ckMutate(`/api/checklists/${c.id}/items`, { method: "POST", body: JSON.stringify({ stage_key: form.querySelector(".ck-add-stage").value, text }) });
  });
  // Gerekçe istemci tarafında da kontrol edilir; asıl kural sunucuda (422)
  const readReason = (sel) => {
    const ta = box.querySelector(sel);
    if (!ta) return "";
    const v = ta.value.trim();
    const msg = ta.parentElement.querySelector(".ck-reason-msg");
    if (v.length < 5) { if (msg) msg.textContent = "Gerekçe zorunlu (en az 5 karakter)."; ta.focus(); return null; }
    if (msg) msg.textContent = "";
    return v;
  };
  box.querySelector(".ck-lock")?.addEventListener("click", () => {
    const reason = c.needs_reason ? readReason(".ck-lock-reason") : "";
    if (reason === null) return;
    if (confirm("Liste onaylanıp kilitlensin mi? Kilitlendikten sonra değiştirilemez.")) {
      ckMutate(`/api/checklists/${c.id}/lock`, { method: "POST", body: JSON.stringify(reason ? { reason } : {}) });
    }
  });
  box.querySelector(".ck-unlock")?.addEventListener("click", () => {
    const reason = readReason(".ck-unlock-reason");
    if (reason === null) return;
    if (confirm("Kilit açılsın mı? Liste yeniden düzenlenebilir olur; gerekçeyle geçilen maddeler sıfırlanır.")) {
      ckMutate(`/api/checklists/${c.id}/unlock`, { method: "POST", body: JSON.stringify({ reason }) });
    }
  });
  box.querySelector(".ck-delete")?.addEventListener("click", async () => {
    if (!confirm("Bu kontrol listesi silinsin mi?")) return;
    try { await ckApi(`/api/checklists/${c.id}`, { method: "DELETE" }); ckState.current = null; loadChecklists(); }
    catch (err) { ckSetStatus(`Hata: ${err.message}`, "error"); if (err.status === 423) loadChecklists(); }
  });
}

function ckItemHtml(it, c) {
  if (it.kind === "auto") {
    const ico = { pass: ["check_circle", "text-primary"], fail: ["cancel", "text-error"], no_data: ["help", "text-outline"] }[it.auto_status] || ["help", "text-outline"];
    const ov = it.auto_override;
    return `<div class="ck-item ${it.auto_status === "pass" ? "done" : ov ? "override" : it.auto_status === "fail" ? "fail" : ""}" title="Analiz verisinden otomatik değerlendirildi; elle işaretlenemez">
      <span class="material-symbols-outlined ck-auto-ico ${ico[1]}" aria-hidden="true">${ico[0]}</span>
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2"><span class="ck-text text-[13.5px] font-medium">${esc(it.text)}</span><span class="chip na !text-[10px]">otomatik</span>${ov ? '<span class="chip warn !text-[10px]">gerekçeyle geçildi</span>' : ""}</div>
        <div class="flex flex-wrap items-center gap-2 mt-1.5"><span class="ck-badge ${esc(it.auto_status)}">${esc(it.auto_display)}</span>
          <span class="text-[11px] text-secondary">${ov ? "Onayda gerekçeyle geçildi (bkz. onay gerekçesi)" : it.auto_status === "no_data" ? "Analizde bu değer yok — geçti sayılmaz" : it.auto_status === "pass" ? "Eşik sağlandı" : "Eşik sağlanmadı"}</span></div>
      </div></div>`;
  }
  const edit = c.can_edit;
  return `<div class="ck-item ${it.checked ? "done" : ""}" data-id="${it.id}">
    <input type="checkbox" ${it.checked ? "checked" : ""} ${edit ? "" : "disabled"} aria-label="Tamamlandı olarak işaretle">
    <div class="min-w-0 flex-1">
      <div class="flex flex-wrap items-center gap-2"><span class="ck-text text-[13.5px] ${it.checked ? "text-secondary" : "font-medium"}">${esc(it.text)}</span>
        ${it.kind === "custom" ? '<span class="chip warn !text-[10px]">özel</span>' : ""}</div>
      ${it.checked ? `<div class="text-[11px] text-primary mt-1">✓ ${esc(it.checked_by || "—")} · ${esc(fmtStamp(it.checked_at))}</div>` : ""}
      ${edit ? `<div class="flex gap-2 mt-2"><input type="text" class="field ck-note flex-1 min-w-0" maxlength="1000" placeholder="not (opsiyonel)" value="${esc(it.note || "")}">
          <button type="button" class="ck-note-save btn btn-outline btn-sm !h-[30px]">Notu kaydet</button></div>`
        : it.note ? `<div class="text-xs text-on-surface-variant italic mt-1 break-words">"${esc(it.note)}"</div>` : ""}
    </div>
    ${edit && it.kind === "custom" ? '<button type="button" class="ck-del icon-btn !text-error shrink-0" title="Özel maddeyi sil" aria-label="Özel maddeyi sil"><span class="material-symbols-outlined">delete</span></button>' : ""}
  </div>`;
}

// --- Şablon editörü (yalnızca owner; sunucu PUT'ta require_owner) ---
const CK_TH_LABEL = {
  searches_min: ["Ana keyword arama >", ""], top10_revenue_min: ["İlk 10 ciro > ($)", ""], reviews_max: ["Ort. yorum <", ""],
  new_brands_min: ["Yeni marka ≥", ""], top3_share_max: ["İlk 3 pay < (%)", "pct"], price_min: ["Fiyat min ($)", ""], price_max: ["Fiyat max ($)", ""],
};

async function openTemplateEditor() {
  try {
    const b = await ckApi("/api/checklists/template");
    ckState.template = b;
    ckState.tplDraft = JSON.parse(JSON.stringify(b.template));
    renderTemplateEditor();
    $("#ck-tpl").style.display = "block";
    $("#ck-tpl").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) { ckSetStatus(`Hata: ${err.message}`, "error"); }
}

function renderTemplateEditor() {
  const t = ckState.tplDraft, meta = ckState.template;
  $("#ck-tpl-meta").textContent = meta.is_default ? "Varsayılan şablon" : `Son değişiklik: ${meta.updated_by || ""} · ${fmtStamp(meta.updated_at)}`;
  $("#ck-tpl-th").innerHTML = Object.entries(CK_TH_LABEL).map(([k, [label, unit]]) => {
    const lim = meta.threshold_limits[k];
    const v = unit === "pct" ? +(t.thresholds[k] * 100).toFixed(2) : t.thresholds[k];
    return `<label class="flex flex-col gap-1.5"><span class="eyebrow">${esc(label)}</span>
      <input type="number" class="field ck-th" data-k="${k}" data-unit="${unit}" step="${lim.integer ? 1 : "any"}" min="${unit === "pct" ? 0 : lim.min}" max="${unit === "pct" ? 100 : lim.max}" value="${esc(v)}"></label>`;
  }).join("");
  $("#ck-tpl-stages").innerHTML = t.stages.map((st, si) => `
    <div class="rounded-xl border border-hairline p-4 ${st.critical ? "ck-stage critical" : ""}" data-si="${si}">
      <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
        <label class="flex flex-col gap-1"><span class="eyebrow">Aşama ${si + 1} başlığı${st.critical ? " · kritik" : ""}</span><input type="text" class="field ck-st-title" maxlength="120" value="${esc(st.title)}"></label>
        <label class="flex flex-col gap-1"><span class="eyebrow">Alt başlık</span><input type="text" class="field ck-st-sub" maxlength="300" value="${esc(st.subtitle || "")}"></label>
      </div>
      <div class="flex flex-col gap-2 mt-3">
        ${st.items.map((it, ii) => it.auto
          ? `<div class="flex items-center gap-2 text-[13px] text-secondary"><span class="chip na !text-[10px]">otomatik</span><span class="break-words">${esc(it.text || "")}</span></div>`
          : `<div class="flex gap-2"><input type="text" class="field ck-it flex-1 min-w-0" data-ii="${ii}" maxlength="300" value="${esc(it.text)}">
              <button type="button" class="icon-btn !text-error ck-it-del shrink-0" data-ii="${ii}" title="Maddeyi sil" aria-label="Maddeyi sil"><span class="material-symbols-outlined">delete</span></button></div>`).join("")}
        <button type="button" class="btn btn-outline btn-sm self-start ck-it-add"><span class="material-symbols-outlined">add</span>Manuel madde ekle</button>
      </div>
    </div>`).join("");
  // Taslağa geri yaz (yeniden render öncesi değerleri korumak için)
  const sync = () => {
    $$("#ck-tpl-th .ck-th").forEach(i => { const n = Number(i.value); t.thresholds[i.dataset.k] = i.dataset.unit === "pct" ? n / 100 : n; });
    $$("#ck-tpl-stages [data-si]").forEach(box => {
      const st = t.stages[Number(box.dataset.si)];
      st.title = box.querySelector(".ck-st-title").value; st.subtitle = box.querySelector(".ck-st-sub").value;
      box.querySelectorAll(".ck-it").forEach(i => { st.items[Number(i.dataset.ii)].text = i.value; });
    });
  };
  ckState.tplSync = sync;
  $$("#ck-tpl-stages [data-si]").forEach(box => {
    const st = t.stages[Number(box.dataset.si)];
    box.querySelector(".ck-it-add").addEventListener("click", () => { sync(); st.items.push({ text: "" }); renderTemplateEditor(); });
    box.querySelectorAll(".ck-it-del").forEach(b => b.addEventListener("click", () => { sync(); st.items.splice(Number(b.dataset.ii), 1); renderTemplateEditor(); }));
  });
}

(function bindChecklistView() {
  $("#ck-tpl-open").addEventListener("click", openTemplateEditor);
  $("#ck-tpl-close").addEventListener("click", () => { $("#ck-tpl").style.display = "none"; $("#ck-tpl-msg").textContent = ""; });
  $("#ck-tpl-save").addEventListener("click", async () => {
    const msg = $("#ck-tpl-msg");
    ckState.tplSync();
    const t = ckState.tplDraft;
    if (t.stages.some(st => st.items.some(it => !it.auto && !String(it.text || "").trim()))) { msg.textContent = "Boş madde metni var."; msg.className = "text-xs text-error"; return; }
    try {
      const b = await ckApi("/api/checklists/template", { method: "PUT", body: JSON.stringify({ template: t }) });
      ckState.template = b; ckState.tplDraft = JSON.parse(JSON.stringify(b.template)); renderTemplateEditor();
      msg.textContent = "Şablon kaydedildi — yeni listelere uygulanacak."; msg.className = "text-xs text-primary";
    } catch (err) { msg.textContent = `Kaydedilemedi: ${err.message}`; msg.className = "text-xs text-error"; }
  });
  $("#ck-tpl-reset").addEventListener("click", async () => {
    if (!confirm("Şablon varsayılana dönsün mü? (Mevcut listeler etkilenmez)")) return;
    const msg = $("#ck-tpl-msg");
    try {
      const b = await ckApi("/api/checklists/template/reset", { method: "POST" });
      ckState.template = b; ckState.tplDraft = JSON.parse(JSON.stringify(b.template)); renderTemplateEditor();
      msg.textContent = "Varsayılan şablona dönüldü."; msg.className = "text-xs text-primary";
    } catch (err) { msg.textContent = `Hata: ${err.message}`; msg.className = "text-xs text-error"; }
  });
})();

// ===========================================================================
// LANSMAN RAPORU — hesaplama SUNUCUDA (api/launch_report.py); burada yalnızca form.
// MCP çağrısı yok. Rapor HTML olarak döner, Blob URL ile yeni sekmede açılır.
// ===========================================================================
const LR_MAX_VARS = 4;
const lrVal = (id) => { const v = $(`#${id}`).value.trim(); return v === "" ? null : Number(v); };

function lrVarRow(v = {}) {
  const row = document.createElement("div");
  row.className = "lr-var rounded-xl border border-hairline p-3";
  const f = (cls, label, attrs, val) => `<label class="block min-w-0"><span class="text-[11px] font-medium text-on-surface-variant">${label}</span>
    <input class="field w-full mt-1 ${cls}" ${attrs} value="${val == null ? "" : esc(val)}"></label>`;
  row.innerHTML = `
    <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
      <label class="flex items-center gap-2 text-sm font-medium"><input type="radio" name="lr-ads" class="lr-ads"> Reklam &amp; Vine bu varyasyonda</label>
      <button type="button" class="lr-var-del icon-btn !text-error" title="Varyasyonu kaldır" aria-label="Varyasyonu kaldır"><span class="material-symbols-outlined">delete</span></button>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-6 gap-3">
      <div class="col-span-2 md:col-span-1">${f("lr-v-name", "İsim", 'type="text" maxlength="80" placeholder="örn. Tekli"', v.name)}</div>
      ${f("lr-v-price", "Satış fiyatı ($)", 'type="number" min="0" step="0.01"', v.price)}
      ${f("lr-v-cogs", "COGS ($)", 'type="number" min="0" step="0.01"', v.cogs)}
      ${f("lr-v-fba", "FBA ücreti ($)", 'type="number" min="0" step="0.01"', v.fba)}
      ${f("lr-v-units", "Sevkiyat adedi", 'type="number" min="0" step="1"', v.units)}
      ${f("lr-v-share", "Sepet ağırlığı (%)", 'type="number" min="0" max="100" step="1" placeholder="otomatik"', v.share)}
    </div>`;
  row.querySelector(".lr-var-del").addEventListener("click", () => { row.remove(); lrSyncVars(); });
  return row;
}

function lrSyncVars() {
  const rows = $$("#lr-vars .lr-var");
  rows.forEach(r => { r.querySelector(".lr-var-del").style.visibility = rows.length > 1 ? "visible" : "hidden"; });
  if (rows.length && !rows.some(r => r.querySelector(".lr-ads").checked)) rows[0].querySelector(".lr-ads").checked = true;
  $("#lr-var-add").disabled = rows.length >= LR_MAX_VARS;
}

function lrEnsureVars() {
  if (!$$("#lr-vars .lr-var").length) { $("#lr-vars").appendChild(lrVarRow()); lrSyncVars(); }
}

/** Ürün Analizi'nden: ana keyword (exact satır) verisi + pazar iade oranı önceden dolar; hepsi düzenlenebilir. */
function openLaunchReport(data) {
  showView("launch");
  const isAsin = data.analysis_mode === "asin";
  const main = isAsin ? null : (data.keyword_rows || []).find(r => (r.keyword || "").toLowerCase() === (data.keyword || "").toLowerCase());
  const set = (id, v) => { if (v != null && v !== "" && Number.isFinite(Number(v))) $(`#${id}`).value = v; };
  if (!isAsin) $("#lr-keyword").value = data.keyword || "";
  if (main) {
    set("lr-kw-purch", main.purchases); set("lr-kw-clicks", main.clicks);
    set("lr-kw-bid", main.bid != null ? Number(main.bid).toFixed(2) : null);
    set("lr-kw-price", main.avgPrice != null ? Number(main.avgPrice).toFixed(2) : null);
  }
  if (data.market_return_rate != null) set("lr-return", (data.market_return_rate * 100).toFixed(2));
  const src = [];
  if (main) src.push(`"${data.keyword}" keyword verisi (aylık)`);
  if (data.market_return_rate != null) src.push("iade oranı: pazar ortalaması");
  const note = $("#lr-prefill");
  note.textContent = src.length ? `✓ Ürün Analizi'nden dolduruldu — ${src.join(" · ")}. Tüm alanlar düzenlenebilir.`
    : "Ürün Analizi'nde ana keyword satırı bulunamadı — keyword verisini elle girin.";
  note.className = "status-line mt-4";
}

function lrPayload() {
  const vars = $$("#lr-vars .lr-var").map(r => {
    const g = (c) => r.querySelector(c).value.trim();
    const num = (c) => g(c) === "" ? null : Number(g(c));
    return { name: g(".lr-v-name"), price: num(".lr-v-price"), cogs: num(".lr-v-cogs"), fba: num(".lr-v-fba"),
             units: num(".lr-v-units"), share: num(".lr-v-share"), _ads: r.querySelector(".lr-ads").checked };
  });
  const budget = document.querySelector('input[name="lr-budget"]:checked').value;
  const ccmode = document.querySelector('input[name="lr-ccmode"]:checked').value;
  const p = {
    product_name: $("#lr-product").value.trim(), main_keyword: $("#lr-keyword").value.trim(),
    variations: vars.map(({ _ads, ...v }) => v), ads_index: Math.max(0, vars.findIndex(v => v._ads)),
    referral_pct: lrVal("lr-referral"), return_pct: lrVal("lr-return"), vine_fee: lrVal("lr-vine-fee"),
    budget_mode: budget, target_acos_pct: budget === "acos" ? lrVal("lr-acos") : null, daily_budget: budget === "daily" ? lrVal("lr-daily") : null,
    us_vine_units: lrVal("lr-us-vine"),
    second_vine: $("#lr-sv-on").checked ? { name: $("#lr-sv-name").value.trim() || "Kanada (CA)", total: lrVal("lr-sv-total"),
      enrolled: lrVal("lr-sv-enrolled"), from_main_batch: $("#lr-sv-src").value === "main" } : null,
    cc_pct: lrVal("lr-cc"), cc_mode: ccmode, cc_low: lrVal("lr-cc-low"), cc_mid: lrVal("lr-cc-mid"),
    cc_expected: ccmode === "expected" ? lrVal("lr-cc-exp") : null, campaign_days: lrVal("lr-days"),
    keyword: { monthly_purchases: lrVal("lr-kw-purch"), monthly_clicks: lrVal("lr-kw-clicks"), bid: lrVal("lr-kw-bid"), market_avg_price: lrVal("lr-kw-price") },
  };
  // Boş bırakılan varsayılanlı alanları sunucu varsayılanına bırak
  for (const k of ["referral_pct", "return_pct", "vine_fee", "cc_pct", "cc_low", "cc_mid", "campaign_days"]) if (p[k] == null) delete p[k];
  return p;
}

function lrClientCheck(p) {
  if (!p.product_name) return "Ürün adı gerekli.";
  if (!p.main_keyword) return "Ana keyword gerekli.";
  for (const [i, v] of p.variations.entries()) {
    if (!v.name || v.price == null || v.cogs == null || v.fba == null || v.units == null) return `${i + 1}. varyasyonda isim, fiyat, COGS, FBA ve adet gerekli.`;
  }
  const shares = p.variations.map(v => v.share);
  if (shares.some(x => x != null) && shares.some(x => x == null)) return "Sepet ağırlığını ya tüm varyasyonlara girin ya da hepsini boş bırakın.";
  if (p.budget_mode === "daily" && !p.daily_budget) return "Günlük reklam bütçesini girin.";
  if (p.us_vine_units == null) return "ABD Vine adedini girin.";
  if (p.cc_mode === "expected" && p.cc_expected == null) return "Beklenen aylık influencer satışını girin.";
  return "";
}

async function submitLaunchReport(e) {
  e.preventDefault();
  const status = $("#lr-status"), btn = $("#lr-submit");
  const p = lrPayload();
  const problem = lrClientCheck(p);
  if (problem) { status.textContent = problem; status.className = "text-xs text-error mt-0.5"; return; }
  // Pencere tıklama anında açılır (açılır pencere engelleyicisi), rapor gelince Blob URL'ye yönlenir.
  const w = window.open("", "_blank");
  if (w) { try { w.document.title = "Lansman raporu hazırlanıyor…"; w.document.body.textContent = "Lansman raporu hazırlanıyor…"; } catch (_) {} }
  btn.disabled = true; status.textContent = "rapor hazırlanıyor…"; status.className = "text-xs text-secondary mt-0.5";
  try {
    const r = await apiFetch(`${API_BASE}/api/launch-report`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(p) });
    if (!r.ok) { const b = await r.json().catch(() => ({})); throw new Error(apiErrorText(b, r.status)); }
    const url = URL.createObjectURL(new Blob([await r.text()], { type: "text/html;charset=utf-8" }));
    setTimeout(() => URL.revokeObjectURL(url), 120000);
    if (w && !w.closed) { w.opener = null; w.location.href = url; status.textContent = "Rapor yeni sekmede açıldı — “PDF olarak kaydet” ile indirin."; }
    else {
      status.innerHTML = `Açılır pencere engellendi. <a class="underline font-semibold" href="${url}" target="_blank" rel="noopener">Raporu aç</a>`;
    }
    status.className = "text-xs text-primary mt-0.5";
  } catch (err) {
    if (w && !w.closed) w.close();
    status.textContent = `Hata: ${err.message}`; status.className = "text-xs text-error mt-0.5";
  } finally { btn.disabled = false; }
}

(function initLaunchReport() {
  const form = $("#lr-form");
  if (!form) return;
  $("#lr-var-add").addEventListener("click", () => { if ($$("#lr-vars .lr-var").length < LR_MAX_VARS) { $("#lr-vars").appendChild(lrVarRow()); lrSyncVars(); } });
  $$('input[name="lr-budget"]').forEach(r => r.addEventListener("change", () => {
    const daily = document.querySelector('input[name="lr-budget"]:checked').value === "daily";
    $("#lr-daily").disabled = !daily; $("#lr-acos").disabled = daily;
  }));
  $$('input[name="lr-ccmode"]').forEach(r => r.addEventListener("change", () => {
    const exp = document.querySelector('input[name="lr-ccmode"]:checked').value === "expected";
    $("#lr-cc-exp").disabled = !exp; $("#lr-cc-low").disabled = exp; $("#lr-cc-mid").disabled = exp;
  }));
  $("#lr-sv-on").addEventListener("change", (e) => { $("#lr-sv").style.display = e.target.checked ? "grid" : "none"; });
  form.addEventListener("submit", submitLaunchReport);
  lrEnsureVars();
})();

// ===========================================================================
// EKİP AKTİVİTESİ (yalnızca owner; sunucu /api/team/activity -> require_owner, diğerleri 403)
// Salt okunur. Kayda tıklamak runAnalysis ile CANLI yeni analiz başlatır (önbellek/kayıtlı sonuç yok).
// ===========================================================================
const tmState = { tab: "queries", data: null, timer: null };
const TM_VERDICT_CLASS = { "Uygun": "ok", "Sınırda": "warn", "Elenmiş": "bad" };

const tmDate = (ts) => ts ? new Date(ts * 1000).toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
function tmLocalDayStart(v) { if (!v) return null; const [y, m, d] = v.split("-").map(Number); return Math.floor(new Date(y, m - 1, d).getTime() / 1000); }
/** Aktif tarih aralığı etiketi (yerel tarih); ikisi de boşsa "Tüm zamanlar". */
function tmRangeLabel() {
  const f = $("#tm-from").value, t = $("#tm-to").value;
  const fmt = (v) => { const [y, m, d] = v.split("-"); return `${d}.${m}.${y}`; };
  if (!f && !t) return "";
  if (f && t) return `${fmt(f)} – ${fmt(t)}`;
  return f ? `${fmt(f)} ve sonrası` : `${fmt(t)} ve öncesi`;
}

/** Kayıttaki anahtar -> runAnalysis'e verilecek değer ("ASIN:B0.." -> "B0.."; "B0.. — başlık" runAnalysis'te ayıklanır) */
function tmAnalysisTarget(keyword) { return String(keyword || "").replace(/^ASIN:/i, "").trim(); }
function tmKeywordHtml(keyword) {
  const k = String(keyword || "");
  const asin = k.match(/^ASIN:(.+)$/i);
  return asin ? `<span class="mono">${esc(asin[1])}</span> <span class="chip na !text-[10px]">ASIN</span>` : esc(k);
}

async function loadTeam() {
  if (currentUser.role !== "owner") return;
  const params = new URLSearchParams();
  const tid = $("#tm-team").value; if (tid !== "") params.set("team_id", tid);
  const uid = $("#tm-user").value; if (uid !== "") params.set("user_id", uid);
  const since = tmLocalDayStart($("#tm-from").value); if (since != null) params.set("since", since);
  const untilDay = tmLocalDayStart($("#tm-to").value); if (untilDay != null) params.set("until", untilDay + 86400);
  const dec = $("#tm-decision").value; if (dec) params.set("decision", dec);
  const q = $("#tm-q").value.trim(); if (q) params.set("q", q);
  const st = $("#tm-status"); st.textContent = "yükleniyor…"; st.className = "status-line mt-4 loading";
  try {
    const r = await apiFetch(`${API_BASE}/api/team/activity?${params}`);
    const b = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(apiErrorText(b, r.status));
    tmState.data = b;
    renderTeam();
    st.textContent = ""; st.className = "status-line mt-4";
  } catch (err) { st.textContent = `Hata: ${err.message}`; st.className = "status-line mt-4 error"; }
}

function renderTeam() {
  const b = tmState.data; if (!b) return;
  // Kişi seçici (seçimi koru)
  const tsel = $("#tm-team"), tcur = tsel.value;
  tsel.innerHTML = `<option value="">Tüm ekipler</option>` + (b.teams || []).map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
  tsel.value = (b.teams || []).some(t => String(t.id) === tcur) ? tcur : "";
  const sel = $("#tm-user"), cur = sel.value;
  const people = b.summary.map(s => ({ id: s.user_id, label: s.user }));   // ad soyad (girilmemişse e-posta)
  sel.innerHTML = `<option value="">Tümü</option>` + people.map(p => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join("");
  sel.value = people.some(p => String(p.id) === cur) ? cur : "";

  // Aktif aralık (özet + listeler aynı aralıkla süzülür)
  const rl = tmRangeLabel();
  $("#tm-range-label").textContent = rl ? `Aralık: ${rl}` : "Tüm zamanlar";
  $("#tm-range-chip").className = `chip ${rl ? "warn" : "ok"}`;
  $("#tm-sum-range").textContent = rl || "tüm zamanlar";
  $("#tm-alltime").style.display = rl ? "" : "none";

  // Özet
  $("#tm-summary tbody").innerHTML = b.summary.length ? b.summary.map(s => `
    <tr class="tm-sum-row cursor-pointer" data-uid="${esc(s.user_id)}" title="Bu kişinin kayıtlarını göster">
      <td class="l"><span class="font-medium break-words">${esc(s.user)}</span>${s.email && s.email !== s.user ? `<div class="text-[11px] text-secondary break-all">${esc(s.email)}</div>` : ""}</td>
      <td class="l"><div class="flex flex-wrap gap-1 min-w-[100px] max-w-[260px] whitespace-normal">${(s.teams || []).map(n => `<span class="chip na !text-[10px] max-w-[140px] truncate" title="${esc(n)}">${esc(n)}</span>`).join("") || "—"}</div></td>
      <td>${s.role ? `<span class="chip ${s.role === "owner" ? "ok" : s.role === "admin" ? "warn" : "na"}">${esc(ROLE_LABEL[s.role] || s.role)}</span>` : "—"}</td>
      <td class="tabular">${fmtNum(s.queries)}</td><td class="tabular">${fmtNum(s.decisions)}</td></tr>`).join("")
    : `<tr><td colspan="5" class="muted !text-center !py-6">Bu filtrede ekip üyesi yok.</td></tr>`;
  $$("#tm-summary .tm-sum-row").forEach(tr => tr.addEventListener("click", () => { $("#tm-user").value = tr.dataset.uid; loadTeam(); }));

  $(".tm-count-q").textContent = `(${fmtNum(b.totals.queries)})`;
  $(".tm-count-d").textContent = `(${fmtNum(b.totals.decisions)})`;
  const trunc = b.totals.queries > b.truncated_at || b.totals.decisions > b.truncated_at;
  $("#tm-trunc").textContent = trunc ? `Her listede en yeni ${fmtNum(b.truncated_at)} kayıt gösteriliyor — daraltmak için filtre kullanın.` : "";

  const vchip = (v) => `<span class="chip ${TM_VERDICT_CLASS[v] || "na"}">${esc(v ?? "—")}</span>`;
  const rowAttrs = (r) => `class="tm-row cursor-pointer" tabindex="0" role="button" data-kw="${esc(r.keyword)}" data-mp="${esc(r.marketplace)}" title="Canlı yeniden analiz et"`;
  $("#tm-queries tbody").innerHTML = b.queries.length ? b.queries.map(r => `
    <tr ${rowAttrs(r)}><td class="l"><span class="break-all">${esc(r.user)}</span></td><td class="l font-medium"><span class="block min-w-[120px] max-w-[280px] whitespace-normal [overflow-wrap:anywhere]">${tmKeywordHtml(r.keyword)}</span></td>
      <td>${esc(r.marketplace)}</td><td class="whitespace-nowrap">${esc(tmDate(r.at))}</td><td>${vchip(r.verdict)}</td></tr>`).join("")
    : `<tr><td colspan="5" class="muted !text-center !py-8">Filtreye uyan arama yok.</td></tr>`;
  $("#tm-decisions tbody").innerHTML = b.decisions.length ? b.decisions.map(r => `
    <tr ${rowAttrs(r)}><td class="l"><span class="break-all">${esc(r.user)}</span></td><td class="l font-medium"><span class="block min-w-[120px] max-w-[280px] whitespace-normal [overflow-wrap:anywhere]">${tmKeywordHtml(r.keyword)}</span></td>
      <td>${esc(r.marketplace)}</td><td class="whitespace-nowrap">${esc(tmDate(r.at))}</td><td>${vchip(r.verdict)}</td>
      <td>${vchip(r.decision)}</td><td class="l"><span class="block min-w-[160px] max-w-[320px] whitespace-normal [overflow-wrap:anywhere] text-xs">${esc(r.note || "")}</span></td></tr>`).join("")
    : `<tr><td colspan="7" class="muted !text-center !py-8">Filtreye uyan karar yok.</td></tr>`;
  $$("#view-team .tm-row").forEach(tr => {
    const go = () => tmReanalyze(tr.dataset.kw, tr.dataset.mp);
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  });
}

// ===========================================================================
// EKİP YÖNETİMİ (yalnızca owner): Ekipler · Üyeler · Davetler · Aktivite
// Tüm kurallar SUNUCUDA (admin/üye 403); buradaki kilitler yalnızca kolaylık.
// ===========================================================================
const tgState = { tab: "teams", teams: [], users: [], invites: [], outsiders: 0, me: null, memFilter: "all", renaming: null };
const tgIsNew = (u) => u.created_at && (Date.now() / 1000 - u.created_at) < 7 * 86400;
const tgDate = (ts) => ts ? new Date(ts * 1000).toLocaleDateString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric" }) : "—";
const ROLE_CHIP = (r) => `<span class="chip ${r === "owner" ? "ok" : r === "admin" ? "warn" : "na"}">${esc(ROLE_LABEL[r] || r)}</span>`;

async function tgApi(url, opts = {}) {
  const r = await apiFetch(`${API_BASE}${url}`, { headers: { "Content-Type": "application/json" }, ...opts });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(apiErrorText(b, r.status));
  return b;
}
function tgStatus(text, cls = "") { const el = $("#tm-status"); el.textContent = text; el.className = `status-line mt-4 ${cls}`.trim(); }

function loadTeamAdmin() {
  const owner = currentUser.role === "owner";
  $("#tm-guest").style.display = owner ? "none" : "block";
  $("#tg-body").style.display = owner ? "block" : "none";
  if (!owner) { tgStatus(""); return; }
  tgSelectTab(tgState.tab);
}

function tgSelectTab(tab) {
  tgState.tab = tab;
  $$("#tg-tabs .tab-btn").forEach(b => { const on = b.dataset.tab === tab; b.classList.toggle("active", on); b.setAttribute("aria-selected", String(on)); });
  $$("#view-team .tg-pane").forEach(p => { p.style.display = p.id === `tg-pane-${tab}` ? "" : "none"; });
  if (tab === "activity") { tgStatus(""); loadTeam(); }
  else if (tab === "skills") skLoad();
  else tgLoad();
}

async function tgLoad() {
  tgStatus("yükleniyor…", "loading");
  try {
    const [t, u, inv, st] = await Promise.all([tgApi("/api/teams"), tgApi("/api/users"), tgApi("/api/invites"), tgApi("/api/auth/status")]);
    tgState.teams = t.teams || []; tgState.outsiders = t.outsiders || 0;
    tgState.users = u.users || []; tgState.me = u.me; tgState.invites = inv.invites || [];
    setTeamBadge(st.new_outsiders_7d || 0);
    tgRenderTeams(); tgRenderMembers(); tgRenderInvites();
    tgStatus("");
  } catch (err) { tgStatus(`Hata: ${err.message}`, "error"); }
}

// --- Ekipler ---
function tgRenderTeams() {
  $(".tg-count-teams").textContent = `(${tgState.teams.length})`;
  $("#tg-outsiders").textContent = `Şu an ekip dışı: ${fmtNum(tgState.outsiders)} kişi.`;
  const box = $("#tg-team-list");
  box.innerHTML = tgState.teams.length ? "" : `<div class="card card-pad text-sm text-secondary">Henüz ekip yok — yukarıdan oluşturun.</div>`;
  tgState.teams.forEach(t => {
    const row = document.createElement("div");
    row.className = "card p-4 flex flex-wrap items-center gap-3";
    const blockers = t.delete_blockers || [];
    const editing = tgState.renaming === t.id;
    row.innerHTML = `
      <div class="min-w-0 flex-1 basis-[220px]">
        ${editing
          ? `<form class="tg-rename flex flex-wrap gap-2"><input type="text" class="field !h-9 flex-1 min-w-[160px]" maxlength="60" aria-label="Yeni ekip adı">
               <button type="submit" class="btn btn-primary btn-sm">Kaydet</button><button type="button" class="btn btn-outline btn-sm tg-rename-cancel">Vazgeç</button></form>`
          : `<div class="font-display font-semibold text-[16px] break-words">${esc(t.name)}</div>`}
        <div class="text-xs text-secondary mt-1">${fmtNum(t.member_count)} üye · oluşturma ${esc(tgDate(t.created_at))}${(t.exclusive_lessons || []).length ? ` · yalnızca bu ekibe atanmış ${fmtNum(t.exclusive_lessons.length)} ders` : ""}</div>
        <div class="tg-row-msg text-xs text-error mt-1"></div>
      </div>
      <div class="flex flex-wrap gap-2">
        <button type="button" class="btn btn-outline btn-sm tg-show"><span class="material-symbols-outlined">group</span>Üyeler</button>
        ${editing ? "" : `<button type="button" class="btn btn-outline btn-sm tg-ren"><span class="material-symbols-outlined">edit</span>Yeniden adlandır</button>`}
        <button type="button" class="btn btn-danger tg-del" ${blockers.length ? `disabled title="${esc("Şu owner/admin'lerin tek ekibi: " + blockers.map(b => b.name || b.email).join(", ") + " — önce başka ekibe ekleyin ya da rollerini düşürün")}"` : ""}><span class="material-symbols-outlined">delete</span>Sil</button>
      </div>`;
    const msg = row.querySelector(".tg-row-msg");
    if (blockers.length) msg.textContent = `Silinemez: ${blockers.map(b => b.name || b.email).join(", ")} için tek ekip (owner/admin en az bir ekipte olmalı).`;
    row.querySelector(".tg-show").addEventListener("click", () => { tgState.memFilter = `t:${t.id}`; tgSelectTab("members"); });
    if (editing) {
      const f = row.querySelector(".tg-rename"), inp = f.querySelector("input");
      inp.value = t.name; setTimeout(() => inp.focus(), 0);
      row.querySelector(".tg-rename-cancel").addEventListener("click", () => { tgState.renaming = null; tgRenderTeams(); });
      f.addEventListener("submit", async (e) => {
        e.preventDefault();
        try { await tgApi(`/api/teams/${encodeURIComponent(t.id)}`, { method: "PUT", body: JSON.stringify({ name: inp.value }) }); tgState.renaming = null; await tgLoad(); }
        catch (err) { msg.textContent = err.message; }
      });
    } else {
      row.querySelector(".tg-ren").addEventListener("click", () => { tgState.renaming = t.id; tgRenderTeams(); });
    }
    row.querySelector(".tg-del").addEventListener("click", async () => {
      let text = `"${t.name}" ekibi silinsin mi?\n\nKullanıcılar ve verileri SİLİNMEZ; yalnızca ${t.member_count} üyelik, ekibe yapılan ders atamaları ve ekibin davet bağlantıları kaldırılır.`;
      const ex = t.exclusive_lessons || [];
      if (ex.length) text += `\n\nUYARI: Şu ders(ler) yalnızca bu ekibe atanmış — ekip silinince kimseye görünmeyecek:\n${ex.map(l => "• " + l.title).join("\n")}`;
      if (!confirm(text)) return;
      try { await tgApi(`/api/teams/${encodeURIComponent(t.id)}`, { method: "DELETE" }); if (tgState.memFilter === `t:${t.id}`) tgState.memFilter = "all"; await tgLoad(); }
      catch (err) { msg.textContent = err.message; }
    });
    box.appendChild(row);
  });
}

// --- Üyeler ---
function tgRenderMembers() {
  const sel = $("#tg-mem-filter");
  sel.innerHTML = `<option value="all">Tümü</option><option value="none">Ekip dışı</option>` +
    tgState.teams.map(t => `<option value="t:${esc(t.id)}">${esc(t.name)}</option>`).join("");
  if (![...sel.options].some(o => o.value === tgState.memFilter)) tgState.memFilter = "all";
  sel.value = tgState.memFilter;
  const f = tgState.memFilter;
  let users = tgState.users.filter(u => f === "all" ? true : f === "none" ? !u.team_ids.length : u.team_ids.includes(Number(f.slice(2))));
  if (f === "none") users = [...users].sort((a, b) => (b.created_at || 0) - (a.created_at || 0) || b.id - a.id);   // en yeniden eskiye
  $("#tg-mem-count").textContent = `${fmtNum(users.length)} kişi`;
  const tname = new Map(tgState.teams.map(t => [t.id, t.name]));
  const owners = tgState.users.filter(u => u.role === "owner").length;
  const box = $("#tg-mem-list");
  box.innerHTML = users.length ? "" : `<div class="card card-pad text-sm text-secondary">Bu filtrede kimse yok.</div>`;
  users.forEach(u => {
    const isMe = u.id === tgState.me;
    const staff = u.role === "owner" || u.role === "admin";
    const perm = !!u.permanent, lastOwner = u.role === "owner" && owners <= 1;
    const roleLock = perm ? "Kalıcı owner — düşürülemez" : lastOwner ? "Son owner — düşürülemez" : "";
    const noDelete = isMe ? "Kendi hesabını silemezsin" : perm ? "Kalıcı owner silinemez" : lastOwner ? "Son owner silinemez" : "";
    const opts = ["owner", "admin", "member"].map(r => `<option value="${r}" ${u.role === r ? "selected" : ""} ${!u.team_ids.length && r !== "member" ? "disabled" : ""}>${ROLE_LABEL[r]}</option>`).join("");
    const row = document.createElement("div");
    row.className = "card p-4 grid grid-cols-1 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1.5fr)_170px_auto] gap-3 md:items-start tg-mem";
    row.dataset.email = u.email;
    row.innerHTML = `
      <div class="min-w-0">
        <div class="flex items-center gap-2 min-w-0"><span class="pf-avatar !w-8 !h-8 !text-[11px]">${esc(initials(u.name || u.email))}</span>
          <div class="min-w-0"><div class="font-medium break-words">${esc(u.name || "(ad girilmemiş)")}</div><div class="text-[11px] text-secondary break-all">${esc(u.email)}${u.username ? ` · @${esc(u.username)}` : ""}</div></div></div>
        <div class="flex flex-wrap gap-1 mt-1">${isMe ? '<span class="chip na !text-[10px]">sen</span>' : ""}${perm ? '<span class="chip ok !text-[10px]"><span class="material-symbols-outlined !text-[12px]" aria-hidden="true">lock</span>kalıcı owner</span>' : ""}${tgIsNew(u) ? '<span class="chip warn !text-[10px] tg-new">yeni</span>' : ""}</div>
        <div class="text-[11px] text-secondary mt-1">Kayıt: ${esc(tgDate(u.created_at))}</div>
      </div>
      <div class="min-w-0">
        <div class="flex flex-wrap gap-1 tg-teams">${u.team_ids.length ? u.team_ids.map(id => `<span class="chip na !text-[11px] max-w-[180px] truncate" title="${esc(tname.get(id) || "")}">${esc(tname.get(id) || "?")}</span>`).join("") : '<span class="chip bad !text-[11px]">Ekip dışı</span>'}</div>
        <button type="button" class="btn btn-outline btn-sm mt-2 tg-team-edit" ${tgState.teams.length ? "" : "disabled title=\"Önce bir ekip oluşturun\""}><span class="material-symbols-outlined">group_add</span>Ekipleri düzenle</button>
        <div class="tg-team-box hidden mt-2 p-2 rounded border border-hairline">
          <div class="tg-teampick">${tgState.teams.map(t => `<label><input type="checkbox" value="${esc(t.id)}" ${u.team_ids.includes(t.id) ? "checked" : ""}><span title="${esc(t.name)}">${esc(t.name)}</span></label>`).join("")}</div>
          <div class="flex flex-wrap gap-2 mt-2"><button type="button" class="btn btn-primary btn-sm tg-team-save">Kaydet</button><button type="button" class="btn btn-outline btn-sm tg-team-cancel">Vazgeç</button></div>
          ${staff ? `<div class="text-[11px] text-secondary mt-1">${esc(ROLE_LABEL[u.role])} en az bir ekipte kalmalı.</div>` : ""}
        </div>
      </div>
      <div class="min-w-0">
        <select class="field !h-9 text-xs w-full tg-role" aria-label="Rol" ${roleLock ? `disabled title="${esc(roleLock)}"` : ""}>${opts}</select>
        <div class="tg-role-msg text-[11px] mt-1 text-secondary">${esc(roleLock || (!u.team_ids.length ? "Owner/admin için önce bir ekibe ekleyin" : ""))}</div>
      </div>
      <div><button type="button" class="btn btn-danger tg-del-btn" ${noDelete ? `disabled title="${esc(noDelete)}"` : ""}>Üyeliği sil</button></div>
      <div class="tg-del-box hidden md:col-span-4 p-3 rounded border border-error/40">
        <div class="text-[12px] text-error leading-snug">Kalıcı silme — geri alınamaz. Hesap, oturumlar, eşikler, arama geçmişi, kararlar, kendi kontrol listeleri, eğitim tamamlamaları ve ekip üyelikleri silinir; başkalarının kayıtlarındaki adı "—" görünür. Onay için e-postayı yaz:</div>
        <div class="flex flex-wrap gap-2 mt-2">
          <input type="text" class="field !h-9 text-xs flex-1 min-w-[180px] tg-del-input" autocomplete="off" spellcheck="false" aria-label="Onay için e-posta" placeholder="${esc(u.email)}">
          <button type="button" class="btn btn-danger tg-del-go" disabled>Kalıcı olarak sil</button>
          <button type="button" class="btn btn-outline btn-sm tg-del-cancel">Vazgeç</button>
        </div>
      </div>
      <div class="tg-act-msg text-[12px] text-error md:col-span-4 empty:hidden"></div>`;
    const actMsg = row.querySelector(".tg-act-msg");
    // ekipler
    const teamBox = row.querySelector(".tg-team-box");
    row.querySelector(".tg-team-edit").addEventListener("click", () => teamBox.classList.toggle("hidden"));
    row.querySelector(".tg-team-cancel").addEventListener("click", () => { teamBox.classList.add("hidden"); tgRenderMembers(); });
    row.querySelector(".tg-team-save").addEventListener("click", async (e) => {
      const ids = $$("input:checked", teamBox).map(i => Number(i.value));
      if (staff && !ids.length) { actMsg.textContent = `${ROLE_LABEL[u.role]} son ekibinden çıkarılamaz — önce rolünü üyeye düşür.`; return; }
      e.currentTarget.disabled = true; actMsg.textContent = "";
      try { await tgApi(`/api/users/${encodeURIComponent(u.id)}/teams`, { method: "POST", body: JSON.stringify({ team_ids: ids }) }); await tgLoad(); }
      catch (err) { e.currentTarget.disabled = false; actMsg.textContent = err.message; }
    });
    // rol
    const sel2 = row.querySelector(".tg-role"), rmsg = row.querySelector(".tg-role-msg");
    sel2.addEventListener("change", async () => {
      const next = sel2.value;
      if (isMe && u.role === "owner" && next !== "owner" &&
          !confirm("Kendi owner yetkini kaldırıyorsun; Ekip Yönetimi'ne erişimin kalmayacak. Devam edilsin mi?")) { sel2.value = u.role; return; }
      if (next === "owner" && u.role !== "owner" && !confirm(`${u.name || u.email} owner yapılsın mı? Owner'lar ekipleri, üyelikleri ve rolleri yönetir.`)) { sel2.value = u.role; return; }
      sel2.disabled = true; rmsg.textContent = "kaydediliyor…"; rmsg.className = "tg-role-msg text-[11px] mt-1 text-secondary";
      try {
        const b = await tgApi(`/api/users/${encodeURIComponent(u.id)}/role`, { method: "POST", body: JSON.stringify({ role: next }) });
        if (b.self_changed) { await checkAuthStatus(); if (currentUser.role !== "owner") { showView("home"); return; } }
        await tgLoad();
      } catch (err) { sel2.disabled = false; sel2.value = u.role; rmsg.textContent = err.message; rmsg.className = "tg-role-msg text-[11px] mt-1 text-error"; }
    });
    // kalıcı silme
    const delBox = row.querySelector(".tg-del-box"), delInput = row.querySelector(".tg-del-input"), delGo = row.querySelector(".tg-del-go");
    const norm = v => String(v || "").trim().toLowerCase();
    row.querySelector(".tg-del-btn").addEventListener("click", () => { delBox.classList.toggle("hidden"); delInput.value = ""; delGo.disabled = true; delInput.focus(); });
    row.querySelector(".tg-del-cancel").addEventListener("click", () => { delBox.classList.add("hidden"); delInput.value = ""; });
    delInput.addEventListener("input", () => { delGo.disabled = norm(delInput.value) !== norm(u.email); });
    delGo.addEventListener("click", async () => {
      if (norm(delInput.value) !== norm(u.email)) return;
      delGo.disabled = true; actMsg.textContent = "";
      try { await tgApi(`/api/users/${encodeURIComponent(u.id)}/delete`, { method: "POST", body: JSON.stringify({ confirm_email: delInput.value }) }); await tgLoad(); }
      catch (err) { delGo.disabled = false; actMsg.textContent = err.message; }
    });
    box.appendChild(row);
  });
}

// --- Davetler ---
function tgRenderInvites() {
  const sel = $("#tg-inv-team"), cur = sel.value;
  sel.innerHTML = tgState.teams.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("") || `<option value="">Önce bir ekip oluşturun</option>`;
  if (tgState.teams.some(t => String(t.id) === cur)) sel.value = cur;
  $("#tg-inv-form button[type=submit]").disabled = !tgState.teams.length;
  const box = $("#tg-inv-list");
  box.innerHTML = tgState.invites.length ? "" : `<div class="px-5 py-6 text-sm text-secondary">Aktif davet yok.</div>`;
  tgState.invites.forEach(i => {
    const row = document.createElement("div");
    row.className = "px-5 py-3 flex flex-wrap items-center gap-3";
    row.innerHTML = `
      <div class="min-w-0 flex-1 basis-[220px]">
        <div class="flex flex-wrap items-center gap-1.5"><span class="font-medium break-all">${esc(i.team_name)}</span>
          <span class="chip ${i.max_uses == null ? "warn" : "na"} !text-[10px]">${i.max_uses == null ? "Çok kullanımlık" : "Tek kullanımlık"}</span>
          <span class="text-xs text-secondary">${fmtNum(i.uses)} kullanım</span></div>
        <div class="text-[11px] text-secondary mt-1 break-all">Son geçerlilik ${esc(tmDate(i.expires_at))} · oluşturan ${esc(i.created_by_name || i.created_by_email || "—")} · ${esc(tmDate(i.created_at))}</div>
        <div class="tg-inv-row-msg text-[11px] text-error"></div>
      </div>
      <button type="button" class="btn btn-danger tg-inv-revoke"><span class="material-symbols-outlined">link_off</span>İptal et</button>`;
    row.querySelector(".tg-inv-revoke").addEventListener("click", async (e) => {
      if (!confirm(`"${i.team_name}" davet bağlantısı iptal edilsin mi? Bu bağlantıyla kaydolanlar ekip dışı başlar.`)) return;
      e.currentTarget.disabled = true;
      try { await tgApi(`/api/invites/${encodeURIComponent(i.id)}/revoke`, { method: "POST" }); await tgLoad(); }
      catch (err) { e.currentTarget.disabled = false; row.querySelector(".tg-inv-row-msg").textContent = err.message; }
    });
    box.appendChild(row);
  });
}

(function initTeamAdmin() {
  if (!$("#tg-tabs")) return;
  $$("#tg-tabs .tab-btn").forEach(b => b.addEventListener("click", () => tgSelectTab(b.dataset.tab)));
  $("#tg-team-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#tg-team-msg"), name = $("#tg-team-name").value.trim();
    if (!name) { msg.textContent = "Ekip adı gerekli."; msg.className = "text-xs w-full text-error"; return; }
    try {
      await tgApi("/api/teams", { method: "POST", body: JSON.stringify({ name }) });
      $("#tg-team-name").value = ""; msg.textContent = `"${name}" ekibi oluşturuldu.`; msg.className = "text-xs w-full text-primary";
      await tgLoad();
    } catch (err) { msg.textContent = err.message; msg.className = "text-xs w-full text-error"; }
  });
  $("#tg-mem-filter").addEventListener("change", (e) => { tgState.memFilter = e.target.value; tgRenderMembers(); });
  $("#tg-inv-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#tg-inv-msg"); msg.textContent = ""; msg.className = "text-xs sm:col-span-2 lg:col-span-4";
    const days = Math.round(Number($("#tg-inv-days").value));
    if (!(days >= 1 && days <= 90)) { msg.textContent = "Geçerlilik 1–90 gün olmalı."; msg.classList.add("text-error"); return; }
    const teamId = Number($("#tg-inv-team").value);
    if (!teamId) return;
    try {
      const b = await tgApi("/api/invites", { method: "POST", body: JSON.stringify({ team_id: teamId, multi_use: $("#tg-inv-uses").value === "multi", days }) });
      const team = tgState.teams.find(t => t.id === teamId);
      $("#tg-inv-link").value = `${location.origin}${location.pathname}?invite=${encodeURIComponent(b.code)}`;
      $("#tg-inv-result-label").textContent = `"${team ? team.name : ""}" için ${$("#tg-inv-uses").value === "multi" ? "çok" : "tek"} kullanımlık davet · ${tmDate(b.expires_at)} tarihine kadar geçerli`;
      $("#tg-inv-result").style.display = "";
      await tgLoad();
    } catch (err) { msg.textContent = err.message; msg.classList.add("text-error"); }
  });
  $("#tg-inv-copy").addEventListener("click", async () => {
    const inp = $("#tg-inv-link");
    try { await navigator.clipboard.writeText(inp.value); } catch { inp.select(); document.execCommand?.("copy"); }
    $("#tg-inv-copy").innerHTML = '<span class="material-symbols-outlined">check</span>Kopyalandı';
    setTimeout(() => { $("#tg-inv-copy").innerHTML = '<span class="material-symbols-outlined">content_copy</span>Kopyala'; }, 1500);
  });
})();

/** Geçmiş'teki gibi: mevcut runAnalysis ile CANLI analiz (önbellek okunmaz; /api/analyze her zaman MCP'ye gider). */
function tmReanalyze(keyword, marketplace) {
  const target = tmAnalysisTarget(keyword);
  if (!target) return;
  showView("search");
  runAnalysis(target, marketplace || "US");
}

(function initTeam() {
  if (!$("#tm-filters")) return;
  ["#tm-team", "#tm-user", "#tm-from", "#tm-to", "#tm-decision"].forEach(sel => $(sel).addEventListener("change", loadTeam));
  $("#tm-q").addEventListener("input", () => { clearTimeout(tmState.timer); tmState.timer = setTimeout(loadTeam, 300); });
  $("#tm-filters").addEventListener("submit", (e) => { e.preventDefault(); loadTeam(); });
  $("#tm-alltime").addEventListener("click", () => { $("#tm-from").value = ""; $("#tm-to").value = ""; loadTeam(); });
  $("#tm-reset").addEventListener("click", () => { ["#tm-team", "#tm-user", "#tm-from", "#tm-to", "#tm-decision", "#tm-q"].forEach(sel => { $(sel).value = ""; }); loadTeam(); });
  $$("#tm-tabs .tab-btn").forEach(btn => btn.addEventListener("click", () => {
    tmState.tab = btn.dataset.tab;
    $$("#tm-tabs .tab-btn").forEach(b => b.classList.toggle("active", b === btn));
    $("#tm-pane-queries").style.display = tmState.tab === "queries" ? "" : "none";
    $("#tm-pane-decisions").style.display = tmState.tab === "decisions" ? "" : "none";
  }));
})();

// ===========================================================================
// ANALİZ ÖNCESİ MALİYET (opsiyonel) — Ürün Analizi arama kutusunun altında.
// Değerler kullanıcı değiştirene ya da "Temizle"ye basana kadar kalır (tarayıcıda; sunucuya gitmez).
// ===========================================================================
const PRE_COST_KEY = "pl_pre_cost";
const PRE_COST_FIELDS = { cogs: "#pc-cogs", fba: "#pc-fba", gen: "#pc-gen" };

/** Dolu ve geçerli (≥0) alanlar sayı, boş/geçersizler null. */
function readPreCost() {
  const out = {};
  for (const [k, sel] of Object.entries(PRE_COST_FIELDS)) {
    const el = $(sel); const v = el ? el.value.trim() : "";
    const n = v === "" ? NaN : Number(v);
    out[k] = Number.isFinite(n) && n >= 0 ? n : null;
  }
  return out;
}

function syncPreCostState() {
  const pc = readPreCost();
  const filled = Object.values(pc).filter(v => v != null).length;
  const chip = $("#pc-state");
  if (chip) { chip.textContent = filled ? `${filled}/3 dolu` : "boş"; chip.className = `chip ${filled ? "ok" : "na"} !text-[10px]`; }
  try {
    const raw = {}; for (const [k, sel] of Object.entries(PRE_COST_FIELDS)) raw[k] = $(sel).value;
    if (Object.values(raw).some(v => v !== "")) localStorage.setItem(PRE_COST_KEY, JSON.stringify(raw)); else localStorage.removeItem(PRE_COST_KEY);
  } catch (_) { /* depolama yoksa yalnızca bu oturumda kalır */ }
}

(function initPreCost() {
  if (!$("#pre-cost")) return;
  try {
    const saved = JSON.parse(localStorage.getItem(PRE_COST_KEY) || "null");
    if (saved) for (const [k, sel] of Object.entries(PRE_COST_FIELDS)) if (typeof saved[k] === "string") $(sel).value = saved[k];
  } catch (_) {}
  Object.values(PRE_COST_FIELDS).forEach(sel => $(sel).addEventListener("input", syncPreCostState));
  $("#pc-clear").addEventListener("click", () => { Object.values(PRE_COST_FIELDS).forEach(sel => { $(sel).value = ""; }); syncPreCostState(); });
  syncPreCostState();
  if (Object.values(readPreCost()).some(v => v != null)) $("#pre-cost").open = true;
})();


// ===========================================================================
// PROFİL (herkes, yalnızca kendi profili) + YETKİNLİK FORMU (15 bölüm, şema sunucudan)
// Form yalnızca kişinin kendisi ve owner'lar tarafından görülür — kural SUNUCUDA (admin/üye 403).
// ===========================================================================
const pfState = { schema: null, profile: null, form: null, timer: null, saving: null, dirty: false, tab: "form" };
const PF_STATUS = { none: ["Başlanmadı", "na"], draft: ["Taslak", "warn"], submitted: ["Gönderildi", "ok"] };
const fmtDay = (ts) => ts ? new Date(ts * 1000).toLocaleDateString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric" }) : "—";

async function pfApi(url, opts = {}) {
  const r = await apiFetch(`${API_BASE}${url}`, { headers: { "Content-Type": "application/json" }, ...opts });
  const b = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(apiErrorText(b, r.status)); e.status = r.status; throw e; }
  return b;
}
async function cfSchema() { if (!pfState.schema) pfState.schema = await pfApi("/api/competency/schema"); return pfState.schema; }

// --- Form çizici (düzenlenebilir / salt okunur) -----------------------------
function cfFieldHtml(f, a, sch, pre, ro) {
  const v = a[f.key];
  const lab = f.label ? `<div class="text-[13px] font-semibold mb-2">${esc(f.label)}</div>` : "";
  const lim = sch.limits;
  if (f.type === "text" || f.type === "textarea") {
    if (ro) return `<div>${lab}<div class="text-sm whitespace-pre-wrap break-words ${v ? "" : "text-secondary"}">${esc(v || "—")}</div></div>`;
    return `<div>${lab}${f.type === "text"
      ? `<input type="text" class="field w-full" data-k="${esc(f.key)}" maxlength="${lim.text}" value="${esc(v || "")}" placeholder="${esc(f.placeholder || "")}" aria-label="${esc(f.label)}">`
      : `<textarea class="field w-full !h-24 py-2" data-k="${esc(f.key)}" maxlength="${lim.textarea}" placeholder="${esc(f.placeholder || "")}" aria-label="${esc(f.label)}">${esc(v || "")}</textarea>`}</div>`;
  }
  if (f.type === "single" || f.type === "multi") {
    const sel = f.type === "single" ? [v].filter(Boolean) : (v || []);
    if (ro) return `<div>${lab}<div class="flex flex-wrap gap-1.5">${sel.length ? sel.map(o => `<span class="chip ok">${esc(o)}</span>`).join("") : '<span class="text-sm text-secondary">—</span>'}</div></div>`;
    const long = f.options.some(o => o.length > 32);
    return `<div>${lab}<div class="${long ? "grid grid-cols-1 sm:grid-cols-2 gap-2" : "flex flex-wrap gap-2"}" data-k="${esc(f.key)}" data-type="${f.type}" role="${f.type === "single" ? "radiogroup" : "group"}" aria-label="${esc(f.label)}">${f.options.map(o => `
      <label class="pf-opt ${long ? "block-opt" : ""}"><input type="${f.type === "single" ? "radio" : "checkbox"}" name="${pre}-${esc(f.key)}" value="${esc(o)}" ${sel.includes(o) ? "checked" : ""}><span>${esc(o)}</span></label>`).join("")}</div></div>`;
  }
  if (f.type === "ratings") {
    const lv = a.levels || {};
    return `<div class="flex flex-col">${f.items.map(it => {
      const n = lv[it.key];
      const right = ro ? `<span class="lv lv-${n || 0}">${n || "—"}</span><span class="text-xs text-secondary w-[70px]">${n ? esc(sch.levels[n]) : "boş"}</span>`
        : `<div class="pf-rate" role="radiogroup" aria-label="${esc(it.label)}">${[1, 2, 3, 4, 5].map(i => `<label title="${i} · ${esc(sch.levels[i])}"><input type="radio" name="${pre}-lv-${esc(it.key)}" data-lv="${esc(it.key)}" value="${i}" ${n === i ? "checked" : ""}>${i}</label>`).join("")}</div>`;
      return `<div class="pf-row"><div class="min-w-0 flex-1 basis-[220px]"><div class="text-[13.5px] font-medium">${esc(it.label)}</div>${it.desc ? `<div class="text-[11.5px] text-secondary">${esc(it.desc)}</div>` : ""}</div>
        <div class="flex items-center gap-2">${right}</div></div>`;
    }).join("")}</div>`;
  }
  if (f.type === "list") {
    const vals = v || [];
    if (ro) return `<div>${lab}${vals.length ? `<ol class="list-decimal pl-5 text-sm flex flex-col gap-1">${vals.map(x => `<li class="break-words">${esc(x)}</li>`).join("")}</ol>` : '<div class="text-sm text-secondary">—</div>'}</div>`;
    return `<div>${lab}<div class="flex flex-col gap-2" data-k="${esc(f.key)}" data-type="list">${Array.from({ length: f.max_items }, (_, i) => `
      <div class="flex items-center gap-2"><span class="text-xs text-secondary w-4 text-right">${i + 1}.</span><input type="text" class="field w-full !h-9" maxlength="${lim.list_item}" value="${esc(vals[i] || "")}" aria-label="${esc(f.label)} ${i + 1}"></div>`).join("")}</div></div>`;
  }
  if (f.type === "languages") {
    const langs = v && v.length ? v : (ro ? [] : [{ name: "İngilizce", cefr: "", note: "", ratings: {} }]);
    if (ro && !langs.length) return `<div class="text-sm text-secondary">—</div>`;
    const card = (e, idx) => `
      <div class="rounded-xl border border-hairline p-3 sm:p-4 cf-lang">
        ${ro ? `<div class="flex flex-wrap items-center gap-2"><span class="font-semibold break-words">${esc(e.name)}</span>${e.cefr ? `<span class="chip ok">${esc(e.cefr)}</span>` : ""}${e.note ? `<span class="text-xs text-secondary break-words">— ${esc(e.note)}</span>` : ""}</div>`
          : `<div class="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_140px_auto] gap-2 items-center">
              <input type="text" class="field !h-9 cf-lang-name" maxlength="40" value="${esc(e.name || "")}" placeholder="Dil (örn. Almanca)" aria-label="Dil adı">
              <select class="field !h-9 cf-lang-cefr" aria-label="Genel düzey"><option value="">Genel düzey</option>${sch.cefr.map(c => `<option ${e.cefr === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
              <button type="button" class="btn btn-outline btn-sm cf-lang-del">Kaldır</button></div>
             <input type="text" class="field !h-9 w-full mt-2 cf-lang-note" maxlength="${lim.text}" value="${esc(e.note || "")}" placeholder="Not (örn. 1688 / WeChat yazışması)" aria-label="Dil notu">`}
        <div class="mt-2">${sch.lang_skills.map(sk => {
          const n = (e.ratings || {})[sk.key];
          return `<div class="pf-row"><div class="text-[13px] min-w-0 flex-1 basis-[180px]">${esc(sk.label)}</div>${ro ? `<span class="lv lv-${n || 0}">${n || "—"}</span>`
            : `<div class="pf-rate" role="radiogroup" aria-label="${esc(sk.label)}">${[1, 2, 3, 4, 5].map(i => `<label title="${i} · ${esc(sch.levels[i])}"><input type="radio" name="${pre}-lang${idx}-${sk.key}" data-skill="${sk.key}" value="${i}" ${n === i ? "checked" : ""}>${i}</label>`).join("")}</div>`}</div>`;
        }).join("")}</div>
      </div>`;
    return `<div class="flex flex-col gap-3" data-k="${esc(f.key)}" data-type="languages">${langs.map(card).join("")}
      ${ro ? "" : `<button type="button" class="btn btn-outline btn-sm self-start cf-lang-add" ${langs.length >= sch.max_languages ? "disabled" : ""}><span class="material-symbols-outlined">add</span>Dil ekle</button>`}</div>`;
  }
  return "";
}

function cfRender(container, sch, answers, { readonly = false, prefix = "cf", progress = null } = {}) {
  const a = answers || {};
  container.innerHTML = sch.sections.map(s => {
    const done = progress ? progress.sections[s.key] : null;
    const legend = s.fields.some(f => f.type === "ratings") && !readonly
      ? `<div class="flex flex-wrap gap-1 text-[10.5px] text-secondary">${Object.entries(sch.levels).map(([n, l]) => `<span class="lv lv-${n} !h-5 !text-[10px]">${n}</span>${esc(l)}`).join(" ")}</div>` : "";
    return `<section class="card card-pad pf-sec" id="${prefix}-sec-${esc(s.key)}">
      <div class="flex flex-wrap items-start justify-between gap-2">
        <h3 class="font-display font-semibold text-[16px] flex items-start gap-2 min-w-0"><span class="pf-no">${String(s.no).padStart(2, "0")}</span><span class="break-words">${esc(s.title)}</span></h3>
        ${done == null ? "" : `<span class="chip ${done ? "ok" : "na"} !text-[10px] cf-done" data-sec="${esc(s.key)}">${done ? "Tamam" : "Eksik"}</span>`}
      </div>
      ${s.intro ? `<p class="text-xs text-secondary mt-1">${esc(s.intro)}</p>` : ""}${legend ? `<div class="mt-2">${legend}</div>` : ""}
      <div class="flex flex-col gap-4 mt-3">${s.fields.map(f => cfFieldHtml(f, a, sch, prefix, readonly)).join("")}</div>
    </section>`;
  }).join("");
}

/** DOM'dan cevapları topla (tek doğruluk kaynağı ekran; sunucu ayrıca şemaya göre süzer). */
function cfCollect(container, sch) {
  const out = { levels: {} };
  sch.sections.forEach(s => s.fields.forEach(f => {
    if (f.type === "ratings") {
      f.items.forEach(it => { const c = container.querySelector(`input[data-lv="${CSS.escape(it.key)}"]:checked`); if (c) out.levels[it.key] = Number(c.value); });
      return;
    }
    const el = container.querySelector(`[data-k="${CSS.escape(f.key)}"]`);
    if (!el) return;
    if (f.type === "text" || f.type === "textarea") out[f.key] = el.value;
    else if (f.type === "single") out[f.key] = el.querySelector("input:checked")?.value || "";
    else if (f.type === "multi") out[f.key] = $$("input:checked", el).map(i => i.value);
    else if (f.type === "list") out[f.key] = $$("input", el).map(i => i.value.trim()).filter(Boolean);
    else if (f.type === "languages") out[f.key] = $$(".cf-lang", el).map(card => ({
      name: card.querySelector(".cf-lang-name").value.trim(), cefr: card.querySelector(".cf-lang-cefr").value,
      note: card.querySelector(".cf-lang-note").value,
      ratings: Object.fromEntries($$("input[data-skill]:checked", card).map(i => [i.dataset.skill, Number(i.value)])),
    })).filter(e => e.name);
  }));
  return out;
}

// --- Profil sayfası ---------------------------------------------------------
async function loadProfile() {
  const logged = !!currentUser.role;
  $("#pf-guest").style.display = logged ? "none" : "block";
  $("#pf-body").style.display = logged ? "block" : "none";
  if (!logged) return;
  const st = $("#pf-status"); st.textContent = "yükleniyor…"; st.className = "status-line mt-4 loading";
  try {
    const [p, sch, f] = await Promise.all([pfApi("/api/profile"), cfSchema(), pfApi("/api/competency/me")]);
    pfState.profile = p;
    pfRenderHeader(); pfRenderPersonal(); pfRenderSecurity();
    if (!pfState.dirty) { pfState.form = f; cfRender($("#pf-form"), sch, f.answers, { prefix: "pf", progress: f.progress }); }
    pfRenderFormMeta(); pfRenderNav();
    pfSelectTab(pfState.tab);
    st.textContent = ""; st.className = "status-line mt-4";
  } catch (err) { st.textContent = `Hata: ${err.message}`; st.className = "status-line mt-4 error"; }
}

function pfRenderHeader() {
  const p = pfState.profile;
  $("#pf-avatar").textContent = initials(p.name || p.email);
  $("#pf-name").textContent = p.name || "(ad girilmemiş)";
  $("#pf-sub").textContent = [p.title, p.username ? "@" + p.username : "", p.email].filter(Boolean).join(" • ");
  $("#pf-teams").innerHTML = `<span class="text-xs text-secondary">Ekipler:</span>` + (p.teams.length
    ? p.teams.map(t => `<span class="chip na">${esc(t.name)}</span>`).join("") : `<span class="chip na">Ekip dışı</span>`);
  $("#pf-map-btn").style.display = p.role === "owner" ? "" : "none";
}

function pfRenderFormMeta() {
  const f = pfState.form; if (!f) return;
  const [label, cls] = PF_STATUS[f.status] || PF_STATUS.none;
  const edited = f.status === "submitted" && f.updated_at && f.submitted_at && f.updated_at > f.submitted_at + 1;
  $("#pf-form-chip").textContent = f.status === "submitted" ? `${label} · ${fmtDay(f.submitted_at)}` : label;
  $("#pf-form-chip").className = `chip ${cls}`;
  $("#pf-form-bar").style.width = `${(f.progress.done / f.progress.total) * 100}%`;
  $("#pf-form-count").textContent = `${f.progress.done} / ${f.progress.total} bölüm tamam${edited ? " · gönderimden sonra düzenlendi" : ""}`;
  $("#pf-nav-count").textContent = `${f.progress.done} / ${f.progress.total}`;
  $("#pf-submit").innerHTML = `<span class="material-symbols-outlined">send</span>${f.status === "submitted" ? "Tekrar Gönder" : "Formu Gönder"}`;
  $$("#pf-form .cf-done").forEach(ch => { const d = f.progress.sections[ch.dataset.sec]; ch.textContent = d ? "Tamam" : "Eksik"; ch.className = `chip ${d ? "ok" : "na"} !text-[10px] cf-done`; });
  $$("#pf-nav a").forEach(a => { const d = f.progress.sections[a.dataset.sec]; a.querySelector(".pf-nav-ic").textContent = d ? "✓" : "○"; a.classList.toggle("text-primary", !!d); });
}

function pfRenderNav() {
  const sch = pfState.schema;
  $("#pf-nav").innerHTML = sch.sections.map(s => `<a href="#pf-sec-${esc(s.key)}" data-sec="${esc(s.key)}" class="flex items-start gap-2 rounded-md px-2 py-1 hover:bg-surface-container-low">
    <span class="pf-nav-ic w-3 shrink-0">○</span><span class="min-w-0">${String(s.no).padStart(2, "0")} · ${esc(s.title)}</span></a>`).join("");
  $$("#pf-nav a").forEach(a => a.addEventListener("click", (e) => { e.preventDefault(); document.getElementById(`pf-sec-${a.dataset.sec}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); }));
  pfRenderFormMeta();
}

function pfRenderPersonal() {
  const p = pfState.profile;
  $("#pf-first").value = p.first_name; $("#pf-last").value = p.last_name; $("#pf-username").value = p.username;
  $("#pf-title").value = p.title; $("#pf-phone").value = p.phone;
  $("#pf-teams-field").innerHTML = p.teams.length ? p.teams.map(t => `<span class="chip na">${esc(t.name)}</span>`).join("") : `<span class="chip na">Ekip dışı</span>`;
}

function pfRenderSecurity() {
  const p = pfState.profile;
  $("#pf-email").textContent = p.email; $("#pf-role").textContent = ROLE_LABEL[p.role] || p.role; $("#pf-created").textContent = fmtDay(p.created_at);
}

function pfSelectTab(tab) {
  pfState.tab = tab;
  $$("#pf-tabs .tab-btn").forEach(b => { const on = b.dataset.tab === tab; b.classList.toggle("active", on); b.setAttribute("aria-selected", String(on)); });
  $$("#view-profile .pf-pane").forEach(p => { p.style.display = p.id === `pf-pane-${tab}` ? "" : "none"; });
}

// Otomatik taslak kaydı (değişiklikten ~0,9 sn sonra; kayıt sürerken gelen değişiklik ardından kaydedilir)
function pfScheduleSave() {
  pfState.dirty = true;
  $("#pf-save-state").textContent = "Değişiklik var — kaydediliyor…";
  clearTimeout(pfState.timer);
  pfState.timer = setTimeout(pfSave, 900);
}
async function pfSave() {
  clearTimeout(pfState.timer);
  if (pfState.saving) { await pfState.saving; if (!pfState.dirty) return; }
  pfState.dirty = false;
  const answers = cfCollect($("#pf-form"), pfState.schema);
  pfState.saving = (async () => {
    try {
      const f = await pfApi("/api/competency/me", { method: "PUT", body: JSON.stringify({ answers }) });
      pfState.form = f; pfRenderFormMeta();
      $("#pf-save-state").textContent = `Taslak kaydedildi · ${new Date().toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })}`;
    } catch (err) { pfState.dirty = true; $("#pf-save-state").textContent = `Kaydedilemedi: ${err.message}`; }
  })();
  await pfState.saving; pfState.saving = null;
  if (pfState.dirty) pfScheduleSave();
}

(function initProfile() {
  if (!$("#view-profile")) return;
  $$("#pf-tabs .tab-btn").forEach(b => b.addEventListener("click", () => pfSelectTab(b.dataset.tab)));
  const form = $("#pf-form");
  form.addEventListener("input", (e) => { if (e.target.matches("input[type=text], textarea")) pfScheduleSave(); });
  form.addEventListener("change", (e) => { if (e.target.matches("input[type=radio], input[type=checkbox], select")) pfScheduleSave(); });
  form.addEventListener("click", (e) => {
    const add = e.target.closest(".cf-lang-add"), del = e.target.closest(".cf-lang-del");
    if (!add && !del) return;
    const box = form.querySelector("[data-type=languages]");
    if (del) del.closest(".cf-lang").remove();
    if (add) {
      // yeni boş dil kartı (radyo adları benzersiz olsun diye ayrı önek)
      const t = document.createElement("div");
      const f = pfState.schema.sections.find(s => s.key === "languages").fields[0];
      t.innerHTML = cfFieldHtml(f, { languages: [{ name: "", cefr: "", note: "", ratings: {} }] }, pfState.schema, `pfn${Date.now()}`, false);
      const card = t.querySelector(".cf-lang");
      add.before(card);
      card.querySelector(".cf-lang-name").focus();
    }
    box.querySelector(".cf-lang-add").disabled = $$(".cf-lang", box).length >= pfState.schema.max_languages;
    pfScheduleSave();
  });
  $("#pf-submit").addEventListener("click", async () => {
    const btn = $("#pf-submit");
    if (pfState.dirty || pfState.saving) await pfSave();
    const pr = pfState.form?.progress;
    if (pr && pr.done < pr.total && !confirm(`${pr.total - pr.done} bölüm henüz eksik. Form yine de gönderilsin mi? (Gönderdikten sonra da düzenleyebilirsin.)`)) return;
    btn.disabled = true;
    try {
      pfState.form = await pfApi("/api/competency/me/submit", { method: "POST" });
      pfRenderFormMeta();
      $("#pf-save-state").textContent = `Form gönderildi · ${fmtDay(pfState.form.submitted_at)}`;
    } catch (err) { $("#pf-save-state").textContent = `Gönderilemedi: ${err.message}`; }
    finally { btn.disabled = false; }
  });
  window.addEventListener("beforeunload", (e) => { if (pfState.dirty) { pfSave(); e.preventDefault(); e.returnValue = ""; } });
  $("#pf-personal-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#pf-personal-msg");
    const body = { first_name: $("#pf-first").value, last_name: $("#pf-last").value, username: $("#pf-username").value,
                   title: $("#pf-title").value, phone: $("#pf-phone").value };
    if (!body.first_name.trim() || !body.last_name.trim()) { msg.textContent = "Ad ve soyad zorunlu."; msg.className = "text-xs text-error"; return; }
    try {
      pfState.profile = await pfApi("/api/profile", { method: "PUT", body: JSON.stringify(body) });
      pfRenderHeader(); pfRenderPersonal();
      msg.textContent = "Kaydedildi."; msg.className = "text-xs text-primary";
      await checkAuthStatus();   // üst çubuktaki ad
    } catch (err) { msg.textContent = err.message; msg.className = "text-xs text-error"; }
  });
  $("#pf-pass-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#pf-pass-msg"), cur = $("#pf-pass-cur").value, n1 = $("#pf-pass-new").value, n2 = $("#pf-pass-new2").value;
    const fail = (t) => { msg.textContent = t; msg.className = "text-xs text-error"; };
    if (!cur || !n1) return fail("Mevcut ve yeni şifre gerekli.");
    if (n1.length < 6) return fail("Yeni şifre en az 6 karakter olmalı.");
    if (n1 !== n2) return fail("Yeni şifreler eşleşmiyor.");
    try {
      await pfApi("/api/profile/password", { method: "POST", body: JSON.stringify({ current_password: cur, new_password: n1 }) });
      ["#pf-pass-cur", "#pf-pass-new", "#pf-pass-new2"].forEach(s => { $(s).value = ""; });
      msg.textContent = "Şifre değiştirildi; diğer oturumların kapatıldı."; msg.className = "text-xs text-primary";
    } catch (err) { fail(err.message); }
  });
  $("#pf-map-btn").addEventListener("click", () => { tgState.tab = "skills"; showView("team"); });
})();

// --- Salt okunur form penceresi (owner: Yetenek Haritası'ndan; kişi: kendisi) ---
async function openCompetencyModal(userId) {
  const m = $("#cf-modal"), body = $("#cf-modal-body");
  m.style.display = "flex"; body.innerHTML = `<div class="status-line loading">yükleniyor…</div>`;
  $("#cf-modal-title").textContent = ""; $("#cf-modal-sub").textContent = ""; $("#cf-modal-avatar").textContent = "";
  try {
    const [sch, d] = await Promise.all([cfSchema(), pfApi(`/api/competency/users/${encodeURIComponent(userId)}`)]);
    const [label] = PF_STATUS[d.status] || PF_STATUS.none;
    $("#cf-modal-avatar").textContent = initials(d.user.name || d.user.email);
    $("#cf-modal-title").textContent = d.user.name || d.user.email || "—";
    $("#cf-modal-sub").textContent = [d.user.title, d.user.email, d.user.teams.map(t => t.name).join(", ") || "Ekip dışı",
      `${label}${d.submitted_at ? " · " + fmtDay(d.submitted_at) : ""} · ${d.progress.done}/${d.progress.total} bölüm`].filter(Boolean).join(" • ");
    body.innerHTML = `<div class="text-xs text-secondary">Bu formu yalnızca kişinin kendisi ve panel yöneticileri (owner) görebilir.</div><div id="cf-modal-form" class="flex flex-col gap-4"></div>`;
    cfRender($("#cf-modal-form"), sch, d.answers, { readonly: true, prefix: "cfm", progress: d.progress });
  } catch (err) { body.innerHTML = `<div class="status-line error">Hata: ${esc(err.message)}</div>`; }
}
(function initCfModal() {
  const m = $("#cf-modal"); if (!m) return;
  const close = () => { m.style.display = "none"; };
  $("#cf-modal-close").addEventListener("click", close);
  m.addEventListener("click", (e) => { if (e.target === m) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && m.style.display === "flex") close(); });
})();

// --- Yetenek Haritası (Ekip Yönetimi sekmesi, yalnızca owner) ---------------
const skState = { team: "all", field: "", min: "" };
async function skLoad() {
  const st = $("#tm-status"); st.textContent = "yükleniyor…"; st.className = "status-line mt-4 loading";
  const params = new URLSearchParams({ team: skState.team || "all" });
  if (skState.field) params.set("field", skState.field);
  if (skState.min) params.set("min_level", skState.min);
  try {
    const d = await pfApi(`/api/skills/map?${params}`);
    skRender(d);
    st.textContent = ""; st.className = "status-line mt-4";
  } catch (err) { st.textContent = `Hata: ${err.message}`; st.className = "status-line mt-4 error"; }
}
function skRender(d) {
  // filtre seçenekleri (seçimi koru)
  $("#sk-team").innerHTML = `<option value="all">Tüm ekipler</option>` + d.teams.map(t => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("") + `<option value="none">Ekip dışı</option>`;
  $("#sk-team").value = skState.team;
  $("#sk-field").innerHTML = `<option value="">Tüm alanlar</option>` + d.sections.map(s => `<optgroup label="${esc(String(s.no).padStart(2, "0") + " · " + s.title)}">
      <option value="s:${esc(s.key)}">— ${esc(s.title)} (bölümün tümü)</option>
      ${d.all_fields.filter(f => f.section_key === s.key).map(f => `<option value="${esc(f.key)}">${esc(f.label)}</option>`).join("")}</optgroup>`).join("");
  $("#sk-field").value = skState.field;
  $("#sk-min").value = skState.min;
  $("#sk-legend").innerHTML = Object.entries(d.levels).map(([n, l]) => `<span class="inline-flex items-center gap-1"><span class="lv lv-${n}">${n}</span>${esc(l)}</span>`).join("");
  $("#sk-count").textContent = `· ${fmtNum(d.rows.length)} kişi`;
  const min = d.filters.min_level;
  const head = `<thead><tr><th class="l sk-name">Kişi</th>${d.fields.map(f => `<th class="sk-f" title="${esc(f.section_title + " — " + f.label)}">${esc(f.label)}</th>`).join("")}</tr></thead>`;
  const rows = d.rows.map(r => `<tr><td class="l sk-name"><button type="button" class="sk-open text-left font-medium text-primary hover:underline break-words" data-id="${esc(r.id)}">${esc(r.name)}</button>
      <div class="text-[11px] text-secondary truncate" title="${esc(r.teams.join(", "))}">${esc(r.teams.join(", ") || "Ekip dışı")}</div></td>
      ${d.fields.map(f => { const n = r.levels[f.key]; return `<td class="${min && (n || 0) < min ? "sk-dim" : ""}"><span class="lv lv-${n || 0}" title="${esc(f.label)}: ${n ? n + " · " + esc(d.levels[n]) : "boş"}">${n || "—"}</span></td>`; }).join("")}</tr>`).join("");
  $("#sk-table").innerHTML = head + `<tbody>${rows || `<tr><td class="l muted !py-6" colspan="${d.fields.length + 1}">Filtreye uyan, formunu göndermiş kişi yok.</td></tr>`}</tbody>`;
  $("#sk-pending-count").textContent = `· ${fmtNum(d.pending.length)} kişi`;
  $("#sk-pending").innerHTML = d.pending.length ? d.pending.map(p => `<div class="px-5 py-3 flex flex-wrap items-center justify-between gap-2">
      <div class="min-w-0"><button type="button" class="sk-open text-left font-medium text-primary hover:underline break-words" data-id="${esc(p.id)}">${esc(p.name)}</button>
        <div class="text-[11px] text-secondary">${esc(p.teams.join(", ") || "Ekip dışı")}</div></div>
      <span class="chip ${p.status === "draft" ? "warn" : "na"}">${p.status === "draft" ? `Taslak · ${p.progress.done}/${p.progress.total} bölüm` : "Başlamadı"}</span></div>`).join("")
    : `<div class="px-5 py-6 text-sm text-secondary">Bu filtrede herkes formunu göndermiş.</div>`;
  $$("#tg-pane-skills .sk-open").forEach(b => b.addEventListener("click", () => openCompetencyModal(Number(b.dataset.id))));
}
(function initSkills() {
  if (!$("#sk-filters")) return;
  $("#sk-team").addEventListener("change", (e) => { skState.team = e.target.value; skLoad(); });
  $("#sk-field").addEventListener("change", (e) => { skState.field = e.target.value; skLoad(); });
  $("#sk-min").addEventListener("change", (e) => { skState.min = e.target.value; skLoad(); });
})();
