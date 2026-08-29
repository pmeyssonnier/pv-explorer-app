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

L'app a cinq onglets — quatre publics (Questions, Séances, Par élu·e, Statistiques) et un réservé à l'admin, masqué tant qu'on n'est pas connecté·e.

**💬 Questions (chat RAG)** — questions en langage naturel, réponses **sourcées** (date + numéro de point) rendues en **Markdown** (tableaux, listes, gras). Trois types de source citée : une délibération (lien vers le PDF officiel), un **débat filmé** (lien **« ▶ voir le débat »** vers l'instant exact de la vidéo, source `video_conseil`), ou une **question écrite** (Q/R hors séance, source `question_ecrite`, réponse repliable). Filtre par commune et par **année** détectée dans la question. Réponses **exportables** (copier / `.md`), **partageables** (lien profond), et posables **à la voix** (dictée). Commande `//lex …`, réservée à une session admin connectée, pour enrichir à chaud le lexique de recherche (synonymes, glossaire).

**📅 Séances** — parcourt les procès-verbaux **séance par séance** : pour chaque point de l'ordre du jour, sa **rubrique**/sous-rubrique d'origine, son titre, un **résumé** en une phrase, qui l'a déposé, qui y a répondu (répondant·e·s multiples affiché·e·s côte à côte), l'issue (approuvé, décidé, reporté, retiré…), le montant engagé et les thématiques. Filtres à facettes cumulables (type de point, intervenant·e, thématique) qui se recalculent les uns les autres ; lien direct vers le PDF (à la bonne page, si connue) et vers le débat filmé quand il existe.

**👤 Par élu·e** — recherche par nom (combobox), historique complet de ce qu'une personne a **déposé** et à quoi elle a **répondu** au fil des séances et des questions écrites. Rôle **courant à la date affichée** (conseiller·ère communal·e / échevin·e / bourgmestre), déduit des mandats déclaratifs (`elus_mandats.json`) — un échevin·e reste conseiller·ère par défaut. Puces de rôle et de législature cumulables, qui partitionnent exactement l'effectif affiché (chaque personne comptée une seule fois).

**📊 Statistiques** — trois sous-onglets sous un **périmètre commun** (*Activité par année → par mois*), qui reste affiché au-dessus d'eux et survit au changement de vue. **Activité par année** : les **4 KPI** (séances, points traités, votes disputés, montants engagés) — **liés aux puces du graphe juste en dessous** : isoler un type (point délibératif/motion/question orale/demande) recalcule aussi les 4 tuiles sur ce seul type —, l'activité citoyenne par type (questions orales, demandes, motions, questions écrites, débats filmés hors PV), l'**issue des points** (approuvé, décidé, reporté, retiré, autres) et les **thématiques**, tous recalculés au périmètre affiché. **Procès-verbaux** : la liste groupée par année (récent d'abord), chaque PV lié à son **PDF officiel** sur `1030.be` ; la pastille du sous-onglet annonce combien le périmètre en contient. **Évolution d'un thème (budget)** : les montants cumulés par année sur tous les points liés à un mot-clé. **Cascade** : cliquer un PV affine les indicateurs à cette séance ; cliquer une thématique filtre les PV concernés. Un lien partagé rouvre la vue affichée (`?tab=stats&vue=pv`).

**📈 Évolution d'un thème** (`/trend`) — agrégation exhaustive des montants **par année** sur tous les points liés à un thème (complémentaire à la recherche sémantique), avec liens vers les PV.

**🔐 Admin** (connexion protégée, un seul compte) — intègre un **nouveau PV** ou une **question écrite** à partir d'un PDF uploadé : extraction par Claude, aperçu de la fusion, puis **publication** (commit direct sur GitHub + réindexation Pinecone de ce seul document) — jamais automatique, toujours après confirmation explicite. Gère les **mandats des élu·e·s** (ajout, édition, suppression avec confirmation) et le **lexique** de recherche. Chaque intégration tourne en tâche de fond avec suivi de progression (extraction longue = plusieurs appels Claude).

**⚙️ Options** — thème **clair / sombre / auto**, et réglages de recherche (sources affichées, étendue `TOP_K`, seuil de pertinence `SCORE_MIN`, modèle, ordre des sources) mémorisés par navigateur (localStorage) et **re-bornés côté serveur**. Numéro de version affiché.

**🗂️ Carte du code** ([`docs/carte-code.html`](docs/carte-code.html)) — pour qui développe sur ce dépôt : inventaire triable/filtrable des fichiers (zone, taille, lignes, dépendances, dernière modification git) et les structures de données de l'app (séance, point de l'ordre du jour, question écrite, élu·e). Fichier statique, s'ouvre directement dans un navigateur, sans serveur.

## Structure du dépôt

```
.
├── backend/                     → API FastAPI (déployée sur Render)
│   ├── app.py                   → point de montage (app + limiter + CORS + routers)
│   ├── config.py                → constantes (modèle, RAG, VERSION, INDEX_NAME), CORS, logger
│   ├── limiter.py               → rate limiter slowapi partagé
│   ├── models/api.py            → schémas Pydantic (requêtes/réponses)
│   ├── prompts/rag.py           → system prompt du chat
│   ├── lexique_store.py         → lexique éditable (synonymes/glossaire), enrichi à chaud par l'admin
│   ├── utils/                   → text.py · dates.py · statut.py (3 dimensions d'un point) · video.py
│   ├── services/                → rag.py · statistics.py · elus.py · seances.py · auth.py ·
│   │                               pv_integration.py · questions_ecrites.py · questions_ecrites_integration.py ·
│   │                               github_publish.py (commits admin) · jobs.py (tâches de fond) · video_merge.py ·
│   │                               pinecone_service.py
│   ├── services/people/         → attribution.py (qui a déposé/répondu) · mandats.py (rôle à une date) ·
│   │                               names.py (normalisation) · registry.py (index par personne)
│   ├── routers/                 → health.py (/health /ready) · ask.py (/ask) ·
│   │                               stats.py (/stats /trend /elus /elu/{key} /seances /seance/{date}) ·
│   │                               admin.py (login, intégration PV/QE, lexique, mandats)
│   ├── index_pv.py              → indexation Pinecone des PV + chapitrage vidéo
│   ├── index_qe.py              → indexation Pinecone des questions écrites (même index, 3e source_type)
│   ├── reindex_points.py        → réindexation CIBLÉE (quelques points, pas tout l'index)
│   ├── voir_audits.py           → audits hors-ligne de la base (complétude…) en une commande
│   ├── requirements.txt         → versions épinglées
│   ├── Procfile · railway.json  → config Render / Railway
│   ├── .env.example             → modèle de configuration
│   └── *.json                   → pv_conseil_schaerbeek.json (PV) · questions_ecrites_schaerbeek.json ·
│                                   elus_mandats.json (mandats déclaratifs) · video_conseil_schaerbeek.json ·
│                                   video_sessions.json · lexique.json
├── frontend/                    → interface citoyen (déployée sur Vercel)
│   ├── index.html               → structure des 5 onglets (Questions, Séances, Par élu·e, Statistiques, Admin)
│   ├── js/                      → un module par fonctionnalité — app.js (câblage), config.js (API_URL),
│   │                               chat.js · seances.js · elus.js · stats.js · admin.js · lexique.js ·
│   │                               combobox.js (recherche partagée) · settings.js · share.js · utils.js …
│   ├── styles.css               → identité visuelle + thème clair / sombre
│   └── vercel.json              → en-têtes de sécurité + CSP
├── scraping/                    → téléchargement des PV (Google Colab) — voir son README
│   ├── pv_scraper_1030.py       → Schaerbeek (1030.be)
│   ├── pv_scraper_evere.py      → Evere (publi.irisnet.be / Editoria)
│   └── README.md                → savoir de scraping + chaîne de bout en bout
├── pipeline/                    → extraction PDF → JSON, audits et backfills (Colab pour l'extraction,
│                                   scripts déterministes du dépôt pour le reste — sans PDF ni LLM)
│   ├── pv_extraction_pipeline.py            → extraction PV PDF → JSON (Claude)
│   ├── questions_ecrites_extraction_pipeline.py → extraction question écrite PDF → JSON (Claude)
│   ├── extract_video_chapters.py            → chapitrage vidéo (YouTube @1030be)
│   ├── audit_completeness.py                → audit de complétude hors-ligne (sans LLM)
│   └── backfill_*.py, reextract_targeted.py, split_statut_decision.py, patch_multi_communes.py…
├── tests/                       → suite pytest (fonctions pures + routes HTTP)
├── e2e/                         → smoke test frontend (Playwright/Chromium headless)
├── docs/carte-code.html         → carte du code : inventaire triable/filtrable + structures de données
├── render.yaml                  → Blueprint Render (déploiement backend)
├── ruff.toml · pytest.ini       → lint + config de tests
├── .github/workflows/           → CI (ruff + pytest + smoke test) et indexation Pinecone
└── .gitignore                   → protège les secrets (.env jamais committé)
```

---

## 0. Clés & variables d'environnement

Récapitulatif de tout ce qu'il faut fournir au clonage du dépôt. **2 vraies
clés secrètes suffisent pour le chat + les statistiques** ; les autres ne
servent qu'au panneau **Admin** (intégrer un PV/une question écrite depuis
l'app) — laisse-les vides si tu n'en as pas besoin, la connexion admin sera
simplement désactivée.

### Backend (Render / local via `.env` — voir `backend/.env.example`)

| Variable | Type | Rôle | Où l'obtenir / valeur |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | 🔒 secret | Réponses Claude (`/ask`) | https://console.anthropic.com |
| `PINECONE_API_KEY` | 🔒 secret | Recherche vectorielle + indexation | https://app.pinecone.io |
| `ALLOWED_ORIGINS` | réglage | Origines CORS autorisées | URL(s) exacte(s) du frontend, séparées par des virgules |
| `PV_JSON_PATH` | réglage | Chemin du JSON des PV | défaut `pv_conseil_schaerbeek.json` |
| `SCORE_MIN` | réglage (optionnel) | Seuil de pertinence | défaut `0.0` (désactivé) |

> Sur **Render**, les secrets sont marqués `sync:false` dans `render.yaml`
> → à **saisir à la main** dans le dashboard. En **local**, copie le modèle :
> `cp backend/.env.example backend/.env` puis remplis-le (jamais committé).

### Panneau Admin (optionnel — un seul compte, voir `backend/services/auth.py`)

| Variable | Type | Rôle | Où l'obtenir / valeur |
|---|---|---|---|
| `ADMIN_USERNAME` | 🔒 secret | Identifiant du compte admin | choisi à la main |
| `ADMIN_PASSWORD_HASH` | 🔒 secret | Mot de passe admin (jamais stocké en clair) | généré une fois : `cd backend && python3 -c "from services.auth import hash_password; print(hash_password('TON_MOT_DE_PASSE'))"` |
| `ADMIN_JWT_SECRET` | 🔒 secret | Signe le cookie de session admin | généré une fois : `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `GITHUB_TOKEN` | 🔒 secret | Publie les intégrations admin (PV/question écrite/lexique/mandats) directement sur GitHub | token *fine-grained* avec droit d'écriture (Contents) sur ce dépôt |
| `GITHUB_REPO` | réglage (optionnel) | Dépôt cible des commits admin | défaut `pmeyssonnier/pv-explorer-app` |
| `GITHUB_BRANCH` | réglage (optionnel) | Branche cible des commits admin | défaut `main` |

### Prérequis Pinecone (pas des variables — à créer côté service)

- Un index nommé **`pv-explorer`**, namespace **`pv`**, embeddings intégrés
  **`multilingual-e5-large`** (le nom d'index est la constante `INDEX_NAME`
  dans `backend/config.py`).
- **Peupler** l'index avec `python backend/index_pv.py` (PV + vidéo) et
  `python backend/index_qe.py` (questions écrites) — voir §1. Les données
  (`pv_conseil_schaerbeek.json`, `questions_ecrites_schaerbeek.json`,
  `video_conseil_schaerbeek.json`, `video_sessions.json`) sont **déjà dans le
  dépôt** — rien à re-télécharger.

### Frontend (Vercel) — **aucune clé**

- Le frontend est statique. Seul ajustement, **dans le code** (pas un secret) :
  `API_PROD` dans `frontend/js/config.js` doit pointer vers l'URL de ton backend
  (détection auto de `localhost` en dev, aucun changement à faire en local).

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

### Questions écrites

Même index/namespace, un 3e `source_type` (`"question_ecrite"`) — indexé à
part avec son propre script :

```bash
python index_qe.py --commune schaerbeek --input questions_ecrites_schaerbeek.json
```

Publier une question écrite depuis le panneau **Admin** l'indexe déjà (upsert
d'un seul document) ; ce script sert à (ré)indexer tout le corpus en une fois.

### Réindexation ciblée (après une correction de données)

Corriger quelques points dans `pv_conseil_schaerbeek.json` (une coquille dans
un nom, un montant) ne justifie pas de tout ré-embedder — `reindex_points.py`
n'envoie que ce qui a réellement changé, calculé depuis git :

```bash
python reindex_points.py --depuis <ref-git-d-avant-la-correction> --dry-run   # aperçu
python reindex_points.py --depuis <ref-git-d-avant-la-correction>             # envoie
python reindex_points.py --id PV-2020-10-28_SP139 --verifier                  # ou une liste d'ID explicite
```

---

## 2. Déployer le backend sur Render

Via le Blueprint `render.yaml` (déjà configuré : tier gratuit, health check,
`--proxy-headers` pour un rate limiting par IP correct derrière le proxy) :

1. https://render.com → connexion GitHub → **New + → Blueprint**
2. Sélectionne le dépôt `pv-explorer-app` → Render lit `render.yaml`
3. Render invite à saisir **6 secrets** (`sync: false` dans `render.yaml`) :
   `ANTHROPIC_API_KEY`, `PINECONE_API_KEY` (obligatoires), et `ADMIN_USERNAME`,
   `ADMIN_PASSWORD_HASH`, `ADMIN_JWT_SECRET`, `GITHUB_TOKEN` pour le panneau
   **Admin** — laisse ces 4 derniers vides si tu ne veux pas ce panneau, la
   connexion admin est alors simplement désactivée (`PV_JSON_PATH` et
   `ALLOWED_ORIGINS` sont déjà remplis dans le Blueprint).
4. **Apply** → Render build et déploie (tier gratuit : le service s'endort après
   ~15 min d'inactivité, ~50 s au réveil).
5. Teste la vivacité `https://TON-SERVICE.onrender.com/health` → `{"status":"ok"}`
   (minimal), et la disponibilité `.../ready` → vérifie l'index Pinecone (statut +
   nombre de vecteurs, ~12 700 avec PV + vidéo + questions écrites de Schaerbeek).

*(Alternative Railway : Root Directory `backend`, mêmes variables, `railway.json`
détecté automatiquement.)*

---

## 3. Déployer le frontend sur Vercel

1. Dans `frontend/js/config.js`, `API_PROD` pointe vers l'URL du backend Render :
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
- **Rate limiting** (`slowapi`) : `/ask` 10/min & 100/jour, `/stats` 30/min,
  `/admin/login` 5/min (anti brute-force), par IP (uvicorn lancé avec
  `--proxy-headers --forwarded-allow-ips='*'` pour que l'IP réelle soit vue
  derrière le proxy Render/Railway).
- **Session admin** : cookie signé (HMAC-SHA256, `ADMIN_JWT_SECRET`), mot de
  passe jamais stocké en clair (PBKDF2-HMAC-SHA256 salé, 600 000 itérations),
  comparaison en temps constant (`hmac.compare_digest`).
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
