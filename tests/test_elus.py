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
from services import elus, seances
from services.people.attribution import audit_authors, audit_respondents

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
    homonym_keys = set(elus._HOMONYM_KEY_OVERRIDES.values())
    for e in lst:
        toks = [elus._norm_tok(t) for t in e["nom"].split()]
        # Une clé d'homonyme (ex. "nyssens_marie") est délibérément composée,
        # pas le seul nom de famille — voir _HOMONYM_KEY_OVERRIDES.
        if e["key"] not in homonym_keys:
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


def test_respondents_bourgmestre_falls_back_to_ff_when_no_titulaire():
    # Cas réel (séances 2019-2020) : le PV dit juste « Bourgmestre » (sans
    # « ff »), mais la séance n'a AUCUN bourgmestre titulaire enregistré —
    # c'est donc le/la bourgmestre f.f. qui présidait et répondait. Sans ce
    # repli, la mention n'était pas résolue du tout (perdue).
    seance = {"bourgmestre": None, "bourgmestre_ff": "Cécile Jodogne"}
    assert elus._respondents("Bourgmestre", seance) == ["Cécile Jodogne"]
    assert elus._respondents("Madame la Bourgmestre", seance) == ["Cécile Jodogne"]


# ── Attribution de l'auteur·e (titre ET résumé) ──────────────────────────────
def test_author_of_falls_back_to_resume_when_title_has_no_author_mention():
    # Cas réel (SP 1, 22/04/2026) : le titre ne nomme personne, seul le
    # résumé le fait — et le 1er intervenant listé ("Ringoot") n'est PAS la
    # demandeuse (juste la 1re personne à être intervenue en débat). Avant
    # ce correctif, ~75% des demandes (384/511) tombaient dans ce cas et
    # étaient attribuées au hasard au 1er intervenant.
    point = {
        "type": "demande_habitant",
        "titre": "Les améliorations à apporter aux transports publics du nord de Bruxelles",
        "resume": "Demande de Madame Bernadette Dupont concernant les améliorations à apporter aux transports publics du nord de Bruxelles",
        "intervenants": ["Madame Ringoot", "Madame Dupont", "Madame Harzé"],
    }
    assert elus._author_of(point) == "Bernadette Dupont"


def test_author_of_motion_checks_resume_and_stays_collective_on_multiple_intervenants():
    # Une motion est attribuée si le titre ou le résumé nomme explicitement
    # l'auteur·e…
    point = {
        "type": "motion",
        "titre": "Retrait du règlement",
        "resume": "Motion de Monsieur Georges Verzin pour ...",
        "intervenants": ["Georges Verzin", "Bourgmestre"],
    }
    assert elus._author_of(point) == "Georges Verzin"

    # …mais reste COLLECTIVE (non attribuée) si aucun texte ne la nomme ET que
    # PLUSIEURS personnes sont intervenues (on ne prend jamais « la 1re »).
    point_collectif = {
        "type": "motion",
        "titre": "Retrait du règlement",
        "resume": "Le Collège présente un amendement.",
        "intervenants": ["Georges Verzin", "Cédric Mahieu"],
    }
    assert elus._author_of(point_collectif) is None


def test_author_of_motion_attributes_sole_named_intervenant():
    # Repli d'attribution : une motion sans auteur nommé dans le texte, mais
    # portée par UNE SEULE personne au débat, lui est attribuée.
    solo = {
        "type": "motion",
        "titre": "Motion pour le climat",   # pas de nom capté par la regex
        "resume": "",
        "intervenants": ["Vanhalewyn"],
    }
    assert elus._author_of(solo) == "Vanhalewyn"

    # Les mentions de rôle pur (préside/répond, ne propose pas) sont ignorées :
    # une seule personne nommée subsiste → attribution.
    solo_role = {
        "type": "motion", "titre": "Motion de soutien", "resume": "",
        "intervenants": ["Courtheoux", "Bourgmestre ff"],
    }
    assert elus._author_of(solo_role) == "Courtheoux"

    # Entrée composée (« MM. X et Y ») → ambiguë → jamais attribuée.
    ambigu = {
        "type": "motion", "titre": "Motion", "resume": "",
        "intervenants": ["MM. Bouhjar et El Arnouki"],
    }
    assert elus._author_of(ambigu) is None


def test_audit_authors_separates_anomalies_from_collective_motions():
    db = {"seances": [{
        "seance": {"date": "2017-05-31"},
        "points": [
            # Anomalie : question orale sans intervenant ni auteur dans le titre.
            {"sp": 63, "type": "question_orale", "titre": "Le taux de participation",
             "resume": "", "intervenants": []},
            # OK : demande avec auteur dans le titre → pas signalée.
            {"sp": 64, "type": "demande_habitant",
             "titre": "Demande de Monsieur Georges Verzin sur X", "intervenants": []},
            # Motion collective (débat multiple) → non attribuée, bucket motions.
            {"sp": 65, "type": "motion", "titre": "Motion sur le climat",
             "resume": "", "intervenants": ["Durant", "Verzin", "Goldstein"]},
            # Motion à intervenant·e unique → désormais ATTRIBUÉE (donc ABSENTE
            # du bucket). Titre volontairement sans nom capté par la regex.
            {"sp": 66, "type": "motion", "titre": "Motion pour le climat",
             "resume": "", "intervenants": ["Vanhalewyn"]},
        ],
    }]}
    report = audit_authors(db)
    anos = report["anomalies"]
    motions = report["motions_non_attribuees"]
    assert [(a["sp"], a["type"]) for a in anos] == [(63, "question_orale")]
    # SP66 (intervenant·e unique) est maintenant attribuée → seule SP65 reste.
    assert [m["sp"] for m in motions] == [65]


def test_audit_respondents_considers_type_and_status():
    db = {"seances": [{
        "seance": {"date": "2018-01-31", "bourgmestre": "Bernard Clerfayt"},
        "points": [
            # Question répondue → OK.
            {"sp": 1, "type": "question_orale", "repondant": "De Herde", "decision": ""},
            # Question sans répondant, non retirée → anomalie.
            {"sp": 2, "type": "question_orale", "repondant": None, "decision": ""},
            # Demande sans répondant, non retirée → anomalie.
            {"sp": 3, "type": "demande_habitant", "repondant": "", "decision": ""},
            # Question sans répondant MAIS reportée → statut exclut l'anomalie.
            {"sp": 4, "type": "question_orale", "repondant": None, "decision": "REPORTÉ"},
            # Question sans répondant mais retirée → exclue par statut.
            {"sp": 5, "type": "question_orale", "repondant": None, "decision": "RETIRÉ"},
            # Délibération sans répondant → type sans réponse attendue → ignorée.
            {"sp": 6, "type": "point_normal", "repondant": None, "decision": "APPROUVÉ"},
            # Question transformée en question écrite → réponse par écrit → exclue.
            {"sp": 7, "type": "question_orale", "repondant": None, "decision": "",
             "resume": "Question de M. X transformée en question écrite."},
        ],
    }]}
    report = audit_respondents(db)
    assert len(report) == 1
    r = report[0]
    assert r["sans_repondant"] == 2      # SP2 et SP3 seulement
    assert r["sp"] == [2, 3]


def test_author_from_text_prefers_full_name_over_particle_truncation():
    # La regex ne capture que des mots capitalisés : « Demande de Monsieur
    # Yvan de Beauffort » ne capte que « Yvan » (la particule « de » en
    # minuscules arrête la capture). Cas réel : 12 mentions concernées dans
    # le corpus. Un·e intervenant·e listé·e commençant par ce nom tronqué
    # donne la forme complète, plus fiable qu'un prénom seul.
    point = {
        "type": "demande_habitant",
        "titre": "Le plan régional de stationnement de la Région Bruxelloise",
        "resume": "Demande de Monsieur Yvan de Beauffort concernant le plan régional de stationnement de la Région Bruxelloise.",
        "intervenants": ["Yvan de Beauffort", "Denis Grimberghs"],
    }
    assert elus._author_of(point) == "Yvan de Beauffort"


def test_author_in_title_matches_without_de_between_demande_and_name():
    # Cas réel (SP 75, 22/04/2026) : « Demande M. DEMIRHAN » — pas de « de »
    # entre « Demande » et la civilité, contrairement à « Demande de M. X ».
    # Le « de/du » de la tournure standard doit donc être optionnel.
    assert elus._AUTHOR_IN_TITLE.search("Aménagements (Demande M. DEMIRHAN)").group(1) == "DEMIRHAN"
    assert elus._AUTHOR_IN_TITLE.search("Sujet (Demande Mme DOUHRI)").group(1) == "DOUHRI"


def test_author_in_title_does_not_capture_lowercase_accented_word():
    # Régression du même correctif : rendre le « de » optionnel a d'abord eu
    # pour effet de bord de capter un mot lambda après un « Demande » employé
    # comme verbe en milieu de phrase (« Demande également aux autorités... »)
    # — la classe [A-ZÀ-Ÿ]/[À-Ÿ] inclut par erreur des minuscules accentuées
    # (« é » U+00E9 tombe dans la plage À(00C0)-Ÿ(0178)). Cas réel exact.
    text = (
        "Motion demandant la libération immédiate et inconditionnelle de trois "
        "prisonniers politiques. Demande également aux autorités fédérales "
        "belges d'intervenir."
    )
    assert elus._AUTHOR_IN_TITLE.search(text) is None


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
    # INVARIANT : une motion n'est attribuée que si l'auteur·e est nommé·e
    # explicitement (titre OU résumé) OU s'il/elle en est le/la SEUL·E
    # intervenant·e (jamais « le 1er » d'un débat collectif). Verzin a 3 motions
    # où il est nommé au résumé + 1 dont il est l'unique intervenant
    # (2017-12-20 « Schaerbeek commune hospitalière »), soit 4.
    assert c["motions"] == 4


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


def test_role_of_college_even_with_few_answers_and_no_deposits():
    # Le champ « répondant » n'est renseigné que par un membre du Collège :
    # répondre au moins une fois sans jamais déposer suffit à qualifier
    # « college », même avec un petit nombre de réponses (ex. mandat
    # écourté) — le seuil de 8 réponses ne doit s'appliquer qu'en cas
    # d'activité mixte (dépôts ET réponses).
    assert elus._role_of(0, 1) == "college"
    assert elus._role_of(0, 3) == "college"
    # Activité mixte avec peu de réponses : signal trop faible, on reste
    # prudent et on garde « conseiller » (ex. présidence ponctuelle de séance).
    assert elus._role_of(10, 1) == "conseiller"
    assert elus._role_of(0, 0) == "conseiller"


def test_college_member_with_few_answers_counted_correctly_in_list():
    # Régression : Bertrand Dhuyvetter (0 dépôt, 3 réponses) était classé
    # « conseiller » (seuil de 8 non atteint), ce qui faisait aussi
    # afficher "(0)" dans le sélecteur côté frontend (n = depose pour un
    # rôle non-college). Doit être « college » avec 3 interventions.
    lst = elus.elus_list()
    d = next((e for e in lst if e["key"] == "dhuyvetter"), None)
    assert d is not None
    assert d["role"] == "college"
    assert d["depose"] == 0
    assert d["repond"] == 3


# ── Rôle par date (mandats déclaratifs, voir services.people.mandats) ──────
def test_elu_detail_exposes_declarative_mandate_history():
    d = elus.elu_detail("verzin")
    assert d is not None
    assert d["mandats"] == {
        "conseiller": [{"debut": 1982, "fin": None}],
        "echevin": [{"debut": 1988, "fin": 2000}, {"debut": 2006, "fin": 2012}],
    }


def test_elu_detail_mandats_none_when_absent_from_declarative_data():
    # Une personne du registre PV absente du fichier déclaratif
    # (elus_mandats.json) : mandats=None (le frontend retombe alors sur le
    # libellé "role" simple, voir elus.js/renderElu), jamais d'exception.
    d = elus.elu_detail("dhuyvetter")
    assert d is not None
    assert d["mandats"] is None


def test_role_field_reflects_current_mandate_not_lifetime_activity_for_ex_college_member():
    # Cécile Jodogne : très active comme échevine/bourgmestre ff par le
    # passé (des centaines de réponses en séance) — l'heuristique d'activité
    # seule (_role_of) la classerait "college" à vie. Ses mandats déclaratifs
    # montrent qu'elle est aujourd'hui simple conseillère (échevinat clos en
    # 2014, bourgmestre ff clos en 2024, seul le mandat de conseillère reste
    # ouvert) : le sélecteur "Tous les rôles" doit refléter son rôle ACTUEL.
    d = elus.elu_detail("jodogne")
    assert d is not None
    assert d["counts"]["repond"] > 400   # l'heuristique seule dirait "college"
    assert d["role"] == "conseiller"


def test_role_field_reflects_current_mandate_not_lifetime_activity_for_new_college_member():
    # Cédric Mahieu : conseiller de longue date (128 dépôts), échevin
    # seulement depuis 2025 — l'heuristique d'activité (dominée par ses
    # années de conseiller) le classerait "conseiller" ; ses mandats
    # déclaratifs le classent "college" dès qu'il devient échevin.
    d = elus.elu_detail("mahieu")
    assert d is not None
    assert d["counts"]["depose"] > d["counts"]["repond"]   # l'heuristique seule dirait "conseiller"
    assert d["role"] == "college"


def test_case_insensitive_key():
    assert elus.elu_detail("VERZIN")["key"] == "verzin"


def test_repond_items_carry_demandeur_when_known():
    # Chaque ligne de réponse doit pouvoir s'afficher seule, hors contexte de
    # page (ex. capture d'écran) : elle porte donc le nom de la personne à
    # l'origine du point, quand la question/demande est attribuable.
    d = elus.elu_detail("nimal")
    with_demandeur = [it for it in d["repond"] if it.get("demandeur")]
    assert with_demandeur
    it = next(x for x in d["repond"] if x["sp"] == 63 and x["date"] == "2026-03-25")
    # Nom canonisé (même casse/ordre que sur la fiche de la personne), pas le
    # texte brut du PV (ex. « YILDIZ Yusuf » ou « Yusuf YILDIZ »).
    assert it["demandeur"] == "Yusuf Yildiz"
    # Certaines réponses (points administratifs, sans auteur individuel
    # identifiable) n'ont légitimement pas de demandeur.
    assert any(it.get("demandeur") is None for it in d["repond"])


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


def test_key_normalizes_erlay_typo_to_eraly():
    # « Monsieur Erlay » (coquille ponctuelle dans un PV) doit rejoindre la
    # fiche de Thomas Eraly, pas créer une fiche « Erlay » à 1 seule entrée.
    assert elus._key("Monsieur Erlay") == elus._key("Eraly") == "eraly"
    assert "erlay" not in elus._index()


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
    # Signal explicite pour le frontend (au lieu de déduire « précis » du
    # simple fait qu'il y ait un « &t= » dans l'URL) : distingue un lien
    # fusionné avec le chapitre vidéo d'un lien de séance générique.
    assert it["video_precise"] is True


def test_video_url_not_precise_when_no_chapter_match():
    d = elus.elu_detail("verzin")
    generic = [it for it in d["depose"] if it["type"] != "video" and it.get("video_url") and not it["video_precise"]]
    assert generic  # Verzin a des points sans chapitre vidéo correspondant.
    for it in generic:
        assert "&t=" not in it["video_url"]


def test_repondant_display_name_is_canonicalized_not_raw_pv_text():
    # Cas réel signalé (capture d'écran) : le champ « repondant » du PV est
    # souvent brut (juste le nom de famille, en MAJUSCULES, ordre Nom Prénom)
    # et incohérent selon les séances — la fiche de la personne elle-même
    # utilise toujours le nom complet, correctement casé et ordonné. Le champ
    # affiché « Répondant·e » doit être résolu de la même façon plutôt que de
    # recopier le texte brut du PV.
    d = elus.elu_detail("douhri")
    it = next(x for x in d["depose"] if x["date"] == "2026-04-22")
    assert it["repondant"] == "Thomas Eraly"          # PV brut : « ERALY Thomas »

    d2 = elus.elu_detail("degrez")
    it2 = next(x for x in d2["depose"] if x["date"] == "2025-10-15")
    # Répondant composé (plusieurs personnes) : chaque nom canonisé, joint
    # par « et », pas la juxtaposition brute du PV.
    assert it2["repondant"] == "Vincent Vanhalewyn et Martin de Brabant"


# ── Vue par séance (onglet « Séances ») ──────────────────────────────────────
def test_seances_list_shape_and_sort():
    lst = elus.seances_list()
    assert lst and isinstance(lst, list)
    for s in lst:
        assert set(s) >= {"date", "n_points", "url", "video_url"}
        # >= 0, pas > 0 : une séance filmée sans PV ni chapitrage vidéo
        # encore fait (voir test_seances_list_includes_video_only_seance)
        # a 0 point, mais reste listée (son lien vidéo reste utile).
        assert s["n_points"] >= 0
    dates = [s["date"] for s in lst]
    assert dates == sorted(dates, reverse=True)  # plus récente en premier


def test_seances_list_includes_video_only_seance():
    """Séance filmée mais sans PV encore extrait/apparié (cas réel :
    2026-06-24, la plus récente au moment d'écrire ce test) : doit quand
    même apparaître dans la liste — sinon invisible dans l'onglet Séances
    pour son année, alors que le débat est disponible."""
    lst = elus.seances_list()
    s = next((x for x in lst if x["date"] == "2026-06-24"), None)
    assert s is not None
    assert s["url"] is None            # pas de PDF : pas de PV pour cette date
    assert s["video_url"]
    assert s["n_points"] == 15


def test_seance_detail_unknown_date_returns_none():
    assert elus.seance_detail("1999-01-01") is None


def test_seance_detail_video_only_seance_builds_points_from_chapters():
    """Sans PV, chaque chapitre vidéo devient un point à part entière (aucun
    candidat PV à apparier) — la séance reste consultable, juste sans PDF."""
    d = elus.seance_detail("2026-06-24")
    assert d is not None
    assert d["url"] is None
    assert d["video_url"]
    assert d["n_points"] == len(d["points"]) == 15
    assert all(p["type"] == "video" for p in d["points"])
    assert all(p["url"] for p in d["points"])  # deep-link précis par chapitre


def test_seance_detail_lists_every_point_including_unattributed():
    # Cas réel : chaque point du PV doit apparaître, y compris ceux sans
    # demandeur·se individuel·le identifiable (points collectifs/administratifs,
    # ex. approbations de convention) — pas seulement les points attribuables.
    d = elus.seance_detail("2026-04-22")
    assert d is not None
    assert d["date"] == "2026-04-22"
    assert d["n_points"] == len(d["points"])
    # Au moins les 59 points du PV brut, plus les chapitres vidéo collectifs
    # (sans auteur·e individuel·le) dont le seuil de fusion plus strict
    # (0.6, voir _match_pv_point) ne trouve pas de correspondance assez sûre
    # — affichés à part plutôt que silencieusement omis (voir
    # test_seance_detail_merges_collective_video_chapter_without_author).
    assert d["n_points"] >= 59
    unattributed = [p for p in d["points"] if not p["demandeur"] and not p["repondant"]]
    assert unattributed  # ex. « Hommage à M. Jacques Bouvier »


def test_video_chapter_without_author_still_gives_precise_link_in_elu_view():
    # Cas réel (29/05/2024, SP 58 — recours Boulevard Lambermont, demande de
    # Georges Verzin) : le chapitre vidéo correspondant n'a PAS de champ
    # `auteur` dans la source. L'index par personne sautait purement et
    # simplement ces chapitres — la fiche affichait donc le lien GÉNÉRIQUE de
    # séance là où l'onglet Séances, qui apparie déjà les chapitres sans
    # auteur·e (seuil 0.6), donnait le lien PRÉCIS pour le même point.
    d = elus.elu_detail("verzin")
    it = next(x for x in d["depose"] if x["date"] == "2024-05-29")
    assert it["video_precise"] is True
    assert "&t=" in it["video_url"]
    # Les deux vues doivent pointer exactement le même instant.
    p = next(x for x in elus.seance_detail("2024-05-29")["points"] if x["sp"] == 58)
    assert it["video_url"] == p["video_url"]


def test_video_chapter_without_author_never_attributes_authorship():
    # La 2e passe n'apporte qu'un LIEN : un chapitre sans auteur·e ne doit
    # créer aucune intervention ni attribuer quoi que ce soit à quelqu'un
    # (contrairement aux chapitres attribués, qui peuvent devenir un « Débat
    # filmé » autonome faute de point PV apparié).
    avant = {k: len(e["depose"]) for k, e in elus._index().items()}
    video = [s for s in elus._load_video() if s.get("date") == "2024-05-29"]
    sans_auteur = [p for s in video for p in s.get("points", []) if not (p.get("auteur") or "").strip()]
    assert sans_auteur          # le cas testé existe bien dans le corpus
    # Aucun « Débat filmé » autonome n'est né de ces chapitres à cette date.
    for k, e in elus._index().items():
        assert len(e["depose"]) == avant[k]
        assert not [it for it in e["depose"] if it["type"] == "video" and it["date"] == "2024-05-29"]


def test_seance_detail_demandeur_repondant_and_video_precise_match_elu_view():
    # Même cas réel que la vue par élu·e (Yousra Douhri, 22/04/2026) : les
    # deux vues doivent s'accorder — même registre de noms canoniques, même
    # fusion PV/vidéo point à point (voir _match_pv_point).
    d = elus.seance_detail("2026-04-22")
    it = next(p for p in d["points"] if p["sp"] == 74)
    assert it["type"] == "demande_habitant"
    assert it["demandeur"] == "Yousra Douhri"
    assert it["repondant"] == "Thomas Eraly"
    assert it["video_precise"] is True
    assert "&t=" in it["video_url"]
    # Rôle résolu à la date de CETTE séance (voir services.people.mandats) :
    # Douhri conseillère depuis 2024, Eraly échevin depuis 2025 — les deux
    # exacts pour le 22/04/2026.
    assert it["demandeur_role"] == "conseiller"
    assert it["repondant_role"] == "college"


def test_seance_point_lists_each_respondent_individually():
    # Cas réel (29/09/2010, SP 96) : le PV donne « Mme la Bourgmestre ff, Mme
    # Essaidi ». L'AFFICHAGE recolle les deux noms — un point montre tous ses
    # répondant·e·s — mais le filtre par intervenant·e de l'onglet Séances a
    # besoin de la liste INDIVIDUELLE, sans quoi « Cécile Jodogne et Tamimount
    # Essaidi » devenait une « personne » unique, introuvable en cherchant
    # l'un des deux noms.
    p = next(x for x in elus.seance_detail("2010-09-29")["points"] if x["sp"] == 96)
    assert p["repondant"] == "Cécile Jodogne et Tamimount Essaidi"
    assert [x["nom"] for x in p["repondants"]] == ["Cécile Jodogne", "Tamimount Essaidi"]


def test_suite_runs_against_the_real_written_questions_base():
    # Garde-fou : QE_JSON_PATH a un défaut RELATIF, qui ne se résout que
    # depuis backend/ (le rootDir de production). Sans le réglage de conftest,
    # la suite tournait donc sur une base amputée de toutes les questions
    # écrites — silencieusement, puisque le chargeur tolère le fichier absent.
    # Ce test échoue si ce réglage disparaît, plutôt que de laisser des
    # centaines d'assertions porter sur des données incomplètes.
    from services.questions_ecrites import load_qe_db
    assert len(load_qe_db().get("questions", [])) > 100
    d = elus.elu_detail("verzin")
    assert [it for it in d["depose"] if it["type"] == "question_ecrite"]


def test_deliberative_point_shows_pv_intervenants():
    # 24/09/2025 SP 6 et SP 7 (primes Be Home / accompagnement social) ont été
    # débattus ensemble : le PV leur donne les MÊMES cinq intervenant·e·s et le
    # même répondant. SP 6 les affichait (attribution manuelle), SP 7 non — le
    # champ « intervenants » d'un point délibératif était ignoré, ne laissant
    # surface qu'aux 9 attributions manuelles. Les deux doivent s'accorder.
    pts = elus.seance_detail("2025-09-24")["points"]
    sp6, sp7 = (next(p for p in pts if p["sp"] == n) for n in (6, 7))
    attendu = ["Cécile Jodogne", "Naïma Belkhatir", "Georges Verzin",
               "Matthieu Degrez", "Elias Ammi"]
    assert [x["nom"] for x in sp6["demandeurs"]] == attendu
    assert [x["nom"] for x in sp7["demandeurs"]] == attendu
    assert sp6["repondant"] == sp7["repondant"] == "Cédric Mahieu"


def test_deliberative_point_intervenants_never_repeat_the_respondent():
    # 03/02/2010 SP 3 : le PV liste Michel de Herde EN TÊTE des intervenant·e·s
    # ET comme répondant (il préside le débat autant qu'il y répond) — le champ
    # « intervenants » ne fait pas la différence. Sa ligne de répondant suffit.
    p = next(x for x in elus.seance_detail("2010-02-03")["points"] if x["sp"] == 3)
    noms = [x["nom"] for x in p["demandeurs"]]
    assert p["repondant"] == "Michel de Herde"
    assert p["repondant"] not in noms
    # Les 8 autres restent, dans l'ordre du PV (de Herde y était en tête).
    assert len(noms) == 8
    assert noms[0].endswith("Courtheoux")


def test_deliberative_point_intervenants_do_not_become_depositions():
    # Garde-fou : afficher les intervenant·e·s d'un point délibératif est un
    # choix D'AFFICHAGE (onglet Séances). Intervenir dans un débat n'est pas
    # déposer un point — l'agrégation par personne ne doit rien y gagner,
    # sinon les « interventions déposées » de chacun·e enflent d'un coup.
    sp7 = next(p for p in elus.seance_detail("2025-09-24")["points"] if p["sp"] == 7)
    assert sp7["demandeurs"]                       # affichés côté séance…
    for nom in [x["nom"] for x in sp7["demandeurs"]]:
        d = elus.elu_detail(elus._key(nom))
        assert d is not None
        assert not [it for it in d["depose"]       # …mais jamais comptés ici
                    if it["date"] == "2025-09-24" and it["sp"] == 7]


def test_seance_point_respondents_keep_their_own_role():
    # 03/03/2010 SP 12 : Frédéric Nimal (conseiller à cette date) répond aux
    # côtés de Cécile Jodogne (Collège). Le rôle COMBINÉ du point vaut
    # "college" (voir _combined_role) — c'est ce qui masquait Nimal au filtre
    # « Conseiller·ère ». Chaque personne porte donc désormais SON rôle.
    p = next(x for x in elus.seance_detail("2010-03-03")["points"] if x["sp"] == 12)
    assert p["repondant_role"] == "college"          # résumé du point, inchangé
    assert p["repondants"] == [
        {"nom": "Frédéric Nimal", "role": "conseiller"},
        {"nom": "Cécile Jodogne", "role": "college"},
    ]


def test_seance_point_respondent_not_resolved_as_person_kept_as_single_entry():
    # « Secrétaire Communal » ne se résout en aucune personne du registre :
    # la mention est conservée telle quelle, en UNE entrée sans rôle — sinon
    # elle disparaîtrait du filtre par intervenant·e, où elle figurait avant.
    p = next(x for x in elus.seance_detail("2012-12-05")["points"] if x["sp"] == 1)
    assert p["repondant"] == "Secrétaire Communal"
    assert p["repondants"] == [{"nom": "Secrétaire Communal", "role": None}]


def test_seance_detail_role_reflects_mandate_at_seance_date_not_a_fixed_label():
    # Frédéric Nimal (clé "nimal") n'est devenu échevin qu'en 2012 (mandats
    # déclaratifs, voir elus_mandats.json) : un point de 2010 où il répond
    # doit le classer "conseiller", pas "college" — contrairement à un rôle
    # unique figé par personne (ce que fait la vue Par élu·e, elus.role, pour
    # le sélecteur "Tous les rôles" — voir test_echevin_has_college_role_and_answers
    # qui le classe "college" comme rôle DOMINANT sur toute sa carrière).
    before = elus.seance_detail("2010-03-03")
    pt = next(p for p in before["points"] if p["repondant"] == "Frédéric Nimal")
    assert pt["repondant_role"] == "conseiller"

    after = elus.seance_detail("2025-10-15")
    pt2 = next(p for p in after["points"] if p["repondant"] == "Frédéric Nimal")
    assert pt2["repondant_role"] == "college"


def test_seance_detail_resolves_demandeur_named_only_in_resume():
    # Régression : SP 1 de cette même séance a son auteure (Bernadette
    # Dupont) nommée seulement dans le résumé, pas le titre ; le 1er
    # intervenant listé ("Ringoot") n'est pas la demandeuse. Sans le
    # correctif d'_author_of, ce point était mal attribué ET son chapitre
    # vidéo restait une entrée "Débat filmé" séparée (la clé d'auteur ne
    # correspondait pas), au lieu d'être fusionné.
    d = elus.seance_detail("2026-04-22")
    it = next(p for p in d["points"] if p["sp"] == 1)
    assert it["demandeur"] == "Bernadette Dupont"
    assert it["video_precise"] is True
    assert not any(
        p["type"] == "video" and "transports publics du nord" in p["titre"]
        for p in d["points"]
    )


def test_seance_detail_resolves_demandeur_from_title_without_de():
    # Régression : SP 75 de cette même séance, « Demande M. DEMIRHAN » (pas
    # de « de » dans le titre) n'était pas attribué à Demirhan avant le
    # correctif de _AUTHOR_IN_TITLE, empêchant aussi la fusion avec son
    # chapitre vidéo.
    d = elus.seance_detail("2026-04-22")
    it = next(p for p in d["points"] if p["sp"] == 75)
    assert it["demandeur"] == "Salih Demirhan"
    assert it["video_precise"] is True


def test_seance_detail_merges_collective_video_chapter_without_author():
    # Cas réel signalé : SP 36 (27/05/2026), motion collective du Collège
    # sur le maintien de l'hôpital Paul Brien — le chapitre vidéo n'a pas
    # d'auteur·e individuel·le (motion collective), donc pas de "clé
    # personne" pour restreindre les candidats comme pour les points avec
    # demandeur·se. Sans repli sur une comparaison au titre de TOUTE la
    # séance, ce chapitre (et ~59% des chapitres vidéo du corpus, tous les
    # points collectifs sans auteur·e) était silencieusement omis de cette
    # vue — ni fusionné, ni même affiché à part.
    d = elus.seance_detail("2026-05-27")
    it = next(p for p in d["points"] if p["sp"] == 36)
    assert it["type"] == "motion"
    assert it["video_precise"] is True
    assert "&t=" in it["video_url"]
    # Aucun chapitre vidéo n'a dû être perdu : au moins celui-ci, fusionné.
    assert not any(p["type"] == "video" and "Paul Brien" in p["titre"] for p in d["points"])


def test_manual_author_overrides_for_point_normal():
    # Cas réels signalés (22/04/2026) : les points de type "point_normal"
    # (administratifs/collectifs, ex. approbation de convention) ne sont
    # jamais attribués automatiquement — le champ "intervenants" y mélange
    # sans distinction citoyen·ne·s/conseiller·ère·s à l'origine de la
    # discussion et membres du Collège qui la président, donc pas de règle
    # générale fiable (voir _author_of). Ces 3 cas ont été vérifiés
    # individuellement (PV + transcript vidéo) et ajoutés manuellement.
    d = elus.seance_detail("2026-04-22")
    cases = {
        12: "Matthieu Degrez",   # ASBL PRO VELO — seul intervenant listé au PV
        13: "Georges Verzin",    # Théâtre de La Balsamine — 1er intervenant listé
        15: "Quentin van den Hove",  # Cimetière parcelle 26 — intervenants vide au
        # PV, retrouvé dans le transcript vidéo ("je vois monsieur Quentin
        # Vandenov qui... veut prendre la parole").
    }
    for sp, nom in cases.items():
        it = next(p for p in d["points"] if p["sp"] == sp)
        assert it["demandeur"] == nom
        assert it["video_precise"] is True


def test_manual_author_override_joint_debate_two_names():
    # SP32 (15/10/2025, "Subside exceptionnel à l'asbl Xtreme Team Parkour")
    # est débattu conjointement avec SP31 ("... WAPA International") par
    # deux intervenants (Clerfayt puis Van den Hove, transcript vidéo) —
    # SP31 liste déjà les deux au PV, SP32 avait "intervenants": [] avant
    # correction manuelle. Override à deux noms ("X et Y") : la clé
    # d'agrégation par personne (_point_author) utilise la 1ère personne,
    # mais l'affichage résout et joint les deux (comme "repondant").
    d = elus.seance_detail("2025-10-15")
    it = next(p for p in d["points"] if p["sp"] == 32)
    assert it["demandeur"] == "Bernard Clerfayt et Quentin van den Hove"
    assert it["repondant"] == "Abobakre Bouhjar"


def test_manual_author_override_joint_debate_five_names():
    # SP6 (24/09/2025, "Prime Be Home Schaerbeekoise") est débattu
    # conjointement avec SP7 ("Primes d'accompagnement social") — le
    # transcript vidéo le dit explicitement (« nous mêlons deux points »).
    # SP7 liste déjà 5 intervenant·e·s + répondant au PV, SP6 avait
    # "intervenants": [] avant correction manuelle à l'identique. Vérifie
    # que l'override à N noms (N > 2) résout et joint chaque personne.
    d = elus.seance_detail("2025-09-24")
    it = next(p for p in d["points"] if p["sp"] == 6)
    # Ponctuation à la française (voir utils.text.liste_fr) : « A, B et C »,
    # pas « A et B et C » — cinq noms recollés par « et » se lisaient comme un
    # libellé brut du PV, alors que l'app les a bel et bien séparés.
    assert it["demandeur"] == (
        "Cécile Jodogne, Naïma Belkhatir, Georges Verzin, "
        "Matthieu Degrez et Elias Ammi"
    )
    assert it["repondant"] == "Cédric Mahieu"


def test_manual_author_override_joint_debate_repondant_also_intervenant():
    # SP60 (25/06/2025, "Nouvelle tarification des infrastructures
    # sportives") est débattu conjointement avec SP59 ("Règlement général
    # pour l'occupation de locaux et terrains communaux") — les chiffres
    # cités dans le transcript (75.277,98€ → 190.571,27€, réductions, âge
    # 21→18) correspondent exactement au résumé PV de SP60. SP59 liste déjà
    # les intervenant·e·s et le répondant au PV, SP60 avait
    # "intervenants": [] avant correction manuelle à l'identique. Cas
    # particulier : l'échevin répondant (Bouhjar) figure aussi parmi les
    # intervenant·e·s du PV (il participe au débat en plus d'y répondre) — il
    # est donc RETIRÉ de la ligne des intervenant·e·s, où il faisait doublon
    # avec sa propre ligne de répondant (un point délibératif n'a pas
    # d'auteur·e : cette ligne liste qui est intervenu, voir TYPE_ACTOR_LABEL
    # « Intervenant·e·s »).
    d = elus.seance_detail("2025-06-25")
    it = next(p for p in d["points"] if p["sp"] == 60)
    assert it["demandeur"] == (
        "Saït Köse, Ibrahim Dönmez, Elias Ammi et Yvan de Beauffort"
    )
    assert it["repondant"] == "Abobakre Bouhjar"
    # Retiré de l'affichage, mais toujours atteignable par le filtre : il
    # reste dans `repondants`, d'où le point se retrouve par son nom.
    assert [x["nom"] for x in it["repondants"]] == ["Abobakre Bouhjar"]
    assert "Abobakre Bouhjar" not in [x["nom"] for x in it["demandeurs"]]


def test_manual_author_override_joint_debate_three_sibling_points():
    # SP21/SP23/SP24 (23/04/2025) sont débattus conjointement avec SP22
    # ("Convention relative à la gestion des immeubles expropriés de la rue
    # du Progrès") — le conseil traite les 4 points comme un seul dossier
    # (règlement d'allocation aux locataires expropriés par Infrabel). SP22
    # liste déjà les intervenant·e·s et le répondant au PV, les 3 autres
    # avaient "intervenants": [] avant correction manuelle à l'identique.
    # Cas particulier : un même override (3 points, même clé de personnes)
    # appliqué à plusieurs points d'une même séance.
    d = elus.seance_detail("2025-04-23")
    expected = (
        "Sadik Köksal, Leila Lahssaini, Matthieu Degrez, "
        "Cécile Jodogne et Isabelle Durant"
    )
    for sp in (21, 23, 24):
        it = next(p for p in d["points"] if p["sp"] == sp)
        assert it["demandeur"] == expected
        assert it["repondant"] == "Thomas Eraly"


def test_match_pv_point_higher_threshold_for_large_candidate_pool():
    # Le seuil par défaut (0.35, calibré pour un petit nombre de candidats
    # déjà restreints par personne) donnerait trop de faux positifs sur un
    # grand bassin de candidats nombreux (points collectifs, comparés à TOUS
    # les points de la séance) — un seuil plus élevé est nécessaire.
    candidates = [{"titre": "Les rodéos urbains"}, {"titre": "Les nuisances dues aux travaux du siège de la VRT"}]
    video_titre = "Le non-remplacement d'une Echevine en 2024 (Motion de Monsieur Cédric MAHIEU)"
    assert elus._match_pv_point(video_titre, candidates, threshold=0.6) is None
    # Un bon candidat (score élevé par inclusion) reste trouvé même avec le
    # seuil relevé, y compris parmi plusieurs candidats (pas de raccourci
    # "candidat unique").
    candidates2 = [
        {"titre": "Modification du cadre du personnel"},
        {"titre": "Les rodéos urbains"},
    ]
    assert elus._match_pv_point(
        "Modification du cadre du personnel - Wijziging van de personeelsformatie",
        candidates2, threshold=0.6,
    ) == candidates2[0]


def test_seance_detail_flags_postponed_points():
    # Cas réel signalé : SP 51 (27/05/2026), question d'Elias Ammi sur les
    # repas gratuits, a été REPORTÉE — jamais débattue ce jour-là, donc pas
    # de répondant·e ni de débat filmé à en attendre. La box du point doit
    # pouvoir l'indiquer plutôt que de laisser croire à un point non traité.
    d = elus.seance_detail("2026-05-27")
    it = next(p for p in d["points"] if p["sp"] == 51)
    assert it["demandeur"] == "Elias Ammi"
    assert it["reporte"] is True
    assert it["repondant"] is None
    # Un point normalement débattu (décision autre que "reporté") ne l'est pas.
    not_reported = next(p for p in d["points"] if p["sp"] == 1)
    assert not_reported["reporte"] is False


def test_is_reportee_case_and_accent_insensitive():
    assert elus._is_reportee("REPORTÉ") is True
    assert elus._is_reportee("Reporté") is True
    assert elus._is_reportee("reporte") is True
    assert elus._is_reportee("APPROUVÉ") is False
    assert elus._is_reportee(None) is False
    assert elus._is_reportee("") is False
    # RETIRÉ est un statut DISTINCT : jamais confondu avec REPORTÉ.
    assert elus._is_reportee("RETIRÉ") is False


def test_is_retire_case_and_accent_insensitive_and_distinct_from_reporte():
    assert elus._is_retire("RETIRÉ") is True
    assert elus._is_retire("Retiré") is True
    assert elus._is_retire("retire") is True
    assert elus._is_retire("REPORTÉ") is False        # statut distinct
    assert elus._is_retire("APPROUVÉ") is False
    assert elus._is_retire(None) is False
    assert elus._is_retire("") is False


def test_decision_summary_withdrawn_point_label():
    # « RETIRÉ » (retiré de l'ordre du jour) → libellé « Retiré », distinct de
    # « Reporté » (voir utils.text._DECISION_LABELS).
    assert elus._decision_summary("RETIRÉ", None) == "Retiré"
    assert elus._decision_summary("REPORTÉ", None) == "Reporté"


def test_decision_summary_unanimous_vote():
    # Cas réel : SP 77 (27/05/2026), désignation d'un représentant à
    # l'ASBL GELS — décidé à l'unanimité, sans intervenant·e listé·e.
    d = elus.seance_detail("2026-05-27")
    it = next(p for p in d["points"] if p["sp"] == 77)
    assert it["decision"] == "Décidé à l'unanimité"


def test_decision_summary_recorded_vote_shows_counts():
    d = elus.seance_detail("2026-05-27")
    it = next(p for p in d["points"] if p["sp"] == 1)
    assert it["decision"] == "Décidé (24 pour, 0 contre, 5 abstentions)"


def test_decision_summary_reported_point():
    d = elus.seance_detail("2026-05-27")
    it = next(p for p in d["points"] if p["sp"] == 51)
    assert it["decision"] == "Reporté"


def test_decision_summary_normalizes_rare_spelling_variants():
    # Coquilles/variantes ponctuelles observées dans le corpus (casse,
    # verbe conjugué différemment) doivent produire le même libellé.
    assert elus._decision_summary("PRENDS POUR INFORMATION", None) == "Pris pour information"
    assert elus._decision_summary("PRENDRE ACTE", None) == "Pris acte"
    assert elus._decision_summary("BESLIST", {"type": "unanimite"}) == "Décidé à l'unanimité"


def test_decision_summary_falls_back_to_homogeneous_case_for_unknown_text():
    # Variante non répertoriée : à défaut de la reconnaître, on la rend lisible
    # plutôt que de la laisser en capitales.
    assert elus._decision_summary("RENVOYÉ EN COMMISSION", None) == "Renvoyé en commission"


def test_decision_variants_fold_into_their_canonical_label():
    # Deux formules isolées du corpus qui formaient chacune leur propre statut,
    # donc leur propre puce dans l'onglet Séances, pour UN point : une prise
    # d'acte assortie d'une dérogation reste une prise d'acte, et « prend
    # information » est la même formule que « prend pour information ».
    assert elus._decision_summary("PREND ACTE + DÉROGATION ART.12", None) == "Pris acte"
    assert elus._decision_summary("PREND INFORMATION", None) == "Pris pour information"


def test_decision_summary_none_when_no_decision():
    assert elus._decision_summary("", None) is None
    assert elus._decision_summary(None, None) is None


# ── _decision_status (utils.text) : variante pour services.rag/ ────────────
# Contrairement à _decision_summary (vote COMPLET, lu depuis le JSON),
# n'a accès qu'au TYPE de vote indexé dans Pinecone (voir index_pv.py) — pas
# de détail pour/contre/abstentions à afficher pour un vote nominal.
def test_decision_status_unanimous_vote():
    from utils.text import _decision_status
    assert _decision_status("DECIDE", "unanimite") == "Décidé à l'unanimité"


def test_decision_status_nominal_vote_has_no_fabricated_counts():
    # Le type de vote seul ne suffit pas à reconstituer les comptes réels
    # (pour/contre/abstentions) — mieux vaut le label seul qu'un chiffre
    # inventé (toujours "0 contre, 0 abstentions" faute de mieux).
    from utils.text import _decision_status
    assert _decision_status("DECIDE", "vote_nominal") == "Décidé"


def test_decision_status_normalizes_same_as_decision_summary():
    from utils.text import _decision_status
    assert _decision_status("PRENDS POUR INFORMATION") == "Pris pour information"
    assert _decision_status("PRENDRE ACTE", None) == "Pris acte"


def test_decision_status_empty_when_no_decision():
    from utils.text import _decision_status
    assert _decision_status("") == ""
    assert _decision_status(None) == ""


def test_thematique_label_normalizes_slug():
    assert elus._thematique_label("transports_publics") == "Transport public"
    assert elus._thematique_label("mobilite-verte") == "Mobilite verte"
    assert elus._thematique_label("") == ""
    assert elus._thematique_label(None) == ""


def test_thematique_label_matches_stats_canonicalisation():
    """Même fusion singulier/pluriel que compute_stats (services.statistics) :
    un même tag brut doit produire le même radical dans les deux vues, à la
    casse près (Statistiques : minuscules ; Séances/Par élu·e : 1re majuscule)."""
    from utils.text import _canon_theme
    for raw in ("transports_publics", "marches_publics", "fournitures"):
        assert elus._thematique_label(raw).lower() == _canon_theme(raw)


def test_seance_detail_exposes_thematiques_and_montant():
    # Cas réel (27/05/2026) : un compte ASBL avec montant négatif (déficit)
    # doit être affiché tel quel, pas filtré comme un montant "hors budget"
    # (cette exclusion, voir _is_excluded_amount, ne concerne que les
    # agrégats /stats et /trend, jamais l'affichage d'un point individuel).
    d = elus.seance_detail("2026-05-27")
    it = next(p for p in d["points"] if p["sp"] == 9)
    assert it["montant_eur"] == -20809.92
    assert it["thematiques"] == ["Compte annuel", "Sport", "Cooperation associative"]
    # Un point sans montant renseigné : None, pas 0 ni absent.
    it77 = next(p for p in d["points"] if p["sp"] == 77)
    assert it77["montant_eur"] is None
    assert it77["thematiques"]


def test_elu_detail_depose_and_repond_expose_thematiques():
    # Avant ce correctif, seule la vue Séances affichait les thématiques —
    # absentes de /elu/{key} (depose ET repond), donc pas visibles dans
    # l'onglet Par élu·e ni dans les sources des réponses du chat.
    d = elus.elu_detail("verzin")
    with_theme = next((it for it in d["depose"] if it.get("thematiques")), None)
    assert with_theme is not None
    assert all(isinstance(t, str) and t for t in with_theme["thematiques"])
    with_theme_r = next((it for it in d["repond"] if it.get("thematiques")), None)
    assert with_theme_r is not None
    # Un point sans thématique renseignée : liste vide, jamais absente/None
    # (le frontend teste `.length`, pas la présence de la clé).
    assert all("thematiques" in it and isinstance(it["thematiques"], list) for it in d["depose"])
    assert all("thematiques" in it and isinstance(it["thematiques"], list) for it in d["repond"])


def test_seance_detail_points_sorted_by_sp():
    d = elus.seance_detail("2026-04-22")
    sps = [p["sp"] for p in d["points"]]
    assert sps == sorted(sps)


def test_endpoint_seances_list():
    r = client.get("/seances")
    assert r.status_code == 200
    body = r.json()
    assert "seances" in body and body["seances"]


def test_endpoint_seance_detail():
    r = client.get("/seance/2026-04-22")
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "2026-04-22"
    assert any(p["sp"] == 74 for p in body["points"])


def test_endpoint_seance_detail_unknown_date_404():
    r = client.get("/seance/1999-01-01")
    assert r.status_code == 404


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


# ── Synthèse par année (source des graphes de l'onglet Statistiques) ──
def test_annees_stats_types_partition_the_points():
    # L'invariant que les puces de l'onglet Séances doivent respecter : chaque
    # point porte un type et un seul, donc la somme des cinq compteurs égale le
    # nombre de points. Il tombait en défaut avant que « Débat filmé » soit un
    # type à part entière (659 affichés pour 676 points en 2025).
    rows = seances.annees_stats()
    assert rows
    for r in rows:
        assert sum(r["types"].values()) == r["points"], r["annee"]


def test_annees_stats_person_reconciliation_holds():
    # Agréger par intervenant·e ne redonne pas le total : deux écarts de sens
    # contraire, que ces compteurs rendent vérifiables.
    for r in seances.annees_stats():
        assert r["points"] == r["points_avec_personne"] + r["points_sans_personne"]
        assert r["somme_par_personne"] == r["points_avec_personne"] + r["surplus"]
        # Une personne peut porter plusieurs points : jamais plus de personnes
        # distinctes que d'occurrences comptées.
        assert r["intervenants"] <= r["somme_par_personne"]


def test_annees_stats_hors_pv_matches_the_seance_view():
    # Le décompte « hors PV » d'une année doit être exactement ce que l'onglet
    # Séances montre : même source (seance_detail), jamais un recomptage
    # parallèle qui rouvrirait l'écart.
    par_date = seances.hors_pv_par_date()
    for r in seances.annees_stats():
        attendu = sum(n for d, n in par_date.items() if d[:4] == r["annee"])
        assert r["types"]["Débat filmé"] == attendu, r["annee"]


def test_statuts_par_date_never_exceeds_the_points_of_the_year():
    # Le graphe des statuts descend année → mois → PV depuis cette seule
    # source. Un point a au plus une issue, et les points sans décision
    # (chapitres vidéo, débats non tranchés) n'en ont aucune : le total d'une
    # année ne peut donc jamais dépasser son nombre de points, sinon
    # l'empilement mentirait sur la hauteur des barres.
    statuts = seances.statuts_par_date()
    assert statuts
    par_annee = {}
    for date, par_statut in statuts.items():
        assert all(n > 0 for n in par_statut.values()), date
        assert all(s and s.strip() == s for s in par_statut), date
        par_annee[date[:4]] = par_annee.get(date[:4], 0) + sum(par_statut.values())
    for r in seances.annees_stats():
        assert par_annee.get(r["annee"], 0) <= r["points"], r["annee"]


def test_statuts_par_date_dates_are_known_seances():
    # Toute date porteuse de statuts doit être une date de séance connue : le
    # graphe regroupe par année via ces clés, une date orpheline créerait une
    # colonne fantôme.
    connues = {s["date"] for s in seances.seances_list()}
    assert connues
    assert set(seances.statuts_par_date()) <= connues


def test_points_egalent_les_statuts_plus_les_sans_decision():
    # Les deux graphes de l'onglet Statistiques comptent les MÊMES points et se
    # lisent l'un sous l'autre : « Activité par année » les totalise, « Issue
    # des points » les répartit. L'empilement restait 67 points en dessous
    # (663 contre 658 en 2024), parce que les points de PV sans décision
    # relevée n'y figuraient pas. Ils y sont désormais, et cette identité est
    # ce qui garantit que les deux graphes ne peuvent plus diverger.
    statuts = seances.statuts_par_date()
    sans = seances.sans_decision_par_date()
    for r in seances.annees_stats():
        annee = r["annee"]
        somme = sum(n for d, m in statuts.items() if d[:4] == annee for n in m.values())
        somme += sum(n for d, n in sans.items() if d[:4] == annee)
        # Les chapitres vidéo sans PV n'ont pas de décision et ne comptent dans
        # aucun des deux graphes : on les retire du total des points.
        points_pv = r["points"] - r["types"]["Débat filmé"]
        assert somme == points_pv, annee


def test_sans_decision_exclut_les_chapitres_video():
    # Un chapitre vidéo sans point de PV n'a pas de décision par construction :
    # le compter ici ferait dépasser le graphe des issues au-dessus du graphe
    # d'activité, exactement l'erreur inverse de celle qu'on corrige.
    sans = seances.sans_decision_par_date()
    hors_pv = seances.hors_pv_par_date()
    assert sans
    for date, n in sans.items():
        detail = seances.seance_detail(date)
        attendu = [p for p in detail["points"]
                   if not p.get("statut") and p["type_label"] != "Débat filmé"]
        assert n == len(attendu), date
    # Une séance qui n'existe que dans le chapitrage n'a aucun point sans
    # décision à déclarer.
    for date, n in hors_pv.items():
        detail = seances.seance_detail(date)
        if all(p["type_label"] == "Débat filmé" for p in detail["points"]):
            assert date not in sans, date


def test_le_statut_ignore_la_mention_du_vote():
    # Le PV écrit souvent l'issue avec son vote : « Pris acte à l'unanimité »,
    # « Pris pour information (33 pour, 0 contre, 0 abstentions) ». Le STATUT
    # doit rester le libellé nu, sinon chaque variante de vote formerait son
    # propre statut — donc sa propre puce dans l'onglet Séances et sa propre
    # part dans le graphe des issues.
    assert seances._decision_label("Pris acte à l'unanimité") == "Pris acte"
    assert seances._decision_label("PRIS POUR INFORMATION (33 POUR, 0 CONTRE, 0 ABSTENTIONS)") \
        == "Pris pour information"
    assert seances._decision_label("Approuvé à l'unanimité") == "Approuvé"


def test_les_statuts_dune_seance_partitionnent_ses_points_decides():
    # Contrôle sur une séance réelle (27/05/2026), celle qui a fait douter :
    # 3 « Pris acte » + 12 « Pris pour information » + 12 « Débat » = les 27
    # points hors des quatre issues nommées. Aucun n'est perdu ni compté deux
    # fois, et le total des statuts plus les points sans décision fait bien le
    # nombre de points de PV de la séance.
    detail = seances.seance_detail("2026-05-27")
    assert detail
    pv = [p for p in detail["points"] if p["type_label"] != "Débat filmé"]
    compte = {}
    for p in pv:
        compte[p.get("statut")] = compte.get(p.get("statut"), 0) + 1
    assert sum(compte.values()) == len(pv)
    nommes = {"Approuvé", "Décidé", "Reporté", "Retiré"}
    autres = sum(n for st, n in compte.items() if st and st not in nommes)
    assert autres == compte.get("Pris acte", 0) + compte.get("Pris pour information", 0) \
        + compte.get("Débat", 0)


def test_les_types_par_date_totalisent_les_points_du_pv():
    # Le graphe « Activité par année » (métrique Points) empile désormais ces
    # quatre types, et sa hauteur doit rester CE QU'IL AFFICHAIT DÉJÀ : le
    # nombre de points du procès-verbal. Les chapitres vidéo sans point de PV
    # n'en font pas partie — ils ne sont pas des points du PV.
    types = seances.types_par_date()
    assert types
    assert all("Débat filmé" not in m for m in types.values())
    for r in seances.annees_stats():
        annee = r["annee"]
        somme = sum(n for d, m in types.items() if d[:4] == annee for n in m.values())
        assert somme == r["points"] - r["types"]["Débat filmé"], annee


def test_les_trois_vues_comptent_les_memes_points():
    # Trois angles, un seul ensemble : par type (types_par_date), par issue
    # (statuts_par_date + sans_decision_par_date). Lire 45 dans l'un et 40 dans
    # l'autre pour la même séance a coûté assez de doutes pour être verrouillé.
    types = seances.types_par_date()
    statuts = seances.statuts_par_date()
    sans = seances.sans_decision_par_date()
    for date, par_type in types.items():
        par_issue = sum((statuts.get(date) or {}).values()) + sans.get(date, 0)
        assert sum(par_type.values()) == par_issue, date


def test_le_croisement_type_issue_retombe_sur_les_deux_lectures():
    # Une seule structure sert les deux graphes de l'onglet : sommée sur les
    # issues elle donne la répartition par type, sommée sur les types elle donne
    # la répartition par issue. C'est ce qui rend leur contradiction impossible
    # — et ce qui permet, en isolant un type, de voir ce qu'il devient.
    croise = seances.issues_par_date()
    types = seances.types_par_date()
    statuts = seances.statuts_par_date()
    sans = seances.sans_decision_par_date()
    assert croise
    for date, par_type in croise.items():
        assert "Débat filmé" not in par_type, date
        for t, issues in par_type.items():
            assert sum(issues.values()) == types[date][t], (date, t)
        plat = {}
        for issues in par_type.values():
            for st, n in issues.items():
                plat[st] = plat.get(st, 0) + n
        assert plat.pop("", 0) == sans.get(date, 0), date
        assert plat == (statuts.get(date) or {}), date
