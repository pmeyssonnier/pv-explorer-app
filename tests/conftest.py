"""Configuration commune aux tests.

Rend importables les modules de `backend/` et `pipeline/` (qui ne sont pas un
package installable) et neutralise les dépendances lourdes/optionnelles dont
les FONCTIONS PURES testées ici n'ont pas besoin (pdfplumber, tqdm). Ainsi la
suite tourne vite, sans télécharger de PDF ni de clé API.
"""
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 0. PV_JSON_PATH en absolu : l'app le lit en relatif (rootDir=backend en prod) ;
#    les tests tournent depuis la racine du dépôt → on pointe le vrai fichier
#    pour que /stats et /trend fonctionnent quel que soit le répertoire courant.
os.environ.setdefault(
    "PV_JSON_PATH", str(ROOT / "backend" / "pv_conseil_schaerbeek.json")
)
# Idem pour les questions écrites, qui manquaient ici : leur chemin par défaut
# est relatif lui aussi, donc introuvable depuis la racine — la suite tournait
# sur une base AMPUTÉE de tout ce volet (aucune question écrite dans les fiches
# ni dans les séances), sans que rien ne le signale. Les tests qui veulent une
# base de questions écrites contrôlée continuent de monkeypatcher QE_JSON_PATH.
os.environ.setdefault(
    "QE_JSON_PATH", str(ROOT / "backend" / "questions_ecrites_schaerbeek.json")
)

# 1. Rendre backend/ et pipeline/ importables (modules à plat, pas de package).
for sub in ("backend", "pipeline"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

# 2. Stubs de secours pour pdfplumber/tqdm — SEULEMENT s'ils ne sont pas
#    installés. pv_extraction_pipeline les importe au chargement ; ce sont
#    maintenant de vraies dépendances de backend/requirements.txt (nécessaires
#    à services/pv_integration.py, pas seulement au pipeline), donc installées
#    en CI comme en prod — le stub ne sert plus que si un environnement local
#    ne les a pas installées mais veut quand même tester les fonctions PURES
#    du pipeline (extract_pdf_metadata, _coerce_sp, etc.).
try:
    import pdfplumber  # noqa: F401
except ImportError:
    sys.modules["pdfplumber"] = types.ModuleType("pdfplumber")
try:
    import tqdm  # noqa: F401
except ImportError:
    tqdm_mod = types.ModuleType("tqdm")
    tqdm_mod.tqdm = lambda iterable=None, **kw: iterable
    sys.modules["tqdm"] = tqdm_mod
