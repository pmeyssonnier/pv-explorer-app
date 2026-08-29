---
name: inserer-pv-qe
description: Intègre un PV de séance ou une question écrite (QE) donné·e par URL ou fichier PDF, sans passer par le pipeline Colab (qui a besoin d'une clé ANTHROPIC_API_KEY que les sessions Claude Code n'ont pas). Utilise ce skill quand l'utilisateur demande d'insérer, intégrer ou publier un PV/une question écrite dont il/elle donne l'URL ou le fichier.
---

# Insérer un PV ou une question écrite (hors pipeline Colab)

## Pourquoi ce skill existe

Le flux normal (`pipeline/pv_extraction_pipeline.py` /
`pipeline/questions_ecrites_extraction_pipeline.py`, lancés depuis Colab, ou
`backend/services/pv_integration.py` / `questions_ecrites_integration.py` via
le panneau admin) appelle l'API Anthropic (`ANTHROPIC_API_KEY`) pour
l'extraction. Une session Claude Code n'a pas cette clé — mais elle est
elle-même Claude : elle peut lire le PDF directement et faire l'extraction
« à la main », à condition de suivre EXACTEMENT les mêmes règles que le
pipeline pour rester cohérent avec les ~10 000 points déjà en base.

Ne jamais réinventer ces règles de mémoire : toujours (re)lire les fichiers
sources ci-dessous avant d'extraire, elles évoluent avec le temps.

## Étapes

### 1. Obtenir le PDF et identifier le type

- URL fournie → télécharger (si l'environnement a accès réseau ; sinon
  demander le fichier à l'utilisateur).
- Fichier local fourni → l'utiliser tel quel.
- PV de séance (« pv-conseil-... ») vs question écrite (numéro/année +
  auteur·e, un seul objet par document) — le nom de fichier et le contenu le
  disent en général sans ambiguïté.

### 2. Relire les règles d'extraction AVANT de lire le PDF

- PV : `pipeline/pv_extraction_pipeline.py`, variable `SYSTEM_PROMPT` (~L480)
  — trois listes de personnes distinctes (`auteurs`/`intervenants`/
  `repondants`, JAMAIS déduites l'une de l'autre), statuts
  APPROUVÉ/REJETÉ/RETIRÉ/REPORTÉ, thématiques, montants, etc.
- QE : `pipeline/questions_ecrites_extraction_pipeline.py`, variable
  `SYSTEM_PROMPT` (~L162) — numéro/année/date/auteur·e/répondant·e/titre/
  question/réponse, langue distincte question/réponse.
- Ne jamais deviner un champ absent (ex. numéro de QE illisible) : le laisser
  `null`/absent plutôt qu'inventer une valeur plausible — un extracteur qui
  invente a déjà causé une perte de données silencieuse par le passé (voir
  historique git PR #199).

### 3. Construire le dict Python et le normaliser avec les fonctions du pipeline

Ne jamais recopier à la main la logique de nettoyage (casse des noms,
dédoublonnage, parsing des votes/montants) : importer et appeler les
fonctions pures existantes, qui n'ont besoin d'aucune clé API.

```python
import sys
sys.path.insert(0, "pipeline")

# PV : un point à la fois
from pv_extraction_pipeline import normalize_point, merge_seance_into_db
point = normalize_point({"sp": 1, "titre": "...", "auteurs": [...], ...})

# QE : une question
from questions_ecrites_extraction_pipeline import normalize_question
question = normalize_question({"numero": ..., "date": "...", "auteur": "...", ...}, "nom_fichier.pdf")
```

Pour un PV, assembler ensuite la structure de séance complète :

```python
seance_struct = {
    "seance": {
        "id": "PV-2026-06-24", "date": "2026-06-24",
        "source_file": "pv-conseil-2026.06.24-sp.pdf",
        "source_url": "https://www.1030.be/data/media/document/pv-conseil-2026.06.24-sp.pdf",
        "extracted_at": None, "heure_ouverture": None, "heure_cloture": None,
        "president": None, "bourgmestre": None, "bourgmestre_ff": None,
        "secretaire_communal": None, "presents_count": None,
        "excuses": [], "absents": [],
    },
    "points": [point1, point2, ...],
}
```

Utiliser `enrich_seance_meta(seance_struct, first_pages)` (même fichier) si
le texte des premières pages est disponible, pour remplir
président/bourgmestre/secrétaire — sinon laisser `None`, ce n'est jamais
bloquant (voir usage réel de ces champs dans
`backend/services/people/attribution.py`).

### 4. Vérifier qu'il n'y a pas déjà une entrée pour cette date/ce numéro

```python
import json
db = json.load(open("backend/pv_conseil_schaerbeek.json"))
existing = next((s for s in db["seances"] if s["seance"]["date"] == seance_struct["seance"]["date"]), None)
```

Pour une QE, même vérification sur `backend/questions_ecrites_schaerbeek.json`
via `id` (`f"QE-{annee}-{numero:03d}"`). Une entrée existante = une
correction, pas une nouvelle insertion — dans ce cas, confirmer avec
l'utilisateur avant d'écraser quoi que ce soit.

### 5. Fusionner, committer, tester, ouvrir une PR — jamais de merge automatique

- PV : `merge_seance_into_db(db, seance_struct)` (même fichier), puis
  recalculer `db["meta"]["seances_incluses"]` et `db["meta"]["total_points"]`
  comme le fait `backend/services/pv_integration.publish_seance`.
- QE : ajouter/remplacer l'entrée dans `db["questions"]`.
- Écrire le JSON (`json.dumps(db, ensure_ascii=False, indent=2)`) dans
  `backend/pv_conseil_schaerbeek.json` ou
  `backend/questions_ecrites_schaerbeek.json`.
- Lancer la suite de tests complète (`python3 -m pytest -q`) — en particulier
  les gardes-fous d'intégrité déjà en place (ex. `tests/test_mandats.py`
  n'est pas concerné ici, mais toute régression ailleurs doit être prise au
  sérieux).
- Suivre le workflow Git standard de ce dépôt (voir CLAUDE.md / instructions
  de session) : commit en français sur la branche désignée, push, PR avec
  Contexte/Changements/Test plan — **jamais de merge automatique**, la
  relecture humaine avant merge est la règle pour toutes les données de ce
  dépôt, en particulier une extraction faite « à la main » sans le
  pipeline habituel.

### 6. Après le merge (rappel à l'utilisateur, pas une étape automatique)

La réindexation Pinecone reste manuelle (`reindex_points.py` ou
l'indexation complète depuis le notebook Colab) — c'est déjà la pratique
établie pour toutes les insertions de ce dépôt, cette PR ne change rien à ce
sujet.

## Limites connues

- Sans accès réseau sortant dans l'environnement, l'URL ne peut pas être
  téléchargée directement — demander le fichier PDF à l'utilisateur à la
  place.
- Une extraction manuelle par lecture directe du PDF est plus lente qu'un
  appel API en un coup, mais suit la même grille de lecture (SYSTEM_PROMPT) —
  ne jamais accélérer en sautant la relecture des règles à l'étape 2.
