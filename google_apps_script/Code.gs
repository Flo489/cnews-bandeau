/**
 * Google Apps Script a coller dans votre Google Sheet (Extensions > Apps Script).
 * Permet :
 *  1) De recevoir automatiquement les bandeaux et analyses via webhook (doPost)
 *  2) Optionnellement, d'executer l'analyse quotidienne Gemini directement depuis Google Sheets
 *
 * Proprietes du script a configurer (Parametres du projet > Proprietes du script) :
 *  - GEMINI_API_KEY : votre cle gratuite Google AI Studio (https://aistudio.google.com/)
 *  - WEBHOOK_SECRET : (optionnel) meme mot de passe que SHEETS_WEBHOOK_SECRET
 */

function ensureSheets_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  let bandeaux = ss.getSheetByName("Bandeaux");
  if (!bandeaux) {
    bandeaux = ss.insertSheet("Bandeaux");
    bandeaux.appendRow([
      "ID",
      "Date",
      "Premiere capture",
      "Derniere capture",
      "Occurrences",
      "Emission",
      "Surtitre",
      "Bandeau Sujet",
      "Fil Info",
      "Source"
    ]);
    bandeaux.setFrozenRows(1);
  }

  let analyses = ss.getSheetByName("Analyses_IA");
  if (!analyses) {
    analyses = ss.insertSheet("Analyses_IA");
    analyses.appendRow([
      "Date",
      "Modele Gemini",
      "Bandeaux analyses",
      "Resume de la journee",
      "Themes principaux",
      "Sujets marquants",
      "Observation editoriale",
      "Genere le"
    ]);
    analyses.setFrozenRows(1);
  }

  return { bandeaux, analyses };
}

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    const props = PropertiesService.getScriptProperties();
    const expectedSecret = props.getProperty("WEBHOOK_SECRET") || "";

    if (expectedSecret && body.secret !== expectedSecret) {
      return ContentService.createTextOutput(
        JSON.stringify({ ok: false, error: "Secret invalide" })
      ).setMimeType(ContentService.MimeType.JSON);
    }

    const { bandeaux, analyses } = ensureSheets_();

    if (body.action === "append_banner" && body.record) {
      const r = body.record;
      bandeaux.appendRow([
        r.id || "",
        r.date_local || "",
        r.first_seen || "",
        r.last_seen || "",
        r.sample_count || 1,
        r.show_name || "",
        r.subject_surtitre || "",
        r.subject_title || "",
        r.ticker_text || "",
        r.source_id || ""
      ]);
      return ContentService.createTextOutput(
        JSON.stringify({ ok: true, type: "banner" })
      ).setMimeType(ContentService.MimeType.JSON);
    }

    if (body.action === "append_analysis" && body.analysis) {
      const a = body.analysis;
      analyses.appendRow([
        a.date || "",
        a.model_used || "",
        a.banners_analyzed || 0,
        a.resume_journee || "",
        JSON.stringify(a.themes_principaux || []),
        (a.sujets_marquants || []).join(" | "),
        a.observation_editoriale || "",
        a.generated_at || ""
      ]);
      return ContentService.createTextOutput(
        JSON.stringify({ ok: true, type: "analysis" })
      ).setMimeType(ContentService.MimeType.JSON);
    }

    return ContentService.createTextOutput(
      JSON.stringify({ ok: false, error: "Action inconnue" })
    ).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(
      JSON.stringify({ ok: false, error: String(err) })
    ).setMimeType(ContentService.MimeType.JSON);
  }
}

/**
 * Option alternative : declencher l'analyse Gemini du jour directement depuis Google Sheets
 * (Declencheurs > Ajouter un declencheur > 1 fois par jour vers minuit).
 */
function analyserJourneeAvecGemini() {
  const props = PropertiesService.getScriptProperties();
  const apiKey = props.getProperty("GEMINI_API_KEY");
  if (!apiKey) {
    throw new Error("Ajoutez GEMINI_API_KEY dans les Proprietes du script.");
  }

  const { bandeaux, analyses } = ensureSheets_();
  const rows = bandeaux.getDataRange().getValues();
  if (rows.length <= 1) return;

  const today = Utilities.formatDate(new Date(), "Europe/Paris", "yyyy-MM-dd");
  const lines = [];
  for (let i = 1; i < rows.length; i++) {
    const rowDate = String(rows[i][1] || "").slice(0, 10);
    if (rowDate === today) {
      const show = rows[i][5] || "";
      const sujet = rows[i][7] || "";
      const fil = rows[i][8] || "";
      lines.push(`- [${show}] Sujet: ${sujet} | Fil: ${fil}`);
    }
  }
  if (lines.length === 0) return;

  const model = "gemini-3.5-flash";
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${apiKey}`;
  const prompt =
    `Resume objectivement en JSON (champs: resume_journee, themes_principaux, sujets_marquants, observation_editoriale) ` +
    `ces ${lines.length} bandeaux CNews du ${today} :\n` +
    lines.slice(0, 120).join("\n");

  const resp = UrlFetchApp.fetch(url, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({
      contents: [{ parts: [{ text: prompt }] }],
      generationConfig: { temperature: 0.2, responseMimeType: "application/json" }
    }),
    muteHttpExceptions: true
  });

  if (resp.getResponseCode() === 200) {
    const parsedJson = JSON.parse(resp.getContentText());
    const text = parsedJson.candidates[0].content.parts[0].text;
    const analysis = JSON.parse(text);
    analyses.appendRow([
      today,
      model,
      lines.length,
      analysis.resume_journee || "",
      JSON.stringify(analysis.themes_principaux || []),
      (analysis.sujets_marquants || []).join(" | "),
      analysis.observation_editoriale || "",
      new Date().toISOString()
    ]);
  }
}
