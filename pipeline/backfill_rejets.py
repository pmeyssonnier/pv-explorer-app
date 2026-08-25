"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LES MOTIONS BATTUES — DÉRIVÉES DU VOTE DÉJÀ STOCKÉ, SANS PDF NI LLM     ║
╚══════════════════════════════════════════════════════════════════════════╝

Sur 222 motions, la base n'en compte AUCUNE comme rejetée. Ce n'est pas ce que
les séances disent : 10 motions ont recueilli plus de contre que de pour, et le
décompte est déjà dans la base, point par point. Trois de leurs résumés le
disent même en toutes lettres (« Motion rejetée par vote nominal : 22 non,
16 oui, 1 abstention »).

La cause était dans le vocabulaire, pas dans la lecture des PV : le schéma
d'extraction n'avait pas de mot pour « non » (voir la liste `decision`, où
« REJETÉ » vient d'être ajouté). Sommé de choisir, l'extracteur prenait le plus
proche — « DÉBAT » huit fois, et deux fois « DÉCIDE », si bien qu'une motion
battue par 19 voix contre 24 s'affichait « Décidé ».

CE QUE CE SCRIPT DÉRIVE, ET CE QU'IL REFUSE DE DÉRIVER

    motion + contre > pour   →  decision = "REJETÉ"        appliqué
    motion + contre = pour   →  rien                       signalé
    autre type + vote perdu  →  rien                       signalé

    Le `debat` n'est PAS effacé : une motion peut avoir été DÉBATTUE puis
    REJETÉE. Les deux dimensions coexistent — c'est exactement ce que leur
    séparation permet de dire (voir utils_statut).

Restreint aux MOTIONS parce que le vote y porte sur la motion elle-même. Sur un
point délibératif, le décompte enregistré appartient parfois à une question
attachée — un amendement, une motion d'ordre : au 2013-10-23, « la demande de
Monsieur Bernard est refusée par 14 oui et 25 non » alors que le point, lui, a
suivi son cours. Appliquer là-bas la même arithmétique inventerait des rejets.
Ces cas sont donc listés, pas corrigés : ils demandent le PDF.

SEUL SCRIPT DU DÉPÔT QUI ÉCRASE UNE DÉCISION EXISTANTE
    backfill_decisions ne remplit que les décisions VIDES, par prudence. Ici,
    deux points portent une décision que leur propre vote contredit : 7 pour /
    34 contre pour un « DÉCIDE ». Entre le mot et le décompte, c'est le
    décompte qui fait foi — un PV n'écrit pas 34 contre par accident. Ces deux
    corrections sont affichées à part, pour être relues une par une.

USAGE (dépôt) :
    python pipeline/backfill_rejets.py --dry-run   # aperçu, rien écrit
    python pipeline/backfill_rejets.py             # applique + réécrit
    python pipeline/backfill_rejets.py --path <chemin>

⚠️ APRÈS APPLICATION : le texte vectorisé de ces points change (« Décision :
   DÉBAT » → « Décision : REJETÉ »). Réindexer CES points seulement — les ID
   sont stables, l'upsert est idempotent, le reste de l'index n'est pas touché.
"""
import argparse
import json
import os

_DEFAULT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "backend", "pv_conseil_schaerbeek.json",
)

DECISION_REJET = "REJETÉ"


def _voix(point: dict):
    """(pour, contre) si le PV a chiffré le vote, (None, None) sinon."""
    v = point.get("vote") or {}
    pour, contre = v.get("pour"), v.get("contre")
    if isinstance(pour, int) and isinstance(contre, int):
        return pour, contre
    return None, None


def backfill_rejets(db: dict) -> dict:
    """Inscrit EN PLACE la décision « REJETÉ » sur les motions que leur vote a
    battues. Retourne {"appliques": [...], "signales": [...]} — les seconds sont
    montrés à l'utilisateur, jamais écrits. Idempotent."""
    appliques, signales = [], []
    for s in db.get("seances", []):
        date = (s.get("seance") or {}).get("date")
        for p in s.get("points", []):
            pour, contre = _voix(p)
            if pour is None or contre < pour:
                continue
            fiche = {
                "date": date, "sp": p.get("sp"), "type": p.get("type"),
                "pour": pour, "contre": contre,
                "abstentions": (p.get("vote") or {}).get("abstentions"),
                "avant": p.get("decision"), "debat": bool(p.get("debat")),
                "titre": (p.get("titre") or "").replace("\n", " ").strip()[:80],
            }
            if p.get("type") != "motion":
                fiche["motif"] = "type non-motion : le vote peut porter sur un amendement"
                signales.append(fiche)
                continue
            if contre == pour:
                fiche["motif"] = "égalité : la proposition n'est pas adoptée, mais le PV seul le confirme"
                signales.append(fiche)
                continue
            if p.get("decision") == DECISION_REJET:
                continue                              # déjà corrigé
            # Le statut de traitement (« traité ») et le débat restent tels
            # quels : la motion a bien été traitée, et souvent débattue avant
            # d'être rejetée. Seule la DÉCISION change.
            p["decision"] = DECISION_REJET
            appliques.append(fiche)
    return {"appliques": appliques, "signales": signales}


def _ligne(f):
    return (f"  {f['date']} SP{f['sp']:<5} [{f['type']}] "
            f"{f['pour']} pour / {f['contre']} contre / {f['abstentions']} abst."
            f"  « {f['avant']} »" + (" + débat" if f["debat"] else ""))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Inscrit « REJETÉ » sur les motions battues par leur propre vote (sans LLM).")
    ap.add_argument("--path", default=_DEFAULT_PATH, help="Base JSON des PV (défaut : dépôt).")
    ap.add_argument("--dry-run", action="store_true", help="Montre le bilan sans écrire.")
    args = ap.parse_args()

    with open(args.path, encoding="utf-8") as f:
        db = json.load(f)

    bilan = backfill_rejets(db)
    appliques, signales = bilan["appliques"], bilan["signales"]

    corriges = [f for f in appliques if f["avant"]]
    combles = [f for f in appliques if not f["avant"]]
    print(f"{len(appliques)} motion(s) rejetée(s) par leur propre vote\n")
    if combles:
        print(f"  Sans décision jusqu'ici ({len(combles)}) — l'issue manquait :")
        for f in combles:
            print(_ligne(f))
        print()
    if corriges:
        print(f"  ⚠ Décision CONTREDITE par le vote ({len(corriges)}) — à relire une par une :")
        for f in corriges:
            print(_ligne(f))
            print(f"        {f['titre']}")
        print()
    if signales:
        print(f"  Signalés, NON corrigés ({len(signales)}) — demandent le PDF :")
        for f in signales:
            print(_ligne(f))
            print(f"        {f['motif']}")
        print()

    if not appliques:
        print("Rien à faire (base déjà corrigée).")
        return
    if args.dry_run:
        print("(dry-run : aucune écriture)")
        return
    with open(args.path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)   # même format que save_database
    print(f"→ base réécrite : {args.path}")
    print("⚠ Réindexer CES points dans Pinecone : leur texte vectorisé a changé.")


if __name__ == "__main__":
    main()
