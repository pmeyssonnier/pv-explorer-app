"""Test de l'intégration des questions écrites dans services/people/registry.py
(l'index par personne alimenté par une 3e source, en plus des PV et de la
vidéo — voir registry._build_all). load_qe_db est monkeypatché : ce test
vérifie l'orchestration (la question devient une entrée "depose" attribuable
à la bonne personne), pas l'extraction elle-même (couverte par
test_questions_ecrites_pipeline.py/test_questions_ecrites_integration.py).
"""
import services.people.registry as registry
from services import elus


def test_written_question_becomes_depose_entry_for_known_person(monkeypatch):
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2025-11-10", "auteur": "Georges Verzin", "titre": "Les nids-de-poule",
        "source_url": "https://1030.be/qe/015.pdf",
    }]})
    index, pairs, nom_by_key = registry._build_all()
    verzin = index["verzin"]
    qe_entries = [it for it in verzin["depose"] if it["type"] == "question_ecrite"]
    assert len(qe_entries) == 1
    entry = qe_entries[0]
    assert entry["date"] == "2025-11-10"
    assert entry["titre"] == "Les nids-de-poule"
    assert entry["url"] == "https://1030.be/qe/015.pdf"
    assert entry["sp"] == 0
    assert entry["thematiques"] == []
    assert entry["video_url"] is None
    assert entry.get("co_auteurs") is None


def test_written_question_cosigned_credits_each_named_author(monkeypatch):
    # Une QE cosignée (« Georges Verzin et Cédric Mahieu », cas réel
    # QE-2021-019) doit créditer CHAQUE personne nommée d'une entrée
    # "depose" distincte — à la différence d'un point de PV, où seul
    # l'auteur·e principal·e est agrégé (voir _point_author). Avant
    # correction, _key() prenait la chaîne entière et n'en retenait que le
    # dernier mot ("Mahieu"), perdant Verzin et créditant Mahieu à tort
    # d'une question dont il n'était que cosignataire.
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2021-05-05", "auteur": "Georges Verzin et Cédric Mahieu",
        "titre": "Le règlement particulier du stationnement.",
        "repondant": "Bernard Clerfayt",
    }]})
    index, pairs, nom_by_key = registry._build_all()
    # Chaque fiche voit L'AUTRE cosignataire (jamais soi-même) dans son
    # entrée "depose" — affichage côte à côte quelle que soit la fiche
    # consultée (voir services/elus.py::_fmt_depose, frontend/js/elus.js).
    # LISTE de noms, pas une chaîne recollée : la fiche consultée s'ajoute en
    # tête côté affichage, et seule la liste complète permet de la ponctuer
    # correctement (« A, B et C » plutôt que « A et B et C »).
    expected_co_auteurs = {"verzin": ["Cédric Mahieu"], "mahieu": ["Georges Verzin"]}
    for key, co_auteur in expected_co_auteurs.items():
        fiche = index[key]
        qe_entries = [it for it in fiche["depose"]
                      if it["type"] == "question_ecrite"
                      and it["titre"] == "Le règlement particulier du stationnement."]
        assert len(qe_entries) == 1, (key, fiche["depose"])
        assert qe_entries[0]["co_auteurs"] == co_auteur
    # Le/la répondant·e voit les deux cosignataires dans son propre "repond".
    clerfayt = index["clerfayt"]
    resp = next(it for it in clerfayt["repond"]
                if it["titre"] == "Le règlement particulier du stationnement.")
    assert resp["demandeur"] == "Georges Verzin et Cédric Mahieu"


def test_written_question_homonym_author_not_merged_into_unrelated_namesake(monkeypatch):
    # Marie Nyssens (autrice d'une question écrite isolée) n'a aucun lien
    # avec Clotilde Nyssens, conseillère communale citée des dizaines de fois
    # dans les PV — sans le registre d'homonymes (_HOMONYM_KEY_OVERRIDES),
    # _key() ne retient que le nom de famille et les fusionne en une seule
    # fiche, qui hérite alors du nom affiché de la personne la plus citée.
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2020-04-16", "auteur": "Marie Nyssens", "titre": "L'engagement dans le Département PPU",
    }]})
    index, pairs, nom_by_key = registry._build_all()
    assert "nyssens_marie" in index
    fiche = index["nyssens_marie"]
    assert fiche["nom"] == "Marie Nyssens"
    qe_entries = [it for it in fiche["depose"] if it["type"] == "question_ecrite"]
    assert len(qe_entries) == 1
    assert qe_entries[0]["titre"] == "L'engagement dans le Département PPU"
    # Pas de fuite dans la fiche de l'homonyme : la question de Marie
    # n'apparaît pas sous la clé "nyssens" (Clotilde).
    if "nyssens" in index:
        assert all(it["titre"] != "L'engagement dans le Département PPU"
                   for it in index["nyssens"]["depose"])
        assert index["nyssens"]["nom"] != "Marie Nyssens"


def test_written_question_thematiques_canonised_on_depose_and_repond(monkeypatch):
    # Même canonisation (_thematique_label) qu'un point de PV — un tag brut
    # au pluriel/singulier différent doit s'afficher identiquement partout.
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2025-11-10", "auteur": "Georges Verzin", "titre": "Les nids-de-poule",
        "repondant": "Bernard Clerfayt", "thematiques": ["voirie", "travaux_publics"],
    }]})
    index, pairs, nom_by_key = registry._build_all()
    entry = next(it for it in index["verzin"]["depose"] if it["type"] == "question_ecrite")
    assert entry["thematiques"] == ["Voirie", "Travaux public"]
    resp = next(it for it in index["clerfayt"]["repond"] if it["titre"] == "Les nids-de-poule")
    assert resp["thematiques"] == ["Voirie", "Travaux public"]


def test_written_question_creates_new_entry_for_unknown_author(monkeypatch):
    # Même logique que l'attribution des points de PV (_point_author) : une
    # personne pas encore vue ailleurs dans le corpus obtient quand même une
    # fiche (clé = nom de famille) — l'auteur·e d'une question écrite est
    # toujours un·e conseiller·ère réel·le, jamais deviné.
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2025-11-10", "auteur": "Alix Nouvelle Personne", "titre": "Test",
    }]})
    index, pairs, nom_by_key = registry._build_all()
    assert "personne" in index
    assert any(it["type"] == "question_ecrite" for it in index["personne"]["depose"])


def test_written_question_skipped_when_key_unresolvable(monkeypatch):
    # `_key()` renvoie une clé vide pour une mention qui ne se réduit à aucun
    # mot exploitable (ex. un artefact d'extraction) — ignorée plutôt que de
    # créer une fiche vide.
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2025-11-10", "auteur": ",", "titre": "Test",
    }]})
    index, pairs, nom_by_key = registry._build_all()
    assert isinstance(index, dict)  # ne lève pas, simplement rien ajouté pour cette entrée


def test_written_question_missing_author_is_skipped(monkeypatch):
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [
        {"date": "2025-11-10", "auteur": None, "titre": "Test"},
        {"date": "2025-11-10", "titre": "Test sans champ auteur"},
    ]})
    # Ne doit pas lever — juste ignorer les entrées sans auteur·e exploitable.
    index, pairs, nom_by_key = registry._build_all()
    assert isinstance(index, dict)


def test_no_written_questions_leaves_registry_unaffected(monkeypatch):
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": []})
    index, pairs, nom_by_key = registry._build_all()
    verzin = index["verzin"]
    assert not any(it["type"] == "question_ecrite" for it in verzin["depose"])


def test_written_question_reponse_text_exposed_on_depose_entry(monkeypatch):
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2025-11-10", "auteur": "Georges Verzin", "titre": "Les nids-de-poule",
        "reponse": "Les travaux sont planifiés pour le prochain trimestre.",
    }]})
    index, pairs, nom_by_key = registry._build_all()
    entry = next(it for it in index["verzin"]["depose"] if it["type"] == "question_ecrite")
    assert entry["reponse"] == "Les travaux sont planifiés pour le prochain trimestre."


def test_written_question_langue_absent_by_default(monkeypatch):
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2025-11-10", "auteur": "Georges Verzin", "titre": "Les nids-de-poule",
    }]})
    index, pairs, nom_by_key = registry._build_all()
    entry = next(it for it in index["verzin"]["depose"] if it["type"] == "question_ecrite")
    assert entry.get("langue") is None


def test_written_question_langue_nl_exposed_on_depose_entry(monkeypatch):
    # Document sans version française (voir pipeline/questions_ecrites_
    # extraction_pipeline.py) : titre/question/réponse en néerlandais, "langue"
    # le signale plutôt que de laisser croire à du français.
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2015-04-24", "auteur": "Bernadette Vriamont",
        "titre": "Resultaten aan het einde van de mobiliteitstest",
        "reponse": "Het begeleidingscomité...", "langue": "nl",
    }]})
    index, pairs, nom_by_key = registry._build_all()
    entry = next(it for it in index["vriamont"]["depose"] if it["type"] == "question_ecrite")
    assert entry["langue"] == "nl"


def test_written_question_repondant_resolved_to_canonical_name_and_reciprocal_entry(monkeypatch):
    # Un·e répondant·e nommé·e (voir la pipeline d'extraction) doit être
    # résolu·e comme pour un point de PV : nom canonique sur la fiche de
    # l'auteur·e ET une entrée "repond" réciproque sur la fiche du/de la
    # répondant·e (même mécanique générique que pour les PV, via
    # repondant_keys/demandeur_keys — voir la fin de _build_all).
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2025-11-10", "auteur": "Georges Verzin", "titre": "Les nids-de-poule",
        "repondant": "Bernard Clerfayt", "source_url": "https://1030.be/qe/015.pdf",
    }]})
    index, pairs, nom_by_key = registry._build_all()
    entry = next(it for it in index["verzin"]["depose"] if it["type"] == "question_ecrite")
    assert entry["repondant"] == "Bernard Clerfayt"

    repond_entry = next(
        it for it in index["clerfayt"]["repond"]
        if it["titre"] == "Les nids-de-poule" and it["date"] == "2025-11-10"
    )
    assert repond_entry["demandeur"] == "Georges Verzin"
    assert repond_entry["url"] == "https://1030.be/qe/015.pdf"
    assert repond_entry["sp"] == 0


def test_written_question_repondant_role_word_alone_not_resolved_as_person(monkeypatch):
    # « Bourgmestre » seul (sans nom) ne peut pas être résolu à UNE personne
    # (contrairement au PV, aucune métadonnée de séance pour lever
    # l'ambiguïté ici) — reste affiché tel quel (casse homogène), sans créer
    # de fiche "bourgmestre".
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2025-11-10", "auteur": "Georges Verzin", "titre": "Test",
        "repondant": "Bourgmestre",
    }]})
    index, pairs, nom_by_key = registry._build_all()
    entry = next(it for it in index["verzin"]["depose"] if it["type"] == "question_ecrite")
    assert entry["repondant"] == "Bourgmestre"
    assert "bourgmestre" not in index


def test_written_question_without_repondant_leaves_field_none(monkeypatch):
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2025-11-10", "auteur": "Georges Verzin", "titre": "Test",
    }]})
    index, pairs, nom_by_key = registry._build_all()
    entry = next(it for it in index["verzin"]["depose"] if it["type"] == "question_ecrite")
    assert entry.get("repondant") is None


def test_elu_detail_exposes_reponse_and_repondant_for_written_question(monkeypatch):
    # Bout en bout jusqu'à /elu/{key} (services.elus._fmt_depose) : la
    # réponse et le/la répondant·e résolu·e doivent être visibles côté API,
    # pas seulement dans l'index interne (registry._build_all).
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2025-11-10", "auteur": "Georges Verzin", "titre": "Les nids-de-poule",
        "reponse": "Les travaux sont planifiés pour le prochain trimestre.",
        "repondant": "Bernard Clerfayt", "source_url": "https://1030.be/qe/015.pdf",
    }]})
    registry._cache["sig"] = None   # force la reconstruction (nouvelle donnée QE monkeypatchée)
    try:
        d = elus.elu_detail("verzin")
        entry = next(it for it in d["depose"] if it["type"] == "question_ecrite")
        assert entry["reponse"] == "Les travaux sont planifiés pour le prochain trimestre."
        assert entry["repondant"] == "Bernard Clerfayt"
    finally:
        # `_sig()` ne dépend que des mtimes de fichiers (inchangés par le
        # monkeypatch de load_qe_db) : sans ce reset, le cache resterait
        # "valide" (même signature) mais peuplé avec cette donnée QE factice
        # une fois load_qe_db revenu à la normale — pollution pour les tests
        # suivants qui lisent le vrai corpus via _index()/elu_detail().
        registry._cache["sig"] = None


def test_elu_detail_exposes_langue_for_written_question(monkeypatch):
    monkeypatch.setattr(registry, "load_qe_db", lambda: {"questions": [{
        "date": "2015-04-24", "auteur": "Bernadette Vriamont",
        "titre": "Resultaten aan het einde van de mobiliteitstest",
        "reponse": "Het begeleidingscomité...", "langue": "nl",
    }]})
    registry._cache["sig"] = None
    try:
        d = elus.elu_detail("vriamont")
        entry = next(it for it in d["depose"] if it["type"] == "question_ecrite")
        assert entry["langue"] == "nl"
    finally:
        registry._cache["sig"] = None


def test_registry_signature_includes_qe_json_mtime(tmp_path, monkeypatch):
    # _sig() doit invalider le cache si le fichier des questions écrites
    # change sur place (dev/redéploiement) — même mécanique que pour le
    # fichier PV/vidéo.
    path = tmp_path / "questions_ecrites_schaerbeek.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(registry, "QE_JSON_PATH", str(path))
    sig1 = registry._sig()
    import os
    import time
    time.sleep(0.01)
    path.write_text("{}", encoding="utf-8")
    os.utime(path, None)
    sig2 = registry._sig()
    assert sig1 != sig2
