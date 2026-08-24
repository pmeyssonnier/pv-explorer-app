"""Tests de pipeline/backfill_qe_thematiques.py — backfill_thematiques
(orchestration pure sur un dict en mémoire), avec classify_thematiques
monkeypatché (aucun appel réel)."""
import backfill_qe_thematiques as backfill


# ── classify_thematiques : forme de l'appel SDK (streaming, comme
# call_claude_api — voir questions_ecrites_extraction_pipeline.py) ─────────
class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class _FakeStream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class _FakeMessages:
    def __init__(self, message):
        self._message = message

    def stream(self, **kwargs):
        self.kwargs = kwargs
        return _FakeStream(self._message)


class _FakeClient:
    def __init__(self, message):
        self.messages = _FakeMessages(message)


def test_classify_thematiques_parses_streamed_json(monkeypatch):
    fake_client = _FakeClient(_FakeMessage('{"thematiques": ["stationnement", "securite_routiere"]}'))
    monkeypatch.setattr(backfill, "get_client", lambda: fake_client)
    result = backfill.classify_thematiques("Titre", "Question", "Réponse")
    assert result == ["stationnement", "securite_routiere"]


def test_classify_thematiques_returns_empty_list_after_retries_on_invalid_json(monkeypatch):
    fake_client = _FakeClient(_FakeMessage("pas du JSON"))
    monkeypatch.setattr(backfill, "get_client", lambda: fake_client)
    monkeypatch.setattr(backfill.time, "sleep", lambda s: None)
    assert backfill.classify_thematiques("Titre", "Question", None) == []


def _db(*questions):
    return {"questions": list(questions)}


def test_backfill_classifies_only_questions_without_thematiques(monkeypatch):
    monkeypatch.setattr(backfill.time, "sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(backfill, "classify_thematiques",
                         lambda titre, question, reponse: calls.append(titre) or ["stationnement"])
    db = _db(
        {"id": "QE-1", "titre": "Sans thème", "thematiques": []},
        {"id": "QE-2", "titre": "Déjà classée", "thematiques": ["voirie"]},
        {"id": "QE-3", "titre": "Champ absent"},
    )
    n = backfill.backfill_thematiques(db)
    assert n == 2
    assert calls == ["Sans thème", "Champ absent"]
    assert db["questions"][0]["thematiques"] == ["stationnement"]
    assert db["questions"][1]["thematiques"] == ["voirie"]   # inchangée
    assert db["questions"][2]["thematiques"] == ["stationnement"]


def test_backfill_force_reclasses_everything(monkeypatch):
    monkeypatch.setattr(backfill.time, "sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(backfill, "classify_thematiques",
                         lambda titre, question, reponse: calls.append(titre) or ["nouveau_theme"])
    db = _db({"id": "QE-1", "titre": "Déjà classée", "thematiques": ["voirie"]})
    n = backfill.backfill_thematiques(db, force=True)
    assert n == 1
    assert calls == ["Déjà classée"]
    assert db["questions"][0]["thematiques"] == ["nouveau_theme"]


def test_backfill_returns_zero_when_nothing_to_do(monkeypatch):
    monkeypatch.setattr(backfill, "classify_thematiques",
                         lambda *a: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))
    db = _db({"id": "QE-1", "titre": "Déjà classée", "thematiques": ["voirie"]})
    assert backfill.backfill_thematiques(db) == 0
