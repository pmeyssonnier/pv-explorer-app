"""Attribution auteur·e / répondant·e d'un point de PV : qui a déposé une
question/demande/motion, qui y a répondu en séance — à partir des champs
bruts du PV (« intervenants », « repondant », titre, résumé, type de point).

Deux rôles distincts — car un·e élu·e n'a pas la même activité selon son
mandat :
  • AUTEUR·E (activité de conseiller·ère) : questions orales, demandes,
    motions qu'il/elle dépose. Attribution :
        - question orale  → 1er intervenant (fiable en séance)
        - demande         → auteur du titre (« Demande de M. X »), sinon 1er
                            intervenant
        - motion          → auteur du titre UNIQUEMENT (les motions sont
                            souvent collectives : ne pas deviner à partir
                            des intervenants)
        - débat filmé     → champ « auteur » du chapitrage vidéo (voir
                            video_merge.py)
  • RÉPONDANT·E (activité de membre du Collège / échevin·e) : points où la
    personne répond en séance (champ « repondant »). Les libellés composés
    (« X et le Bourgmestre ») sont scindés ; un rôle seul (« Bourgmestre »)
    est résolu via les métadonnées de la séance.
"""
import re

import lexique_store
from utils.statut import STATUT_TRAITE, dimensions
from utils.text import _DECISION_LABELS
from services.people.names import (
    _RESP_SPLIT, _clean, _is_role_token, _key, _looks_like_name,
    _split_person_names, _strip_accents,
)

# Auteur mentionné dans le titre/résumé d'une demande/motion (« Motion de M.
# Axel Bernard », mais aussi « Demande M. Demirhan » sans « de » — la
# tournure varie selon les séances, d'où le « de/du » optionnel ci-dessous).
# Lettre capitale de tête : classe explicite plutôt que [A-ZÀ-Ÿ]/[À-Ÿ], qui
# inclut par erreur des minuscules accentuées (le bloc Latin-1 Supplement
# n'est pas trié majuscules/minuscules à part — ex. « é » U+00E9 tombe dans
# la plage À(00C0)-Ÿ(0178) ci-dessus), et faisait capter un mot lambda après
# « Demande » employé comme verbe en milieu de phrase (« Demande également
# aux autorités... ») plutôt que la tournure « Demande de/M./Mme X ».
_UPPER_ACCENTS = "ÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸÑŒ"
_AUTHOR_IN_TITLE = re.compile(
    r"(?:Demande|Motion|Interpellation|Verzoek|Motie)\s+(?:d[eu']?\s+)?"
    r"(?:M\.?|Mme|Monsieur|Madame|de heer|Mevrouw)?\s*"
    rf"([A-Z{_UPPER_ACCENTS}][\wÀ-ÿ'’-]+(?:\s+[A-Z{_UPPER_ACCENTS}][\wÀ-ÿ'’-]+){{0,2}})"
)

# ── Types de points → libellés d'affichage ───────────────────────────────────
_TYPE_LABEL = {
    "question_orale": "Question orale",
    "demande_habitant": "Demande",
    "motion": "Motion",
    "video": "Débat filmé",
    # Question écrite adressée au Collège hors séance (voir
    # services/questions_ecrites*.py) — canal totalement séparé des questions
    # orales, jamais liée à un point de PV/SP.
    "question_ecrite": "Question écrite",
}


def _first_named_intervenant(interv: list):
    """1er intervenant clairement attribuable à UNE personne : ignore les
    entrées de rôle pur (« Secrétaire communal ») et les mentions composées
    (« MM. Bouhjar et El Arnouki », ambiguës quant à qui est réellement le
    1er intervenant) plutôt que de les prendre à la lettre."""
    for raw in interv or []:
        names = _split_person_names(raw)
        if len(names) == 1:
            return names[0]
    return None


def _sole_named_intervenant(interv: list):
    """Unique personne nommée d'une liste d'intervenant·e·s — None si zéro,
    plusieurs, ou entrée COMPOSÉE ambiguë (« MM. X et Y »). Les mentions de rôle
    pur (« Bourgmestre ff », qui préside/répond mais ne PROPOSE pas) sont
    ignorées. Sert de repli d'attribution pour les MOTIONS portées par une seule
    personne au débat (voir _author_of) : le reste des motions, à débat
    multiple, demeure collectif et non attribué."""
    names = []
    for raw in interv or []:
        part = _split_person_names(raw)
        if len(part) > 1:        # entrée composée → ambiguïté franche : on renonce
            return None
        if part:                 # une personne ; sinon rôle pur → ignoré
            names.append(part[0])
    uniq = list(dict.fromkeys(names))
    return uniq[0] if len(uniq) == 1 else None


def _author_from_text(text: str, interv: list):
    """Cherche « Demande/Motion de M./Mme X » dans un texte donné (titre ou
    résumé). Si le nom capté s'arrête avant une particule en minuscule (la
    regex ne capture que des mots capitalisés, ex. « Yvan » au lieu de
    « Yvan de Beauffort ») et qu'un·e intervenant·e listé·e commence par ce
    nom tronqué, on préfère la forme complète — plus fiable qu'un prénom
    seul, sans pour autant deviner l'auteur·e à partir des intervenant·e·s."""
    m = _AUTHOR_IN_TITLE.search(text or "")
    if not m:
        return None
    name = m.group(1)
    low = name.lower()
    for iv in interv or []:
        if iv.lower() != low and iv.lower().startswith(low):
            return iv
    return name


def _author_of(point: dict):
    """Nom de l'auteur·e d'un point PV selon son type (None si non attribuable).

    Le nom de l'auteur·e est parfois donné dans le titre (« Demande de Mme
    X… »), parfois seulement dans le résumé (« resume ») quand le titre
    n'en porte pas trace — les deux champs sont donc vérifiés."""
    typ = point.get("type")
    title = point.get("titre") or ""
    resume = point.get("resume") or ""
    interv = point.get("intervenants") or []
    if typ == "question_orale":
        return _first_named_intervenant(interv)
    if typ == "demande_habitant":
        name = _author_from_text(title, interv) or _author_from_text(resume, interv)
        return name or _first_named_intervenant(interv)
    if typ == "motion":
        # Attribution par le texte qui nomme explicitement l'auteur·e (titre OU
        # résumé) ; à défaut, repli PRUDENT : une motion portée par une SEULE
        # personne nommée au débat lui est attribuée. Un débat à plusieurs
        # intervenant·e·s reste collectif (non attribué) — on ne prend jamais
        # « le 1er » comme auteur. Voir _sole_named_intervenant.
        return (_author_from_text(title, interv)
                or _author_from_text(resume, interv)
                or _sole_named_intervenant(interv))
    return None


# Attribution manuelle ponctuelle, vérifiée individuellement (PV + transcript
# vidéo), pour des points normalement jamais attribués automatiquement — type
# "point_normal" (administratif/collectif) : le champ « intervenants » y
# mélange sans distinction citoyen·ne·s/conseiller·ère·s à l'origine de la
# discussion et membres du Collège qui la président ou y répondent, donc pas
# de règle générale fiable (contrairement à question_orale/demande_habitant/
# motion, voir _author_of) — seuls des cas confirmés individuellement sont
# ajoutés ici. Clé = (date, sp).
_MANUAL_AUTHOR_OVERRIDES = {
    ("2026-04-22", 12): "Matthieu Degrez",
    ("2026-04-22", 13): "Georges Verzin",
    ("2026-04-22", 15): "Quentin Van den Hove",
    # SP32 débattu conjointement avec SP31 (même échange, même réponse de
    # l'échevin Bouhjar) : SP31 liste déjà les deux intervenants au PV
    # (Clerfayt + van de Hove), SP32 non — complété manuellement à
    # l'identique. Forme jointe « X et Y » (voir _point_author, résolue par
    # personne pour l'affichage comme pour « repondant »).
    ("2025-10-15", 32): "Bernard Clerfayt et Quentin Van den Hove",
    # SP6 débattu conjointement avec SP7 (le conseil l'annonce explicitement :
    # « nous mêlons deux points, celle sur la prime biome et celle sur la
    # prime d'accompagnement social ») : SP7 liste déjà les 5 intervenant·e·s
    # au PV et le même répondant, SP6 non — complété manuellement à l'identique.
    ("2025-09-24", 6): "Cécile Jodogne et Naïma Belkhatir et Georges Verzin et Matthieu Degrez et Elias Ammi",
    # SP60 débattu conjointement avec SP59 (même règlement sport, chiffres
    # cités dans le débat — 75.277,98€ → 190.571,27€, réductions, âge 21→18 —
    # correspondant exactement au résumé PV de SP60) : SP59 liste déjà les
    # intervenant·e·s et le répondant au PV, SP60 non — complété manuellement
    # à l'identique.
    ("2025-06-25", 60): "Saït Köse et Ibrahim Dönmez et Elias Ammi et Yvan de Beauffort et Abobakre Bouhjar",
    # SP21/SP23/SP24 débattus conjointement avec SP22 (même dossier — le
    # règlement d'allocation aux habitants expropriés de la rue du Progrès,
    # présenté par le conseil comme un seul paquet de 4 points) : SP22 liste
    # déjà les intervenant·e·s et le répondant au PV (Köksal, Lahssaini,
    # Degrez, Jodogne, Durant / Eraly), les 3 autres non — complétés
    # manuellement à l'identique.
    ("2025-04-23", 21): "Sadik Köksal et Leila Lahssaini et Matthieu Degrez et Cécile Jodogne et Isabelle Durant",
    ("2025-04-23", 23): "Sadik Köksal et Leila Lahssaini et Matthieu Degrez et Cécile Jodogne et Isabelle Durant",
    ("2025-04-23", 24): "Sadik Köksal et Leila Lahssaini et Matthieu Degrez et Cécile Jodogne et Isabelle Durant",
}


def _point_author(point: dict, pairs: set, date: str | None = None):
    """Auteur·e d'un point PV + sa clé, si attribuable à UNE personne (jamais
    un mot de rôle seul comme dernier mot, ex. « Secrétaire communal »).
    Factorisé pour être partagé entre l'agrégation par personne
    (registry._build_all) et la vue par séance (seances.seance_detail), qui
    doivent s'accorder sur qui est le/la demandeur·se d'un point donné.

    Une override manuelle peut nommer plusieurs personnes (« X et Y »,
    débat conjoint avec un autre point) : la clé (utilisée pour l'agrégation
    par personne) est alors celle de la 1ère personne nommée, mais le texte
    complet est renvoyé tel quel — à charge de l'appelant·e de le
    redécouper pour l'affichage (voir seance_detail, comme pour
    « repondant »)."""
    author = _MANUAL_AUTHOR_OVERRIDES.get((date, point.get("sp"))) or _author_of(point)
    if not author:
        return None, None
    names = _split_person_names(author) or [author]
    primary = names[0]
    last = primary.split()[-1] if primary.split() else ""
    if not last or _is_role_token(last):
        return author, None
    return author, _key(primary, pairs)


def _respondents(raw: str, seance: dict) -> list:
    """Noms de personnes extraits d'un champ « repondant » (composé possible).
    Un rôle seul est résolu via les métadonnées de séance (bourgmestre / ff)."""
    names = []
    for part in _RESP_SPLIT.split(raw or ""):
        c = _clean(part).strip()
        if not c:
            continue
        toks = [t for t in c.split() if not _is_role_token(t)]
        if toks:
            names.append(" ".join(toks))
            continue
        low = _strip_accents(c).lower()  # rôle seul → résolution via la séance
        if "ff" in low or "f.f" in low:
            bff = seance.get("bourgmestre_ff")
            if bff and _looks_like_name(bff):
                names.append(bff)
        elif "bourgmestre" in low or "burgemeester" in low:
            # « Bourgmestre » seul (sans mention « ff ») : normalement le
            # titulaire, mais si la séance n'en a aucun d'enregistré (champ
            # absent), c'est que c'était le/la bourgmestre f.f. qui présidait
            # — on retombe donc sur ce nom plutôt que de perdre la mention.
            b = seance.get("bourgmestre") or seance.get("bourgmestre_ff")
            if b and _looks_like_name(b):
                names.append(b)
    return names


# ── Issue d'un point (décision + vote) ──────────────────────────────────────
# Partagé entre l'agrégation par personne (registry._build_all, pour le champ
# « decision » d'une réponse en séance) et la vue par séance (seance_detail) —
# vivent ici, pas dans services.seances, pour rester importables par les deux
# sans import circulaire (services.seances importe déjà depuis ce module ET
# depuis services.people.registry).

# Mention du VOTE parfois recollée à la décision par le PV (« Pris acte à
# l'unanimité », « Pris pour information (33 pour, 0 contre, 0 abstentions) »).
# Le statut doit rester le libellé nu : sans ce retrait, chaque variante de
# décompte formerait son propre statut — donc sa propre puce dans l'onglet
# Séances et sa propre part dans le graphe des issues. Le décompte n'est pas
# perdu : `_decision_summary` le réaffiche depuis le champ `vote`, qui en est
# la source structurée. La base actuelle écrit des libellés nus ; c'est ce qui
# se passera si l'extraction change qui est couvert ici.
_MENTION_VOTE = re.compile(
    r"\s*(?:[-–—,;:]\s*)?(?:"
    r"a l'unanimite"
    r"|\(\s*\d+\s*(?:pour|voix)[^)]*\)"
    r"|par \d+\s*(?:voix|pour)\b.*"
    r")\s*$"
)


def _decision_label(decision):
    """Statut canonique d'un point, SANS le détail du vote : « Approuvé »,
    « Décidé », « Pris pour information », « Reporté », « Retiré »… None si
    aucune décision (ex. chapitre vidéo sans point de PV). Ce que
    `_decision_summary` complète ensuite du décompte des voix — mais un
    libellé stable est ce qu'il faut pour regrouper/filtrer les points par
    issue (voir les puces de statut de l'onglet Séances), là où « Approuvé à
    l'unanimité » et « Approuvé (33 pour, 9 contre) » sont un même statut."""
    d = (decision or "").strip()
    if not d:
        return None
    norm = _strip_accents(d).lower()
    nu = _MENTION_VOTE.sub("", norm).strip()
    if nu and nu != norm:
        # _strip_accents ne change pas la longueur (NFD puis retrait des
        # diacritiques) : la coupe se reporte telle quelle sur le texte
        # d'origine, accents compris. On se garde quand même du cas contraire.
        if len(norm) == len(d):
            d = d[:len(nu)].strip()
        norm = nu
    # Lexique éditable en priorité, puis libellés en dur, puis repli casse
    # (variantes rares/coquilles non répertoriées).
    return (lexique_store.decisions().get(norm) or _DECISION_LABELS.get(norm)
            or (d[:1].upper() + d[1:].lower()))


def _decision_summary(decision, vote):
    """Résumé lisible de l'issue d'un point (décision + vote quand il y en a
    un), pour indiquer explicitement, selon chaque cas, pourquoi il n'y a
    par exemple ni répondant·e ni débat filmé à trouver (point voté sans
    discussion, reporté, pris pour information...) plutôt que de laisser
    croire à une recherche infructueuse. None si la décision est vide."""
    label = _decision_label(decision)
    if not label:
        return None
    vote = vote if isinstance(vote, dict) else {}
    vtype = vote.get("type")
    if vtype == "unanimite":
        return f"{label} à l'unanimité"
    if vtype == "vote_nominal":
        pour = vote.get("pour")
        contre = vote.get("contre") or 0
        abst = vote.get("abstentions") or 0
        bits = []
        if pour is not None:
            bits.append(f"{pour} pour")
        bits.append(f"{contre} contre")
        bits.append(f"{abst} abstention{'s' if abst != 1 else ''}")
        return f"{label} ({', '.join(bits)})"
    return label


# ── Audit « sans demandeur » (hors-ligne, sans PDF ni LLM) ───────────────────
# Types de points pour lesquels un·e AUTEUR·E individuel·le est ATTENDU·E. Les
# motions en sont EXCLUES : elles sont souvent collectives et volontairement
# attribuées par le seul titre (voir _author_of) — une motion sans auteur nommé
# n'est donc PAS une anomalie d'extraction. On les recense néanmoins à part.
_TYPES_AUTEUR_ATTENDU = frozenset({"question_orale", "demande_habitant", "interpellation"})


def audit_authors(db: dict, pairs: set | None = None) -> dict:
    """Recense les points où un·e demandeur·se est ATTENDU·E mais introuvable,
    en séparant deux catégories très différentes :

      • anomalies : questions orales / demandes / interpellations SANS auteur
        attribuable — de vrais trous d'extraction (l'auteur figure normalement
        dans le PDF « Question de M. X - … ») à corriger par ré-extraction ;
      • motions_non_attribuees : motions sans auteur — souvent COLLECTIVES (pas
        une anomalie). Chaque entrée porte `intervenants` et `intervenant_unique`
        (True si un seul intervenant·e nommé·e → candidate raisonnable à une
        attribution, contrairement aux motions à débat multiple).

    Hors-ligne : n'utilise que la base déjà extraite (aucun PDF, aucun LLM).
    `pairs` (jeu de paires nom↔clé) n'influence que la résolution de clé, pas la
    détection d'un auteur — l'audit fonctionne donc avec l'ensemble vide."""
    pairs = pairs if pairs is not None else set()
    anomalies, motions = [], []
    for s in db.get("seances", []):
        date = (s.get("seance") or {}).get("date")
        for p in s.get("points", []):
            typ = p.get("type")
            if typ not in _TYPES_AUTEUR_ATTENDU and typ != "motion":
                continue
            author, _key = _point_author(p, pairs, date)
            if author:
                continue
            interv = list(p.get("intervenants") or [])
            entry = {
                "date": date, "sp": p.get("sp"), "type": typ,
                "titre": (p.get("titre") or "").replace("\n", " ").strip(),
                "intervenants": interv, "intervenant_unique": len(interv) == 1,
            }
            (motions if typ == "motion" else anomalies).append(entry)
    anomalies.sort(key=lambda e: (e["date"] or "", e["sp"] or 0))
    motions.sort(key=lambda e: (e["date"] or "", e["sp"] or 0))
    return {"anomalies": anomalies, "motions_non_attribuees": motions}


def print_author_audit(report: dict) -> dict:
    """Résumé lisible de audit_authors : anomalies détaillées, motions non
    attribuées agrégées (dont candidates à intervenant·e unique). Retourne un
    dict de compteurs."""
    anomalies = report.get("anomalies", [])
    motions = report.get("motions_non_attribuees", [])
    candidates = [m for m in motions if m["intervenant_unique"]]
    bar = "━" * 56
    print(f"\n{bar}\n  AUDIT « SANS DEMANDEUR » (hors-ligne)\n{bar}")
    print(f"  Anomalies (question/demande sans auteur) : {len(anomalies)}")
    for e in anomalies:
        print(f"    ⚠ {e['date']} SP{e['sp']} [{e['type']}] : {e['titre'][:70]}")
    print(f"  Motions non attribuées : {len(motions)} "
          f"(dont {len(candidates)} à intervenant·e unique — attribuables)")
    for m in candidates:
        print(f"    ? {m['date']} SP{m['sp']} — intervenant : {m['intervenants'][0]}")
    print(f"{bar}\n")
    return {"anomalies": len(anomalies), "motions_non_attribuees": len(motions),
            "motions_attribuables": len(candidates)}


# ── Audit « sans répondant » (hors-ligne) ────────────────────────────────────
# Seuls les points appelant une RÉPONSE du Collège en attendent un·e : questions
# orales, demandes d'habitants, interpellations. Les délibérations/motions, elles,
# sont votées, pas « répondues » — exclues d'office (par le TYPE).
_TYPES_REPONDANT_ATTENDU = frozenset({"question_orale", "demande_habitant", "interpellation"})


# Question orale RENVOYÉE À L'ÉCRIT : « transformée en question écrite » ou
# « réponse … par écrit ». La réponse est alors différée et donnée par écrit au
# nom du Collège — aucun·e répondant·e oral·e nommé·e n'est attendu·e ce jour-là.
_RE_RENVOI_ECRIT = re.compile(r"transform\w+ en question [ée]crite|par [ée]crit", re.IGNORECASE)


def _sans_reponse_attendue(point: dict) -> bool:
    """Aucune réponse ORALE attendue → un `repondant` vide y est NORMAL (pas une
    anomalie) dans deux cas :
      • point RETIRÉ / REPORTÉ (statut de traitement) — pas débattu en séance ;
      • question orale transformée en question écrite / à répondre par écrit
        (resume) — réponse différée, donnée par écrit au nom du Collège."""
    if dimensions(point)[0] != STATUT_TRAITE:
        return True
    return bool(_RE_RENVOI_ECRIT.search(point.get("resume") or ""))


def audit_respondents(db: dict, types_repondant_attendu=_TYPES_REPONDANT_ATTENDU) -> list[dict]:
    """Recense les points où un·e RÉPONDANT·E est attendu·e mais absent·e, en
    tenant compte du TYPE (seules questions/demandes/interpellations appellent une
    réponse du Collège), du STATUT (RETIRÉ/REPORTÉ exclu) et du RENVOI À L'ÉCRIT
    (question transformée en question écrite → réponse par écrit, exclue). Détails :
    (un point RETIRÉ/REPORTÉ n'a pas été débattu →
    aucune réponse attendue). Hors-ligne (sans PDF ni LLM). Un·e répondant·e est
    jugé·e présent·e dès que _respondents en extrait au moins un nom (un rôle seul,
    ex. « Bourgmestre », est résolu via les métadonnées de séance). Retourne un
    rapport, une entrée par séance concernée, trié par date."""
    report = []
    for s in db.get("seances", []):
        meta = s.get("seance") or {}
        date = meta.get("date")
        manquants = []
        for p in s.get("points", []):
            if p.get("type") not in types_repondant_attendu:
                continue
            if _sans_reponse_attendue(p):
                continue
            if _respondents(p.get("repondant"), meta):
                continue
            manquants.append(p)
        if manquants:
            report.append({
                "date": date,
                "sans_repondant": len(manquants),
                "sp": sorted(p.get("sp") for p in manquants if isinstance(p.get("sp"), int)),
            })
    report.sort(key=lambda r: r["date"] or "")
    return report


def print_respondent_audit(report: list[dict]) -> dict:
    """Résumé lisible de audit_respondents : total et détail par séance. Retourne
    un dict agrégé (total, séances, dates)."""
    total = sum(r["sans_repondant"] for r in report)
    bar = "━" * 56
    print(f"\n{bar}\n  AUDIT « SANS RÉPONDANT » (hors-ligne — type + statut pris en compte)\n{bar}")
    print(f"  Questions/demandes (non retirées) sans répondant : {total} "
          f"(sur {len(report)} séance(s))")
    for r in report:
        print(f"    ⚠ {r['date']} : {r['sans_repondant']} — SP {r['sp']}")
    print(f"{bar}\n")
    return {"total_sans_repondant": total, "seances": len(report),
            "dates": [r["date"] for r in report]}
