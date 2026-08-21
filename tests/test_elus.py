"""Tests de l'agrégation « Interventions par élu·e » (services.elus) et de ses
endpoints /elus et /elu/{key}.

Ces fonctions sont déterministes (lecture de la base JSON des PV + chapitrage
vidéo, aucun embedding) : on vérifie la normalisation des noms, la distinction
des rôles (auteur·e / répondant·e) et les invariants d'attribution.
"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

import app
from services import elus

client = TestClient(app.app)
ROOT = Path(__file__).resolve().parent.parent


# ── Normalisation des noms ───────────────────────────────────────────────────
def test_key_matches_pv_surname_and_video_fullname():
    # « Georges VERZIN » (vidéo, majuscules) et « Verzin » (PV) → même clé.
    assert elus._key("Georges VERZIN") == "verzin"
    assert elus._key("Verzin") == "verzin"
    assert elus._key("Mme Nyssens") == "nyssens"
    assert elus._key("Noël, Bourgmestre") == "noel"


def test_key_resolves_reversed_name_order_via_pairs():
    # Le PV mélange « Prénom Nom » et « Nom Prénom » selon les séances/sources,
    # ce qui scindait une même personne en deux fiches (ex. Verzin à 64
    # interventions + un doublon « Georges » à 2 -- bug signalé en prod).
    pairs = elus._build_name_registry(
        elus.load_db().get("seances", []), elus._load_video()
    )
    assert elus._key("Georges Verzin", pairs) == "verzin"
    assert elus._key("Verzin Georges", pairs) == "verzin"
    assert elus._key("VERZIN GEORGES", pairs) == "verzin"
    assert elus._key("Nimal Frederic", pairs) == "nimal"
    # Un même mot peut être le nom de famille d'une personne et le prénom
    # d'une autre (« Bernard ») : sans preuve de paire inversée, on ne
    # fusionne pas à tort (Axel Bernard != Bernard Tassier).
    assert elus._key("Bernard Tassier", pairs) == "tassier"
    assert elus._key("Axel Bernard", pairs) == "bernard"


def test_elus_list_has_no_reversed_name_duplicates():
    keys = {e["key"] for e in elus.elus_list()}
    # Ces clés n'existaient que par scission incorrecte (ordre « Nom Prénom »
    # pris pour le nom de famille) : elles ne doivent plus apparaître.
    assert "georges" not in keys
    assert "frederic" not in keys
    assert "vincent" not in keys
    assert "(ff)" not in keys
    assert "ff" not in keys


def test_key_resolves_reversed_particle_surname():
    # Nom à particule composé (« Van den Hove », « De Brabant ») lui aussi
    # mélangé dans les deux ordres selon les sources.
    assert elus._key("Van den Hove Quentin") == "hove"
    assert elus._key("Quentin Van den Hove") == "hove"
    assert elus._key("De Brabant Martin") == "brabant"
    assert elus._key("Martin de Brabant") == "brabant"
    # Nom à particule seul (sans prénom) : pas un ordre inversé.
    assert elus._key("de Brabant") == "brabant"
    assert elus._key("Van den Hove") == "hove"


def test_is_role_token_handles_parentheses():
    # « Jodogne (Bourgmestre ff) » ne doit pas produire une clé "(ff)" à part
    # -- le rôle entre parenthèses doit être reconnu comme les autres.
    assert elus._is_role_token("(ff)")
    assert elus._is_role_token("(Bourgmestre")
    assert elus._is_role_token("ff)")


def test_respondents_rejects_implausible_seance_metadata():
    # Garde-fou contre un artefact d'extraction PDF : bourgmestre_ff contenant
    # une phrase entière mal extraite ne doit jamais devenir un "nom".
    seance = {
        "bourgmestre_ff": (
            "De werking van de burgerinterpellaties-Vraag van Mevrouw "
            "Houaria Ouazrhari Madame Ouazrhari expose son point Madame la"
        ),
    }
    assert elus._respondents("Bourgmestre ff", seance) == []


def test_clean_strips_trailing_period():
    assert elus._clean("Diana Dolce.") == "Diana Dolce"


def test_display_name_enriched_from_intervenants_list():
    # « M. Denys »/« Denys » (répondant, sans prénom) et « Luc Denys »/
    # « Luc DENYS » (intervenants, jamais auteur·e ni répondant·e) désignent
    # la même personne : le nom d'affichage doit reprendre le prénom connu
    # ailleurs, sans que cela ajoute une intervention comptabilisée.
    d = elus.elu_detail("denys")
    assert d is not None
    assert d["nom"] == "Luc Denys"
    d2 = elus.elu_detail("decoux")
    assert d2 is not None
    assert d2["nom"] == "Dominique Decoux"


def test_split_person_names_handles_role_prefix_and_compounds():
    # Un intervenant peut porter un rôle collé au nom, ou désigner plusieurs
    # personnes à la fois : jamais un simple split() naïf.
    assert elus._split_person_names("Bourgmestre Audrey Henry") == ["Audrey Henry"]
    assert elus._split_person_names("MM. Bouhjar et El Arnouki") == ["Bouhjar", "El Arnouki"]
    assert elus._split_person_names("Secrétaire communal") == []
    assert elus._split_person_names("Messieurs Verzin et de Beauffort") == ["Verzin", "de Beauffort"]


def test_display_name_prefers_correct_order_and_rejects_junk_variants():
    # Une mention en ordre inversé (« Verzin Georges ») ou un artefact
    # d'extraction avec mot répété (« Bernard BERNARD ») ne doivent jamais
    # gagner face à une variante déjà en bon ordre.
    variants = {"Verzin Georges": 1, "Georges Verzin": 5, "VERZIN GEORGES": 1}
    assert elus._best_display_variant(variants, "verzin") == "Georges Verzin"
    variants2 = {"Bernard BERNARD": 3, "Axel Bernard": 1}
    assert elus._best_display_variant(variants2, "bernard") == "Axel Bernard"


def test_elus_list_has_no_role_word_or_compound_display_names():
    # Régression : l'enrichissement du nom d'affichage via la liste des
    # intervenant·e·s (test_display_name_enriched_from_intervenants_list)
    # traitait autrefois chaque mention telle quelle, laissant fuiter des
    # rôles collés (« Bourgmestre Clerfayt ») et des mentions à plusieurs
    # personnes (« Messieurs Verzin et de Beauffort ») comme "meilleur" nom.
    lst = elus.elus_list()
    keys = {e["key"] for e in lst}
    assert "communal" not in keys  # « Secrétaire communal » n'est pas une personne
    for e in lst:
        toks = [elus._norm_tok(t) for t in e["nom"].split()]
        assert toks[-1] == e["key"], e  # ordre « Prénom (particule) Nom »
        assert len(set(toks)) == len(toks), e  # pas de mot répété (artefact)
        assert not any(elus._is_role_token(t) for t in e["nom"].split()), e


def test_display_name_override_for_names_absent_from_sources():
    # « Malingreau » n'apparaît que par son seul nom de famille dans le PV
    # (aucune source ne donne son prénom) : complété manuellement.
    d = elus.elu_detail("malingreau")
    assert d is not None
    assert d["nom"] == "Alain Malingreau"
    d2 = elus.elu_detail("smeysters")
    assert d2 is not None
    assert d2["nom"] == "Christine Smeysters"
    d3 = elus.elu_detail("sobieski")
    assert d3 is not None
    assert d3["nom"] == "Christine Sobieski"


def test_titlecase_particles_and_caps():
    assert elus._titlecase("DEGREZ") == "Degrez"
    assert elus._titlecase("Yvan de Beauffort") == "Yvan de Beauffort"


def test_respondents_splits_compounds_and_resolves_roles():
    seance = {"bourgmestre": "Bernard Clerfayt", "bourgmestre_ff": "Cécile Jodogne"}
    # « X et le Bourgmestre » → X + le bourgmestre nommé (via la séance).
    names = elus._respondents("De Herde et M. le Bourgmestre", seance)
    keys = {elus._key(n) for n in names}
    assert "herde" in keys and "clerfayt" in keys
    # Rôle seul « la Bourgmestre ff » → résolu en Jodogne.
    assert elus._key(elus._respondents("la Bourgmestre ff", seance)[0]) == "jodogne"


# ── Liste des élu·e·s ────────────────────────────────────────────────────────
def test_elus_list_shape_and_sort():
    lst = elus.elus_list()
    assert lst and isinstance(lst, list)
    for e in lst:
        assert set(e) >= {"key", "nom", "role", "depose", "repond"}
        assert e["role"] in ("conseiller", "college")
    totals = [e["depose"] + e["repond"] for e in lst]
    assert totals == sorted(totals, reverse=True)  # tri par activité décroissante


# ── Détail : distinction des rôles ───────────────────────────────────────────
def test_verzin_is_conseiller_with_expected_activity():
    d = elus.elu_detail("verzin")
    assert d is not None
    assert d["role"] == "conseiller"
    c = d["counts"]
    # Agrégation structurée bien plus exhaustive que la recherche sémantique.
    assert c["questions"] >= 25
    assert c["demandes"] >= 20
    assert c["videos"] >= 1
    # INVARIANT clé : les motions ne sont attribuées que si l'auteur·e est nommé·e
    # dans le titre — on ne devine jamais depuis les intervenants (motions souvent
    # collectives). Verzin n'a donc aucune motion faussement attribuée.
    assert c["motions"] == 0


def test_detail_items_sorted_recent_first():
    d = elus.elu_detail("verzin")
    dates = [it["date"] for it in d["depose"]]
    assert dates == sorted(dates, reverse=True)
    # Chaque item déposé porte un libellé de type lisible.
    assert all(it["type_label"] for it in d["depose"])


def test_echevin_has_college_role_and_answers():
    # Un membre du Collège répond beaucoup et dépose peu → rôle « college ».
    d = elus.elu_detail("nimal")
    assert d is not None
    assert d["role"] == "college"
    assert d["counts"]["repond"] > d["counts"]["depose"]


def test_case_insensitive_key():
    assert elus.elu_detail("VERZIN")["key"] == "verzin"


# ── Endpoints HTTP ───────────────────────────────────────────────────────────
def test_endpoint_elus_list():
    r = client.get("/elus")
    assert r.status_code == 200
    assert len(r.json()["elus"]) > 10


def test_endpoint_elu_detail_and_404():
    r = client.get("/elu/verzin")
    assert r.status_code == 200
    assert r.json()["nom"] == "Georges Verzin"
    assert client.get("/elu/nom-inexistant-xyz").status_code == 404


# ── Coquilles et auteur·e·s non-personnes (chapitrage vidéo) ────────────────
def test_key_normalizes_known_typo():
    # « Houaria Ouazrhrari » (coquille dans un titre de chapitre vidéo) doit
    # rejoindre la même fiche que la graphie correcte, pas en créer une à part.
    assert elus._key("Houaria Ouazrhrari") == elus._key("Houaria Ouazrhari") == "ouazrhari"


def test_non_person_video_authors_excluded():
    # Ces « auteur·e·s » de chapitrage vidéo sont en réalité des organismes
    # (contre-signataires de points de convention/partenariat), jamais des
    # personnes : ils ne doivent produire aucune fiche.
    for org in ("CLAD", "Greentech VZW", "Gemeente Schaarbeek"):
        assert elus._is_non_person_video_author(org)
    keys = {e["key"] for e in elus.elus_list()}
    assert not keys & {"clad", "vzw", "schaarbeek"}


def test_ouazrhari_merged_and_org_authors_absent_from_index():
    idx = elus._index()
    assert "ouazrhrari" not in idx
    assert "ouazrhari" in idx
    for org_key in ("clad", "greentech vzw", "gemeente schaarbeek"):
        assert org_key not in idx


# ── Fusion PV/vidéo du même point ────────────────────────────────────────────
def test_match_pv_point_single_candidate():
    c = {"titre": "Le plan Good Move"}
    assert elus._match_pv_point("Le plan Good Move (Demande de Mme X)", [c]) is c


def test_match_pv_point_containment_among_several():
    wrong = {"titre": "Le chantier de la VRT"}
    right = {"titre": "Le plan Good Move"}
    assert elus._match_pv_point(
        "Le plan Good Move (Demande de Madame X) - Good Move (Verzoek van Mevrouw X)",
        [wrong, right],
    ) is right


def test_match_pv_point_no_good_candidate_returns_none():
    # Aucun des candidats ne correspond au sujet du point vidéo (cas réel du
    # corpus) : mieux vaut ne pas fusionner (deux entrées séparées) qu'une
    # fusion fausse.
    candidates = [{"titre": "Les rodéos urbains"},
                  {"titre": "Les nuisances dues aux travaux du siège de la VRT"}]
    video_titre = "Le non-remplacement d'une Echevine en 2024 (Motion de Monsieur Cédric MAHIEU)"
    assert elus._match_pv_point(video_titre, candidates) is None


def test_pv_and_video_same_point_merged_into_one_intervention():
    # Cas réel signalé : un point déposé (PV) dont la séance a aussi été
    # chapitrée en vidéo apparaissait deux fois (« Demande » + « Débat
    # filmé ») pour le même sujet. Doit maintenant n'être qu'UNE seule
    # intervention, avec le lien vidéo précis (l'instant du point) plutôt
    # que le lien générique de début de séance.
    d = elus.elu_detail("genevois")
    assert d is not None
    assert d["counts"]["depose"] == 1
    it = d["depose"][0]
    assert it["type"] == "demande_habitant"
    assert it["url"] and it["url"].endswith(".pdf")          # lien PV
    assert "&t=" in (it["video_url"] or "")                  # lien vidéo précis, pas générique


# ── Intégrité du fichier de chapitrage vidéo ─────────────────────────────────
def test_video_chapters_file_valid():
    path = ROOT / "backend" / "video_conseil_schaerbeek.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    seances = data.get("seances", data) if isinstance(data, dict) else data
    assert seances, "au moins une séance filmée chapitrée attendue"
    # Au moins un point porte un auteur ET un deep-link horodaté.
    pts = [p for s in seances for p in s.get("points", [])]
    assert any(p.get("auteur") for p in pts)
    assert any((p.get("deeplink") or "").startswith("http") for p in pts)
