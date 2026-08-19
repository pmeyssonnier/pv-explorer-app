# pv-explorer

> Outil citoyen pour interroger en langage naturel les procès-verbaux du Conseil communal de Schaerbeek (et, à terme, d'autres communes bruxelloises). Recherche sémantique (RAG) + statistiques, réponses citées.

[![CI](https://github.com/pmeyssonnier/pv-explorer-/actions/workflows/ci.yml/badge.svg)](https://github.com/pmeyssonnier/pv-explorer-/actions/workflows/ci.yml)
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

**💬 Chat (RAG)** — questions en langage naturel, réponses **sourcées** (date + numéro de point) rendues en **Markdown** (tableaux, listes, gras). Filtre par commune et par **année** détectée dans la question. Réponses **exportables** (copier / `.md`).

**📊 Statistiques** — exploration par **drill-down** : *Activité par année → par mois*. Les **4 KPI** (séances, points, votes, montants engagés) et les **thématiques** (avec le montant engagé par thème) se recalculent au périmètre affiché. **Cascade** : cliquer un PV affine les indicateurs à cette séance ; cliquer une thématique filtre les PV concernés. **Liste des procès-verbaux** groupée par année (récent d'abord), chaque PV lié à son **PDF officiel** sur `1030.be` (`source_url`).

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
│   └── PV_Schaerbeek_scrapper.ipynb   → notebook d'orchestration (Colab)
├── tests/                    → suite pytest (fonctions pures + routes HTTP)
├── render.yaml               → Blueprint Render (déploiement backend)
├── ruff.toml · pytest.ini    → lint + config de tests
├── .github/workflows/        → CI (ruff + pytest) et indexation Pinecone
└── .gitignore                → protège les secrets (.env jamais committé)
```

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
```

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
