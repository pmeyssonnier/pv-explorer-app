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


def test_author_of_motion_checks_resume_but_never_guesses_from_intervenants():
    # Une motion doit rester non attribuée si ni le titre ni le résumé ne
    # nomment explicitement l'auteur·e (jamais de repli sur les
    # intervenant·e·s, contrairement aux demandes — motions souvent
    # collectives, cf. docstring de _author_of).
    point = {
        "type": "motion",
        "titre": "Retrait du règlement",
        "resume": "Motion de Monsieur Georges Verzin pour ...",
        "intervenants": ["Georges Verzin", "Bourgmestre"],
    }
    assert elus._author_of(point) == "Georges Verzin"

    point_no_mention = {
        "type": "motion",
        "titre": "Retrait du règlement",
        "resume": "Le Collège présente un amendement.",
        "intervenants": ["Georges Verzin", "Cédric Mahieu"],
    }
    assert elus._author_of(point_no_mention) is None


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
    # INVARIANT clé : les motions ne sont attribuées que si l'auteur·e est
    # nommé·e explicitement (titre OU résumé) — on ne devine jamais depuis
    # les intervenant·e·s (motions souvent collectives). Verzin a 3 motions
    # dont il est nommément l'auteur au résumé (ex. « Motion de Monsieur
    # Georges VERZIN... »), aucune de plus.
    assert c["motions"] == 3


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
        assert s["n_points"] > 0
    dates = [s["date"] for s in lst]
    assert dates == sorted(dates, reverse=True)  # plus récente en premier


def test_seance_detail_unknown_date_returns_none():
    assert elus.seance_detail("1999-01-01") is None


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
    assert elus._decision_summary("PREND ACTE + DÉROGATION ART.12", None) == "Prend acte + dérogation art.12"


def test_decision_summary_none_when_no_decision():
    assert elus._decision_summary("", None) is None
    assert elus._decision_summary(None, None) is None


def test_thematique_label_normalizes_slug():
    assert elus._thematique_label("transports_publics") == "Transports publics"
    assert elus._thematique_label("mobilite-verte") == "Mobilite verte"
    assert elus._thematique_label("") == ""
    assert elus._thematique_label(None) == ""


def test_seance_detail_exposes_thematiques_and_montant():
    # Cas réel (27/05/2026) : un compte ASBL avec montant négatif (déficit)
    # doit être affiché tel quel, pas filtré comme un montant "hors budget"
    # (cette exclusion, voir _is_excluded_amount, ne concerne que les
    # agrégats /stats et /trend, jamais l'affichage d'un point individuel).
    d = elus.seance_detail("2026-05-27")
    it = next(p for p in d["points"] if p["sp"] == 9)
    assert it["montant_eur"] == -20809.92
    assert it["thematiques"] == ["Comptes annuels", "Sports", "Cooperation associative"]
    # Un point sans montant renseigné : None, pas 0 ni absent.
    it77 = next(p for p in d["points"] if p["sp"] == 77)
    assert it77["montant_eur"] is None
    assert it77["thematiques"]


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
