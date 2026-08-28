"""
╔══════════════════════════════════════════════════════════════════════════╗
║  PIPELINE EXTRACTION PV CONSEIL COMMUNAL SCHAERBEEK → JSON              ║
║  Google Colab - Python 3.10+                          VERSION 2.4        ║
╚══════════════════════════════════════════════════════════════════════════╝

CHANGELOG v2.4 (fusion de deux forks parallèles du v2.2) :
  Réunit deux jeux de changements DÉVELOPPÉS EN PARALLÈLE sur le v2.2 :

  Fork A — qualité des données & date de repli :
    - extract_seance_date_from_text : repli déterministe qui déduit la date
      depuis le CONTENU (mois FR+NL, accents perdus, texte collé) quand le nom
      de fichier ne porte pas de date exploitable. (Comblait un trou promis en
      commentaire mais jamais codé.)
    - Couche de normalisation (normalize_point & co) : nettoie la sortie LLM —
      parse montants "65.000 € TVAC"→float, coerce bool/int, dédoublonne les
      intervenants, structure le vote. Réduit la surface d'hallucination/format.
    - _set_meta_date factorisé (nom de fichier ET contenu suivent les mêmes règles).

  Fork B — traçabilité & complétude :
    - GREFFE 1 : `page` par point (le LLM reporte la balise [Page N]) BORNÉE à
      l'intervalle réel du chunk (_fix_point_pages, anti-hallucination) ; chaque
      point porte aussi `source_file` → lien vérifiable vers le PV d'origine.
    - GREFFE 2 : complétude déterministe. expected_sp_from_pages compte les
      points attendus par regex (ancre `SP n.-`, sans LLM) ; verify_completeness
      compare au LLM ; si des SP manquent et RECOVER_MISSING=True,
      _recover_missing_points relance le LLM page par page (borné). Rapport
      stocké (`seance.extraction_check`) + agrégé par completeness_report(db).
    - export_csv expose page + source_file.

  Point de fusion clé : normalize_point CONSERVE désormais `page` (sinon la
  reconstruction du point l'aurait supprimé, neutralisant la GREFFE 1).

CHANGELOG v2.2 (robustesse PV denses) :
  - MAX_TOKENS 4096 → 8192 (chunks denses ne tronquent plus le JSON)
  - CHUNK_SIZE 12 → 8 (réponses JSON plus fiables)
  - split-on-failure : si un chunk échoue (JSON tronqué/invalide après
    MAX_RETRIES), on le découpe récursivement en deux jusqu'à la page unique.
    Plus aucun point n'est perdu silencieusement sur un PV dense.
  - sp normalisé en int (_coerce_sp) + tris robustes (_sp_key) : Claude renvoie
    parfois sp en texte ("12"), ce qui plantait dédup/tri (str vs int).

CHANGELOG v2.1 (corrections code review Opus 4.8) :
  - FIX #1 : client API instancié dans run_pipeline() (plus au niveau module)
  - FIX #2 : logger initialisé paresseusement (plus de crash à l'import)
  - FIX #3 : variable `raw` toujours définie avant le bloc except
  - FIX #4 : shutil importé en tête de fichier
  - FIX #5 : cache basé sur SHA-256 (plus MD5)
  - FIX #6 : commentaire modèle corrigé (claude-haiku-4-5)
  - FIX #7 : enrich_seance_meta extrait les noms depuis le regex (plus de hardcode)
  - DESIGN : rotation des backups + écriture atomique

COÛT (10 500 pages) : Haiku ≈ $42 | Sonnet ≈ $157
"""

import os
import re
import json
import time
import shutil          # FIX #4
import hashlib
import logging
import math
import unicodedata
from pathlib import Path
from datetime import datetime, date as _date
from typing import Callable, Optional

import pdfplumber
import anthropic
from tqdm import tqdm

from utils_statut import (
    classer_decision, decision_manquante, dimensions, mot_issue, poser_decision,
)

# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════
CONFIG = {
    "DRIVE_ROOT":   "/content/drive/MyDrive/PV_Schaerbeek",
    "INPUT_DIR":    "/content/drive/MyDrive/PV_Schaerbeek/input",
    "OUTPUT_DIR":   "/content/drive/MyDrive/PV_Schaerbeek/output",
    "CACHE_DIR":    "/content/drive/MyDrive/PV_Schaerbeek/cache",
    "BACKUP_DIR":   "/content/drive/MyDrive/PV_Schaerbeek/backups",
    "LOG_FILE":     "/content/drive/MyDrive/PV_Schaerbeek/pipeline.log",
    "DB_JSON_PATH": "/content/drive/MyDrive/PV_Schaerbeek/pv_conseil_schaerbeek.json",
    "PROGRESS_FILE": "/content/drive/MyDrive/PV_Schaerbeek/progress.json",

    # Laisser "" pour lire depuis la variable d'environnement ANTHROPIC_API_KEY
    "ANTHROPIC_API_KEY": "",
    "MODEL":      "claude-haiku-4-5-20251001",   # FIX #6 : nom correct
    "MAX_TOKENS": 8192,          # assez pour un chunk complet (fini les JSON tronqués)

    "CHUNK_SIZE":         8,     # chunks plus petits = réponses JSON plus fiables
    "API_DELAY_SEC":      1.5,
    "SKIP_ALREADY_DONE":  True,
    "MAX_RETRIES":        3,
    "MAX_BACKUPS":        10,     # DESIGN : rotation

    # GREFFE 2 : si des points attendus (comptés par regex) manquent après
    # extraction LLM, relance ciblée page par page pour les récupérer.
    "RECOVER_MISSING":    True,
}

# ══════════════════════════════════════════════════════════════════════════
# LOGGER (FIX #2 : init paresseuse — plus de crash à l'import)
# ══════════════════════════════════════════════════════════════════════════
_logger: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("pv_pipeline")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)
    log_path = Path(CONFIG["LOG_FILE"])
    if log_path.parent.exists():
        try:
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except OSError:
            pass
    _logger = logger
    return logger


def _attach_file_logging():
    logger = get_logger()
    log_path = Path(CONFIG["LOG_FILE"])
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers) and log_path.parent.exists():
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)

# ══════════════════════════════════════════════════════════════════════════
# CLIENT API (FIX #1 : instancié paresseusement)
# ══════════════════════════════════════════════════════════════════════════
_client: Optional[anthropic.Anthropic] = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is not None:
        return _client
    api_key = CONFIG.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or api_key.startswith("sk-ant-..."):
        raise ValueError(
            "Clé API manquante. Renseigne CONFIG['ANTHROPIC_API_KEY'] "
            "ou définis la variable d'environnement ANTHROPIC_API_KEY."
        )
    _client = anthropic.Anthropic(api_key=api_key)
    return _client

# ══════════════════════════════════════════════════════════════════════════
# EXTRACTION TEXTE PDF
# ══════════════════════════════════════════════════════════════════════════
def _set_meta_date(meta: dict, y, mo, d) -> bool:
    """Écrit une date ISO validée dans `meta`.

    Centralisé pour que la date issue du nom de fichier et celle issue du texte
    suivent exactement les mêmes règles.
    """
    try:
        _date(int(y), int(mo), int(d))          # rejette 2503-20-20, mois 18, etc.
        if not (2000 <= int(y) <= 2100):
            return False
        meta["date"] = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        meta["seance_id"] = f"PV-{meta['date']}"
        return True
    except (TypeError, ValueError):
        return False


def extract_pdf_metadata(pdf_path: Path) -> dict:
    """Déduit la date depuis le NOM de fichier, en gérant plusieurs formats
    Schaerbeek et en VALIDANT la date. Si aucune date plausible n'est trouvée,
    date=None → la pipeline prend alors la date extraite du CONTENU du PV."""
    name = pdf_path.stem
    meta = {"filename": pdf_path.name, "filepath": str(pdf_path), "date": None, "seance_id": None}

    # Ordre = du plus fiable au plus ambigu. On s'arrête à la 1re date VALIDE.
    #   1) AAAA(sep)MM(sep)JJ   ex: 2026.02.11 / 2026-02-11
    #   2) AAAAMMJJ collés      ex: 20260211
    #   3) JJMMAAAA collés      ex: 250320201830 -> 25/03/2020
    m = re.search(r"(20\d{2})[._\-](\d{1,2})[._\-](\d{1,2})", name)
    if m and _set_meta_date(meta, *m.groups()):
        return meta
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", name)
    if m and _set_meta_date(meta, *m.groups()):
        return meta
    m = re.search(r"(\d{2})(\d{2})(20\d{2})", name)
    if m and _set_meta_date(meta, m.group(3), m.group(2), m.group(1)):
        return meta
    return meta   # date=None -> repli sur date_seance (contenu) dans process_pdf


_MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11,
    "december": 12,
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def extract_seance_date_from_text(text: str) -> Optional[str]:
    """Déduit la date depuis le texte extrait du PV.

    Les PDF Schaerbeek perdent parfois les accents ou les espaces à l'extraction
    (`SEANCE DU20 SEPTEMBRE 2023`). Cette fonction tolère ces variantes et sert
    de repli déterministe quand le nom du PDF ne contient pas de date exploitable.
    """
    norm = _strip_accents(text or "").lower()
    norm = re.sub(r"\s+", " ", norm)
    meta = {"date": None, "seance_id": None}

    for m in re.finditer(
        r"\b(?:seance\s+du|vergadering\s+van)\s*(\d{1,2})\s+([a-z]+)\s+(20\d{2})",
        norm,
    ):
        month = _MONTHS.get(m.group(2))
        if month and _set_meta_date(meta, m.group(3), month, m.group(1)):
            return meta["date"]

    m = re.search(
        r"\b(?:seance|vergadering).{0,40}?(\d{1,2})[./\-](\d{1,2})[./\-](20\d{2})",
        norm,
    )
    if m and _set_meta_date(meta, m.group(3), m.group(2), m.group(1)):
        return meta["date"]
    return None


def extract_text_from_pdf(pdf_path: Path) -> list[dict]:
    log = get_logger()
    pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = (page.extract_text() or "").strip()
                pages.append({"page_num": i + 1, "text": text, "char_count": len(text)})
                # pdfplumber construit TOUS les objets Page dès l'ouverture
                # (pas paresseux) et met en cache les données de mise en page
                # de chaque page dès extract_text() (chars/mots/rects) — sans
                # ce close(), la mémoire croît linéairement avec le nombre de
                # pages et n'est libérée qu'à la sortie du `with`. Sur un PV
                # dense (ex. un vieux PV bilingue FR/NL, ~2x le texte), ça
                # suffit à dépasser le RAM du tier gratuit Render et faire
                # tuer le process en plein milieu de l'extraction — observé en
                # production. close() est sans risque ici : `page` n'est plus
                # utilisée après cette itération.
                page.close()
        log.info(f"  PDF extrait : {len(pages)} pages — {sum(p['char_count'] for p in pages):,} chars")
    except Exception as e:
        log.error(f"  Erreur extraction {pdf_path.name}: {e}")
    return pages


def chunk_pages(pages: list[dict], chunk_size: int) -> list[list[dict]]:
    return [pages[i:i + chunk_size] for i in range(0, len(pages), chunk_size)]


def _coerce_sp(point: dict) -> dict:
    """Claude renvoie parfois sp en texte ("12", "12 bis") et parfois en int.
    On normalise en int quand c'est un nombre — sinon les tris/dédup plantent
    (TypeError: '<' not supported between 'str' and 'int')."""
    sp = point.get("sp")
    if isinstance(sp, str):
        m = re.match(r"\s*(\d+)", sp)
        if m:
            point["sp"] = int(m.group(1))
    return point


def _clean_str(value, default: str = "") -> str:
    if value is None:
        return default
    return re.sub(r"\s+", " ", str(value)).strip()


def _clean_str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    seen = set()
    for item in value:
        s = _clean_str(item)
        if not s:
            continue
        key = s.lower()
        if key not in seen:
            out.append(s)
            seen.add(key)
    return out


# Casse d'affichage homogène pour les listes de PERSONNES (auteurs/
# intervenants/repondants) — un PV imprime souvent les patronymes tout en
# majuscules (« Cécile JODOGNE »), que Claude reproduit sinon tel quel.
# Dupliquée depuis backend/services/people/names._titlecase plutôt
# qu'importée : pipeline/ doit rester exécutable seule (Colab), sans
# dépendance à backend/. Même règle : « DEGREZ » → « Degrez », particules en
# minuscules (« Yvan de Beauffort », « Jean-Pierre van Gorp »).
_NAME_PARTICLES = {"de", "du", "des", "van", "von", "den", "der", "ter", "ten",
                    "la", "le", "el", "di", "da", "d'", "of"}


def _titlecase_name(name: str) -> str:
    out = []
    for i, tok in enumerate(name.split()):
        if not tok:
            continue
        low = tok.lower()
        if i > 0 and low in _NAME_PARTICLES:
            out.append(low)
        elif "-" in tok:
            out.append("-".join(w[:1].upper() + w[1:].lower() for w in tok.split("-") if w))
        else:
            out.append(tok[:1].upper() + tok[1:].lower())
    return " ".join(out)


def _clean_person_list(value) -> list[str]:
    """Comme _clean_str_list, mais recase chaque nom — jamais appliqué aux
    thématiques ou autres listes de texte libre, qui ne sont pas des noms."""
    return [_titlecase_name(s) for s in _clean_str_list(value)]


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "vrai", "oui", "yes"}
    return bool(value)


def _coerce_int_or_none(value) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", ".").strip()))
    except (TypeError, ValueError):
        return None


def _coerce_amount(value) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    # Isole le nombre au format belge ("." = milliers, "," = décimales) en
    # ignorant la devise ET les suffixes texte ("65.000 € TVAC", "1.234,56 EUR
    # HTVA"…) : sans ça, "tvac" restait collé et cassait le float → None.
    m = re.search(r"\d[\d.\s]*(?:,\d+)?", str(value))
    if not m:
        return None
    num = re.sub(r"[\s.]", "", m.group(0)).replace(",", ".")
    try:
        n = float(num)
        return n if math.isfinite(n) else None
    except ValueError:
        return None


def _normalize_vote(value) -> dict:
    vote = value if isinstance(value, dict) else {}
    vote_type = _clean_str(vote.get("type")) or None
    return {
        "type": vote_type,
        "pour": _coerce_int_or_none(vote.get("pour")),
        "contre": _coerce_int_or_none(vote.get("contre")) or 0,
        "abstentions": _coerce_int_or_none(vote.get("abstentions")) or 0,
    }


def normalize_point(point: dict) -> Optional[dict]:
    """Normalise un point produit par Claude.

    Claude peut renvoyer des champs absents, `null`, ou typés en chaîne. On
    préfère nettoyer avant la fusion plutôt que propager des formes instables
    dans la base JSON.
    """
    if not isinstance(point, dict):
        return None
    point = dict(point)
    _coerce_sp(point)
    if point.get("sp") is None:
        return None
    statut_traitement, decision, debat = classer_decision(_clean_str(point.get("decision")))
    return {
        "sp": point.get("sp"),
        # GREFFE 1 : passe-plat BRUT — la coercition/bornage se fait dans
        # _fix_point_pages (qui a le contexte du chunk et récupère "Page 7"→7).
        "page": point.get("page"),
        "urgence": _coerce_bool(point.get("urgence")),
        "type": _clean_str(point.get("type"), "point_normal") or "point_normal",
        "rubrique": _clean_str(point.get("rubrique")),
        "sous_rubrique": _clean_str(point.get("sous_rubrique")),
        "titre": _clean_str(point.get("titre")),
        "resume": _clean_str(point.get("resume")),
        # Trois listes DISTINCTES, chacune lue du texte du PV. Aucune n'est
        # dérivée d'une autre : un répondant recopié depuis les intervenants
        # (ou l'inverse) inventerait un rôle que le PV n'attribue pas. En
        # particulier, `repondants` ne vient JAMAIS de l'ancien champ
        # `repondant` — un extracteur qui ne renseigne que celui-ci produit une
        # liste vide, ce qui se voit, plutôt qu'une donnée d'origine ambiguë.
        "auteurs": _clean_person_list(point.get("auteurs")),
        "intervenants": _clean_person_list(point.get("intervenants")),
        "repondants": _clean_person_list(point.get("repondants")),
        # Les trois dimensions, séparées dès l'entrée (voir utils_statut) :
        # ce que Claude écrit dans `decision` mêle ce que le point est DEVENU
        # (« APPROUVÉ ») à la façon dont il a été TRAITÉ (« REPORTÉ », renvoyé
        # à une séance ultérieure) et à son déroulement (« DÉBAT », discuté
        # sans rien trancher). Les ranger ici, et non à la lecture, garde la
        # base homogène : un PV intégré par le panneau admin arrive sous la
        # même forme que celle qu'a produite split_statut_decision.
        "decision": decision,
        "statut_traitement": statut_traitement,
        "debat": debat,
        "vote": _normalize_vote(point.get("vote")),
        "montant_eur": _coerce_amount(point.get("montant_eur")),
        "thematiques": _clean_str_list(point.get("thematiques")),
    }


def normalize_extraction_result(data: dict) -> Optional[dict]:
    """Valide la forme minimale `{date_seance, points}` renvoyée par Claude."""
    if not isinstance(data, dict):
        return None
    points = []
    for raw_point in data.get("points") or []:
        point = normalize_point(raw_point)
        if point is not None:
            points.append(point)
    return {
        "date_seance": _clean_str(data.get("date_seance")) or None,
        "points": points,
    }


def _sp_key(point: dict):
    """Clé de tri robuste : les sp entiers d'abord (triés), les autres après."""
    sp = point.get("sp")
    return (0, sp) if isinstance(sp, int) else (1, str(sp))


# ── GREFFE 1 : traçabilité page (filet déterministe) ─────────────────────────
def _fix_point_pages(points: list[dict], chunk: list[dict]) -> list[dict]:
    """Borne le champ `page` (déjà coercé en int|None par normalize_point) à
    l'intervalle RÉEL des pages du chunk. Une page absente ou hors intervalle
    (hallucination) est ramenée à la 1re page du chunk. Garantit un `page`
    exploitable pour tout point, sans faire confiance aveuglément au modèle."""
    page_nums = [p["page_num"] for p in chunk]
    lo, hi, fallback = min(page_nums), max(page_nums), page_nums[0]
    for pt in points:
        pg = pt.get("page")
        if isinstance(pg, str):
            m = re.search(r"\d+", pg)          # tolère "Page 4", "p.4", etc.
            pg = int(m.group()) if m else None
        pt["page"] = pg if isinstance(pg, int) and lo <= pg <= hi else fallback
    return points

# ══════════════════════════════════════════════════════════════════════════
# PROMPT SYSTEM
# ══════════════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """Tu es un expert en analyse de procès-verbaux du Conseil Communal belge (Schaerbeek).
Tu dois extraire les points de l'ordre du jour à partir du texte brut de pages de PV et les structurer en JSON.

SCHÉMA JSON D'UN POINT :
{
  "sp": <int>, "page": <int>, "urgence": <bool>,
  "type": <"point_normal"|"point_urgent"|"motion"|"demande_habitant"|"question_orale">,
  "rubrique": <string>, "sous_rubrique": <string>,
  "titre": <string>, "resume": <string>,
  "auteurs": <array>, "intervenants": <array>, "repondants": <array>,
  "decision": <"DÉCIDE"|"PREND ACTE"|"APPROUVÉ"|"REJETÉ"|"PREND POUR INFORMATION"|"REPORTÉ"|"RETIRÉ"|"DÉBAT"|"MINUTE DE SILENCE">,
  "vote": {"type": <"unanimite"|"vote_nominal"|"reporte"|null>, "pour": <int|null>, "contre": <int>, "abstentions": <int>},
  "montant_eur": <float|null>, "thematiques": <array snake_case>
}

RÈGLES :
- Ne DEVINE JAMAIS une valeur absente. Si une information n'est pas écrite dans
  le texte, mets `null` (ou 0 pour contre/abstentions). N'extrais que ce qui est
  réellement présent — un montant, un nombre de voix ou une date inventés sont
  des erreurs graves. Mieux vaut `null` qu'une valeur plausible mais fausse.
- "page" = le numéro de la balise [Page N] où COMMENCE le point (son titre "SP n.-").
  Reporte l'entier N tel quel ; en cas de doute, la page du titre du point.
- "Approuvé à l'unanimité" → vote.type="unanimite"
- "Décidé par X voix contre Y et Z abstentions" → vote.type="vote_nominal"
- "La motion est rejetée" / "n'est pas adoptée" / "verworpen" (NL) →
  decision="REJETÉ". Un rejet EST une décision : le conseil s'est prononcé, il
  a dit non. Ne le note jamais "DÉBAT" — le débat est ce qui a précédé le vote,
  pas son résultat. Si le vote chiffré donne PLUS DE CONTRE QUE DE POUR (ou
  autant : à égalité, la proposition n'est pas adoptée), la décision est
  "REJETÉ", quelle que soit la formule employée par le PV.
- "Ce point est reporté" / "wordt uitgesteld" (NL) → decision="REPORTÉ",
  vote.type="reporte". Un point REPORTÉ est renvoyé à une séance ultérieure.
- "Ce point est retiré de l'ordre du jour" / "Dit punt wordt aan de agenda
  onttrokken" (NL) → decision="RETIRÉ", vote.type=null. Un point RETIRÉ est
  ÔTÉ de l'ordre du jour — statut DISTINCT de REPORTÉ, ne les confonds jamais.
- N'OMETS JAMAIS un point qui porte une ancre "SP n.-", même s'il est reporté,
  retiré, sans résumé ni décision (ex. un simple "SP 22.- Titre. Ce point est
  retiré de l'ordre du jour.") : il garde son numéro SP et doit apparaître dans
  la liste. Un point retiré/reporté a un titre mais souvent ni intervenants, ni
  montant, ni vote chiffré — mets `null`/0, jamais l'omission du point entier.
- TROIS LISTES DE PERSONNES, jamais confondues, jamais déduites l'une de l'autre :
  * "auteurs" = qui DÉPOSE le point ("Motion de Monsieur X", "Demande de Madame Y",
    "Question orale de..."). Vide pour un point délibératif, porté par le Collège.
  * "intervenants" = qui PREND LA PAROLE dans le débat.
  * "repondants" = qui RÉPOND au nom du Collège ("Réponse de Madame Z",
    "Monsieur l'Échevin W répond").
  Un nom cité sous plusieurs de ces rôles figure dans plusieurs listes — c'est
  voulu. Chaque liste ne contient QUE des noms lus dans le texte : jamais un
  répondant recopié depuis les intervenants, ni l'inverse. Liste vide si le PV
  ne dit rien, jamais `null`.
- "X et Y" désigne DEUX personnes : sépare-les en deux entrées de la liste, ne
  recolle jamais deux noms dans une seule chaîne.
- SP > 67 souvent urgences ; motions = "Motion de..." ; habitants = "Demande de..."
- Extrais montants même dans considérants (ex: "65.000 € TVAC")
- IGNORE le texte néerlandais (version NL) pour éviter doublons

RÉPONDS UNIQUEMENT en JSON valide :
{"date_seance": <"YYYY-MM-DD" ou null>, "points": [...]}
Pas de markdown, uniquement le JSON brut.
"""


def make_user_prompt(pages_chunk: list[dict], date_hint: Optional[str] = None) -> str:
    date_str = f"\nDate de séance connue : {date_hint}" if date_hint else ""
    pages_text = "\n\n---PAGE---\n\n".join(
        f"[Page {p['page_num']}]\n{p['text']}" for p in pages_chunk if p["char_count"] > 50
    )
    return f"Extrais tous les points SP présents dans ces pages de PV.{date_str}\n\nTEXTE :\n{pages_text}\n\nRetourne le JSON structuré."

# ══════════════════════════════════════════════════════════════════════════
# CACHE (FIX #5 : SHA-256)
# ══════════════════════════════════════════════════════════════════════════
def get_cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def load_from_cache(cache_key: str) -> Optional[dict]:
    cache_file = Path(CONFIG["CACHE_DIR"]) / f"{cache_key}.json"
    if cache_file.exists():
        try:
            with open(cache_file, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_to_cache(cache_key: str, data: dict):
    Path(CONFIG["CACHE_DIR"]).mkdir(parents=True, exist_ok=True)
    with open(Path(CONFIG["CACHE_DIR"]) / f"{cache_key}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ══════════════════════════════════════════════════════════════════════════
# APPEL API (FIX #3 : `raw` toujours défini)
# ══════════════════════════════════════════════════════════════════════════
def call_claude_api(user_prompt: str) -> Optional[dict]:
    log = get_logger()
    cache_key = get_cache_key(user_prompt)
    cached = load_from_cache(cache_key)
    if cached is not None:
        log.debug(f"    Cache hit : {cache_key}")
        return normalize_extraction_result(cached)

    client = get_client()
    for attempt in range(CONFIG["MAX_RETRIES"]):
        raw = ""   # FIX #3 : défini avant tout usage dans les except
        try:
            response = client.messages.create(
                model=CONFIG["MODEL"], max_tokens=CONFIG["MAX_TOKENS"],
                system=SYSTEM_PROMPT, messages=[{"role": "user", "content": user_prompt}]
            )
            # Bloc texte robuste (ne suppose pas que content[0] soit du texte)
            raw = next((b.text for b in response.content
                        if getattr(b, "type", None) == "text"), "").strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()
            # Tolère du texte avant/après le JSON (erreur "Extra data") :
            # on isole du 1er '{' au dernier '}'.
            if not raw.startswith("{"):
                i = raw.find("{")
                if i != -1:
                    raw = raw[i:]
            if not raw.endswith("}"):
                j = raw.rfind("}")
                if j != -1:
                    raw = raw[:j + 1]
            data = json.loads(raw)
            data = normalize_extraction_result(data)
            if data is None:
                raise ValueError("Réponse JSON sans structure attendue")
            save_to_cache(cache_key, data)
            time.sleep(CONFIG["API_DELAY_SEC"])
            return data
        except json.JSONDecodeError as e:
            log.warning(f"    JSON invalide (tentative {attempt+1}): {e}")
            log.debug(f"    Réponse brute (200c): {raw[:200]}")
            time.sleep(2 ** attempt)
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            log.warning(f"    Rate limit — attente {wait}s")
            time.sleep(wait)
        except anthropic.APIError as e:
            log.error(f"    Erreur API (tentative {attempt+1}): {e}")
            time.sleep(5)
        except Exception as e:
            log.error(f"    Erreur inattendue (tentative {attempt+1}): {e}")
            time.sleep(5)
    log.error(f"    Échec après {CONFIG['MAX_RETRIES']} tentatives")
    return None

# ══════════════════════════════════════════════════════════════════════════
# TRAITEMENT PDF
# ══════════════════════════════════════════════════════════════════════════
def _extract_chunk_points(chunk: list[dict], seance_date: Optional[str],
                          depth: int = 0) -> tuple[list, Optional[str]]:
    """Extrait les points d'un chunk de pages. Si l'appel API échoue
    (typiquement un JSON tronqué parce que le chunk est trop dense pour
    MAX_TOKENS), on DÉCOUPE le chunk en deux et on réessaie chaque moitié
    récursivement — jusqu'à la page unique. Ainsi aucun point n'est jamais
    perdu silencieusement, même sur les PV les plus denses.
    Retourne (points, date_seance éventuellement complétée)."""
    log = get_logger()
    result = call_claude_api(make_user_prompt(chunk, seance_date))
    if result is not None:
        if result.get("date_seance") and not seance_date:
            seance_date = result["date_seance"]
        points = _fix_point_pages(result.get("points", []), chunk)   # GREFFE 1
        return points, seance_date

    # Échec : on tente de scinder le chunk (sauf s'il ne reste qu'une page).
    if len(chunk) <= 1:
        log.warning(f"    Page {chunk[0]['page_num']} ignorée (échec API même en page unique)")
        return [], seance_date

    mid = len(chunk) // 2
    log.warning(
        f"    Chunk pages {chunk[0]['page_num']}-{chunk[-1]['page_num']} en échec — "
        f"découpage en 2 (niveau {depth+1})"
    )
    points = []
    for half in (chunk[:mid], chunk[mid:]):
        sub_points, seance_date = _extract_chunk_points(half, seance_date, depth + 1)
        points.extend(sub_points)
    return points, seance_date


# ── GREFFE 2 : complétude déterministe (regex) + récupération ciblée ─────────
RE_SP_ANCHOR = re.compile(r"SP\s*(\d+)\s*\.-", re.IGNORECASE)


# Le lexique éditable (backend/lexique.json, section « extraction ») peut AJOUTER
# des formules aux regex de statut ci-dessous SANS toucher au code : l'admin les
# enrichit via la commande « //lex retrait|report|approbation|rejet = … » (voir
# backend/lexique_store.py). La pipeline tourne AUSSI hors backend (Colab) : le
# fichier est cherché en best-effort et son absence est tolérée (on garde alors
# les seules formules en dur). Chargé une fois au chargement du module.
def _load_lexique_extraction() -> dict:
    empty = {"retrait": [], "report": [], "approbation": [], "rejet": []}
    candidates = []
    env = os.environ.get("PV_LEXIQUE_PATH")
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve().parent
    candidates += [
        here.parent / "backend" / "lexique.json",   # dépôt : pipeline/ ↔ backend/
        here / "lexique.json",
        Path("backend/lexique.json"),
        Path("lexique.json"),
    ]
    for p in candidates:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        extr = (data or {}).get("extraction") or {}
        return {k: [s for s in (extr.get(k) or []) if isinstance(s, str) and s.strip()]
                for k in empty}
    return empty


_LEX_EXTRACTION = _load_lexique_extraction()


def _loosen(phrase: str) -> str:
    """Échappe une formule libre du lexique puis assouplit espaces (→ \\s+) et
    apostrophes (droite « ' » et typographique « ’ » interchangeables) — pour
    matcher le texte brut d'un PDF quelle qu'en soit la ponctuation exacte."""
    esc = re.escape(phrase.strip())
    esc = re.sub(r"(\\\s|\s)+", r"\\s+", esc)      # runs d'espaces (échappés ou non) → \s+
    esc = re.sub(r"\\?['’]", "['’]", esc)          # apostrophe → classe
    return esc


def _with_lex(base_pattern: str, famille: str) -> str:
    """base_pattern éventuellement complété (alternation) par les formules du
    lexique pour cette famille (retrait/report/approbation/rejet). Sans lexique
    ou famille vide : le motif en dur est renvoyé inchangé."""
    extra = _LEX_EXTRACTION.get(famille) or []
    if not extra:
        return base_pattern
    return base_pattern + "".join("|" + _loosen(p) for p in extra)

# Formules (FR + NL) des DEUX statuts distincts d'un point non traité tel quel —
# mêmes cas que la règle du SYSTEM_PROMPT, mais détectés ici PAR REGEX (sans LLM)
# pour le filet de sécurité déterministe (voir _synthesize_deferred_points) : un
# tel point est souvent réduit à son seul titre + cette phrase, et le LLM l'omet
# parfois sur une page dense — on le reconstruit alors plutôt que de le perdre
# (trou dans la numérotation SP). RETIRÉ (ôté de l'ordre du jour) et REPORTÉ
# (renvoyé à une séance ultérieure) sont deux statuts DIFFÉRENTS.
RE_RETIRE = re.compile(
    _with_lex(
        r"retir[ée]\s+de\s+l['’]ordre\s+du\s+jour"
        r"|retir[ée]\s+de\s+l['’]agenda"
        r"|aan\s+de\s+agenda\s+onttrokken"
        r"|onttrokken\s+aan\s+de\s+agenda",
        "retrait",
    ),
    re.IGNORECASE,
)
# « est reporté » exigé (pas le seul mot « reporté », qui apparaît aussi dans
# des titres, ex. « Un point reporté quelconque ») — c'est la formulation réelle
# des PV : « Ce point est reporté [à une séance ultérieure] ».
RE_REPORTE = re.compile(
    _with_lex(
        r"point\s+est\s+report[ée]"
        r"|dit\s+punt\s+wordt\s+(?:\w+\s+)?uitgesteld",
        "report",
    ),
    re.IGNORECASE,
)
# Union des deux — pour « ce point est-il différé d'une manière ou d'une autre ? »
# (détection dans le filet, et découpe du titre avant la phrase de statut).
RE_DEFERRED = re.compile(f"{RE_RETIRE.pattern}|{RE_REPORTE.pattern}", re.IGNORECASE)

# Décision du Conseil captée déterministiquement quand le LLM l'a ratée sur un
# point pourtant clairement voté (marqueur « DÉCISION DU CONSEIL -=- BESLISSING
# VAN DE RAAD … approuvé … / goedgekeurd … »). Deux formes réelles :
#   • unanimité : « approuvé à l'unanimité » / « goedgekeurd met eenparigheid »
#   • nominal   : « approuvé par 26 voix contre 15 [et 3 abstentions] »
RE_APPROVED_UNANIME = re.compile(
    r"approuv[ée]s?\s+à\s+l['’]unanimit[ée]"
    r"|goedgekeurd\s+met\s+eenparigheid",
    re.IGNORECASE,
)
RE_APPROVED_VOTE = re.compile(
    r"approuv[ée]s?\s+par\s+(\d+)\s+voix\s+contre\s+(\d+)"
    r"(?:\s+et\s+(\d+)\s+abstention)?",
    re.IGNORECASE,
)
# « Approbation » / « Goedkeuring » (le suffixe d'agenda « … - Approbation »
# des points soumis au vote) traité comme SYNONYME d'« approuvé » : un point
# ainsi soumis et NON retiré/reporté/rejeté a été approuvé (vote non chiffré →
# type inconnu). Repli de PLUS BASSE priorité (voir _recover_decision_from_window) :
# retrait/report et les votes chiffrés passent avant, un rejet le bloque.
RE_APPROVED_INTENT = re.compile(
    _with_lex(r"\bapprobation\b|\bgoedkeuring\b", "approbation"), re.IGNORECASE,
)
# Rejet EXPLICITE du point, tel que le PV l'écrit (« La motion est rejetée »,
# « verworpen »). Volontairement plus étroit que RE_APPROVAL_BLOCK ci-dessous :
# celui-ci sert à BLOQUER une approbation douteuse, où le doute suffit ; ici on
# ÉCRIT une décision, et le participe passé est requis — le substantif « rejet »
# parle presque toujours du contenu du point (« Rejet de la demande de réétude
# du tunnel », qui est ce que la motion DEMANDE), pas de son sort.
RE_REJETE = re.compile(
    r"rejet[ée]{1,2}s?\b|\bnon\s+approuv|\bverworpen\b|\bniet\s+goedgekeurd"
    r"|n['’]a\s+pas\s+[ée]t[ée]\s+adopt|n['’]est\s+pas\s+adopt",
    re.IGNORECASE,
)

# Bloque le repli « Approbation → APPROUVÉ » : rejet explicite OU tournure
# négative où « approbation » n'affirme pas l'approbation du point (« sans/ni
# approbation », « refus/défavorable »). On ne présume alors rien.
RE_APPROVAL_BLOCK = re.compile(
    _with_lex(
        r"rejet[ée]s?|non\s+approuv|verworpen|niet\s+goedgekeurd"
        r"|sans\s+approbation|ni\s+approbation|refus|défavorable|zonder\s+goedkeuring",
        "rejet",
    ),
    re.IGNORECASE,
)


def _anchor_window(text: str, sp: int) -> str:
    """Texte brut compris entre l'ancre 'SP n.-' et l'ancre du point suivant
    (ou 600 caractères à défaut) — le périmètre d'un point donné dans la page."""
    m = re.search(rf"SP\s*{sp}\s*\.-", text, re.IGNORECASE)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = RE_SP_ANCHOR.search(rest)
    return rest[:nxt.start()] if nxt else rest[:600]


def _anchor_window_spanning(pages: list[dict], sp: int) -> str:
    """Fenêtre d'un point sur le texte GLOBAL (toutes les pages concaténées dans
    l'ordre) et SANS plafond de caractères : de l'ancre 'SP n.-' jusqu'à l'ancre
    suivante, même si elle est sur la page d'après. Nécessaire quand le point a
    un titre long (bilingue) ou déborde d'une page à l'autre — cas réel du
    SP 21 (2016-10-26) : titre VRT/RTBF très long + formule de retrait au-delà
    des 600 premiers caractères ET SP 22 sur la page suivante, que le fenêtrage
    mono-page/plafonné (_anchor_window) rataient tous les deux."""
    text = "\n".join(p["text"] for p in sorted(pages, key=lambda p: p.get("page_num") or 0))
    m = re.search(rf"SP\s*{sp}\s*\.-", text, re.IGNORECASE)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = RE_SP_ANCHOR.search(rest)
    # Jusqu'à l'ancre suivante ; à défaut (dernier point du PV), une fenêtre
    # généreuse (le statut d'un point tient largement dans 4000 caractères).
    return rest[:nxt.start()] if nxt else rest[:4000]


def _extract_anchor_title(window: str, sp: int) -> str:
    """Titre FR d'un point à partir du texte suivant son ancre : jusqu'au 1er
    séparateur bilingue '-=-' (le titre NL suit) ou à la formule de statut
    (retrait/report), au plus court. Espaces normalisés, borné à 300 caractères."""
    end = len(window)
    sep = window.find("-=-")
    if sep != -1:
        end = min(end, sep)
    dm = RE_DEFERRED.search(window)
    if dm:
        end = min(end, dm.start())
    return re.sub(r"\s+", " ", window[:end]).strip()[:300]


def _synthesize_deferred_points(pages: list[dict], missing_sp: list[int]) -> list[dict]:
    """Filet déterministe : pour un SP encore manquant après la récupération LLM,
    si le texte brut autour de son ancre 'SP n.-' contient une formule de retrait
    ou de report, reconstruit un point minimal avec le BON statut (RETIRÉ ou
    REPORTÉ, deux statuts distincts) plutôt que de laisser un trou dans la
    numérotation. Ne fabrique JAMAIS de point pour un SP manquant SANS cette
    preuve textuelle : un vrai point de fond manquant relève d'une ré-extraction,
    pas d'un stub inventé."""
    expected = expected_sp_from_pages(pages)
    out = []
    for sp in missing_sp:
        pg = expected.get(sp)
        # Fenêtre globale (toutes pages, sans plafond) — même robustesse que
        # _apply_deferred_status_to_extracted : titre long / débordement de page.
        window = _anchor_window_spanning(pages, sp)
        decision, vote_type = _deferred_status_from_window(window)
        if not decision:
            continue
        out.append({
            "sp": sp,
            "page": pg,
            "type": "point_normal",
            "titre": _extract_anchor_title(window, sp),
            "decision": decision,
            "vote": {"type": vote_type, "pour": None, "contre": 0, "abstentions": 0},
        })
    return out


def _deferred_status_from_window(window: str):
    """(decision, vote_type) déduit du texte brut d'un point : RETIRÉ (vote_type
    None) si retrait de l'ordre du jour, REPORTÉ ("reporte") si report, sinon
    (None, None). RETIRÉ prioritaire si les deux formules coexistent (rare) : le
    retrait est l'acte final, il prime sur un report antérieur. Utilisé pour la
    reconstruction des SP OMIS (statut différé uniquement — un point de fond
    manquant relève d'une ré-extraction, pas d'un stub)."""
    if RE_RETIRE.search(window or ""):
        return "RETIRÉ", None
    if RE_REPORTE.search(window or ""):
        return "REPORTÉ", "reporte"
    return None, None


def _recover_decision_from_window(window: str):
    """(decision, vote) complet déduit du texte brut d'un point EXTRAIT mais sans
    décision. Couvre, par PRIORITÉ DÉCROISSANTE :
      1. retrait (RETIRÉ) / report (REPORTÉ) — l'acte prime sur tout le reste ;
      1bis. rejet explicite (REJETÉ) — le conseil a dit non ; une formule
         d'approbation restée dans la fenêtre ne doit pas le recouvrir ;
      2. approbation chiffrée : vote nominal (« approuvé par X voix contre Y »)
         ou unanimité (« approuvé à l'unanimité / goedgekeurd met eenparigheid »)
         — cas SP 7/19/37 du 2010-03-31 ;
      3. approbation d'agenda : « Approbation » / « Goedkeuring » seul (le point
         a été soumis au vote et approuvé, sans détail chiffré) → APPROUVÉ, vote
         de type inconnu. Bloqué par un signal de rejet explicite.
    (None, None) si aucun marqueur fiable : on n'invente jamais."""
    w = window or ""
    if RE_RETIRE.search(w):
        return "RETIRÉ", {"type": None, "pour": None, "contre": 0, "abstentions": 0}
    if RE_REPORTE.search(w):
        return "REPORTÉ", {"type": "reporte", "pour": None, "contre": 0, "abstentions": 0}
    # Avant les repêchages d'approbation : un rejet écrit noir sur blanc prime
    # sur toute formule d'agenda (« … - Approbation ») restée dans la fenêtre.
    # Le décompte des voix n'est pas inventé — le PV l'écrit rarement dans la
    # même phrase, et le champ `vote` a ses propres règles.
    if RE_REJETE.search(w):
        return "REJETÉ", {"type": None, "pour": None, "contre": 0, "abstentions": 0}
    m = RE_APPROVED_VOTE.search(w)
    if m:
        return "APPROUVÉ", {
            "type": "vote_nominal",
            "pour": int(m.group(1)), "contre": int(m.group(2)),
            "abstentions": int(m.group(3) or 0),
        }
    if RE_APPROVED_UNANIME.search(w):
        return "APPROUVÉ", {"type": "unanimite", "pour": None, "contre": 0, "abstentions": 0}
    # Repli le plus bas : « Approbation »/« Goedkeuring » (synonyme d'approuvé),
    # sauf rejet explicite. Vote inconnu — on n'invente pas l'unanimité.
    if RE_APPROVED_INTENT.search(w) and not RE_APPROVAL_BLOCK.search(w):
        return "APPROUVÉ", {"type": None, "pour": None, "contre": 0, "abstentions": 0}
    return None, None


def _recover_missing_decisions(points: list[dict], pages: list[dict]) -> int:
    """Corrige les points DÉJÀ extraits mais laissés SANS décision, à partir de
    DEUX sources de texte : (a) les champs `resume`/`titre` que le LLM a lui-même
    extraits pour ce point (texte propre, normalisé, attribué sans ambiguïté),
    et (b) le texte brut du PDF (fenêtre autour de l'ancre 'SP n.-'). Le LLM
    garde parfois le titre mais oublie la décision, ou décrit le statut dans le
    `resume` sans remplir `decision`. Trois cas réels du corpus :
      • SP 21 (2016-10-26) : RETIRÉ, gardé sans statut (titre bilingue très long,
        formule au-delà des 600 premiers car., sur la page suivante) ;
      • SP 45 (2016-10-26) : RETIRÉ, `resume`=« Point retiré de l'ordre du jour »
        mais `decision` vide (le texte brut portait une variante d'espace/
        apostrophe que la regex ratait ; le resume normalisé, lui, matche) ;
      • SP 7/19/37 (2010-03-31) : APPROUVÉS (unanimité / vote nominal) dont le
        « DÉCISION DU CONSEIL … approuvé … » n'a pas été repris.
    Complète _synthesize_deferred_points (qui ne traite que les SP OMIS).
    N'écrase JAMAIS une décision déjà présente. Retourne le nombre corrigé."""
    n = 0
    for pt in points:
        # Ne pas toucher : décision réelle déjà présente — ou point REPORTÉ /
        # RETIRÉ / DÉBATTU, dont le champ `decision` est vide À BON DROIT une
        # fois les dimensions séparées (voir utils_statut). Sans ce test, la
        # récupération réécrirait « REPORTÉ » dans la décision d'un point dont
        # le report est déjà rangé dans son statut de traitement.
        if not decision_manquante(pt):
            continue
        sp = pt.get("sp")
        if not isinstance(sp, int):
            continue
        # Texte du point tel qu'extrait par le LLM (propre) + fenêtre brute
        # globale (toutes pages, sans plafond) : capte la décision où qu'elle
        # soit — resume normalisé (cas SP 45) OU texte brut (cas SP 21).
        extracted_text = f"{pt.get('resume') or ''} {pt.get('titre') or ''}"
        combined = f"{extracted_text}\n{_anchor_window_spanning(pages, sp)}"
        decision, vote = _recover_decision_from_window(combined)
        if not decision:
            continue
        # Ces points sont DÉJÀ normalisés : ils portent les trois dimensions,
        # et un « RETIRÉ » récupéré ici doit rejoindre le statut de traitement
        # plutôt que la décision (voir utils_statut.poser_decision).
        poser_decision(pt, decision)
        pt["vote"] = vote
        n += 1
    return n


def expected_sp_from_pages(pages: list[dict]) -> dict:
    """Compte, PAR REGEX (sans LLM), les points attendus : {sp: page_de_début}.
    L'ancre 'SP n.-' est présente dans tous les PV Schaerbeek (validée 2018→2026).
    Déterministe : sert de vérité-terrain pour détecter ce que le LLM aurait raté."""
    found = {}
    for p in pages:
        for m in RE_SP_ANCHOR.finditer(p["text"]):
            found.setdefault(int(m.group(1)), p["page_num"])  # 1re occurrence
    return found


def verify_completeness(pages: list[dict], points: list[dict]) -> dict:
    """Compare les SP attendus (regex) aux SP extraits (LLM). Rapport actionnable."""
    expected = expected_sp_from_pages(pages)
    got = {p["sp"] for p in points if isinstance(p.get("sp"), int)}
    missing = sorted(set(expected) - got)
    return {
        "expected": len(expected),
        "extracted": len(got),
        "missing_sp": missing,
        "extra_sp": sorted(got - set(expected)),
        "missing_pages": sorted({expected[sp] for sp in missing}),
        "ok": not missing,
    }


def _dedup_by_sp(points: list[dict]) -> list[dict]:
    """Dédup par sp en gardant l'enregistrement le plus renseigné. Factorisé pour
    être rejouable après une récupération ciblée."""
    sp_map = {}
    for p in points:
        _coerce_sp(p)
        sp = p.get("sp")
        if sp is None:
            continue
        if sp not in sp_map or sum(1 for v in p.values() if v) > sum(1 for v in sp_map[sp].values() if v):
            sp_map[sp] = p
    return sorted(sp_map.values(), key=_sp_key)


def _recover_missing_points(pages: list[dict], report: dict,
                            seance_date: Optional[str]) -> list[dict]:
    """Relance le LLM PAGE PAR PAGE sur les pages portant un SP manquant, et ne
    garde que les points dont le sp était effectivement manquant. Passage unique
    et borné (pas de boucle) — récupère les points perdus sur PV denses."""
    log = get_logger()
    missing = set(report["missing_sp"])
    targets = set(report["missing_pages"])
    recovered = []
    for pg in [p for p in pages if p["page_num"] in targets]:
        pts, _ = _extract_chunk_points([pg], seance_date)
        recovered.extend(pt for pt in pts if _coerce_sp(pt).get("sp") in missing)
    if recovered:
        log.info(f"  ↻ Récupération ciblée : {len(recovered)} point(s) sur pages {sorted(targets)}")
    return recovered


def process_pdf(pdf_path: Path, progress_cb: Optional[Callable[[dict], None]] = None) -> Optional[dict]:
    """`progress_cb`, si fourni, est appelé avec un petit dict d'avancement à
    chaque étape notable (extraction du PDF, puis après CHAQUE chunk envoyé à
    Claude) — utilisé par l'intégration admin (services/pv_integration.py)
    pour afficher une progression réelle côté navigateur plutôt qu'un simple
    minuteur. Optionnel et sans effet sur l'extraction elle-même (le run
    Colab historique ne le passe pas)."""
    log = get_logger()
    log.info(f"\n{'='*60}")
    log.info(f"Traitement : {pdf_path.name}")
    meta = extract_pdf_metadata(pdf_path)
    log.info(f"  Date inférée : {meta['date']}")

    pages = extract_text_from_pdf(pdf_path)
    if not pages:
        log.error("  Aucune page extraite — PDF peut-être scanné/image")
        return None
    if not meta["date"]:
        text_date = extract_seance_date_from_text(" ".join(p["text"] for p in pages[:2]))
        if text_date:
            meta["date"] = text_date
            meta["seance_id"] = f"PV-{text_date}"
            log.info(f"  Date extraite du contenu : {text_date}")
    pages = [p for p in pages if p["char_count"] > 100]
    log.info(f"  Pages utiles : {len(pages)}")

    chunks = chunk_pages(pages, CONFIG["CHUNK_SIZE"])
    log.info(f"  Chunks : {len(chunks)} × {CONFIG['CHUNK_SIZE']} pages")
    if progress_cb:
        progress_cb({"stage": "extraction", "pages": len(pages), "chunk": 0, "total_chunks": len(chunks), "points_so_far": 0})

    all_points = []
    seance_date = meta["date"]
    for i, chunk in enumerate(chunks):
        log.info(f"  Chunk {i+1}/{len(chunks)} (pages {chunk[0]['page_num']}-{chunk[-1]['page_num']})")
        points, seance_date = _extract_chunk_points(chunk, seance_date)
        log.info(f"    → {len(points)} points extraits")
        all_points.extend(p for p in (normalize_point(p) for p in points) if p is not None)
        if progress_cb:
            progress_cb({"stage": "extraction", "pages": len(pages), "chunk": i + 1,
                         "total_chunks": len(chunks), "points_so_far": len(all_points)})

    if not all_points:
        log.warning("  Aucun point extrait")
        return None

    deduped = _dedup_by_sp(all_points)
    log.info(f"  Points dédupliqués : {len(all_points)} → {len(deduped)}")
    if progress_cb:
        progress_cb({"stage": "verification", "pages": len(pages), "points_so_far": len(deduped)})

    # GREFFE 2 : contrôle de complétude (regex vs LLM) + récupération ciblée.
    check = verify_completeness(pages, deduped)
    if not check["ok"]:
        log.warning(
            f"  ⚠ Complétude : {check['extracted']}/{check['expected']} points — "
            f"{len(check['missing_sp'])} SP manquants {check['missing_sp']} "
            f"(pages {check['missing_pages']})"
        )
        if CONFIG["RECOVER_MISSING"]:
            recovered = _recover_missing_points(pages, check, seance_date)
            if recovered:
                recovered = [p for p in (normalize_point(p) for p in recovered) if p is not None]
                deduped = _dedup_by_sp(all_points + recovered)
                check = verify_completeness(pages, deduped)   # re-vérifie
                log.info(
                    f"  ↻ Après récupération : {check['extracted']}/{check['expected']} — "
                    + ("complet ✅" if check["ok"] else f"encore manquant {check['missing_sp']}")
                )
        # Filet déterministe : un SP encore manquant dont le texte brut porte une
        # formule de retrait/report (ex. SP retiré de l'ordre du jour, omis par le
        # LLM sur page dense) est reconstruit en point minimal REPORTÉ plutôt que
        # laissé comme un trou. Sans appel LLM — s'exécute même si RECOVER_MISSING
        # est off. Aucun stub inventé sans preuve textuelle (voir la fonction).
        if check["missing_sp"]:
            synth = _synthesize_deferred_points(pages, check["missing_sp"])
            if synth:
                synth = [p for p in (normalize_point(p) for p in synth) if p is not None]
                deduped = _dedup_by_sp(deduped + synth)
                check = verify_completeness(pages, deduped)
                log.info(
                    f"  ⊕ Points retirés/reportés reconstruits : {len(synth)} — "
                    + ("complet ✅" if check["ok"] else f"encore manquant {check['missing_sp']}")
                )
    else:
        log.info(f"  ✅ Complétude : {check['extracted']}/{check['expected']} points (regex = LLM)")

    # Filet déterministe (2/2) : un point EXTRAIT mais sans décision, dont le
    # texte brut porte une décision (retrait/report OU approbation votée),
    # reçoit le bon statut. Complète la reconstruction des SP omis ci-dessus —
    # ici le LLM a gardé le point mais oublié la décision (SP 21 retiré du
    # 2016-10-26 ; SP 7/19/37 approuvés du 2010-03-31).
    fixed = _recover_missing_decisions(deduped, pages)
    if fixed:
        log.info(f"  ⊛ Décision (retiré/reporté/approuvé) rétablie sur {fixed} point(s) déjà extrait(s)")

    # GREFFE 1 : chaque point porte son fichier source (page déjà présente) →
    # lien vérifiable vers le PV d'origine.
    for p in deduped:
        p.setdefault("source_file", meta["filename"])

    seance_struct = {
        "seance": {
            "id": meta["seance_id"] or f"PV-{seance_date}", "date": seance_date,
            "source_file": meta["filename"], "extracted_at": datetime.now().isoformat(),
            "heure_ouverture": None, "heure_cloture": None, "president": None,
            "bourgmestre": None, "bourgmestre_ff": None, "secretaire_communal": None,
            "presents_count": None, "excuses": [], "absents": [],
            "extraction_check": check,   # GREFFE 2 : rapport de complétude versionné
        },
        "points": deduped
    }
    enrich_seance_meta(seance_struct, pages[:2])
    return seance_struct


def _clean_name(raw: str) -> str:
    # Même recasage que _titlecase_name (auteurs/intervenants/repondants) :
    # ces 4 noms sont capturés par regex depuis le texte brut du PV, pas
    # depuis le JSON de Claude, mais le PV les imprime tout aussi souvent en
    # majuscules (« Cécile JODOGNE, Bourgmestre »). bourgmestre_ff en
    # particulier sert de repli d'affichage quand le nom résolu ne matche
    # aucun·e élu·e du registre (voir attribution._respondents) — sans ce
    # recasage, ce repli resterait tout en majuscules.
    return _titlecase_name(re.sub(r"\s+", " ", raw).strip(" :-\t"))


def enrich_seance_meta(seance_struct: dict, first_pages: list[dict]):
    """FIX #7 : extrait les noms depuis le texte, ne les hardcode plus."""
    text = " ".join(p["text"] for p in first_pages)
    s = seance_struct["seance"]

    m = re.search(r"ouvre\s+en\s+séance\s+publique\s+à\s+(\d{1,2})h(\d{0,2})", text, re.IGNORECASE)
    if m:
        s["heure_ouverture"] = f"{m.group(1)}:{m.group(2) or '00'}"
    m = re.search(r"séance\s+(?:publique\s+)?est\s+levée\s+à\s+(\d{1,2})h(\d{0,2})", text, re.IGNORECASE)
    if m:
        s["heure_cloture"] = f"{m.group(1)}:{m.group(2) or '00'}"

    NAME = r"([A-ZÀ-Ý][\wÀ-ÿ'\-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'\-]+)*)"
    m = re.search(NAME + r"\s*,?\s*Président\s+du\s+[Cc]onseil", text)
    if m:
        s["president"] = _clean_name(m.group(1))
    m = re.search(NAME + r"\s*,?\s*Bourgmestre\s+ff", text, re.IGNORECASE)
    if m:
        s["bourgmestre_ff"] = _clean_name(m.group(1))
    else:
        m = re.search(NAME + r"\s*,?\s*Bourgmestre\b", text)
        if m:
            s["bourgmestre"] = _clean_name(m.group(1))
    m = re.search(NAME + r"\s*,?\s*Secrétaire\s+[Cc]ommunal\b", text)
    if m:
        s["secretaire_communal"] = _clean_name(m.group(1))

    presents = re.search(r"PRÉSENTS[^:]*:(.*?)(?:ABSENTS|EXCUSÉS|EN DÉBUT)", text, re.DOTALL | re.IGNORECASE)
    if presents:
        count = len(re.findall(r"M\.\-h\.|Mme\-mevr\.|MM\.\-hh\.|Mmes\-mevr\.", presents.group(1)))
        if count:
            s["presents_count"] = count

# ══════════════════════════════════════════════════════════════════════════
# FUSION BASE JSON
# ══════════════════════════════════════════════════════════════════════════
def load_database() -> dict:
    log = get_logger()
    db_path = Path(CONFIG["DB_JSON_PATH"])
    if db_path.exists():
        with open(db_path, encoding="utf-8") as f:
            db = json.load(f)
        log.info(f"Base existante chargée : {len(db.get('seances', []))} séances")
        return db
    log.info("Nouvelle base créée")
    return {"meta": {"nom": "Base PV Conseil Communal Schaerbeek", "version": "2.1",
                     "date_creation": datetime.now().isoformat(), "seances_incluses": [],
                     "total_points": 0}, "seances": []}


def _rotate_backups():
    """DESIGN : garde seulement les MAX_BACKUPS backups récents."""
    backup_dir = Path(CONFIG["BACKUP_DIR"])
    if not backup_dir.exists():
        return
    backups = sorted(backup_dir.glob("pv_conseil_schaerbeek.backup_*.json"))
    for old in backups[:max(0, len(backups) - CONFIG["MAX_BACKUPS"])]:
        try:
            old.unlink()
        except OSError:
            pass


def save_database(db: dict):
    log = get_logger()
    db_path = Path(CONFIG["DB_JSON_PATH"])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists():
        backup_dir = Path(CONFIG["BACKUP_DIR"])
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(db_path, backup_dir / f"pv_conseil_schaerbeek.backup_{stamp}.json")
        _rotate_backups()

    db["meta"]["seances_incluses"] = [s["seance"]["date"] for s in db["seances"] if s["seance"]["date"]]
    db["meta"]["total_points"] = sum(len(s["points"]) for s in db["seances"])
    db["meta"]["last_updated"] = datetime.now().isoformat()

    # Écriture atomique (évite corruption si crash pendant l'écriture)
    tmp = db_path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    tmp.replace(db_path)
    log.info(f"Base sauvegardée : {db['meta']['total_points']} points, {len(db['seances'])} séances")


def merge_seance_into_db(db: dict, new_seance: dict) -> bool:
    log = get_logger()
    new_date = new_seance["seance"]["date"]
    if not new_date:
        log.warning("  Séance sans date — ignorée")
        return False
    existing = next((s for s in db["seances"] if s["seance"]["date"] == new_date), None)
    if existing:
        for p in existing["points"]:
            _coerce_sp(p)
        existing_sps = {p.get("sp") for p in existing["points"]}
        added = updated = 0
        for new_point in new_seance["points"]:
            _coerce_sp(new_point)
            sp = new_point.get("sp")
            if sp not in existing_sps:
                existing["points"].append(new_point)
                added += 1
            else:
                idx = next(i for i, p in enumerate(existing["points"]) if p.get("sp") == sp)
                if sum(1 for v in new_point.values() if v) > sum(1 for v in existing["points"][idx].values() if v):
                    existing["points"][idx] = new_point
                    updated += 1
        existing["points"].sort(key=_sp_key)
        for k, v in new_seance["seance"].items():
            if v and not existing["seance"].get(k):
                existing["seance"][k] = v
        log.info(f"  Séance {new_date} mise à jour : +{added} ajoutés, {updated} enrichis")
    else:
        db["seances"].append(new_seance)
        db["seances"].sort(key=lambda s: s["seance"].get("date") or "")
        log.info(f"  Séance {new_date} ajoutée ({len(new_seance['points'])} points)")
    return True

# ══════════════════════════════════════════════════════════════════════════
# PROGRESSION
# ══════════════════════════════════════════════════════════════════════════
def load_progress() -> set:
    pf = Path(CONFIG["PROGRESS_FILE"])
    if pf.exists():
        try:
            with open(pf) as f:
                return set(json.load(f).get("done", []))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def save_progress(done: set):
    pf = Path(CONFIG["PROGRESS_FILE"])
    pf.parent.mkdir(parents=True, exist_ok=True)
    tmp = pf.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump({"done": sorted(done), "last_updated": datetime.now().isoformat()}, f, indent=2)
    tmp.replace(pf)


def get_pdf_list(input_dir: str) -> list[Path]:
    log = get_logger()
    pdfs = sorted(Path(input_dir).glob("**/*.pdf"))
    log.info(f"PDF trouvés : {len(pdfs)} fichiers dans {input_dir}")
    return pdfs


def _pdf_year(pdf_path: Path) -> Optional[int]:
    """Année déduite du NOM de fichier (via extract_pdf_metadata), ou None si le
    nom ne contient pas de date lisible. Sert au filtre year= de run_pipeline."""
    d = extract_pdf_metadata(pdf_path).get("date")
    return int(d[:4]) if d else None


def filter_pdfs_by_year(pdfs: list[Path], year: int) -> list[Path]:
    """Ne garde que les PV de l'année demandée (d'après la date du nom de
    fichier). Les PDF dont le nom ne porte pas de date sont écartés : à ce stade
    l'année n'est connue que par le nom (le contenu n'est lu qu'au traitement)."""
    log = get_logger()
    year = int(year)
    kept, no_date = [], 0
    for p in pdfs:
        py = _pdf_year(p)
        if py == year:
            kept.append(p)
        elif py is None:
            no_date += 1
    msg = f"Filtre année {year} : {len(kept)}/{len(pdfs)} PDF retenus"
    if no_date:
        msg += f" ({no_date} sans date lisible dans le nom, ignorés)"
    log.info(msg)
    return kept

# ══════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPALE
# ══════════════════════════════════════════════════════════════════════════
def run_pipeline(max_files: Optional[int] = None, dry_run: bool = False,
                 force_reprocess: bool = False, year: Optional[int] = None) -> dict:
    for d in [CONFIG["DRIVE_ROOT"], CONFIG["OUTPUT_DIR"], CONFIG["CACHE_DIR"], CONFIG["BACKUP_DIR"]]:
        Path(d).mkdir(parents=True, exist_ok=True)
    _attach_file_logging()

    log = get_logger()
    log.info("\n" + "█"*60)
    log.info("DÉMARRAGE PIPELINE PV EXTRACTION v2.2")
    log.info(f"Modèle : {CONFIG['MODEL']} | Chunk : {CONFIG['CHUNK_SIZE']} pages")
    if year is not None:
        log.info(f"Filtre : année {year} uniquement")
    log.info("█"*60)

    if not dry_run:
        try:
            get_client()
        except ValueError as e:
            log.error(str(e))
            return {}

    done = load_progress() if (CONFIG["SKIP_ALREADY_DONE"] and not force_reprocess) else set()
    db = load_database()
    pdfs = get_pdf_list(CONFIG["INPUT_DIR"])
    if year is not None:
        pdfs = filter_pdfs_by_year(pdfs, year)
        if not pdfs:
            log.warning(f"Aucun PV pour l'année {year} — rien à traiter.")
            return db
    if max_files:
        pdfs = pdfs[:max_files]

    stats = {"total": len(pdfs), "done": 0, "skipped": 0, "failed": 0, "points": 0}
    for pdf_path in tqdm(pdfs, desc="PDFs traités", unit="fichier"):
        fname = pdf_path.name
        if fname in done:
            log.info(f"⏭  Déjà traité : {fname}")
            stats["skipped"] += 1
            continue
        if dry_run:
            pages = extract_text_from_pdf(pdf_path)
            log.info(f"[DRY RUN] {fname} : {len(pages)} pages")
            stats["done"] += 1
            continue
        seance = process_pdf(pdf_path)
        if seance is None:
            stats["failed"] += 1
            (Path(CONFIG["OUTPUT_DIR"]) / f"FAILED_{fname}.txt").write_text(
                f"Échec : {fname}\n{datetime.now().isoformat()}")
            continue
        seance_out = Path(CONFIG["OUTPUT_DIR"]) / f"{seance['seance']['id']}.json"
        with open(seance_out, "w", encoding="utf-8") as f:
            json.dump(seance, f, ensure_ascii=False, indent=2)
        log.info(f"  Séance sauvegardée : {seance_out.name}")
        merge_seance_into_db(db, seance)
        stats["points"] += len(seance["points"])
        save_database(db)
        done.add(fname)
        save_progress(done)
        stats["done"] += 1

    log.info("\n" + "═"*60)
    log.info("RAPPORT FINAL")
    log.info(f"  Total: {stats['total']} | Traités: {stats['done']} | Ignorés: {stats['skipped']} | Échoués: {stats['failed']}")
    log.info(f"  Points extraits : {stats['points']}")
    log.info(f"  Base finale : {db['meta']['total_points']} points / {len(db['seances'])} séances")
    log.info("═"*60)
    return db

# ══════════════════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════
def validate_database(db: dict) -> dict:
    log = get_logger()
    issues = []
    for seance in db["seances"]:
        date = seance["seance"]["date"]
        seen = set()
        for p in seance["points"]:
            sp = p.get("sp")
            if sp in seen:
                issues.append(f"{date}: SP {sp} dupliqué")
            seen.add(sp)
            if not p.get("titre"):
                issues.append(f"{date} SP{p.get('sp','?')}: titre manquant")
            m = p.get("montant_eur")
            if m and (m < 0 or m > 100_000_000):
                issues.append(f"{date} SP{p.get('sp','?')}: montant suspect {m}")
    log.info(f"\nValidation : {len(issues)} problèmes")
    for issue in issues[:20]:
        log.warning(f"  ⚠ {issue}")
    if len(issues) > 20:
        log.warning(f"  ... et {len(issues)-20} autres")
    return {"total_issues": len(issues), "issues": issues}


def export_csv(db: dict, output_path: str):
    import csv
    log = get_logger()
    rows = []
    for seance in db["seances"]:
        date = seance["seance"]["date"]
        for p in seance["points"]:
            vote = p.get("vote", {}) or {}
            rows.append({
                "date": date, "sp": p.get("sp"), "page": p.get("page"),
                "source_file": p.get("source_file") or seance["seance"].get("source_file"),
                "type": p.get("type"),
                "rubrique": p.get("rubrique"), "sous_rubrique": p.get("sous_rubrique"),
                "titre": (p.get("titre") or "")[:200], "resume": (p.get("resume") or "")[:300],
                # L'issue du point — décision, ou report/retrait/débat quand
                # il n'y en a pas eu : cette colonne servait à lire les deux,
                # elle continue de le faire sur une base séparée.
                "decision": mot_issue(p), "vote_type": vote.get("type"),
                "statut_traitement": dimensions(p)[0], "debat": dimensions(p)[2],
                "vote_pour": vote.get("pour"), "vote_contre": vote.get("contre"),
                "vote_abstentions": vote.get("abstentions"), "montant_eur": p.get("montant_eur"),
                "urgence": p.get("urgence"),
                "auteurs": "; ".join(p.get("auteurs") or []),
                "intervenants": "; ".join(p.get("intervenants") or []),
                "repondants": "; ".join(p.get("repondants") or []),
                "thematiques": ", ".join(p.get("thematiques") or []),
            })
    if not rows:
        log.warning("Base vide — pas de CSV")
        return
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    log.info(f"CSV exporté : {output_path} ({len(rows)} lignes)")


def completeness_report(db: dict) -> dict:
    """GREFFE 2 (niveau base) : récapitule les rapports de complétude stockés par
    séance. Sert d'éval versionnée anti-régression : toute séance où regex > LLM
    est signalée. À exécuter après run_pipeline(), ou en CI sur la base publiée."""
    total_expected = total_extracted = 0
    incomplete = []
    for s in db["seances"]:
        chk = s["seance"].get("extraction_check")
        if not chk:
            continue
        total_expected += chk["expected"]
        total_extracted += chk["extracted"]
        if not chk["ok"]:
            incomplete.append((s["seance"]["date"], chk["missing_sp"]))
    print(f"\n{'━'*50}\n  COMPLÉTUDE (regex vs LLM)\n{'━'*50}")
    print(f"  Points attendus : {total_expected} | extraits : {total_extracted}")
    print(f"  Séances incomplètes : {len(incomplete)}")
    for date, missing in incomplete[:20]:
        print(f"    ⚠ {date} : SP manquants {missing}")
    print(f"{'━'*50}\n")
    return {"expected": total_expected, "extracted": total_extracted,
            "incomplete_seances": incomplete}


def stats_summary(db: dict):
    from collections import Counter
    all_points = [p for s in db["seances"] for p in s["points"]]
    print(f"\n{'━'*50}\n  STATISTIQUES BASE PV SCHAERBEEK\n{'━'*50}")
    print(f"  Séances: {len(db['seances'])} | Points: {len(all_points)}")
    print("\n  Par type :")
    for t, c in Counter(p.get("type") for p in all_points).most_common():
        print(f"    {str(t):25s} : {c}")
    print("\n  Par rubrique (top 10) :")
    for r, c in Counter(p.get("rubrique") for p in all_points).most_common(10):
        print(f"    {str(r):30s} : {c}")
    montants = [p["montant_eur"] for p in all_points if p.get("montant_eur")]
    if montants:
        print(f"\n  Montants : total {sum(montants):,.0f}€ | max {max(montants):,.0f}€ | {len(montants)} mentions")
    print(f"{'━'*50}\n")


# UTILISATION COLAB :
#   run_pipeline(max_files=2, dry_run=True)    # test sans API
#   db = run_pipeline()                         # production (tous les PV)
#   db = run_pipeline(year=2021)                # une seule année (ex. 2021)
#   db = run_pipeline(year=2021, dry_run=True)  # vérifier quels PV seraient traités
#   validate_database(db); stats_summary(db)
#   completeness_report(db)                     # éval regex vs LLM (points manqués)
#   export_csv(db, "/content/drive/MyDrive/PV_Schaerbeek/pv_all_points.csv")

if __name__ == "__main__":
    db = run_pipeline(max_files=5)
    stats_summary(db)
