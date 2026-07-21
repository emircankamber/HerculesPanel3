// SellerSprite PL Panel — frontend
// Backend'i aynı origin'den servis ediyorsan boş bırak; ayrı deploy ettiysen
// tam URL yaz (örn. "https://pl-panel-api.up.railway.app").
const API_BASE = "";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ---------------------------------------------------------------------------
// Navigasyon
// ---------------------------------------------------------------------------
$$(".nav-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    $$(".nav-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    $$(".view").forEach(v => v.classList.remove("active"));
    $(`#view-${btn.dataset.view}`).classList.add("active");
    if (btn.dataset.view === "history") loadHistory();
    if (btn.dataset.view === "decisions") loadDecisions();
  });
});

// ---------------------------------------------------------------------------
// Arama formu
// ---------------------------------------------------------------------------
$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const keyword = $("#kw-input").value.trim();
  const marketplace = $("#market-input").value;
  const categoryOverride = $("#category-override-input").value.trim();
  if (!keyword) return;

  const btn = $("#search-btn");
  const status = $("#status-line");
  btn.disabled = true;
  status.textContent = "SellerSprite MCP'den veri çekiliyor… (9-10 çağrı, birkaç saniye sürebilir)";
  status.className = "status-line";

  try {
    const res = await fetch(`${API_BASE}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
    status.textContent = data.source === "cache"
      ? "önbellekten yüklendi (24 saat içinde daha önce çekilmiş)"
      : "canlı SellerSprite verisi yüklendi";
    renderPanel(data);
  } catch (err) {
    status.textContent = `Hata: ${err.message}`;
    status.className = "status-line error";
  } finally {
    btn.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Panel render
// ---------------------------------------------------------------------------
function renderPanel(data) {
  const tpl = $("#tpl-panel").content.cloneNode(true);
  const root = tpl.querySelector(".panel");

  root.querySelector(".kw-title").textContent = data.keyword;
  root.querySelector(".kw-sub").textContent = `${data.marketplace} · canlı SellerSprite MCP verisi${data.category_used ? " · Kategori: " + data.category_used : ""}`;

  // --- Ön öneri rozeti ---
  const pa = data.pre_assessment || {};
  const badge = root.querySelector(".verdict-badge");
  const verdictClass = { "Uygun": "uygun", "Sınırda": "sinirda", "Elenmiş": "elenmis" }[pa.verdict] || "sinirda";
  badge.textContent = pa.verdict || "—";
  badge.classList.add(verdictClass);
  root.querySelector(".neg-count").textContent = pa.negative_count ?? "—";

  // --- Ön değerlendirme kriter grid ---
  const critGrid = root.querySelector(".crit-grid");
  const fmtCrit = (v) => v === null || v === undefined ? "n/a" : (typeof v === "number" ? (Math.abs(v) < 3 ? (v * 100).toFixed(1) + "%" : v.toFixed(2)) : v);
  (pa.criteria || []).forEach(c => {
    const div = document.createElement("div");
    div.className = "crit";
    div.dataset.label = c.label;
    div.dataset.direction = c.direction;
    div.dataset.threshold = c.threshold;
    const dirLabel = c.direction === ">=" ? "≥" : "≤";
    div.innerHTML = `
      <span>${c.label}<br><span class="crit-val">${fmtCrit(c.value)} <small>(${dirLabel}${fmtCrit(c.threshold)})</small></span></span>
      <span class="crit-flag ${c.flag === "OK" ? "ok" : c.flag === "OLUMSUZ" ? "olumsuz" : "na"}">${c.flag}</span>`;
    critGrid.appendChild(div);
  });

  // --- Ön değerlendirmeyi kar analizindeki net marjla güncelle (canlı) ---
  function updateNetMarginCriterion(marginValue) {
    const card = [...critGrid.children].find(el => el.dataset.label === "Net Kar Marjı (kar analizi)");
    if (!card) return;
    const threshold = parseFloat(card.dataset.threshold);
    const flag = marginValue >= threshold ? "OK" : "OLUMSUZ";
    card.querySelector(".crit-val").innerHTML = `${(marginValue * 100).toFixed(1)}% <small>(≥${(threshold * 100).toFixed(1)}%)</small>`;
    const flagEl = card.querySelector(".crit-flag");
    flagEl.textContent = flag;
    flagEl.className = `crit-flag ${flag === "OK" ? "ok" : "olumsuz"}`;

    // Toplam olumsuz sayısını ve ön öneriyi yeniden hesapla
    const negCount = [...critGrid.children].filter(el => el.querySelector(".crit-flag").textContent === "OLUMSUZ").length;
    root.querySelector(".neg-count").textContent = negCount;
    const newVerdict = negCount === 0 ? "Uygun" : (negCount < 4 ? "Sınırda" : "Elenmiş");
    const verdictClass = { "Uygun": "uygun", "Sınırda": "sinirda", "Elenmiş": "elenmis" }[newVerdict];
    badge.textContent = newVerdict;
    badge.className = `verdict-badge ${verdictClass}`;
  }

  // --- Pazar özeti ---
  const stats = data.market_stats || {};
  const statGrid = root.querySelector(".stat-grid");
  const statEntries = [
    ["Ort. Fiyat", stats.avgPrice, "$"],
    ["Ort. Rating", stats.avgRating, ""],
    ["Ort. Review", stats.avgRatings, ""],
    ["Toplam Marka", stats.brands, ""],
    ["Ort. Satıcı", stats.avgSellers, ""],
    ["Yeni Ürün (12ay)", stats.newProducts, ""],
    ["Yeni Ürün %", stats.newProductProportion, "%mul100"],
    ["İlk Listing", stats.firstShelfDate, ""],
  ];
  statEntries.forEach(([label, value, unit]) => {
    if (value === undefined || value === null) return;
    const div = document.createElement("div");
    div.className = "stat-card";
    let display = value;
    if (unit === "$") display = "$" + Number(value).toFixed(2);
    if (unit === "%mul100") {
      const v = Number(value);
      // GÜVENLİK: SellerSprite bazı alanları zaten yüzde (örn. 27.78) bazılarını
      // oran (0.2778) olarak dönebiliyor. Büyüklüğe göre otomatik algıla —
      // >1 ise zaten yüzdedir, tekrar 100'le çarpma (daha önce "%2778.0" hatası buradan geliyordu).
      display = (v > 1 ? v : v * 100).toFixed(1) + "%";
    }
    div.innerHTML = `<div class="stat-label">${label}</div><div class="stat-value">${display}</div>`;
    statGrid.appendChild(div);
  });

  // --- Grafikler ---
  requestAnimationFrame(() => {
    drawBrandChart(root.querySelector(".chart-brand"), data.brand_concentration || []);
    drawPriceChart(root.querySelector(".chart-price"), data.price_distribution || []);
    drawLaunchChart(root.querySelector(".chart-launch"), data.launch_distribution || []);
    drawTrendChart(root.querySelector(".chart-trend"), data.demand_trend);
  });

  // --- Relevant keywords tablosu ---
  const tbody = root.querySelector(".kw-tbody");
  (data.keyword_rows || []).forEach(row => {
    const tr = document.createElement("tr");
    const acos = row.acos;
    const acosClass = acos == null ? "" : acos < 0.2 ? "acos-good" : acos < 0.5 ? "acos-mid" : "acos-bad";
    tr.innerHTML = `
      <td style="font-family:var(--font-ui)">${row.keyword ?? ""}</td>
      <td>${fmtNum(row.searches)}</td>
      <td>${fmtNum(row.clicks)}</td>
      <td>${fmtNum(row.purchases)}</td>
      <td>${row.click_cvr != null ? (row.click_cvr * 100).toFixed(1) + "%" : "n/a"}</td>
      <td>${row.bid != null ? "$" + row.bid.toFixed(2) : "n/a"}</td>
      <td class="${acosClass}">${acos != null ? (acos * 100).toFixed(1) + "%" : "n/a"}</td>
      <td>${row.cpa != null ? "$" + row.cpa.toFixed(2) : "n/a"}</td>
      <td>${row.relevancy ?? "n/a"}</td>`;
    tbody.appendChild(tr);
  });

  // --- Kar analizi (canlı) ---
  const profitInputs = ["cogs", "sale", "fba", "ref", "acos", "ret", "gen"].map(k => root.querySelector(`.p-${k}`));

  // Gerçek pazar verisiyle önceden doldur (kullanıcı hâlâ istediği gibi değiştirebilir)
  const mainRowForProfit = (data.keyword_rows || []).find(
    r => (r.keyword || "").toLowerCase() === data.keyword.toLowerCase()
  );
  const marketAvgPrice = data.market_stats?.avgPrice;
  if (marketAvgPrice) root.querySelector(".p-sale").value = marketAvgPrice.toFixed(2);
  if (mainRowForProfit?.acos != null) root.querySelector(".p-acos").value = (mainRowForProfit.acos * 100).toFixed(1);
  if (data.market_return_rate != null) root.querySelector(".p-ret").value = (data.market_return_rate * 100).toFixed(2);
  // Kaynağını panelde belirt (şeffaflık — hangi değerler gerçek, hangileri hâlâ manuel varsayım)
  const profitSourceNote = root.querySelector(".profit-source-note");
  if (profitSourceNote) {
    const sources = [];
    if (marketAvgPrice) sources.push("Satış Fiyatı: pazar ortalaması");
    if (mainRowForProfit?.acos != null) sources.push("ACOS: bu keyword için hesaplanan");
    if (data.market_return_rate != null) sources.push("Return Rate: pazar ortalaması");
    profitSourceNote.textContent = sources.length
      ? `✓ Gerçek veriyle dolduruldu — ${sources.join(" · ")}. COGS/FBA/Referral Fee hâlâ manuel girilmeli.`
      : "";
  }
  let lastProfitResult = null;  // Excel export'ta kullanılacak
  const recalcProfit = async () => {
    const [cogs, sale, fba, ref, acos, ret, gen] = profitInputs.map(i => parseFloat(i.value) || 0);
    try {
      const res = await fetch(`${API_BASE}/api/profit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cogs, sale_price: sale, fba_fee: fba, referral_fee: ref,
          acos: acos / 100, return_rate: ret / 100, overhead_rate: gen / 100,
        }),
      });
      const p = await res.json();
      lastProfitResult = { ...p, inputs: { cogs, sale, fba, ref, acos, ret, gen } };
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
      tbody.innerHTML = "<tr><td colspan='8'>Otomatik rakip bulunamadı — ASIN'leri manuel gir.</td></tr>";
      return;
    }
    rows.forEach(r => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${r.asin ?? ""}</td><td>${r.brand ?? ""}</td><td>$${(r.price ?? 0).toFixed(2)}</td>
        <td>${fmtNum(r.units)}</td><td>$${fmtNum(Math.round(r.revenue || 0))}</td>
        <td>${r.bsr ?? "n/a"}</td><td>${r.rating ?? "n/a"}</td><td>${fmtNum(r.reviews ?? r.ratings)}</td>`;
      tbody.appendChild(tr);
    });
  }

  // Otomatik gelen rakipleri (backend'in competitor_lookup çağrısından) hemen göster
  if (data.top_competitors && data.top_competitors.length) {
    competitorRows = data.top_competitors.map(i => ({
      asin: i.asin, brand: i.brand, price: i.price,
      units: i.units ?? 0, revenue: i.revenue ?? 0,
      bsr: i.bsr, rating: i.rating, reviews: i.ratings,
    }));
    renderCompetitorRows(competitorRows);
  } else {
    root.querySelector(".comp-tbody").innerHTML = "<tr><td colspan='8'>Otomatik rakip bulunamadı — ASIN'leri manuel gir.</td></tr>";
  }

  root.querySelector(".competitor-fetch-btn").addEventListener("click", async () => {
    const asinsRaw = root.querySelector(".competitor-asins").value.trim();
    if (!asinsRaw) return;
    const asins = asinsRaw.split(",").map(s => s.trim()).filter(Boolean);
    const tbody = root.querySelector(".comp-tbody");
    tbody.innerHTML = "<tr><td colspan='8'>yükleniyor…</td></tr>";
    try {
      const res = await fetch(`${API_BASE}/api/competitors`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asins, marketplace: data.marketplace }),
      });
      const result = await res.json();
      const items = result?.data?.items || result?.items || [];
      competitorRows = items.map(i => ({
        asin: i.asin, brand: i.brand, price: i.price ?? i.averagePrice,
        units: i.units ?? i.amzUnit ?? 0, revenue: i.revenue ?? i.amzSales ?? 0,
        bsr: i.bsr, rating: i.rating, reviews: i.ratings,
      }));
      renderCompetitorRows(competitorRows);
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan='8'>Hata: ${err.message}</td></tr>`;
    }
  });

  // --- Hercules Signal Engine ---
  root.querySelector(".signal-compute-btn").addEventListener("click", async () => {
    const status = root.querySelector(".signal-status");
    status.textContent = "hesaplanıyor…";
    try {
      const payload = buildSignalsPayload(data, competitorRows, root);
      const res = await fetch(`${API_BASE}/api/signals`, {
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
    const res = await fetch(`${API_BASE}/api/proof-assets/${encodeURIComponent(data.keyword)}`);
    const result = await res.json();
    const list = root.querySelector(".proof-list");
    list.innerHTML = "";
    (result.assets || []).forEach(a => {
      const div = document.createElement("div");
      div.className = "proof-item";
      div.innerHTML = `<span>${a.type} <span style="color:var(--text-muted)">(${a.points}p)</span> ${a.note ? "· " + a.note : ""}</span>
        <span style="display:flex;align-items:center;gap:8px;">
          <span class="proof-item-status ${a.status}">${a.status}</span>
          ${a.status === "pending" ? `<button data-id="${a.id}">Onayla</button>` : ""}
        </span>`;
      const btn = div.querySelector("button");
      if (btn) btn.addEventListener("click", async () => {
        await fetch(`${API_BASE}/api/proof-assets/approve`, {
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
    await fetch(`${API_BASE}/api/proof-assets`, {
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
    await fetch(`${API_BASE}/api/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keyword: data.keyword, marketplace: data.marketplace, decision, note }),
    });
    root.querySelector(".decision-saved-msg").textContent = "✓ kaydedildi";
  });

  // --- Excel export: tam rapor ---
  root.querySelector(".export-report-btn").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "hazırlanıyor…";
    try {
      const exportPayload = { ...data, profit_analysis: lastProfitResult };
      const res = await fetch(`${API_BASE}/api/export/report`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(exportPayload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await downloadBlob(res, `${data.keyword}_rapor.xlsx`);
    } catch (err) {
      alert(`Excel oluşturulamadı: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });

  // --- Excel export: sadece keyword tablosu ---
  root.querySelector(".export-keywords-btn").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "hazırlanıyor…";
    try {
      const res = await fetch(`${API_BASE}/api/export/keywords`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keyword: data.keyword, keyword_rows: data.keyword_rows || [] }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await downloadBlob(res, `${data.keyword}_keywords.xlsx`);
    } catch (err) {
      alert(`Excel oluşturulamadı: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });

  const container = $("#result-container");
  container.innerHTML = "";
  container.appendChild(tpl);

  // --- Debug (geçici) ---
  const debugEl = document.querySelector(".debug-json");
  if (debugEl) {
    debugEl.textContent = JSON.stringify(data._debug ?? { not_found: "..._debug alanı yok, backend güncel değil" }, null, 2);
  }
}

async function downloadBlob(response, filename) {
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.replace(/[^a-zA-Z0-9 _\-\.]/g, "_");
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function fmtNum(v) {
  if (v == null) return "n/a";
  return Number(v).toLocaleString("tr-TR");
}

// ---------------------------------------------------------------------------
// Grafikler (Chart.js) — SellerSprite alan adları netleşince burada eşleştir
// ---------------------------------------------------------------------------
const CHART_COLORS = ["#2FBF9F", "#4C8DFF", "#E8A33D", "#E5484D", "#8B7FD4", "#5FA8D3", "#C4787A", "#7A8B99"];

function baseOptions(extra = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: { y: { grid: { color: "#2C323D" }, ticks: { color: "#9AA3B2" } },
              x: { grid: { display: false }, ticks: { color: "#9AA3B2" } } },
    ...extra,
  };
}

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
    data: { labels, datasets: [{ data: values, backgroundColor: CHART_COLORS, borderColor: "#1B1F26", borderWidth: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: "58%",
      plugins: { legend: { position: "bottom", labels: { color: "#9AA3B2", boxWidth: 10, font: { size: 10.5 } } } } },
  });
}

function drawPriceChart(canvas, items) {
  // Backend artık düz liste gönderiyor (data.data?.items sarmalı YOK — gerçek yanıt "data" doğrudan liste)
  if (!items || !items.length) return;
  new Chart(canvas, {
    type: "bar",
    data: { labels: items.map(i => i.label ?? i.range), datasets: [{ data: items.map(i => (i.unitsRatio ?? i.ratio ?? i.percentage ?? 0) * 100), backgroundColor: "#4C8DFF", borderRadius: 4 }] },
    options: baseOptions({ scales: { y: { ticks: { callback: v => v + "%", color: "#9AA3B2" }, grid: { color: "#2C323D" } }, x: { grid: { display: false }, ticks: { color: "#9AA3B2" } } } }),
  });
}

function drawLaunchChart(canvas, items) {
  if (!items || !items.length) return;
  new Chart(canvas, {
    type: "bar",
    data: { labels: items.map(i => i.label ?? i.range), datasets: [{ data: items.map(i => (i.unitsRatio ?? i.ratio ?? i.percentage ?? 0) * 100), backgroundColor: "#E8A33D", borderRadius: 4 }] },
    options: baseOptions({ scales: { y: { ticks: { callback: v => v + "%", color: "#9AA3B2" }, grid: { color: "#2C323D" } }, x: { grid: { display: false }, ticks: { color: "#9AA3B2" } } } }),
  });
}

function drawTrendChart(canvas, trend) {
  const items = trend?.data?.items || trend?.items || [];
  if (!items.length) return;
  new Chart(canvas, {
    type: "line",
    data: { labels: items.map(i => i.date?.slice(0, 7) ?? ""), datasets: [{ data: items.map(i => i.glanceViews ?? i.value ?? 0), borderColor: "#2FBF9F", backgroundColor: "rgba(47,191,159,0.1)", fill: true, tension: 0.3, pointRadius: 2 }] },
    options: baseOptions(),
  });
}

// ---------------------------------------------------------------------------
// Geçmiş görünümü
// ---------------------------------------------------------------------------
async function loadHistory() {
  const list = $("#history-list");
  list.innerHTML = "yükleniyor…";
  try {
    const res = await fetch(`${API_BASE}/api/recent`);
    const rows = await res.json();
    list.innerHTML = "";
    if (!rows.length) { list.innerHTML = "<p>Henüz hiç keyword analiz edilmemiş.</p>"; return; }
    rows.forEach(r => {
      const div = document.createElement("div");
      div.className = "hist-row";
      const date = new Date(r.fetched_at * 1000).toLocaleString("tr-TR");
      div.innerHTML = `<span class="hist-kw">${r.keyword} <span class="hist-meta">(${r.marketplace})</span></span>
        <span class="hist-meta">${r.verdict ?? "—"} · ${date}</span>`;
      div.addEventListener("click", () => {
        $("#kw-input").value = r.keyword;
        $("#market-input").value = r.marketplace;
        $$(".nav-btn")[0].click();
      });
      list.appendChild(div);
    });
  } catch {
    list.innerHTML = "Geçmiş yüklenemedi (backend erişilebilir mi kontrol et).";
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
    ["Market", result.market.score, "#4C8DFF"],
    ["Demand", result.demand.score, "#2FBF9F"],
    ["Truth", result.truth.score, "#8B7FD4"],
    ["Risk", result.risk.score, "#E5484D"],
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
  boBadge.textContent = result.blue_ocean ? "🌊 Blue Ocean" : "Blue Ocean değil";
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
    const res = await fetch(`${API_BASE}/api/decisions`);
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
          <div class="decision-card-kw">${item.keyword}</div>
          <div class="decision-card-meta"><span>${item.marketplace}</span><span>${date}</span></div>
          ${item.note ? `<div class="decision-card-note">"${item.note}"</div>` : ""}
        `;
        card.addEventListener("click", () => {
          // Sorgu sayfasına dön, keyword'ü doldur, otomatik tekrar analiz et
          $$(".nav-btn")[0].click();
          $("#kw-input").value = item.keyword;
          $("#market-input").value = item.marketplace;
          $("#search-form").requestSubmit();
        });
        container.appendChild(card);
      });
    }
    summary.textContent = `toplam ${total} kararlandırılmış keyword`;
  } catch (err) {
    summary.textContent = `Hata: ${err.message}`;
  }
}
