# Scraping & pipeline d'extraction des PV

Ce dossier (et `../pipeline/`) regroupe les outils qui alimentent la base de
données interrogée par l'app : **télécharger les PV** des sites communaux, puis
les **convertir en JSON structuré** (via Claude) au format attendu par
`backend/index_pv.py`.

```
PDF communaux ──scraping──▶ Google Drive ──pipeline (Claude)──▶ pv_conseil_<commune>.json
                                                                      │
                                                                      ▼
                                              backend/index_pv.py ─▶ Pinecone ─▶ app
```

## Fichiers

| Fichier | Rôle |
|---|---|
| `scraping/pv_scraper_1030.py` | Télécharge les PV de **Schaerbeek** (1030.be) → Drive |
| `scraping/pv_scraper_evere.py` | Télécharge les PV d'**Evere** (publi.irisnet.be / Editoria) → Drive |
| `pipeline/pv_extraction_pipeline.py` | **PDF → JSON** structuré (pdfplumber + Claude), fusion + cache + backups |
| `pipeline/patch_multi_communes.py` | Étend la pipeline au **multi-commune** (champ `commune` par séance) |

## ⚠️ Pourquoi Google Colab (et pas cet environnement / une VM cloud)

Les sites communaux bruxellois **bloquent les IP de datacenter** (réponses 403).
Depuis **Google Colab**, l'IP sortante est une IP Google, acceptée. **Tous ces
scripts sont conçus pour tourner dans Colab**, avec Google Drive monté pour
persister les PDF et le JSON. Ils ne fonctionneront pas depuis un runner
GitHub/Render ni depuis l'environnement Claude Code (accès réseau restreint).

Cellules Colab type :
```python
!pip install requests beautifulsoup4 selenium tqdm pdfplumber anthropic --quiet
from google.colab import drive; drive.mount('/content/drive')
```

## Savoir acquis par commune (pièges confirmés)

### Schaerbeek — `1030.be`
- Page : `https://www.1030.be/fr/proces-verbaux` — les liens PDF sont chargés
  **en JavaScript** (d'où Selenium) **et** l'IP cloud est bloquée (d'où Colab).
- **Patterns URL confirmés — 2 emplacements coexistent** :
  - `/data/media/import/pv_conseil_YYYY.MM.DD_sp.pdf` (underscores)
  - `/data/media/import/PV%20Conseil%20YYYY.MM.DD.pdf` (espaces URL-encodés, PV anciens ~2017)
  - `/data/media/document/pv-conseil-YYYY.MM.DD.pdf` (tirets, avec ou sans `-sp` / `-sp_N`)

  La date garde toujours ses **points** (`YYYY.MM.DD`). Le scraper sonde ces
  formes ; `is_pv_pdf()` reconnaît les trois nomenclatures.
- Le scraper combine deux méthodes : (A) Selenium pour lire les liens JS,
  (C) **sondage proactif** d'URLs reconstruites par pattern (le Conseil siège en
  général le **dernier mercredi du mois**, hors juillet/août ; exceptions dans
  `CONFIRMED_DATES`). `probe_url()` rejette les « 404 douces » (HTTP 200 + HTML)
  en vérifiant le content-type PDF **et** la taille.

### Evere — `publi.irisnet.be` (plateforme régionale **Editoria**)
- Le site communal `evere.brussels` bloque les IP cloud (403) ; **Editoria**
  répond depuis Colab.
- **Navigation en arbre par `vipKey`** :
  Organisation (préfixe `O`) → Catégories année (préfixe `C`) → Catégories
  séance (préfixe `C`, liens `<a class="click-show-content" data-bk="C…">`) →
  fichiers via `/web/download?pubKey=P…` (préfixe `P`).
- **Clé de découverte des années** (catégorie « Conseil communal ») :
  `C02f81f14-c560-48f0-93c5-b41fd3e4d591` → `discover_year_keys()` trouve
  automatiquement les années futures.
- **FR et NL partagent le même `pubKey`** → dédupliquer par `pubKey`.
- **Années 2015–2019 : VIDES** sur la plateforme (archives non publiées).
- **Formats de dates incohérents selon l'année** (piège majeur) :
  | Année | Format observé | Exemple |
  |---|---|---|
  | 2020 | `YYYY.MM.DD` | `2020.11.26` |
  | 2021 | `DD-MM-YYYY` | `28-01-2021` |
  | 2022 | `MM-DD-YYYY` (US !) | `01-27-2022` |
  | 2023 | `YYYY-MM-DD` | `2023-01-26` |
  | 2024+ | `YYYY.MM.DD` | `2024.01.25` |
  `extract_date_any()` désambiguïse : si le 1ᵉʳ nombre > 12 → c'est le jour
  (DD-MM) ; si le 2ᵉ > 12 → mois-jour US ; sinon on suppose l'européen DD-MM.
  → **Années 2021–2023 à re-scraper** avec cette logique corrigée.

### Saint-Josse — `sjtn.brussels` (à faire)
- HTML **statique** simple (pages *agenda-politique* et *conseil-communal*).
- Filtres : `include r"/(pv_conseil_|pv_\d{2}\.\d{2}\.\d{2,4})"` ;
  `exclude r"(necp|college)"` — `necp` = notes explicatives (ordre du jour) ;
  `procesverbalpublic_college` = PV du **collège** (autre organe, à exclure).
- Dates `DD.MM.YY` et `DD.MM.YYYY` mélangées.
- ~7 PV identifiés.

### Bruxelles-Ville — **bloquée** (piste ouverte)
- WAF « edsh02 » : bloque **même** via Selenium / undetected-chromedriver.
- Pistes : `opendata.bruxelles.be`, ou demande de **transparence
  administrative** (l'art. 89 de la Nouvelle Loi Communale oblige la
  publication des PV).

## Pipeline d'extraction PDF → JSON

`pipeline/pv_extraction_pipeline.py` :
- `pdfplumber` extrait le texte, découpé en **chunks de 12 pages** ;
- chaque chunk est envoyé à **Claude** (`claude-haiku-4-5` par défaut, bon
  rapport coût/qualité — Sonnet possible) avec un **system prompt** qui impose
  le schéma JSON d'un point (voir `backend/pv_conseil_schaerbeek.json` →
  `meta.schema_point`) ;
- **cache SHA-256** (pas de double appel API), **backups rotatifs**, écriture
  **atomique**, reprise via `progress.json`, `validate_database()` + `export_csv()`.
- Coût indicatif (≈10 500 pages) : **Haiku ≈ 42 $**, Sonnet ≈ 157 $.
- La clé API est lue depuis `ANTHROPIC_API_KEY` (env) — **jamais** en dur.

```python
import os; os.environ["ANTHROPIC_API_KEY"] = "sk-ant-…"   # dans Colab, pas dans le dépôt
from pv_extraction_pipeline import run_pipeline, validate_database, stats_summary
db = run_pipeline()            # ou run_pipeline(max_files=2, dry_run=True) pour tester
validate_database(db); stats_summary(db)
```

## Multi-commune : comment ça s'articule avec l'app déployée

⚠️ **Design retenu (déployé)** : **un seul index Pinecone, un seul namespace
`pv`**, chaque vecteur porte une **métadonnée `commune`**. Recherche croisée
toutes communes par défaut, filtre optionnel par commune.
(≠ l'ancienne idée « un namespace par commune », abandonnée.)

`pipeline/patch_multi_communes.py` s'aligne sur ce design :
- `detect_commune()` déduit la commune du chemin `input/<commune>/xxx.pdf` ;
- il ajoute **`seance["commune"]`** dans le JSON et **préfixe l'`id`** de séance
  (`evere-PV-2024-01-25`) pour l'unicité inter-communes ;
- il fusionne par clé **`(commune, date)`** (deux communes peuvent siéger le
  même jour) ;
- **idempotent** : l'original des fonctions est capturé une seule fois
  (`hasattr` sur `pipe._process_pdf_original`) — sinon double exécution =
  `RecursionError` (bug déjà rencontré et corrigé).

Côté indexation, **`backend/index_pv.py` lit `seance["commune"]` par séance**
(repli sur l'argument `--commune`). Donc :
- **JSON mono-commune** (ex. Schaerbeek actuel, sans champ `commune`) :
  `python index_pv.py --commune schaerbeek --input pv_conseil_schaerbeek.json`
- **JSON multi-commune fusionné** (produit par le patch, chaque séance taguée) :
  `python index_pv.py --input pv_conseil_all.json` → chaque vecteur prend la
  commune de sa séance.

> Note migration : les séances Schaerbeek historiques ont des `id` **non
> préfixés** (`PV-2025-12-17`). Si un jour tu régénères Schaerbeek via la
> pipeline (qui préfixe en `schaerbeek-PV-…`), les `id` de vecteurs changent —
> fais alors une réindexation `--reset` pour éviter les doublons.

## Chaîne complète (de bout en bout)

1. **Scraper** (Colab) : `run_scraper()` / `run_scraper_evere()` → PDF dans
   `Drive/PV_Schaerbeek/input/<commune>/`.
2. **Pipeline** (Colab) : appliquer `patch_multi_communes.py` puis
   `run_pipeline()` → `pv_conseil_<…>.json`.
3. **Déposer** le JSON dans `backend/` du dépôt, committer. C'est la seule
   copie utilisée (par `app.py`, `index_pv.py` et `render.yaml`).
4. **Indexer** : lancer le workflow GitHub Actions *« Indexer les PV »*
   (inputs `commune` / `input`) — voir `.github/workflows/index-pinecone.yml`.
5. L'app (stats + recherche) couvre alors les nouvelles séances.

## Sécurité

- **Jamais** de `.env` ni de clé API dans un commit (le `.gitignore` bloque
  `.env`, `*.key`, etc. — vérifier `git status` avant push).
- La clé Anthropic de la pipeline passe **uniquement** par la variable
  d'environnement `ANTHROPIC_API_KEY` (dans Colab).
