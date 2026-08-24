"""Tests des greffes v2.4 du pipeline d'extraction (fonctions PURES, sans API) :
normalisation, parsing montant, traçabilité page, complétude, date-repli.
Verrouille en CI les comportements testés « en isolation » lors du merge.
"""
import pytest

from pv_extraction_pipeline import (
    _coerce_amount, normalize_point, _fix_point_pages,
    expected_sp_from_pages, verify_completeness, extract_seance_date_from_text,
    _synthesize_deferred_points, _extract_anchor_title, RE_RETIRE, RE_REPORTE,
    _recover_missing_decisions,
)
from reextract_targeted import targets_from_audit


# ── targets_from_audit : sélection des séances à re-extraire ────────────────
def test_targets_from_audit_filtre_seuil():
    report = [
        {"date": "2016-11-30", "status": "incomplet", "missing_sp": list(range(12, 27))},
        {"date": "2022-12-21", "status": "incomplet", "missing_sp": [87]},  # off-by-one
        {"date": "2020-01-29", "status": "ok"},
    ]
    assert targets_from_audit(report, min_missing=1) == ["2016-11-30", "2022-12-21"]
    # min_missing=2 écarte le off-by-one (souvent un faux positif regex)
    assert targets_from_audit(report, min_missing=2) == ["2016-11-30"]


# ── _coerce_amount (dont la régression « TVAC ») ────────────────────────────
@pytest.mark.parametrize("raw, val", [
    ("65.000 € TVAC", 65000.0),        # suffixe texte ignoré (le bug corrigé)
    ("1.234,56 € HTVA", 1234.56),
    ("65.000 €", 65000.0),
    ("1 234,50", 1234.50),             # espace comme séparateur de milliers
    (65000, 65000.0),
    (12.5, 12.5),
    (None, None),
    ("", None),
    ("gratuit", None),                 # aucun chiffre → None
])
def test_coerce_amount(raw, val):
    assert _coerce_amount(raw) == val


# ── normalize_point : conserve `page` (point de fusion) + normalise ─────────
def test_normalize_point_preserve_page_and_normalize():
    p = normalize_point({"sp": 12, "page": 7, "titre": "  Objet  ",
                         "montant_eur": "65.000 € TVAC", "urgence": "oui"})
    assert p["sp"] == 12
    assert p["page"] == 7                # ← sinon la traçabilité serait perdue
    assert p["montant_eur"] == 65000.0
    assert p["titre"] == "Objet"
    assert p["urgence"] is True


@pytest.mark.parametrize("bad", [{"titre": "sans sp"}, "pas un dict", None])
def test_normalize_point_rejette_sans_sp(bad):
    assert normalize_point(bad) is None


# ── _fix_point_pages : bornage à l'intervalle réel du chunk ─────────────────
def test_fix_point_pages_bounds_to_chunk():
    chunk = [{"page_num": 5}, {"page_num": 6}, {"page_num": 7}]
    pts = [{"page": 6}, {"page": 99}, {"page": None}, {"page": "Page 5"}]
    _fix_point_pages(pts, chunk)
    # 6 dans l'intervalle ; 99 hors-borne → repli 1re page ; None → repli ; "Page 5" → 5
    assert [p["page"] for p in pts] == [6, 5, 5, 5]


# ── expected_sp_from_pages : ancre déterministe « SP n.- » ──────────────────
def test_expected_sp_from_pages():
    pages = [{"page_num": 1, "text": "SP 1.- Objet A ... SP 2.- Objet B"},
             {"page_num": 2, "text": "SP 3.- Objet C"}]
    assert expected_sp_from_pages(pages) == {1: 1, 2: 1, 3: 2}


# ── verify_completeness : détecte ce que le LLM a raté ──────────────────────
def test_verify_completeness_detecte_manquant():
    pages = [{"page_num": 1, "text": "SP 1.- A SP 2.- B SP 3.- C"}]
    r = verify_completeness(pages, [{"sp": 1}, {"sp": 3}])
    assert r["missing_sp"] == [2]
    assert r["missing_pages"] == [1]
    assert r["ok"] is False


def test_verify_completeness_complet():
    pages = [{"page_num": 1, "text": "SP 1.- A SP 2.- B"}]
    assert verify_completeness(pages, [{"sp": 1}, {"sp": 2}])["ok"] is True


# ── Filet déterministe : points retirés/reportés reconstruits ───────────────
# Deux statuts DISTINCTS (RETIRÉ vs REPORTÉ) — voir le SYSTEM_PROMPT + backend
# _is_reportee/_is_retire. Cas réel : SP 22 du PV du 2016-10-26, retiré de
# l'ordre du jour, omis par le LLM (signalé manquant par verify_completeness).
_RETIRE_RAW = (
    "SP 22.- Vivaqua-Hydrobru - Examen du projet de fusion -=- Vivaqua-Hydrobru "
    "- Studie van het fusieontwerp Ce point est retiré de l'ordre du jour -=- "
    "Dit punt wordt aan de agenda onttrokken. 25 SP 23.- Autre point"
)


def test_synthesize_withdrawn_point_gets_retire_status():
    pages = [{"page_num": 25, "text": _RETIRE_RAW}]
    out = _synthesize_deferred_points(pages, [22])
    assert len(out) == 1
    p = out[0]
    assert p["sp"] == 22
    assert p["decision"] == "RETIRÉ"                 # PAS "REPORTÉ" — statut distinct
    assert p["vote"]["type"] is None                 # un point retiré n'est jamais voté
    assert p["titre"] == "Vivaqua-Hydrobru - Examen du projet de fusion"
    assert p["page"] == 25


def test_synthesize_reported_point_gets_reporte_status():
    raw = "SP 40.- Un point reporté quelconque -=- NL Ce point est reporté. 12 SP 41.- x"
    out = _synthesize_deferred_points([{"page_num": 12, "text": raw}], [40])
    assert len(out) == 1
    assert out[0]["decision"] == "REPORTÉ"
    assert out[0]["vote"]["type"] == "reporte"
    # Le titre ne doit PAS être tronqué par les mots « point reporté » qu'il
    # contient : seule la formule « est reporté » marque le statut.
    assert out[0]["titre"] == "Un point reporté quelconque"


def test_synthesize_ignores_missing_point_without_deferral_phrase():
    # Un vrai point de fond omis (sans formule de retrait/report) ne doit JAMAIS
    # être reconstruit en stub — il relève d'une ré-extraction, pas d'un faux.
    raw = "SP 50.- Un vrai point avec du contenu -=- NL. Approuvé à l'unanimité. 8"
    assert _synthesize_deferred_points([{"page_num": 8, "text": raw}], [50]) == []


def test_retire_and_reporte_regexes_are_disjoint():
    # Les deux formules ne se recouvrent pas (statuts distincts).
    assert RE_RETIRE.search("Ce point est retiré de l'ordre du jour")
    assert not RE_REPORTE.search("Ce point est retiré de l'ordre du jour")
    assert RE_REPORTE.search("Ce point est reporté")
    assert not RE_RETIRE.search("Ce point est reporté")
    # NL
    assert RE_RETIRE.search("Dit punt wordt aan de agenda onttrokken")
    assert RE_REPORTE.search("Dit punt wordt uitgesteld")


def test_extract_anchor_title_stops_at_bilingual_separator():
    window = " Vivaqua-Hydrobru - Examen du projet de fusion -=- NL titre"
    assert _extract_anchor_title(window, 22) == "Vivaqua-Hydrobru - Examen du projet de fusion"


# ── _recover_missing_decisions : corrige un point EXTRAIT sans statut ──
# Cas réel SP 21 (2016-10-26) : le LLM garde le point (titre long) mais oublie
# le statut RETIRÉ, contrairement au SP 22 voisin plus court.
def test_apply_deferred_status_sets_retire_on_extracted_empty_decision():
    raw = ("SP 21.- Convention de collaboration entre la VRT, la RTBF et la Commune "
           "- Addendum -=- NL. Ce point est retiré de l'ordre du jour -=- Dit punt "
           "wordt aan de agenda onttrokken. SP 22.- Vivaqua")
    pts = [{"sp": 21, "page": 24, "titre": "Convention…", "decision": "", "vote": {"type": None}}]
    n = _recover_missing_decisions(pts, [{"page_num": 24, "text": raw}])
    assert n == 1
    assert pts[0]["decision"] == "RETIRÉ"
    assert pts[0]["vote"]["type"] is None


def test_apply_deferred_status_never_overwrites_a_real_decision():
    raw = "SP 30.- x Ce point est retiré de l'ordre du jour. SP 31.- y"
    pts = [{"sp": 30, "page": 1, "titre": "x", "decision": "APPROUVÉ", "vote": {"type": "unanimite"}}]
    assert _recover_missing_decisions(pts, [{"page_num": 1, "text": raw}]) == 0
    assert pts[0]["decision"] == "APPROUVÉ"


def test_apply_deferred_status_leaves_undecided_point_without_deferral_phrase():
    # Un point sans décision ET sans formule de retrait/report reste tel quel
    # (ex. point débattu sans vote formel) — pas de statut inventé.
    raw = "SP 40.- Un point normal sans phrase de statut. SP 41.- y"
    pts = [{"sp": 40, "page": 1, "titre": "x", "decision": "", "vote": {"type": None}}]
    assert _recover_missing_decisions(pts, [{"page_num": 1, "text": raw}]) == 0
    assert pts[0]["decision"] == ""


def test_apply_deferred_status_spans_pages_and_long_titles():
    # Régression réelle SP 21 (2016-10-26) : titre bilingue TRÈS long (la
    # formule de retrait tombe au-delà des 600 premiers caractères de l'ancre)
    # ET point suivant (SP 22) sur la PAGE d'après. L'ancien fenêtrage
    # mono-page + plafond 600 ratait la formule ; le fenêtrage global la capte.
    fr = ("Convention de collaboration entre la VRT, la RTBF, la Région de "
          "Bruxelles-Capitale et la Commune de Schaerbeek, d'une part, et l'Agence "
          "de Développement Territorial (ADT), d'autre part, concernant le projet "
          "de réaménagement du site VRT/RTBF - Addendum")
    nl = ("Samenwerkingsovereenkomst tussen de VRT, de RTBF, het Brussels "
          "Hoofdstedelijk Gewest en de Gemeente Schaarbeek enerzijds, en het "
          "Agentschap voor Territoriale Ontwikkeling anderzijds, betreffende het "
          "herinrichtingsproject van de VRT/RTBF-site - Addendum")
    pages = [
        {"page_num": 24, "text": f"SP 20.- Autre point. DÉCIDE. SP 21.- {fr} -=- {nl}"},
        {"page_num": 25, "text": ("Le point est retiré de l'ordre du jour -=- Dit punt "
                                  "wordt aan de agenda onttrokken. SP 22.- Vivaqua")},
    ]
    pts = [{"sp": 21, "page": 24, "titre": "Convention…", "decision": "", "vote": {"type": None}}]
    assert _recover_missing_decisions(pts, pages) == 1
    assert pts[0]["decision"] == "RETIRÉ"


def test_recover_decision_from_normalized_resume_when_raw_text_variant():
    # Cas réel SP 45 (2016-10-26) : le LLM a mis « Point retiré de l'ordre du
    # jour » dans le RESUME mais laissé `decision` vide ; le texte brut du PDF
    # portait une variante d'espace/apostrophe non captée par la regex. Le
    # resume normalisé, lui, matche → on doit s'en servir.
    pts = [{"sp": 45, "page": 98, "titre": "Règlement académie de musique - Approbation",
            "resume": "Point retiré de l'ordre du jour", "decision": "", "vote": {"type": None}}]
    raw = "SP 45.- Règlement académie - Approbation SP 46.- Autre"  # brut SANS la formule
    assert _recover_missing_decisions(pts, [{"page_num": 98, "text": raw}]) == 1
    assert pts[0]["decision"] == "RETIRÉ"


def test_recover_decision_no_false_positive_from_approbation_in_title():
    # Un titre finissant par « - Approbation » (intention d'agenda) NE doit PAS
    # produire un faux « APPROUVÉ » : seul « approuvé …/goedgekeurd … » (décision
    # réelle) compte, pas le mot « Approbation ».
    pts = [{"sp": 50, "page": 1, "titre": "Convention X - Approbation",
            "resume": "Approbation de la convention X avec l'ASBL Y", "decision": "", "vote": {"type": None}}]
    raw = "SP 50.- Convention X - Approbation SP 51.- z"
    assert _recover_missing_decisions(pts, [{"page_num": 1, "text": raw}]) == 0
    assert pts[0]["decision"] == ""


def test_recover_decision_approved_unanime_on_extracted_empty_decision():
    # Cas réel SP 19/37 (2010-03-31) : point voté (« DÉCISION DU CONSEIL …
    # approuvé à l'unanimité ») dont le LLM a oublié la décision.
    raw = ("SP 19.- Désignation de la zone de Police 5344 -=- NL. DECISION DU CONSEIL "
           "-=- BESLISSING VAN DE RAAD Par appel nominal, approuvé à l'unanimité "
           "goedgekeurd met eenparigheid van stemmen SP 20.- x")
    pts = [{"sp": 19, "page": 3, "titre": "Désignation…", "decision": "", "vote": {"type": None}}]
    assert _recover_missing_decisions(pts, [{"page_num": 3, "text": raw}]) == 1
    assert pts[0]["decision"] == "APPROUVÉ"
    assert pts[0]["vote"]["type"] == "unanimite"


def test_recover_decision_approved_by_nominal_vote_captures_counts():
    # Cas réel SP 7 (2010-03-31) : « approuvé par 26 voix contre 15 ».
    raw = ("SP 7.- Taxe sur les immeubles subdivisés -=- NL. DECISION DU CONSEIL "
           "-=- BESLISSING VAN DE RAAD Par appel nominal, approuvé par 26 voix contre 15 "
           "SP 8.- x")
    pts = [{"sp": 7, "page": 2, "titre": "Taxe…", "decision": "", "vote": {"type": None}}]
    assert _recover_missing_decisions(pts, [{"page_num": 2, "text": raw}]) == 1
    assert pts[0]["decision"] == "APPROUVÉ"
    assert pts[0]["vote"] == {"type": "vote_nominal", "pour": 26, "contre": 15, "abstentions": 0}


def test_recover_decision_nominal_vote_with_abstentions():
    raw = ("SP 5.- Objet -=- NL. Par appel nominal, approuvé par 30 voix contre 8 "
           "et 3 abstentions SP 6.- x")
    pts = [{"sp": 5, "page": 1, "titre": "Objet", "decision": "", "vote": {"type": None}}]
    assert _recover_missing_decisions(pts, [{"page_num": 1, "text": raw}]) == 1
    assert pts[0]["vote"] == {"type": "vote_nominal", "pour": 30, "contre": 8, "abstentions": 3}


# ── extract_seance_date_from_text : FR/NL + accents/espaces perdus ──────────
@pytest.mark.parametrize("text, date", [
    ("Séance du 20 septembre 2023", "2023-09-20"),
    ("SEANCE DU20 SEPTEMBRE 2023", "2023-09-20"),   # accents + espace perdus
    ("Vergadering van 15 januari 2024", "2024-01-15"),
    ("Séance du 05/03/2020 à 18h", "2020-03-05"),
    ("aucune date ici", None),
])
def test_extract_date_from_text(text, date):
    assert extract_seance_date_from_text(text) == date
