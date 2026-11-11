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

function formatSignedScore(score) {
  const n = Number(score || 0);
  return n > 0 ? `+${n}` : `${n}`;
}

function stanceBadgeClass(stance) {
  if (!stance) return "pill-neutre";
  if (stance.includes("Critique")) return "pill-critique";
  if (stance.includes("Tribune") || stance.includes("Invité")) return "pill-tribune";
  return "pill-neutre";
}

function renderPoliticalCursor(stats) {
  const cursor = stats.political_cursor || {};
  const score = Math.max(-100, Math.min(100, Number(cursor.score ?? 0)));
  const pctPos = ((score + 100) / 200) * 100;

  const pointer = document.getElementById("spectrum-pointer");
  const pointerVal = document.getElementById("spectrum-pointer-val");
  const badge = document.getElementById("cursor-badge");

  pointer.style.left = `${pctPos.toFixed(1)}%`;
  pointerVal.textContent = formatSignedScore(score);
  badge.textContent = `${formatSignedScore(score)} / 100 · ${cursor.label || "Analyse en cours"}`;

  document.getElementById("pct-left").textContent = `${cursor.left_themes_pct ?? 0}%`;
  document.getElementById("pct-neutral").textContent = `${cursor.neutral_themes_pct ?? 0}%`;
  document.getElementById("pct-right").textContent = `${cursor.right_themes_pct ?? 0}%`;

  const methodParts = [];
  if (cursor.methodology) {
    methodParts.push(escapeHtml(cursor.methodology));
  }
  if (cursor.ai_justification) {
    methodParts.push(`<strong>Analyse IA Gemini :</strong> ${escapeHtml(cursor.ai_justification)}`);
  }
  document.getElementById("cursor-methodology").innerHTML =
    methodParts.join("<br><br>") || "En attente de données...";

  document.getElementById("updated-pill").textContent =
    "Mise à jour : " + formatDateTime(stats.updated_at);
}

function renderPartiesStudy(stats) {
  const parties = stats.parties_study || [];
  const cursor = stats.political_cursor || {};
  const timeline = stats.political_timeline || [];

  // 1. Focus La France Insoumise (LFI) & étude comparative
  const lfiBox = document.getElementById("lfi-focus-box");
  const lfiData = parties.find((p) => p.party_id === "LFI") || {
    total_mentions: 0,
    critique_count: 0,
    neutre_count: 0,
    tribune_count: 0,
    active_hours: [],
    excerpts: [],
  };

  const lfiExcerptsHtml = (lfiData.excerpts || [])
    .map(
      (ex) =>
        `<li><strong>${escapeHtml(ex.time)}</strong> <span class="${stanceBadgeClass(
          ex.stance
        )}">${escapeHtml(ex.stance)}</span> : « ${escapeHtml(ex.excerpt)} » <em>(${escapeHtml(
          ex.reason
        )})</em></li>`
    )
    .join("");

  lfiBox.innerHTML = `
    <h3>Focus : Traitement de La France Insoumise (LFI) & des partis à l'antenne</h3>
    <p>
      <strong>La France Insoumise (LFI) :</strong>
      <strong>${lfiData.total_mentions}</strong> sujet(s) / bandeau(x) détecté(s), dont
      <strong>${lfiData.critique_count}</strong> à cadrage critique ou polémique,
      <strong>${lfiData.neutre_count}</strong> factuel(s) et
      <strong>${lfiData.tribune_count}</strong> en tribune.
      ${
        lfiData.active_hours && lfiData.active_hours.length > 0
          ? `Créneau(x) horaire(s) de passage : <strong>${escapeHtml(
              lfiData.active_hours.join(", ")
            )}</strong>.`
          : "Aucune mention sur le créneau actuel."
      }
    </p>
    ${lfiExcerptsHtml ? `<ul style="margin: 6px 0 8px; padding-left: 20px;">${lfiExcerptsHtml}</ul>` : ""}
    ${
      cursor.ai_parties_study
        ? `<p><strong>Synthèse comparative IA (LFI & autres partis) :</strong> ${escapeHtml(
            cursor.ai_parties_study
          )}</p>`
        : ""
    }
  `;

  // 2. Cartes récapitulatives par parti / bloc politique
  const grid = document.getElementById("party-cards-grid");
  grid.innerHTML = parties
    .map(
      (p) => `
      <div class="party-card" style="border-top: 3px solid ${escapeHtml(p.color || "#0f172a")}">
        <div class="party-card-title">
          <span>${escapeHtml(p.party_short)}</span>
          <span class="party-count-badge">${p.total_mentions} bandeau(x)</span>
        </div>
        <div class="stance-pills">
          <span class="pill-critique">Critiques : ${p.critique_count}</span>
          <span class="pill-neutre">Factuel : ${p.neutre_count}</span>
          <span class="pill-tribune">Tribune/Invité : ${p.tribune_count}</span>
        </div>
        <div class="party-hours">
          <strong>Heures :</strong> ${
            p.active_hours && p.active_hours.length > 0
              ? escapeHtml(p.active_hours.join(", "))
              : "—"
          }
        </div>
      </div>
    `
    )
    .join("");

  // 3. Graphique empilé : Tonalité par parti (Critique vs Factuel vs Tribune)
  if (chartInstances.partyStanceChart) {
    chartInstances.partyStanceChart.destroy();
  }
  const stanceCtx = document.getElementById("partyStanceChart");
  chartInstances.partyStanceChart = new Chart(stanceCtx, {
    type: "bar",
    data: {
      labels: parties.map((p) => p.party_short),
      datasets: [
        {
          label: "Critique / Polémique",
          data: parties.map((p) => p.critique_count),
          backgroundColor: "#dc2626",
          borderRadius: 4,
        },
        {
          label: "Neutre / Factuel",
          data: parties.map((p) => p.neutre_count),
          backgroundColor: "#64748b",
          borderRadius: 4,
        },
        {
          label: "Tribune / Invité en plateau",
          data: parties.map((p) => p.tribune_count),
          backgroundColor: "#16a34a",
          borderRadius: 4,
        },
      ],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: "y",
      scales: {
        x: { stacked: true, ticks: { stepSize: 1 } },
        y: { stacked: true },
      },
      plugins: {
        legend: { position: "bottom" },
      },
    },
  });

  // 4. Graphique horaire : À quelle heure quel parti est abordé / critiqué
  const hourlyRaw = stats.hourly_party_activity || [];
  // Cible les heures ayant de l'activité (+/- 1h autour) ou affiche 06h-23h par défaut
  const activeHourIndices = hourlyRaw
    .map((h, i) =>
      parties.some((p) => (h[p.party_id] || 0) > 0) ? i : -1
    )
    .filter((i) => i !== -1);

  let filteredHours = hourlyRaw.slice(6, 24);
  if (activeHourIndices.length > 0) {
    const minH = Math.max(0, Math.min(...activeHourIndices) - 2);
    const maxH = Math.min(23, Math.max(...activeHourIndices) + 2);
    filteredHours = hourlyRaw.slice(minH, maxH + 1);
  }

  if (chartInstances.partyHourlyChart) {
    chartInstances.partyHourlyChart.destroy();
  }
  const hourlyCtx = document.getElementById("partyHourlyChart");
  chartInstances.partyHourlyChart = new Chart(hourlyCtx, {
    type: "bar",
    data: {
      labels: filteredHours.map((h) => h.hour),
      datasets: parties.map((p) => ({
        label: p.party_short,
        data: filteredHours.map((h) => h[p.party_id] || 0),
        backgroundColor: p.color,
        borderRadius: 4,
      })),
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { stacked: true },
        y: { stacked: true, ticks: { stepSize: 1 } },
      },
      plugins: {
        legend: { position: "bottom" },
      },
    },
  });

  // 5. Chronologie horodatée des bandeaux politiques
  const tlContainer = document.getElementById("political-timeline-list");
  if (timeline.length === 0) {
    tlContainer.innerHTML =
      '<p class="muted">Aucun bandeau mentionnant explicitement un parti sur la période.</p>';
  } else {
    tlContainer.innerHTML = [...timeline]
      .reverse()
      .map(
        (item) => `
        <div class="timeline-item">
          <span class="timeline-time">${escapeHtml(item.time)}</span>
          <span class="timeline-party">${escapeHtml(item.party_short)}</span>
          <span class="${stanceBadgeClass(item.stance)}">${escapeHtml(item.stance)}</span>
          <span class="timeline-excerpt">${escapeHtml(item.excerpt)}</span>
          <small class="muted">${escapeHtml(item.reason)}</small>
        </div>
      `
      )
      .join("");
  }
}

function renderThemeAndKeywordCharts(stats) {
  const themes = stats.themes_breakdown || [];
  if (chartInstances.themesChart) {
    chartInstances.themesChart.destroy();
  }
  const tCtx = document.getElementById("themesChart");
  chartInstances.themesChart = new Chart(tCtx, {
    type: "bar",
    data: {
      labels: themes.map((t) => `${t.theme} (${t.weighted_share_pct}%)`),
      datasets: [
        {
          label: "Part du temps d'antenne (%)",
          data: themes.map((t) => t.weighted_share_pct),
          backgroundColor: themes.map((t) => t.color || "#0f172a"),
          borderRadius: 6,
        },
      ],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: "y",
      plugins: {
        legend: { display: false },
      },
      scales: {
        x: {
          max: 100,
          ticks: { callback: (v) => `${v}%` },
        },
      },
    },
  });

  const kws = (stats.top_keywords || []).slice(0, 10);
  if (chartInstances.keywordsChart) {
    chartInstances.keywordsChart.destroy();
  }
  const kCtx = document.getElementById("keywordsChart");
  chartInstances.keywordsChart = new Chart(kCtx, {
    type: "bar",
    data: {
      labels: kws.map((k) => k.keyword),
      datasets: [
        {
          label: "Sujets distincts",
          data: kws.map((k) => k.count),
          backgroundColor: "#0f172a",
          borderRadius: 6,
        },
      ],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: "y",
      plugins: {
        legend: { display: false },
      },
      scales: {
        x: { ticks: { stepSize: 1 } },
      },
    },
  });
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
      '<p class="muted">Première analyse Gemini en cours de génération...</p>';
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
      <p><strong>Synthèse factuelle (${escapeHtml(a.date)}) :</strong> ${escapeHtml(a.resume_journee)}</p>
      ${
        a.etude_lfi_et_partis
          ? `<p><strong>Analyse des partis & de LFI :</strong> ${escapeHtml(a.etude_lfi_et_partis)}</p>`
          : ""
      }
      <div class="ai-themes">${themesHtml}</div>
      ${highlightsHtml ? `<strong>Faits & titres marquants :</strong><ul>${highlightsHtml}</ul>` : ""}
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

function populateTableFilters(banners) {
  const themeSelect = document.getElementById("theme-filter");
  const partySelect = document.getElementById("party-filter");
  const curTheme = themeSelect.value;
  const curParty = partySelect.value;

  const themes = [...new Set(banners.map((b) => b.primary_theme).filter(Boolean))].sort();
  themeSelect.innerHTML = '<option value="">Tous les thèmes</option>';
  themes.forEach((t) => {
    const opt = document.createElement("option");
    opt.value = t;
    opt.textContent = t;
    themeSelect.appendChild(opt);
  });
  if (themes.includes(curTheme)) themeSelect.value = curTheme;

  const partySet = new Set();
  banners.forEach((b) => {
    (b.parties || []).forEach((p) => partySet.add(p.party_short));
  });
  const parties = [...partySet].sort();
  partySelect.innerHTML = '<option value="">Tous les partis politiques</option>';
  parties.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = p;
    partySelect.appendChild(opt);
  });
  if (parties.includes(curParty)) partySelect.value = curParty;
}

function renderBannersTable() {
  const q = document.getElementById("search-input").value.trim().toLowerCase();
  const themeFilter = document.getElementById("theme-filter").value;
  const partyFilter = document.getElementById("party-filter").value;
  const tbody = document.getElementById("banners-tbody");

  document.getElementById("banners-count-label").textContent = allBanners.length;

  const filtered = allBanners.filter((b) => {
    if (themeFilter && b.primary_theme !== themeFilter) return false;
    if (partyFilter) {
      const hasParty = (b.parties || []).some((p) => p.party_short === partyFilter);
      if (!hasParty) return false;
    }
    if (!q) return true;
    const hay = `${b.subject_surtitre} ${b.subject_title} ${b.ticker_text} ${b.primary_theme} ${(
      b.parties || []
    )
      .map((p) => p.party_name)
      .join(" ")}`.toLowerCase();
    return hay.includes(q);
  });

  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted">Aucun bandeau correspondant.</td></tr>';
    return;
  }

  tbody.innerHTML = filtered
    .slice(0, 150)
    .map((b) => {
      const orientClass = escapeHtml(b.orientation || "neutre");
      const partiesHtml =
        (b.parties || []).length > 0
          ? (b.parties || [])
              .map(
                (p) =>
                  `<div style="margin-bottom:4px;"><strong>${escapeHtml(
                    p.party_short
                  )}</strong><br><span class="${stanceBadgeClass(p.stance)}">${escapeHtml(
                    p.stance
                  )}</span></div>`
              )
              .join("")
          : '<span class="muted">—</span>';

      return `
        <tr>
          <td style="white-space:nowrap;">${escapeHtml(formatDateTime(b.first_seen))}</td>
          <td><span class="badge-theme ${orientClass}">${escapeHtml(
            b.primary_theme || "Actualité"
          )}</span></td>
          <td>${partiesHtml}</td>
          <td>
            ${
              b.subject_surtitre
                ? `<span class="surtitre">${escapeHtml(b.subject_surtitre)}</span>`
                : ""
            }
            <strong>${escapeHtml(b.subject_title || "—")}</strong>
          </td>
          <td>${escapeHtml(b.ticker_text || "—")}</td>
          <td>${escapeHtml(b.sample_count || 1)}</td>
        </tr>
      `;
    })
    .join("");
}

function exportCsv() {
  const headers = [
    "id",
    "date_local",
    "first_seen",
    "last_seen",
    "sample_count",
    "primary_theme",
    "orientation",
    "axis_score",
    "subject_surtitre",
    "subject_title",
    "ticker_text",
  ];
  const rows = [headers.join(";")];
  allBanners.forEach((b) => {
    const vals = headers.map((h) => `"${String(b[h] ?? "").replaceAll('"', '""')}"`);
    rows.push(vals.join(";"));
  });
  const blob = new Blob(["\uFEFF" + rows.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "cnews_analyse_impartiale.csv";
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
      c: stats.political_cursor?.score,
    });
    if (sig === lastUpdatedSignature) {
      return;
    }
    lastUpdatedSignature = sig;
    allBanners = banners;

    renderPoliticalCursor(stats);
    renderPartiesStudy(stats);
    renderThemeAndKeywordCharts(stats);
    renderAiSection(stats.daily_ai_analyses || []);
    populateTableFilters(allBanners);
    renderBannersTable();
  } catch (err) {
    console.error(err);
  }
}

document.getElementById("search-input").addEventListener("input", renderBannersTable);
document.getElementById("theme-filter").addEventListener("change", renderBannersTable);
document.getElementById("party-filter").addEventListener("change", renderBannersTable);
document.getElementById("export-csv-btn").addEventListener("click", exportCsv);

refreshDashboard();
setInterval(refreshDashboard, 5000);
