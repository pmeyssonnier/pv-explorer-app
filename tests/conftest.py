"""Configuration commune aux tests.

Rend importables les modules de `backend/` et `pipeline/` (qui ne sont pas un
package installable) et neutralise les dépendances lourdes/optionnelles dont
les FONCTIONS PURES testées ici n'ont pas besoin (pdfplumber, tqdm). Ainsi la
suite tourne vite, sans télécharger de PDF ni de clé API.
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 1. Rendre backend/ et pipeline/ importables (modules à plat, pas de package).
for sub in ("backend", "pipeline"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

# 2. Stubs des dépendances lourdes non requises par les fonctions pures.
#    pv_extraction_pipeline importe pdfplumber et tqdm au chargement ; on les
#    remplace par des modules factices pour pouvoir tester extract_pdf_metadata,
#    _coerce_sp, etc. sans les installer.
if "pdfplumber" not in sys.modules:
    sys.modules["pdfplumber"] = types.ModuleType("pdfplumber")
if "tqdm" not in sys.modules:
    tqdm_mod = types.ModuleType("tqdm")
    tqdm_mod.tqdm = lambda iterable=None, **kw: iterable
    sys.modules["tqdm"] = tqdm_mod
