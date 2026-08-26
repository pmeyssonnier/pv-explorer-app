"""Backfill de `repondants` (liste) depuis `repondant` (singulier) — sans PDF ni LLM.

`index_pv.py` et `models.api.Source` ne lisent QUE `repondants` (voir PR #169,
« sans repli sur l'ancien champ »). Tant que la base n'a pas été RÉ-EXTRAITE,
`repondants` reste absent partout, alors que `repondant` porte déjà
l'information sur 2 171 points. Ce script ne fait que la REFORMATER — via la
même fonction, `attribution._respondents`, déjà utilisée en direct par
`services.seances` pour l'affichage de l'onglet Séances. Ces tests vérifient
qu'écrire son résultat dans le JSON ne change rien à ce que l'API rendait déjà.
"""
from backfill_repondants import backfill_repondants
from services.people.attribution import _respondents


def _db(points, date="1999-01-07", **meta):
    return {"seances": [{"seance": {"date": date, **meta}, "points": points}]}


def test_un_repondant_simple_devient_une_liste_d_un_nom():
    db = _db([{"sp": 1, "repondant": "Noël"}])
    changes = backfill_repondants(db)
    assert db["seances"][0]["points"][0]["repondants"] == ["Noël"]
    assert changes[0]["apres"] == ["Noël"]


def test_un_repondant_compose_devient_deux_noms():
    db = _db([{"sp": 1, "repondant": "Cédric Mahieu et Justine Harzé"}])
    backfill_repondants(db)
    assert db["seances"][0]["points"][0]["repondants"] == ["Cédric Mahieu", "Justine Harzé"]


def test_un_role_seul_se_resout_via_les_metadonnees_de_seance():
    # « Bourgmestre ff » seul (aucun nom dans le texte) → résolu par le nom de
    # la personne qui assurait l'intérim ce jour-là — même règle que
    # l'affichage de l'onglet Séances.
    db = _db([{"sp": 1, "repondant": "Bourgmestre ff"}], bourgmestre_ff="Jodogne")
    backfill_repondants(db)
    assert db["seances"][0]["points"][0]["repondants"] == ["Jodogne"]


def test_un_role_non_resoluble_donne_une_liste_vide_pas_une_absence():
    # « Secrétaire communal » n'est pas un rôle que _respondents sait
    # résoudre : le champ est quand même écrit, vide — même résultat que
    # l'affichage en donnerait déjà aujourd'hui, pas une régression.
    db = _db([{"sp": 1, "repondant": "Secrétaire communal"}])
    changes = backfill_repondants(db)
    assert db["seances"][0]["points"][0]["repondants"] == []
    assert changes[0]["apres"] == []


def test_repondant_vide_n_est_pas_touche():
    db = _db([{"sp": 1, "repondant": ""}, {"sp": 2}])
    assert backfill_repondants(db) == []
    assert "repondants" not in db["seances"][0]["points"][0]
    assert "repondants" not in db["seances"][0]["points"][1]


def test_repondant_singulier_n_est_pas_efface():
    """services.seances continue de lire `repondant` pour l'affichage : le
    backfill AJOUTE `repondants`, il ne retire rien."""
    db = _db([{"sp": 1, "repondant": "Noël"}])
    backfill_repondants(db)
    assert db["seances"][0]["points"][0]["repondant"] == "Noël"


def test_backfill_est_idempotent():
    db = _db([{"sp": 1, "repondant": "Noël"}, {"sp": 2, "repondant": "Secrétaire communal"}])
    backfill_repondants(db)
    avant = [dict(p) for p in db["seances"][0]["points"]]
    assert backfill_repondants(db) == []                 # 2e passe : rien
    assert db["seances"][0]["points"] == avant


def test_un_point_deja_backfille_n_est_pas_rejuge():
    """Un point déjà séparé fait foi, même si son repondant a l'air différent —
    on ne réévalue jamais un champ déjà écrit."""
    db = _db([{"sp": 1, "repondant": "Noël", "repondants": ["Quelqu'un d'autre"]}])
    assert backfill_repondants(db) == []
    assert db["seances"][0]["points"][0]["repondants"] == ["Quelqu'un d'autre"]


def test_meme_resultat_que_l_affichage_en_direct():
    """Le backfill n'invente rien : il fige exactement ce que _respondents
    calcule déjà en direct pour l'onglet Séances — même fonction, mêmes
    arguments."""
    meta = {"date": "2010-02-03", "bourgmestre_ff": "Jodogne"}
    for raw in ["Noël", "Cédric Mahieu et Justine Harzé", "Bourgmestre ff", ""]:
        db = _db([{"sp": 1, "repondant": raw}], **{k: v for k, v in meta.items() if k != "date"})
        backfill_repondants(db)
        attendu = _respondents(raw, meta)
        assert db["seances"][0]["points"][0].get("repondants", []) == attendu
