# pv-explorer

> Outil citoyen pour interroger en langage naturel les procès-verbaux du Conseil communal de Schaerbeek (et, à terme, d'autres communes bruxelloises). Recherche sémantique (RAG) + statistiques, réponses citées.

[![CI](https://github.com/pmeyssonnier/pv-explorer-app/actions/workflows/ci.yml/badge.svg)](https://github.com/pmeyssonnier/pv-explorer-app/actions/workflows/ci.yml)
![RAG](https://img.shields.io/badge/RAG-Pinecone-4f8ef7)
![Claude](https://img.shields.io/badge/LLM-Claude-c8952b)
![FastAPI](https://img.shields.io/badge/API-FastAPI-4a7c59)
![Civic Tech](https://img.shields.io/badge/civic--tech-Schaerbeek-6d2233)

**Topics GitHub :** `rag` · `civic-tech` · `fastapi` · `pinecone` · `claude` · `schaerbeek` · `open-data` · `french`

---

Application permettant aux citoyens d'interroger les délibérations du Conseil
communal en langage naturel, avec réponses sourcées et statistiques.

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Frontend   │────▶│   Backend    │────▶│  Pinecone   │
│  (Vercel)   │     │   (Render)   │     │ (vectoriel) │
│  index.html │     │   app.py     │     │             │
└─────────────┘     └──────┬───────┘     └─────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │  Claude API  │
                    └──────────────┘
```

**Déploiement actuel** : backend sur **Render** (`pv-explorer-api.onrender.com`),
frontend sur **Vercel** (`pv-explorer.vercel.app`). *(Railway reste une
alternative — `railway.json` est fourni.)*

## Fonctionnalités

**💬 Chat (RAG)** — questions en langage naturel, réponses **sourcées** (date + numéro de point) rendues en **Markdown** (tableaux, listes, gras). Filtre par commune et par **année** détectée dans la question. Réponses **exportables** (copier / `.md`), **partageables** (lien profond), et posables **à la voix** (dictée). Les sources incluent, quand elles existent, un lien **« ▶ voir le débat »** vers l'instant exact de la vidéo du conseil (source `video_conseil`).

**📊 Statistiques** — trois vues sous un **périmètre commun** (*Activité par année → par mois*), qui reste affiché au-dessus d'elles et survit au changement de vue. **Activité par année** : les **4 KPI** (séances, points, votes, montants engagés), l'activité globale, l'**activité citoyenne** par type (questions orales, demandes, motions, questions écrites, débats filmés hors PV), l'**issue des points** (approuvé, décidé, reporté, retiré, autres) et les **thématiques** — tous recalculés au périmètre affiché. **Procès-verbaux** : la liste groupée par année (récent d'abord), chaque PV lié à son **PDF officiel** sur `1030.be` (`source_url`) ; la pastille du sous-onglet annonce combien le périmètre en contient. **Évolution d'un thème (budget)** : les montants cumulés par année sur tous les points liés à un mot-clé. **Cascade** : cliquer un PV affine les indicateurs à cette séance ; cliquer une thématique filtre les PV concernés. Un lien partagé rouvre la vue affichée (`?tab=stats&vue=pv`).

**📈 Évolution d'un thème** (`/trend`) — agrégation exhaustive des montants **par année** sur tous les points liés à un thème (complémentaire à la recherche sémantique), avec liens vers les PV.

**⚙️ Options** — thème **clair / sombre / auto**, et réglages de recherche (sources affichées, étendue `TOP_K`, seuil de pertinence `SCORE_MIN`, modèle, ordre des sources) mémorisés par navigateur (localStorage) et **re-bornés côté serveur**. Numéro de version affiché.

## Structure du dépôt

```
.
├── backend/                  → API FastAPI (déployée sur Render)
│   ├── app.py                → point de montage (app + limiter + CORS + routers)
│   ├── config.py             → constantes (modèle, RAG : TOP_K/MAX_SOURCES/SCORE_MIN, VERSION), CORS, logger
│   ├── limiter.py            → rate limiter slowapi partagé
│   ├── models/api.py         → schémas Pydantic (requêtes/réponses)
│   ├── prompts/rag.py        → system prompt
│   ├── utils/                → text.py (normalisation) · dates.py (filtre année)
│   ├── services/             → rag.py · statistics.py · pinecone_service.py
│   ├── routers/              → health.py (/health, /ready) · ask.py (/ask) · stats.py (/stats, /trend)
│   ├── index_pv.py           → indexation Pinecone (--commune, --input, --only-year)
│   ├── requirements.txt      → versions épinglées
│   ├── Procfile · railway.json → config Render / Railway
│   ├── .env.example          → modèle de configuration
│   └── pv_conseil_schaerbeek.json  → base des PV (lue par /stats et /trend)
├── frontend/                 → interface citoyen (déployée sur Vercel)
│   ├── index.html            → structure (chat, statistiques, menu ⚙️ Options)
│   ├── app.js                → logique (RAG, drill-down stats, cascade, thème, export)
│   ├── styles.css            → identité visuelle + thème clair / sombre
│   └── vercel.json           → en-têtes de sécurité + CSP
├── scraping/                 → téléchargement des PV (Google Colab) — voir son README
│   ├── pv_scraper_1030.py    → Schaerbeek (1030.be)
│   ├── pv_scraper_evere.py   → Evere (publi.irisnet.be / Editoria)
│   └── README.md             → savoir de scraping + chaîne de bout en bout
├── pipeline/                 → extraction PDF → JSON + audit (Colab)
│   ├── pv_extraction_pipeline.py      → extraction PDF → JSON (Haiku)
│   ├── audit_completeness.py          → audit de complétude hors-ligne (sans LLM)
│   ├── reextract_targeted.py          → re-extraction ciblée des séances à trous
│   ├── patch_multi_communes.py
│   ├── PV_Schaerbeek_scrapper.ipynb   → notebook d'orchestration (Colab)
│   ├── extract_video_chapters.py      → chapitrage vidéo (YouTube @1030be)
│   └── extract_video_chapters.ipynb   → notebook Colab (script à jour, télécharge et exécute)
├── tests/                    → suite pytest (fonctions pures + routes HTTP)
├── e2e/                      → smoke test frontend (Playwright/Chromium headless)
├── render.yaml               → Blueprint Render (déploiement backend)
├── ruff.toml · pytest.ini    → lint + config de tests
├── .github/workflows/        → CI (ruff + pytest + smoke test) et indexation Pinecone
└── .gitignore                → protège les secrets (.env jamais committé)
```

---

## 0. Clés & variables d'environnement

Récapitulatif de tout ce qu'il faut fournir au clonage du dépôt. **Seules 2
vraies clés secrètes** sont nécessaires ; le reste, ce sont des réglages.

### Backend (Render / local via `.env` — voir `backend/.env.example`)

| Variable | Type | Rôle | Où l'obtenir / valeur |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | 🔒 secret | Réponses Claude (`/ask`) | https://console.anthropic.com |
| `PINECONE_API_KEY` | 🔒 secret | Recherche vectorielle + indexation | https://app.pinecone.io |
| `ALLOWED_ORIGINS` | réglage | Origines CORS autorisées | URL(s) exacte(s) du frontend, séparées par des virgules |
| `PV_JSON_PATH` | réglage | Chemin du JSON des PV | défaut `pv_conseil_schaerbeek.json` |
| `SCORE_MIN` | réglage (optionnel) | Seuil de pertinence | défaut `0.0` (désactivé) |

> Sur **Render**, les deux secrets sont marqués `sync:false` dans `render.yaml`
> → à **saisir à la main** dans le dashboard. En **local**, copie le modèle :
> `cp backend/.env.example backend/.env` puis remplis-le (jamais committé).

### Prérequis Pinecone (pas des variables — à créer côté service)

- Un index nommé **`pv-explorer`**, namespace **`pv`**, embeddings intégrés
  **`multilingual-e5-large`** (le nom d'index est la constante `INDEX_NAME`
  dans `backend/config.py`).
- **Peupler** l'index avec `python backend/index_pv.py` (voir §1). Les données
  (`pv_conseil_schaerbeek.json`, `video_conseil_schaerbeek.json`,
  `video_sessions.json`) sont **déjà dans le dépôt** — rien à re-télécharger.

### Frontend (Vercel) — **aucune clé**

- Le frontend est statique. Seul ajustement, **dans le code** (pas un secret) :
  `API_PROD` en haut de `frontend/app.js` doit pointer vers l'URL de ton backend.

### GitHub Actions (CI)

| Workflow | Secret requis |
|---|---|
| `ci.yml` (ruff + pytest + smoke test frontend) | **aucun** |
| `index-pinecone.yml` (indexation depuis Actions) | `PINECONE_API_KEY` — *Settings → Secrets and variables → Actions* (uniquement si tu indexes via GitHub plutôt qu'en local/Colab) |

> ⚠️ **Ne committe jamais ces clés** (le `.gitignore` bloque `.env`). Au clonage,
> **génère des clés neuves** et révoque toute clé qui aurait pu être exposée.

---

## 1. Indexer les PV dans Pinecone

En local :

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env          # puis édite .env avec tes vraies clés
set -a; source .env; set +a   # charge les variables (Linux/Mac)
python index_pv.py            # indexe Schaerbeek (défaut)
```

Clés gratuites : **Pinecone** (https://app.pinecone.io, sans carte) ·
**Anthropic** (https://console.anthropic.com).

> 💡 **Sans machine locale** : indexe via **GitHub Actions** — onglet
> *Actions → « Indexer les PV dans Pinecone » → Run workflow* (la clé est lue
> depuis le secret `PINECONE_API_KEY`). Les inputs `commune` / `input`
> choisissent quoi indexer.

### Multi-commune

Un seul index Pinecone, un seul namespace `pv` ; chaque vecteur porte une
métadonnée `commune`. Recherche **croisée par défaut** (toutes communes),
filtre par commune optionnel (sélecteur du frontend, ou champ `commune` dans la
requête `/ask`). `index_pv.py` lit `seance["commune"]` par séance (repli sur
`--commune`).

```bash
# (Ré)indexer Schaerbeek — upsert idempotent (ID stable)
python index_pv.py --commune schaerbeek --input pv_conseil_schaerbeek.json

# Plus tard : Evere (le JSON doit d'abord être produit par la pipeline — voir scraping/README.md)
python index_pv.py --commune evere --input pv_conseil_evere.json

# Chapitrage vidéo des conseils (débats filmés → liens « ▶ voir le débat »).
# JSON produit par pipeline/extract_video_chapters.py ; upsert idempotent,
# détecté automatiquement (source_type "video_conseil").
python index_pv.py --commune schaerbeek --input pv_video_conseil_schaerbeek.json
```

> 🎬 **Chapitrage vidéo (débats filmés)** : le JSON ci-dessus est produit par
> `pipeline/extract_video_chapters.py`, à lancer dans **Colab** (YouTube y est
> joignable, contrairement à certains sites qui bloquent les IP cloud) :
>
> [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/pmeyssonnier/pv-explorer-app/blob/main/pipeline/extract_video_chapters.ipynb)
>
> Le notebook télécharge et exécute toujours la **dernière version** du
> script depuis le dépôt (aucune copie à tenir à jour). Il produit deux
> fichiers à committer dans `backend/` : `video_conseil_schaerbeek.json`
> (chapitres, lu directement par le backend — pas de réindexation requise
> pour l'onglet « Par élu·e ») et `video_sessions.json` (liens « ▶ vidéo »).

- **Ne pas** utiliser `--reset` pour ajouter une commune : il vide **tout**
  l'index. L'upsert par ID est idempotent → une simple réindexation suffit.
- **Unicité** : les séances de communes différentes doivent avoir des `id`
  distincts (la pipeline préfixe l'id par la commune, ex. `evere-PV-2024-01-25`).

Pour produire les données de nouvelles communes (scraping + extraction PDF→JSON),
voir **[`scraping/README.md`](scraping/README.md)**.

---

## 2. Déployer le backend sur Render

Via le Blueprint `render.yaml` (déjà configuré : tier gratuit, health check,
`--proxy-headers` pour un rate limiting par IP correct derrière le proxy) :

1. https://render.com → connexion GitHub → **New + → Blueprint**
2. Sélectionne le dépôt `pv-explorer-app` → Render lit `render.yaml`
3. Renseigne les variables « secret » : `ANTHROPIC_API_KEY`, `PINECONE_API_KEY`
   *(`PV_JSON_PATH` et `ALLOWED_ORIGINS` sont dans le Blueprint)*
4. **Apply** → Render build et déploie (tier gratuit : le service s'endort après
   ~15 min d'inactivité, ~50 s au réveil).
5. Teste la vivacité `https://TON-SERVICE.onrender.com/health` → `{"status":"ok"}`
   (minimal), et la disponibilité `.../ready` → vérifie l'index Pinecone (statut +
   nombre de vecteurs, ~8 700 pour Schaerbeek).

*(Alternative Railway : Root Directory `backend`, mêmes variables, `railway.json`
détecté automatiquement.)*

---

## 3. Déployer le frontend sur Vercel

1. Dans `frontend/app.js`, `API_PROD` pointe vers l'URL du backend Render :
   ```js
   const API_PROD = "https://pv-explorer-api.onrender.com";
   ```
2. https://vercel.com → **Add New… → Project** → importe le dépôt
3. **Root Directory** : `frontend` · Framework **Other** (statique) → **Deploy**
4. Récupère l'URL Vercel (ex. `https://pv-explorer.vercel.app`).

---

## 4. Sécurité

Le code est durci ; il reste à fournir les bonnes valeurs :

- **CORS restreint** : `app.py` lit `ALLOWED_ORIGINS` (jamais `"*"`). Sur Render,
  c'est fixé dans `render.yaml` à l'URL Vercel de production (plusieurs origines
  possibles, séparées par des virgules). En dev, si absent → seul `localhost`.
- **Rate limiting** (`slowapi`) : `/ask` 10/min & 100/jour, `/stats` 30/min, par
  IP (uvicorn lancé avec `--proxy-headers --forwarded-allow-ips='*'` pour que
  l'IP réelle soit vue derrière le proxy Render/Railway).
- **Longueur de question bornée** (max 500) et **messages d'erreur génériques**
  côté client (pas de fuite d'exception).
- **Surveille ta consommation** sur https://console.anthropic.com
- **Jamais** de `.env`/clé dans un commit (le `.gitignore` bloque `.env`,
  `*.key`… — vérifie `git status` avant push).

---

## Coûts (usage citoyen modéré)

| Service    | Coût                          |
|------------|-------------------------------|
| Pinecone   | Gratuit (tier Starter)        |
| Vercel     | Gratuit (statique)            |
| Render     | Gratuit (s'endort à l'inactivité) ou ~7 $/mois (always-on) |
| Claude API | ~0,003 $/question             |

→ Quelques centaines de questions/mois : **moins de 10 $/mois** tout compris.
Extraction PDF→JSON (one-shot) : ≈ 42 $ Haiku pour ~10 500 pages.

---

## Développement local complet

```bash
# Terminal 1 — backend
cd backend
set -a; source .env; set +a
uvicorn app:app --reload --port 8000

# Terminal 2 — frontend
cd frontend
python3 -m http.server 5500
# ouvre http://localhost:5500
```

### Smoke test frontend (Playwright)

Vérifie en Chromium headless que l'app charge sans erreur console, que les 4
onglets publics, le thème, le login admin et le panneau Options réagissent, et
qu'il n'y a pas de débordement horizontal en mobile (390px). Ne teste pas les
données — complète pytest, ne le remplace pas.

```bash
cd e2e
npm ci
npx playwright install --with-deps chromium   # une seule fois
npx playwright test
```

Lance lui-même un backend (`uvicorn`, données locales uniquement — aucune clé
API requise) et le frontend statique avec la CSP de production ; voir
`e2e/playwright.config.js`.
