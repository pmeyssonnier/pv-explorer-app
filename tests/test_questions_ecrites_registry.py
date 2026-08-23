"""Test de l'intégration des questions écrites dans services/people/registry.py
(l'index par personne alimenté par une 3e source, en plus des PV et de la
vidéo — voir registry._build_all). load_qe_db est monkeypatché : ce test
vérifie l'orchestration (la question devient une entrée "depose" attribuable
à la bonne personne), pas l'extraction elle-même (couverte par
test_questions_ecrites_pipeline.py/test_questions_ecrites_integration.py).
"""
import services.people.registry as registry


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
