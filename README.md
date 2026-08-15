# pv-explorer

> Outil citoyen pour interroger en langage naturel les procès-verbaux du Conseil communal de Schaerbeek. Recherche sémantique (RAG) + statistiques, réponses citées.

![RAG](https://img.shields.io/badge/RAG-Pinecone-4f8ef7)
![Claude](https://img.shields.io/badge/LLM-Claude-c8952b)
![FastAPI](https://img.shields.io/badge/API-FastAPI-4a7c59)
![Civic Tech](https://img.shields.io/badge/civic--tech-Schaerbeek-6d2233)

**Topics GitHub :** `rag` · `civic-tech` · `fastapi` · `pinecone` · `claude` · `schaerbeek` · `open-data` · `french`

---

Application permettant aux citoyens d'interroger l'ensemble des délibérations
du Conseil communal de Schaerbeek en langage naturel, avec réponses sourcées
et statistiques.

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Frontend   │────▶│   Backend    │────▶│  Pinecone   │
│  (Vercel)   │     │  (Railway)   │     │ (vectoriel) │
│  index.html │     │   app.py     │     │             │
└─────────────┘     └──────┬───────┘     └─────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │  Claude API  │
                    └──────────────┘
```

## Structure du dépôt

```
.
├── backend/                  → API FastAPI (déployée sur Railway/Render)
│   ├── app.py                → endpoints /ask (RAG) et /stats
│   ├── index_pv.py           → script d'indexation Pinecone (lancer 1×)
│   ├── requirements.txt
│   ├── railway.json          → config Railway
│   ├── Procfile              → config Railway/Render
│   ├── .env.example          → modèle de configuration
│   └── pv_conseil_schaerbeek.json
├── frontend/                 → interface citoyen (déployée sur Vercel)
│   ├── index.html
│   └── vercel.json
├── data/                     → copie de référence de la base
├── render.yaml               → config Render (alternative à Railway)
└── .gitignore                → protège les secrets (.env jamais committé)
```

---

## 1. Créer le dépôt GitHub et pousser le code

Depuis ce dossier (`pv_repo/`), en ligne de commande :

```bash
# Initialiser Git
git init
git add .
git commit -m "Prototype Q&R PV Schaerbeek"

# Créer le dépôt sur GitHub (2 options) :

# Option A — avec GitHub CLI (le plus simple, si 'gh' installé)
gh repo create pv-explorer --public --source=. --push

# Option B — manuellement
#   1. Va sur https://github.com/new
#   2. Nom : pv-explorer — laisse le dépôt vide (pas de README)
#   3. Puis :
git remote add origin https://github.com/TON-PSEUDO/pv-explorer.git
git branch -M main
git push -u origin main
```

> ⚠️ Le fichier `.gitignore` empêche déjà de committer tout fichier `.env`
> contenant tes clés. Vérifie avant de pousser : `git status` ne doit
> **jamais** lister de `.env`.

---

## 2. Indexer les PV dans Pinecone

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env          # puis édite .env avec tes vraies clés
set -a; source .env; set +a   # charge les variables (Linux/Mac)
python index_pv.py            # indexe Schaerbeek (défaut)
```

Clés gratuites :
- **Pinecone** : https://app.pinecone.io (tier gratuit, sans carte bancaire)
- **Anthropic** : https://console.anthropic.com

> 💡 Sans machine locale, tu peux aussi indexer via **GitHub Actions** :
> onglet *Actions → « Indexer les PV dans Pinecone » → Run workflow* (la clé est
> lue depuis le secret `PINECONE_API_KEY`). Les inputs `commune` / `input`
> permettent de choisir quoi indexer.

### Multi-commune

L'app gère plusieurs communes dans **un seul index** / **un seul namespace** :
chaque vecteur porte une métadonnée `commune`. La recherche est **croisée par
défaut** (toutes communes) ; le filtre par commune est optionnel (sélecteur du
frontend, ou champ `commune` dans la requête `/ask`).

```bash
# (Ré)indexer Schaerbeek — met à jour les vecteurs en place (ID stable),
# ajoute/actualise la métadonnée commune="schaerbeek" sans toucher aux autres.
python index_pv.py --commune schaerbeek --input pv_conseil_schaerbeek.json

# Plus tard : indexer Evere (le fichier JSON doit d'abord être produit par la
# pipeline d'extraction PDF→JSON — chantier séparé, non inclus ici).
python index_pv.py --commune evere --input pv_conseil_evere.json
```

- **Ne pas** utiliser `--reset` pour ajouter une commune : `--reset` vide **tout**
  l'index (toutes communes). L'upsert par ID est idempotent, donc une simple
  réindexation suffit.
- **Convention d'unicité** : les séances de communes différentes doivent avoir
  des `id` distincts dans leur JSON (les IDs de vecteurs ne sont pas préfixés par
  commune). La pipeline Evere doit garantir cette unicité.

> **Migration des vecteurs Schaerbeek existants** : les vecteurs indexés avant
> l'ajout du multi-commune n'ont pas le champ `commune`. Relance une fois
> l'indexation Schaerbeek (commande ci-dessus, ou le workflow Actions) pour que
> les 193 vecteurs portent `commune="schaerbeek"` et soient filtrables.

---

## 3. Déployer le backend sur Railway

1. https://railway.app → **New Project** → **Deploy from GitHub repo**
2. Sélectionne ton dépôt `pv-explorer`
3. **Settings** → **Root Directory** : `backend`
4. **Variables** → ajoute :
   - `ANTHROPIC_API_KEY`
   - `PINECONE_API_KEY`
   - `PV_JSON_PATH` = `pv_conseil_schaerbeek.json`
   - `ALLOWED_ORIGINS` = `https://pv-explorer.vercel.app` (l'URL de ton frontend)
5. Railway détecte `railway.json` et démarre automatiquement.
6. Copie l'URL publique générée (ex: `https://pv-explorer.up.railway.app`)
7. Teste : ouvre `TON-URL/health` → doit afficher `index_ok: true`

*(Alternative Render : le fichier `render.yaml` est prêt — New → Blueprint.)*

---

## 4. Déployer le frontend sur Vercel

1. Dans `frontend/index.html`, remplace `API_PROD` par ton URL Railway :
   ```js
   const API_PROD = "https://pv-explorer.up.railway.app";
   ```
2. Commit + push ce changement.
3. https://vercel.com → **Add New Project** → importe ton dépôt
4. **Root Directory** : `frontend`
5. Deploy. Ton outil est en ligne 🎉

---

## 5. Sécurité — déjà en place, à configurer

Le code est **déjà durci**. Il reste seulement à fournir les bonnes valeurs :

- **CORS restreint** : `app.py` lit la variable `ALLOWED_ORIGINS` (jamais `"*"`).
  Sur Railway, ajoute la variable avec l'URL exacte de ton frontend Vercel :
  ```
  ALLOWED_ORIGINS=https://pv-explorer.vercel.app
  ```
  (plusieurs origines possibles, séparées par des virgules). En dev, si la
  variable est absente, seul `localhost` est autorisé.
- **Rate limiting actif** (via `slowapi`) : `/ask` limité à 10/min et 100/jour,
  `/stats` à 30/min, par adresse IP. Protège ta clé API contre l'abus.
  Aucune action requise — c'est appliqué au démarrage.
- **Surveille ta consommation** sur https://console.anthropic.com

> Vérifié par test : au-delà des seuils, l'API renvoie `429 Too Many Requests`,
> et une origine non déclarée ne reçoit pas d'en-tête CORS autorisant l'accès.

---

## Coûts (usage citoyen modéré)

| Service    | Coût                          |
|------------|-------------------------------|
| Pinecone   | Gratuit (tier Starter)        |
| Vercel     | Gratuit (statique)            |
| Railway    | ~5 $/mois (ou tier gratuit)   |
| Claude API | ~0,003 $/question             |

→ Quelques centaines de questions/mois : **moins de 10 $/mois** tout compris.

---

## Développement local complet

```bash
# Terminal 1 — backend
cd backend
export $(cat .env | xargs)
uvicorn app:app --reload --port 8000

# Terminal 2 — frontend
cd frontend
python3 -m http.server 5500
# ouvre http://localhost:5500
```
