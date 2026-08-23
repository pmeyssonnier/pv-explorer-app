"""Tests de pipeline/questions_ecrites_extraction_pipeline.py — fonctions
pures (normalisation, fusion en base) et process_pdf en orchestration, avec
extraction PDF/appel Claude monkeypatchés (aucun appel réel).
"""
import questions_ecrites_extraction_pipeline as qe


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


def test_normalize_question_rejects_missing_numero():
    assert qe.normalize_question(_raw(numero=None), "x.pdf") is None


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
