"""Tests de pipeline/questions_ecrites_extraction_pipeline.py — fonctions
pures (normalisation, fusion en base) et process_pdf en orchestration, avec
extraction PDF/appel Claude monkeypatchés (aucun appel réel).
"""
import questions_ecrites_extraction_pipeline as qe


# ── call_claude_api : forme de l'appel SDK (streaming, pas create) ──────────
# Un max_tokens élevé (voir CONFIG) fait REFUSER l'appel non-streamé par le
# SDK (ValueError "Streaming is required..." — rencontré en production) :
# garde-fou pour ne jamais revenir accidentellement à .create().
class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_FakeTextBlock(text)]
        self.stop_reason = stop_reason


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
        self.stream_calls = []

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        return _FakeStream(self._message)

    def create(self, **kwargs):
        raise AssertionError("call_claude_api doit utiliser .stream(), pas .create()")


class _FakeClient:
    def __init__(self, message):
        self.messages = _FakeMessages(message)


def test_call_claude_api_uses_streaming_and_parses_json(monkeypatch):
    message = _FakeMessage('{"numero": 1, "date": "2025-01-01", "auteur": "X"}')
    fake_client = _FakeClient(message)
    monkeypatch.setattr(qe, "get_client", lambda: fake_client)
    data = qe.call_claude_api("texte du pdf")
    assert data == {"numero": 1, "date": "2025-01-01", "auteur": "X"}
    assert len(fake_client.messages.stream_calls) == 1
    assert fake_client.messages.stream_calls[0]["max_tokens"] == qe.CONFIG["MAX_TOKENS"]


def test_call_claude_api_gives_up_after_retries_on_truncated_response(monkeypatch):
    # Réponse coupée par MAX_TOKENS en plein milieu d'une chaîne JSON : aucune
    # des 3 tentatives (identiques) ne peut réussir — retourne None sans lever.
    message = _FakeMessage('{"numero": 1, "date": "2025-01-01", "auteur": "Cha', stop_reason="max_tokens")
    fake_client = _FakeClient(message)
    monkeypatch.setattr(qe, "get_client", lambda: fake_client)
    monkeypatch.setattr(qe.time, "sleep", lambda s: None)
    assert qe.call_claude_api("texte du pdf") is None
    assert len(fake_client.messages.stream_calls) == qe.CONFIG["MAX_RETRIES"]


# ── _valid_iso_date ──────────────────────────────────────────────────────────
def test_valid_iso_date_accepts_real_calendar_date():
    assert qe._valid_iso_date("2025-11-10") == "2025-11-10"


def test_valid_iso_date_rejects_malformed_or_impossible_date():
    assert qe._valid_iso_date("10/11/2025") is None
    assert qe._valid_iso_date("2025-13-40") is None
    assert qe._valid_iso_date("") is None
    assert qe._valid_iso_date(None) is None


# ── normalize_question ───────────────────────────────────────────────────────
def _raw(**overrides):
    data = {
        "numero": 15, "date": "2025-11-10", "auteur": "Georges Verzin",
        "titre": "Les nids-de-poule", "question": "Quand seront-ils réparés ?",
        "reponse": "Les travaux sont planifiés pour le prochain trimestre.",
        "repondant": "Bernard Clerfayt",
    }
    data.update(overrides)
    return data


def test_normalize_question_builds_stable_id_from_year_and_number():
    q = qe.normalize_question(_raw(), "015.verzin.pdf")
    assert q["id"] == "QE-2025-015"
    assert q["annee"] == 2025
    assert q["numero"] == 15
    assert q["auteur"] == "Georges Verzin"
    assert q["source_file"] == "015.verzin.pdf"
    assert q["reponse"] == "Les travaux sont planifiés pour le prochain trimestre."
    assert q["repondant"] == "Bernard Clerfayt"


def test_normalize_question_cleans_thematiques_list():
    q = qe.normalize_question(_raw(thematiques=["stationnement", "securite_routiere"]), "x.pdf")
    assert q["thematiques"] == ["stationnement", "securite_routiere"]


def test_normalize_question_thematiques_defaults_to_empty_list():
    # Absente de la réponse Claude (ancien schéma, ou champ omis) : jamais
    # None, toujours une liste — cohérent avec un point de PV normalisé.
    q = qe.normalize_question(_raw(), "x.pdf")
    assert q["thematiques"] == []


def test_normalize_question_repondant_none_when_not_signed():
    # Comme "reponse" : une réponse non signée nommément (ou absente) ne
    # doit jamais produire un nom deviné.
    q = qe.normalize_question(_raw(repondant=None), "x.pdf")
    assert q["repondant"] is None
    q2 = qe.normalize_question(_raw(repondant=""), "x.pdf")
    assert q2["repondant"] is None


def test_normalize_question_recase_auteur_et_repondant():
    # Le prompt demande déjà une « casse standard » à Claude, mais ce n'est
    # pas garanti côté code (un document source tout en majuscules la
    # contourne parfois) — même repli déterministe que côté PV
    # (pv_extraction_pipeline._titlecase_name).
    q = qe.normalize_question(_raw(auteur="CÉCILE JODOGNE", repondant="BERNARD CLERFAYT"), "x.pdf")
    assert q["auteur"] == "Cécile Jodogne"
    assert q["repondant"] == "Bernard Clerfayt"


def test_normalize_question_coerces_string_numero():
    # Claude peut renvoyer "015" (chaîne, zéro de tête conservé) — doit
    # devenir l'entier 15, pas rester une chaîne ni planter.
    q = qe.normalize_question(_raw(numero="015"), "x.pdf")
    assert q["numero"] == 15


def test_normalize_question_reponse_none_when_not_yet_answered():
    # Une question toute juste posée peut ne pas encore avoir de réponse
    # publiée — nullable, jamais une chaîne vide ou devinée.
    q = qe.normalize_question(_raw(reponse=None), "x.pdf")
    assert q["reponse"] is None
    q2 = qe.normalize_question(_raw(reponse=""), "x.pdf")
    assert q2["reponse"] is None


def test_normalize_question_langues_none_by_default():
    # Cas normal (contenu en français) : jamais de valeur devinée, None.
    q = qe.normalize_question(_raw(), "x.pdf")
    assert q["question_langue"] is None
    assert q["reponse_langue"] is None


def test_normalize_question_langue_nl_when_no_french_in_document():
    # QE-2015-001 (voir backend/questions_ecrites_schaerbeek.json) : un
    # document sans AUCUNE version française — titre/question/réponse sont
    # alors le texte néerlandais tel quel, les deux champs "..._langue" le
    # signalent.
    q = qe.normalize_question(_raw(
        titre="Resultaten aan het einde van de mobiliteitstest",
        question="Op 1 mei loopt de mobiliteitstest ten einde.",
        reponse="Het begeleidingscomité...", question_langue="nl", reponse_langue="nl",
    ), "x.pdf")
    assert q["question_langue"] == "nl"
    assert q["reponse_langue"] == "nl"
    assert q["titre"] == "Resultaten aan het einde van de mobiliteitstest"


def test_normalize_question_langue_independent_between_question_and_reponse():
    # QE-2026-004 (voir backend/questions_ecrites_schaerbeek.json) : question
    # posée uniquement en néerlandais, mais réponse rédigée en français —
    # chaque champ "..._langue" est indépendant, pas un seul drapeau global.
    q = qe.normalize_question(_raw(
        titre="De sluiting van het Recypark Noord", question="Het Recypark...",
        question_langue="nl", reponse="La Direction de Bruxelles Propreté...", reponse_langue=None,
    ), "x.pdf")
    assert q["question_langue"] == "nl"
    assert q["reponse_langue"] is None


def test_normalize_question_langue_rejects_unexpected_value():
    # Seule "nl" est un signal valide (voir SYSTEM_PROMPT) — toute autre
    # valeur (hallucination, faute de frappe du modèle) retombe à None plutôt
    # que de propager un code langue non prévu par le frontend.
    q = qe.normalize_question(_raw(question_langue="fr", reponse_langue="fr"), "x.pdf")
    assert q["question_langue"] is None
    assert q["reponse_langue"] is None


def test_normalize_question_rejects_missing_numero():
    assert qe.normalize_question(_raw(numero=None), "x.pdf") is None


# ── _numero_from_filename : repli quand le document n'imprime aucun numéro ──
# (cas vécu : plusieurs questions écrites de 2010 n'ont RIEN d'autre que
# "Question de M. X, du <date>" — Claude renvoie alors numero=None, comme
# demandé par SYSTEM_PROMPT quand rien n'est visible dans le texte).
def test_numero_from_filename_dash_separated():
    assert qe._numero_from_filename("question-ecrite-09-2010.pdf") == 9
    assert qe._numero_from_filename("Question_ecrite_01-2010.pdf") == 1


def test_numero_from_filename_no_separator_before_year():
    # Nommage réellement rencontré à l'upload (pas de tiret entre le numéro
    # et l'année) : "012010" -> numéro 1, année 2010 (les 4 derniers
    # chiffres), jamais l'inverse.
    assert qe._numero_from_filename("Question_ecrite_012010.pdf") == 1
    assert qe._numero_from_filename("Question_ecrite_052010.pdf") == 5


def test_numero_from_filename_none_when_no_convention_match():
    assert qe._numero_from_filename("scan_du_conseil.pdf") is None
    assert qe._numero_from_filename("") is None
    assert qe._numero_from_filename(None) is None


def test_normalize_question_falls_back_to_filename_numero_when_absent_from_text():
    q = qe.normalize_question(_raw(numero=None), "question-ecrite-05-2010.pdf")
    assert q is not None
    assert q["numero"] == 5
    assert q["id"] == "QE-2025-005"


def test_normalize_question_prefers_text_numero_over_filename():
    # Le texte du document (Claude) prime toujours sur le nom de fichier,
    # simple repli — jamais l'inverse.
    q = qe.normalize_question(_raw(numero=15), "question-ecrite-09-2010.pdf")
    assert q["numero"] == 15


def test_normalize_question_still_rejects_when_neither_text_nor_filename_has_numero():
    assert qe.normalize_question(_raw(numero=None), "upload.pdf") is None


def test_normalize_question_rejects_invalid_date():
    assert qe.normalize_question(_raw(date="pas une date"), "x.pdf") is None
    assert qe.normalize_question(_raw(date=None), "x.pdf") is None


def test_normalize_question_rejects_missing_auteur():
    assert qe.normalize_question(_raw(auteur=""), "x.pdf") is None
    assert qe.normalize_question(_raw(auteur=None), "x.pdf") is None


def test_normalize_question_rejects_non_dict_input():
    assert qe.normalize_question(None, "x.pdf") is None
    assert qe.normalize_question("pas un dict", "x.pdf") is None


# ── merge_question_into_db ───────────────────────────────────────────────────
def _db(*questions):
    return {"meta": {"total_questions": len(questions)}, "questions": list(questions)}


def test_merge_question_into_db_appends_new_entry():
    db = _db()
    q = qe.normalize_question(_raw(), "x.pdf")
    assert qe.merge_question_into_db(db, q) is True
    assert len(db["questions"]) == 1
    assert db["questions"][0]["id"] == "QE-2025-015"


def test_merge_question_into_db_replaces_same_id_not_duplicates():
    # Un re-upload du même numéro/année est une CORRECTION, pas un doublon.
    q1 = qe.normalize_question(_raw(titre="Ancien titre"), "x.pdf")
    db = _db(q1)
    q2 = qe.normalize_question(_raw(titre="Titre corrigé"), "x.pdf")
    assert qe.merge_question_into_db(db, q2) is True
    assert len(db["questions"]) == 1
    assert db["questions"][0]["titre"] == "Titre corrigé"


def test_merge_question_into_db_sorts_by_year_then_number_descending():
    db = _db()
    for numero, date in [(3, "2024-02-01"), (15, "2025-11-10"), (1, "2025-01-05")]:
        qe.merge_question_into_db(db, qe.normalize_question(_raw(numero=numero, date=date), "x.pdf"))
    ids = [q["id"] for q in db["questions"]]
    assert ids == ["QE-2025-015", "QE-2025-001", "QE-2024-003"]


def test_merge_question_into_db_rejects_entry_without_id():
    assert qe.merge_question_into_db(_db(), {"annee": 2025}) is False


# ── Garde-fou contre l'écrasement silencieux (voir QuestionConflictError) ──
# Cas vécu : trois questions écrites de 2010 sans numéro imprimé (Vriamont,
# Van Gorp, Lejeune de Schiervel), chacune extraite séparément avec
# "numero": 1 — la 2e et la 3e ont silencieusement écrasé la précédente sous
# le même id QE-2010-001 avant l'ajout de ce garde-fou.
def test_merge_question_into_db_raises_on_conflicting_author():
    q1 = qe.normalize_question(_raw(auteur="Georges Verzin"), "x.pdf")
    db = _db(q1)
    q2 = qe.normalize_question(_raw(auteur="Bernadette Vriamont"), "y.pdf")
    assert q1["id"] == q2["id"]  # même année+numéro -> même id, à tort
    try:
        qe.merge_question_into_db(db, q2)
        assert False, "aurait dû lever QuestionConflictError"
    except qe.QuestionConflictError as e:
        assert "Georges Verzin" in str(e) and "Bernadette Vriamont" in str(e)
    # La base n'a PAS été modifiée par la tentative en conflit.
    assert db["questions"][0]["auteur"] == "Georges Verzin"


def test_merge_question_into_db_raises_on_conflicting_date_same_author():
    q1 = qe.normalize_question(_raw(date="2025-11-10"), "x.pdf")
    db = _db(q1)
    q2 = qe.normalize_question(_raw(date="2025-01-05"), "y.pdf")
    try:
        qe.merge_question_into_db(db, q2)
        assert False, "aurait dû lever QuestionConflictError"
    except qe.QuestionConflictError:
        pass


def test_merge_question_into_db_allows_correction_same_author_and_date():
    # Même auteur·e ET même date : une vraie correction (titre/texte
    # retravaillé), jamais un conflit — voir aussi le test historique
    # test_merge_question_into_db_replaces_same_id_not_duplicates.
    q1 = qe.normalize_question(_raw(reponse=None), "x.pdf")
    db = _db(q1)
    q2 = qe.normalize_question(_raw(reponse="La réponse est arrivée."), "y.pdf")
    assert qe.merge_question_into_db(db, q2) is True
    assert db["questions"][0]["reponse"] == "La réponse est arrivée."


# ── process_pdf (orchestration, extraction/appel Claude monkeypatchés) ──────
def test_process_pdf_reports_progress_stages(monkeypatch, tmp_path):
    monkeypatch.setattr(qe, "extract_text_from_pdf", lambda p: "texte extrait")
    monkeypatch.setattr(qe, "call_claude_api", lambda text: _raw())
    reports = []
    pdf_path = tmp_path / "015.verzin.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    result = qe.process_pdf(pdf_path, progress_cb=reports.append)
    assert result["id"] == "QE-2025-015"
    assert reports == [{"stage": "extraction"}, {"stage": "verification"}]


def test_process_pdf_returns_none_when_no_text_extracted(monkeypatch):
    import pathlib
    monkeypatch.setattr(qe, "extract_text_from_pdf", lambda p: "")
    assert qe.process_pdf(pathlib.Path("scanned.pdf")) is None


def test_process_pdf_returns_none_when_claude_call_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(qe, "extract_text_from_pdf", lambda p: "texte")
    monkeypatch.setattr(qe, "call_claude_api", lambda text: None)
    pdf_path = tmp_path / "x.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    assert qe.process_pdf(pdf_path) is None


def test_process_pdf_returns_none_when_extraction_incomplete(monkeypatch, tmp_path):
    # Claude répond, mais sans numéro/date/auteur exploitables — échec
    # explicite (voir normalize_question), pas une entrée orpheline publiée.
    monkeypatch.setattr(qe, "extract_text_from_pdf", lambda p: "texte")
    monkeypatch.setattr(qe, "call_claude_api", lambda text: {"titre": "Sans numéro"})
    pdf_path = tmp_path / "x.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    assert qe.process_pdf(pdf_path) is None
