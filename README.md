# Observatoire des bandeaux CNews (100 % gratuit, sans carte bancaire)

Programme complet pour capturer automatiquement les bandeaux à l'écran sur **CNews** (en ignorant les publicités), stocker l'historique dans un tableur **Google Sheets**, générer une **analyse quotidienne par Gemini** (API gratuite Google AI Studio) et afficher les statistiques sur une **page web simple hébergée gratuitement sur GitHub Pages**.

---

## Comment le programme lit l'écran et évite les publicités

1. **Acquisition vidéo sans contourner de DRM** : le script interroge l'API publique Dailymotion de CNews (`x3b68jn` / secours `x5idxxp`) et télécharge uniquement les derniers segments vidéo HLS (`<= 720p`, sans audio).
2. **Filtre anti-publicité en 4 niveaux** (calibré sur des captures réelles `1280x720`) :
   - **Logo `C NEWS` en haut à gauche** (`y: 4.8%-9%`, `x: 4.5%-13.8%`) : vérifie la présence du carré noir `[C]` + rectangle rouge `[NEWS]` et confirme via OCR (`NEWS` / `DIRECT`). Pendant une publicité ou un jingle, ce badge disparaît.
   - **Géométrie stricte du bas d'écran** : isole le bandeau noir du fil info (`y: 87%-99.5%`, `x: 0%-82.5%`), le cartouche d'émission (`x: 82.5%-100%`) et le bandeau sujet/citation (`y: 73.5%-87.2%`) uniquement lorsqu'une barre blanche/rouge de synthé est présente (pour ne jamais lire le décor du plateau).
   - **Filtrage des mentions légales publicitaires** (`l'énergie est notre avenir`, `mangerbouger`, `pour votre santé`, etc.).
3. **OCR français hors ligne (`RapidOCR` ONNX PP-OCRv5 Latin)** : transcrit avec précision les accents (`é`, `à`, `ç`, `ê`) sans carte graphique ni PyTorch, puis déduplique les textes consécutifs identiques.

---

## Limite honnête du 100 % gratuit sans carte bancaire

- **Tableau de bord web (GitHub Pages)** : **en ligne 24h/24, gratuit, jamais en veille, aucune carte requise.**
- **Google Sheets + Gemini API (Google AI Studio)** : **100 % gratuit en France, aucune carte requise.**
- **Capture automatique en ligne (GitHub Actions)** : fonctionne gratuitement sur dépôt public **par échantillonnage périodique** (toutes les 15 minutes via `cron`), pas en flux continu 24/7 ininterrompu. De plus, les serveurs GitHub étant souvent aux États-Unis, le direct CNews sur Dailymotion peut parfois être géobloqué hors de France : si cela arrive, utilisez simplement `run_local.bat` sur votre PC en France pour capturer en continu tout en envoyant les données vers votre Google Sheet en ligne.

---

## Mise en ligne gratuite en 4 étapes (zéro carte bancaire)

### Étape 1 — Créer votre clé gratuite Gemini (Google AI Studio)
1. Allez sur [Google AI Studio](https://aistudio.google.com/) avec votre compte Google.
2. Cliquez sur **Get API key** $\rightarrow$ **Create API key**.
3. Copiez la clé (`GEMINI_API_KEY`). *(Aucune carte bancaire demandée.)*

### Étape 2 — Préparer le tableur Google Sheets
1. Créez une nouvelle feuille sur [Google Sheets](https://sheets.google.com).
2. Ouvrez **Extensions $\rightarrow$ Apps Script**, collez tout le contenu de `google_apps_script/Code.gs`.
3. (Optionnel) Dans **Paramètres du projet $\rightarrow$ Propriétés du script**, ajoutez `WEBHOOK_SECRET` (un mot de passe de votre choix).
4. Cliquez sur **Déployer $\rightarrow$ Nouveau déploiement $\rightarrow$ Application Web** :
   - *Exécuter en tant que* : **Moi**
   - *Qui a accès* : **Tout le monde** (protégé par votre `WEBHOOK_SECRET` si défini)
5. Copiez l'URL du déploiement (`GOOGLE_APPS_SCRIPT_URL`).

### Étape 3 — Déployer sur GitHub (capture périodique + page web gratuite)
1. Créez un compte gratuit sur [GitHub](https://github.com) et un dépôt **public** (les minutes GitHub Actions et GitHub Pages sont gratuites et sans carte bancaire sur dépôt public).
2. Poussez le dossier `cnews-bandeau/` dans ce dépôt.
3. Dans **Settings $\rightarrow$ Secrets and variables $\rightarrow$ Actions $\rightarrow$ New repository secret**, ajoutez :
   - `GEMINI_API_KEY` : votre clé Google AI Studio
   - `GOOGLE_APPS_SCRIPT_URL` : l'URL Apps Script copiée à l'étape 2
   - `SHEETS_WEBHOOK_SECRET` : (optionnel) votre mot de passe
4. Dans **Settings $\rightarrow$ Pages**, choisissez **Deploy from a branch**, branche `main`, dossier `/docs`, puis **Save**.
5. Votre page web simple sera accessible à `https://<votre-pseudo>.github.io/<nom-du-depot>/` et se mettra à jour automatiquement !

### Étape 4 — (Optionnel) Lancer en continu depuis votre PC en France
Double-cliquez sur `run_local.bat` (après avoir copié `.env.example` en `.env` avec vos clés).

Commandes utiles :
```bash
# Capture ponctuelle (comme GitHub Actions)
python -m src.main --once

# Surveillance continue en local
python -m src.main --watch --interval 5

# Bilan statistique + analyse quotidienne Gemini
python -m src.main --analyze-daily
```
