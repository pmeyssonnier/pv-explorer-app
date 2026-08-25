#!/usr/bin/env python3
"""Lance les audits HORS-LIGNE de la base des PV en une commande, et affiche
leurs résultats. Aucun PDF, aucune clé API : les deux audits ci-dessous ne
lisent que le JSON déjà versionné.

  • audit_decisions  : points délibératifs présents mais SANS décision
                       (questions/demandes/interpellations exclues — vide normal).
  • audit_authors    : « sans demandeur » — anomalies (question/demande sans
                       auteur) + motions non attribuées.
  • audit_respondents : « sans répondant » — questions/demandes non retirées
                       auxquelles aucun·e membre du Collège ne répond (type +
                       statut du point pris en compte).

Le 3ᵉ audit, audit_completeness (SP totalement ABSENTS), relit les PDF : il
n'est PAS lancé ici (il tourne en Colab, cf. Phase 1 du notebook).

USAGE :
    python voir_audits.py                 # base du dépôt (backend/…json)
    python voir_audits.py --path <chemin> # base explicite
    PV_JSON_PATH=<chemin> python voir_audits.py
"""
import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
# Rend importables les modules à plat de pipeline/ et backend/ (comme conftest).
for _sub in ("pipeline", "backend"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DEFAULT_DB = os.path.join(_ROOT, "backend", "pv_conseil_schaerbeek.json")


def main() -> None:
    ap = argparse.ArgumentParser(description="Audits hors-ligne de la base des PV.")
    ap.add_argument("--path", default=os.environ.get("PV_JSON_PATH", _DEFAULT_DB),
                    help="Base JSON des PV (défaut : celle du dépôt, ou $PV_JSON_PATH).")
    args = ap.parse_args()

    with open(args.path, encoding="utf-8") as f:
        db = json.load(f)

    # Imports après ajustement du sys.path (et sans dépendance PDF : audit_decisions
    # n'importe plus pv_extraction_pipeline au chargement — voir audit_completeness).
    from audit_completeness import audit_decisions, print_decision_audit
    from services.people.attribution import (
        audit_authors, print_author_audit,
        audit_respondents, print_respondent_audit,
    )

    print_decision_audit(audit_decisions(db))
    print_author_audit(audit_authors(db))
    print_respondent_audit(audit_respondents(db))


if __name__ == "__main__":
    main()
