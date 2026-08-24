"""
╔══════════════════════════════════════════════════════════════════════════╗
║  BACKFILL DÉTERMINISTE DES DÉCISIONS — SANS PDF NI LLM (donc GRATUIT)     ║
╚══════════════════════════════════════════════════════════════════════════╝

Pour les points DÉLIBÉRATIFS déjà extraits mais laissés SANS décision, déduit le
statut à partir des SEULS champs `resume`/`titre` déjà présents dans la base,
via la MÊME logique déterministe que la récupération du pipeline
(_recover_decision_from_window : retrait > report > vote nominal > unanimité >
« Approbation »/« Goedkeuring »). Ne lit aucun PDF, n'appelle jamais Claude.

À utiliser quand la décision est MANIFESTE dans le texte déjà extrait (ex.
resume « Point retiré de l'ordre du jour », ou titre « … - Approbation ») —
évite une ré-extraction payante. Les cas où le statut n'est PAS dans le
resume/titre (il faut relire le PDF brut) restent du ressort de
reextract_targeted.

Prudence — n'écrit un statut que si c'est sûr :
  • uniquement les points à `decision` VIDE (jamais d'écrasement) ;
  • IGNORE les types sans décision attendue (questions orales, demandes
    d'habitants, interpellations, questions écrites, débats filmés : une
    décision vide y est NORMALE) ;
  • n'écrit que si la logique déterministe trouve un statut — jamais d'invention.

Produit exactement les mêmes champs (`decision`, `vote`) qu'une ré-extraction,
donc le résultat est identique — sans le coût.

USAGE (dépôt) :
    python pipeline/backfill_decisions.py --dry-run   # aperçu, rien écrit
    python pipeline/backfill_decisions.py             # applique + réécrit le JSON
    python pipeline/backfill_decisions.py --path <chemin>   # base explicite

USAGE (Colab, après Phase 0 du notebook — base sur le Drive) :
    from backfill_decisions import backfill_decisions
    import json
    from pv_extraction_pipeline import CONFIG
    p = CONFIG['DB_JSON_PATH']
    db = json.load(open(p, encoding='utf-8'))
    for c in backfill_decisions(db):
        print(c['date'], f"SP{c['sp']}", '→', c['decision'])
    json.dump(db, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
"""
import argparse
import json
import os

from pv_extraction_pipeline import _recover_decision_from_window
from audit_completeness import TYPES_SANS_DECISION

# Chemin par défaut : la base versionnée du dépôt (pipeline/ ↔ backend/).
_DEFAULT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "backend", "pv_conseil_schaerbeek.json",
)

_DECISION_VIDE = {"", "-", "—", "None"}


def _decision_manquante(point: dict) -> bool:
    d = point.get("decision")
    return d is None or (isinstance(d, str) and d.strip() in _DECISION_VIDE)


def backfill_decisions(db: dict, types_sans_decision=TYPES_SANS_DECISION) -> list[dict]:
    """Remplit EN PLACE les décisions manquantes déductibles du resume/titre.
    Retourne la liste des changements appliqués (date, sp, type, decision, titre).
    N'écrase jamais une décision existante ; ignore les types sans décision
    attendue ; n'écrit que si _recover_decision_from_window trouve un statut."""
    changes = []
    for s in db.get("seances", []):
        date = (s.get("seance") or {}).get("date")
        for p in s.get("points", []):
            if not _decision_manquante(p):
                continue
            if p.get("type") in types_sans_decision:
                continue
            text = f"{p.get('resume') or ''} {p.get('titre') or ''}"
            decision, vote = _recover_decision_from_window(text)
            if not decision:
                continue
            p["decision"] = decision
            p["vote"] = vote
            changes.append({
                "date": date, "sp": p.get("sp"), "type": p.get("type"),
                "decision": decision,
                "titre": (p.get("titre") or "").replace("\n", " ").strip()[:80],
            })
    return changes


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill déterministe des décisions (sans LLM).")
    ap.add_argument("--path", default=_DEFAULT_PATH, help="Base JSON des PV (défaut : dépôt).")
    ap.add_argument("--dry-run", action="store_true", help="Montre les changements sans écrire.")
    args = ap.parse_args()

    with open(args.path, encoding="utf-8") as f:
        db = json.load(f)

    changes = backfill_decisions(db)
    for c in changes:
        print(f"  {c['date']} SP{c['sp']} [{c['type']}] → {c['decision']}  {c['titre']}")
    print(f"\n{len(changes)} décision(s) récupérée(s) (déterministe, sans LLM).")

    if not changes:
        print("Rien à faire.")
        return
    if args.dry_run:
        print("(dry-run : aucune écriture)")
        return
    with open(args.path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)   # même format que save_database
    print(f"→ base réécrite : {args.path}")


if __name__ == "__main__":
    main()
