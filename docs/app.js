let allBanners = [];
let allAnalyses = [];
let lastUpdatedSignature = "";
const chartInstances = {};

function escapeHtml(str) {
  return String(str || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function renderKpis(stats) {
  const metrics = stats.capture_metrics || {};
  document.getElementById("kpi-total").textContent = stats.total_unique_banners ?? 0;
  document.getElementById("kpi-updated").textContent =
    "Mise a jour : " + formatDateTime(stats.updated_at);

  document.getElementById("kpi-subjects").textContent = stats.subject_banners_count ?? 0;
  document.getElementById("kpi-tickers").textContent =
    "Fils info : " + (stats.ticker_banners_count ?? 0);

  document.getElementById("kpi-rejected").textContent = metrics.rejected_ads_or_jingles ?? 0;
  document.getElementById("kpi-checked").textContent =
    "Images verifiees : " + (metrics.total_frames_checked ?? 0);

  const analyses = stats.daily_ai_analyses || [];
  document.getElementById("kpi-ai-count").textContent = analyses.length;
  document.getElementById("kpi-ai-model").textContent =
    analyses.length > 0
      ? "Modele : " + (analyses[0].model_used || "Gemini") + " (" + (analyses[0].date || "") + ")"
      : "En attente du prochain cycle";

  const statusPill = document.getElementById("status-pill");
  statusPill.textContent = "Statut : " + (metrics.last_status || "pret");
}

function renderChart(canvasId, type, labels, values, labelText, color) {
  if (chartInstances[canvasId]) {
    chartInstances[canvasId].destroy();
  }
  const ctx = document.getElementById(canvasId);
  chartInstances[canvasId] = new Chart(ctx, {
    type,
    data: {
      labels,
      datasets: [
        {
          label: labelText,
          data: values,
          backgroundColor: color,
          borderRadius: type === "bar" ? 6 : 0,
        },
      ],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: canvasId === "keywordsChart" ? "y" : "x",
      plugins: {
        legend: { display: type === "doughnut" },
      },
    },
  });
}

function renderCharts(stats) {
  const kws = (stats.top_keywords || []).slice(0, 10);
  renderChart(
    "keywordsChart",
    "bar",
    kws.map((k) => k.keyword),
    kws.map((k) => k.count),
    "Occurrences",
    "#d90429"
  );

  const hourly = stats.hourly_distribution || [];
  renderChart(
    "hourlyChart",
    "bar",
    hourly.map((h) => h.hour),
    hourly.map((h) => h.count),
    "Bandeaux par heure",
    "#1d4ed8"
  );

  const shows = (stats.shows || []).slice(0, 8);
  renderChart(
    "showsChart",
    "doughnut",
    shows.map((s) => s.name),
    shows.map((s) => s.count),
    "Emissions",
    ["#0f172a", "#d90429", "#1d4ed8", "#059669", "#d97706", "#7c3aed", "#475569", "#dc2626"]
  );
}

function renderAiSection(analyses) {
  allAnalyses = analyses || [];
  const select = document.getElementById("ai-date-select");
  const box = document.getElementById("ai-content");
  const prevValue = select.value || "0";
  select.innerHTML = "";

  if (allAnalyses.length === 0) {
    select.style.display = "none";
    box.innerHTML =
      '<p class="muted">Premiere analyse Gemini en cours de generation...</p>';
    return;
  }

  select.style.display = "inline-block";
  allAnalyses.forEach((item, idx) => {
    const opt = document.createElement("option");
    opt.value = String(idx);
    opt.textContent = `${item.date} (${item.banners_analyzed || 0} bandeaux · ${item.model_used || "Gemini"})`;
    select.appendChild(opt);
  });

  const displayAnalysis = (idx) => {
    const a = allAnalyses[idx] || allAnalyses[0];
    const themesHtml = (a.themes_principaux || [])
      .map(
        (t) =>
          `<div class="theme-chip"><strong>${escapeHtml(t.theme)}</strong> (${escapeHtml(
            t.occurrences
          )})<br><small>${escapeHtml((t.mots_cles || []).join(", "))}</small></div>`
      )
      .join("");
    const highlightsHtml = (a.sujets_marquants || [])
      .map((s) => `<li>${escapeHtml(s)}</li>`)
      .join("");

    box.innerHTML = `
      <p><strong>Synthese (${escapeHtml(a.date)}) :</strong> ${escapeHtml(a.resume_journee)}</p>
      <div class="ai-themes">${themesHtml}</div>
      ${highlightsHtml ? `<strong>Sujets marquants :</strong><ul>${highlightsHtml}</ul>` : ""}
      ${
        a.observation_editoriale
          ? `<p class="muted"><em>${escapeHtml(a.observation_editoriale)}</em></p>`
          : ""
      }
    `;
  };

  const chosenIdx = Number(prevValue) < allAnalyses.length ? Number(prevValue) : 0;
  select.value = String(chosenIdx);
  select.onchange = () => displayAnalysis(Number(select.value));
  displayAnalysis(chosenIdx);
}

function populateShowFilter(banners) {
  const select = document.getElementById("show-filter");
  const current = select.value;
  const shows = [...new Set(banners.map((b) => b.show_name).filter(Boolean))].sort();
  select.innerHTML = '<option value="">Toutes les emissions</option>';
  shows.forEach((show) => {
    const opt = document.createElement("option");
    opt.value = show;
    opt.textContent = show;
    select.appendChild(opt);
  });
  if (shows.includes(current)) {
    select.value = current;
  }
}

function renderBannersTable() {
  const q = document.getElementById("search-input").value.trim().toLowerCase();
  const show = document.getElementById("show-filter").value;
  const tbody = document.getElementById("banners-tbody");

  const filtered = allBanners.filter((b) => {
    if (show && b.show_name !== show) return false;
    if (!q) return true;
    const hay = `${b.subject_surtitre} ${b.subject_title} ${b.ticker_text} ${b.show_name}`.toLowerCase();
    return hay.includes(q);
  });

  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="muted">Aucun bandeau correspondant.</td></tr>';
    return;
  }

  tbody.innerHTML = filtered
    .slice(0, 150)
    .map(
      (b) => `
      <tr>
        <td>${escapeHtml(formatDateTime(b.first_seen))}</td>
        <td><span class="badge-show">${escapeHtml(b.show_name || "CNews")}</span></td>
        <td>
          ${b.subject_surtitre ? `<span class="surtitre">${escapeHtml(b.subject_surtitre)}</span>` : ""}
          ${escapeHtml(b.subject_title || "—")}
        </td>
        <td>${escapeHtml(b.ticker_text || "—")}</td>
        <td>${escapeHtml(b.sample_count || 1)}</td>
      </tr>
    `
    )
    .join("");
}

function exportCsv() {
  const headers = ["id", "date_local", "first_seen", "last_seen", "sample_count", "show_name", "subject_surtitre", "subject_title", "ticker_text"];
  const rows = [headers.join(";")];
  allBanners.forEach((b) => {
    const vals = headers.map((h) => `"${String(b[h] ?? "").replaceAll('"', '""')}"`);
    rows.push(vals.join(";"));
  });
  const blob = new Blob(["\uFEFF" + rows.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "cnews_bandeaux.csv";
  a.click();
  URL.revokeObjectURL(url);
}

async function refreshDashboard() {
  try {
    const [statsResp, bannersResp] = await Promise.all([
      fetch("data/stats.json?t=" + Date.now(), { cache: "no-store" }),
      fetch("data/banners.json?t=" + Date.now(), { cache: "no-store" }),
    ]);
    const stats = statsResp.ok ? await statsResp.json() : {};
    const banners = bannersResp.ok ? await bannersResp.json() : [];

    const sig = JSON.stringify({
      u: stats.updated_at,
      b: banners.length,
      l: banners[0]?.last_seen,
      a: (stats.daily_ai_analyses || [])[0]?.generated_at,
    });
    if (sig === lastUpdatedSignature) {
      return;
    }
    lastUpdatedSignature = sig;
    allBanners = banners;

    renderKpis(stats);
    renderCharts(stats);
    renderAiSection(stats.daily_ai_analyses || []);
    populateShowFilter(allBanners);
    renderBannersTable();
  } catch (err) {
    document.getElementById("status-pill").textContent = "Reconnexion...";
    console.error(err);
  }
}

document.getElementById("search-input").addEventListener("input", renderBannersTable);
document.getElementById("show-filter").addEventListener("change", renderBannersTable);
document.getElementById("export-csv-btn").addEventListener("click", exportCsv);

refreshDashboard();
setInterval(refreshDashboard, 5000);
