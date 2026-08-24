"""Tests de services.statistics.compute_stats — spécifiquement le graphe
« Activité citoyenne par an » (activite_types_par_annee/activite_type_order),
qui combine les points de PV (question orale/demande/motion) et les questions
écrites (canal séparé, comptées par leur propre champ "annee" — voir
services/questions_ecrites*.py). Fonction pure (db en entrée) : aucune
dépendance à la vraie base JSON, sauf pour load_qe_db, monkeypatché.
"""
import services.statistics as statistics


def _db(*points_par_seance):
    """points_par_seance : liste de (date, [points]). Construit une base
    minimale au format attendu par compute_stats."""
    return {"seances": [
        {"seance": {"date": date}, "points": points}
        for date, points in points_par_seance
    ]}


def test_activite_types_counts_pv_types_by_year(monkeypatch):
    monkeypatch.setattr(statistics, "load_qe_db", lambda: {"questions": []})
    db = _db(("2025-01-10", [
        {"type": "question_orale", "sp": 1},
        {"type": "question_orale", "sp": 2},
        {"type": "demande_habitant", "sp": 3},
        {"type": "motion", "sp": 4},
    ]))
    s = statistics.compute_stats(db)
    row = next(r for r in s["activite_types_par_annee"] if r["annee"] == "2025")
    assert row["Question orale"] == 2
    assert row["Demande"] == 1
    assert row["Motion"] == 1
    assert row["Question écrite"] == 0


def test_activite_types_excludes_administrative_point_types(monkeypatch):
    # point_normal/point_urgent = ~95 % du volume total, jamais comptés ici
    # (écraseraient visuellement les 4 types "citoyens" dans le même graphe).
    monkeypatch.setattr(statistics, "load_qe_db", lambda: {"questions": []})
    db = _db(("2025-01-10", [
        {"type": "point_normal", "sp": 1},
        {"type": "point_urgent", "sp": 2},
        {"type": "question_orale", "sp": 3},
    ]))
    s = statistics.compute_stats(db)
    row = next(r for r in s["activite_types_par_annee"] if r["annee"] == "2025")
    assert row["Question orale"] == 1
    assert sum(row[t] for t in s["activite_type_order"]) == 1


def test_activite_types_includes_written_questions_by_their_own_year(monkeypatch):
    # Une question écrite n'est jamais liée à une séance/un PV — comptée par
    # son propre champ "annee", pas par une date de séance.
    monkeypatch.setattr(statistics, "load_qe_db", lambda: {"questions": [
        {"annee": 2024, "numero": 1}, {"annee": 2024, "numero": 2}, {"annee": 2025, "numero": 1},
    ]})
    db = _db(("2024-06-01", [{"type": "question_orale", "sp": 1}]))
    s = statistics.compute_stats(db)
    by_year = {r["annee"]: r for r in s["activite_types_par_annee"]}
    assert by_year["2024"]["Question écrite"] == 2
    # 2025 n'a aucune séance dans ce fixture mais une question écrite : doit
    # quand même apparaître comme une ligne à part entière.
    assert by_year["2025"]["Question écrite"] == 1
    assert by_year["2025"]["Question orale"] == 0


def test_activite_type_order_is_fixed_and_returned():
    assert statistics.ACTIVITY_TYPE_ORDER == [
        "Question orale", "Demande", "Motion", "Question écrite",
    ]


def test_activite_types_tolerates_qe_load_failure(monkeypatch):
    # Fichier des questions écrites absent/corrompu : ne doit jamais faire
    # planter /stats, juste 0 question écrite pour l'année concernée.
    def boom():
        raise FileNotFoundError("questions_ecrites_schaerbeek.json")
    monkeypatch.setattr(statistics, "load_qe_db", boom)
    db = _db(("2025-01-10", [{"type": "question_orale", "sp": 1}]))
    s = statistics.compute_stats(db)
    row = next(r for r in s["activite_types_par_annee"] if r["annee"] == "2025")
    assert row["Question écrite"] == 0


def test_activite_types_skips_seances_and_questions_without_year(monkeypatch):
    monkeypatch.setattr(statistics, "load_qe_db", lambda: {"questions": [
        {"annee": None, "numero": 1}, {"numero": 2},
    ]})
    db = _db(("", [{"type": "question_orale", "sp": 1}]))
    s = statistics.compute_stats(db)
    assert "" not in {r["annee"] for r in s["activite_types_par_annee"]}


def test_seances_resume_carries_per_seance_activite_breakdown(monkeypatch):
    # Alimente le drill-down Année → Mois du graphe « Activité citoyenne »
    # (agrégation côté client, voir frontend/js/stats.js activityRows()).
    monkeypatch.setattr(statistics, "load_qe_db", lambda: {"questions": []})
    db = _db(("2025-03-10", [
        {"type": "question_orale", "sp": 1},
        {"type": "question_orale", "sp": 2},
        {"type": "demande_habitant", "sp": 3},
        {"type": "point_normal", "sp": 4},
    ]))
    s = statistics.compute_stats(db)
    resume = next(r for r in s["seances_resume"] if r["date"] == "2025-03-10")
    assert resume["activite"] == {"Question orale": 2, "Demande": 1}


def test_qe_resume_lists_written_question_dates_and_thematiques(monkeypatch):
    # Bucketage par mois côté client (les questions écrites n'ont pas de séance).
    monkeypatch.setattr(statistics, "load_qe_db", lambda: {"questions": [
        {"annee": 2025, "numero": 1, "date": "2025-03-05", "thematiques": ["voirie"]},
        {"annee": 2025, "numero": 2, "date": "2025-04-12"},
        {"annee": 2025, "numero": 3},   # pas de date → ignorée
    ]})
    db = _db(("2025-03-10", [{"type": "question_orale", "sp": 1}]))
    s = statistics.compute_stats(db)
    assert s["qe_resume"] == [
        {"date": "2025-03-05", "thematiques": ["voirie"]},
        {"date": "2025-04-12", "thematiques": []},
    ]


def test_qe_thematiques_merge_into_themes_par_annee(monkeypatch):
    # Les thématiques des questions écrites doivent s'ajouter à celles des
    # points de PV dans le même compteur par année (voir themes_par_annee).
    monkeypatch.setattr(statistics, "load_qe_db", lambda: {"questions": [
        {"annee": 2025, "numero": 1, "date": "2025-03-05", "thematiques": ["voirie"]},
    ]})
    db = _db(("2025-01-10", [
        {"type": "point_normal", "sp": 1, "thematiques": ["voirie"]},
    ]))
    s = statistics.compute_stats(db)
    assert dict(s["themes_par_annee"]["2025"])["voirie"] == 2
    assert dict(s["themes_par_annee"]["toutes"])["voirie"] == 2
