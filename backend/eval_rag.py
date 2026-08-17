"""
╔══════════════════════════════════════════════════════════════════════════╗
║  CALIBRAGE DU SEUIL DE PERTINENCE RAG (SCORE_MIN)                        ║
║  Mesure la distribution des scores de similarité sur un jeu de questions ║
║  de référence — pour choisir SCORE_MIN sur des DONNÉES, pas au hasard.   ║
╚══════════════════════════════════════════════════════════════════════════╝

Idée : une bonne valeur de SCORE_MIN sépare les questions PERTINENTES (qui
doivent ramener des passages) des questions HORS-SUJET (qui ne devraient rien
ramener de solide). On mesure donc les deux bandes de scores et on regarde
l'écart.

USAGE (depuis backend/, avec PINECONE_API_KEY défini) :
    python eval_rag.py

Coût : négligeable (~1 embedding par question ≈ 20 tokens). Nécessite un index
Pinecone interrogeable (donc quota d'embedding mensuel non épuisé).
"""
import os
import sys
import statistics

from config import NAMESPACE, TOP_K
from utils.dates import _year_filter
from services.pinecone_service import get_pinecone_index
from services.rag import _hit_score

# ── Jeu de questions de référence, par catégorie ────────────────────────────
# Les 4 premières catégories DOIVENT ramener des passages pertinents ;
# « hors_sujet » sert de plancher de bruit (à quel score plafonnent des
# requêtes sans rapport avec les PV ?). C'est l'écart entre les deux qui guide
# le choix du seuil. Complète/adapte librement — vise 30-50 questions au total.
REFERENCE_QUESTIONS = {
    "factuel_annee": [
        "Quelles décisions ont été prises en 2018 ?",
        "Quels marchés publics en 2020 ?",
        "Décisions du conseil communal en 2015",
        "Votes du conseil en 2024",
    ],
    "thematique": [
        "Quels subsides pour les clubs de sport ?",
        "Décisions sur la propreté publique",
        "Aménagement des pistes cyclables",
        "Rénovation des écoles communales",
        "Sécurité et prévention dans les quartiers",
        "Logements sociaux à Schaerbeek",
        "Budget de la culture et des bibliothèques",
        "Plantation d'arbres et espaces verts",
    ],
    "longitudinal": [
        "Comment le budget propreté a-t-il évolué depuis 2012 ?",
        "Évolution des subsides sportifs au fil des ans",
        "Dépenses de voirie depuis 2015",
    ],
    "entite_precise": [
        "Marché public pour l'éclairage public",
        "Convention avec une ASBL de quartier",
        "Achat de véhicules pour les services communaux",
        "Travaux au parc Josaphat",
        "Taxe communale sur les immeubles",
    ],
    "faiblement_represente": [
        "Politique sur les trottinettes électriques",
        "Intelligence artificielle dans l'administration",
        "Jumelage international de la commune",
    ],
    "hors_sujet": [
        "Quelle est la météo demain à Bruxelles ?",
        "Recette de la tarte au sucre",
        "Qui a gagné la Coupe du monde de football ?",
        "Comment réparer une chambre à air de vélo ?",
        "Quel est le prix du bitcoin aujourd'hui ?",
        "Résume-moi le dernier film de science-fiction",
    ],
}

# Catégories dont on attend des résultats pertinents (tout sauf hors_sujet).
IN_SCOPE = [c for c in REFERENCE_QUESTIONS if c != "hors_sujet"]


def scores_for(question: str) -> list[float]:
    """Scores triés (décroissants) des TOP_K passages pour une question —
    même construction de requête que /ask (filtre année inclus)."""
    query = {"inputs": {"text": question}, "top_k": TOP_K}
    yf = _year_filter(question)
    if yf:
        query["filter"] = {"year": yf}
    res = get_pinecone_index().search(namespace=NAMESPACE, query=query)
    hits = res.get("result", {}).get("hits", [])
    return sorted((_hit_score(h) for h in hits), reverse=True)


def _fmt(x: float) -> str:
    return f"{x:.3f}"


def main():
    if not os.environ.get("PINECONE_API_KEY"):
        print("❌ PINECONE_API_KEY manquante.")
        sys.exit(1)

    per_q = {}   # question -> scores
    print("Interrogation de l'index (une ligne par question)...\n")
    for cat, questions in REFERENCE_QUESTIONS.items():
        print(f"── {cat} " + "─" * (60 - len(cat)))
        for q in questions:
            try:
                sc = scores_for(q)
            except Exception as e:
                blob = f"{type(e).__name__} {e}"
                if any(k in blob for k in ("RESOURCE_EXHAUSTED", "429", "token limit")):
                    print("\n❌ Quota d'embedding Pinecone épuisé — impossible de mesurer.")
                    print("   Rétablis le quota (upgrade / reset mensuel) puis relance.")
                    sys.exit(2)
                print(f"   ⚠️ {q[:50]} → erreur : {e}")
                continue
            per_q[q] = sc
            top1 = sc[0] if sc else float("nan")
            top5 = statistics.mean(sc[:5]) if sc else float("nan")
            print(f"   top1={_fmt(top1)}  top5̄={_fmt(top5)}  n={len(sc):2d}  {q[:48]}")
        print()

    # ── Analyse : bandes in-scope vs hors-sujet ─────────────────────────────
    in_top1 = [per_q[q][0] for c in IN_SCOPE for q in REFERENCE_QUESTIONS[c]
               if per_q.get(q)]
    off_top1 = [per_q[q][0] for q in REFERENCE_QUESTIONS["hors_sujet"]
                if per_q.get(q)]
    if not in_top1 or not off_top1:
        print("Pas assez de données pour l'analyse.")
        return

    print("═" * 64)
    print("DISTRIBUTION DES MEILLEURS SCORES (top-1 par question)")
    print("═" * 64)

    def band(label, xs):
        xs = sorted(xs)
        med = statistics.median(xs)
        print(f"  {label:12s} n={len(xs):2d}  min={_fmt(xs[0])}  "
              f"médiane={_fmt(med)}  max={_fmt(xs[-1])}")

    band("PERTINENT", in_top1)
    band("HORS-SUJET", off_top1)

    gap_lo, gap_hi = max(off_top1), min(in_top1)
    print()
    if gap_hi > gap_lo:
        mid = (gap_lo + gap_hi) / 2
        print(f"✅ Séparation NETTE : hors-sujet ≤ {_fmt(gap_lo)} < "
              f"{_fmt(gap_hi)} ≤ pertinent")
        print(f"   → SCORE_MIN candidat ≈ {_fmt(mid)} (milieu de l'écart)")
    else:
        print(f"⚠️ CHEVAUCHEMENT : hors-sujet monte à {_fmt(gap_lo)}, "
              f"pertinent descend à {_fmt(gap_hi)}.")
        print("   → pas de seuil parfait ; voir le tableau de rétention ci-dessous.")

    # ── Tableau de rétention : combien de passages resteraient par seuil ─────
    print("\n" + "═" * 64)
    print("RÉTENTION PAR SEUIL CANDIDAT")
    print("  (passages moyens gardés pour une question pertinente ; "
          "questions hors-sujet laissant encore ≥1 passage)")
    print("═" * 64)
    lo = min(min(s) for s in per_q.values() if s)
    hi = max(max(s) for s in per_q.values() if s)
    step = max(0.01, round((hi - lo) / 12, 3))
    thr = round(lo, 3)
    print(f"  {'seuil':>7} | {'passages/question pertinente':>28} | "
          f"{'hors-sujet non filtrés':>22}")
    while thr <= hi:
        kept = [sum(1 for x in per_q[q] if x >= thr)
                for c in IN_SCOPE for q in REFERENCE_QUESTIONS[c] if per_q.get(q)]
        avg_kept = statistics.mean(kept) if kept else 0
        off_pass = sum(1 for q in REFERENCE_QUESTIONS["hors_sujet"]
                       if per_q.get(q) and any(x >= thr for x in per_q[q]))
        print(f"  {thr:7.3f} | {avg_kept:28.1f} | {off_pass:22d}")
        thr = round(thr + step, 3)

    print("\nLecture : choisis le seuil le plus BAS qui met « hors-sujet non "
          "filtrés » à 0 tout en gardant assez de passages pertinents (idéal "
          "≥ 5-8). Mets-le ensuite dans la variable d'env SCORE_MIN.")


if __name__ == "__main__":
    main()
