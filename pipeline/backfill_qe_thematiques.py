"""
╔══════════════════════════════════════════════════════════════════════════╗
║  BACKFILL THÉMATIQUES — QUESTIONS ÉCRITES DÉJÀ PUBLIÉES                   ║
╚══════════════════════════════════════════════════════════════════════════╝

questions_ecrites_extraction_pipeline.py extrait désormais "thematiques"
(voir SYSTEM_PROMPT), mais uniquement pour les PDF traités APRÈS l'ajout de
ce champ — les questions déjà publiées auparavant ont "thematiques": [] (ou
le champ absent). Ce script les reclasse, à partir du texte DÉJÀ EN BASE
(titre/question/réponse) : jamais besoin du PDF d'origine ni de repasser
par extract_text_from_pdf.

Idempotent par défaut (ne retraite que "thematiques" absent/vide) — relancer
sans risque si interrompu ou si de nouvelles questions sans thème arrivent
entre-temps. --force reclasse tout, y compris les entrées déjà classées.

USAGE (Colab, même API key que le reste du pipeline) :
    export ANTHROPIC_API_KEY="sk-ant-..."
    cd backend && python3 ../pipeline/backfill_qe_thematiques.py
    python3 ../pipeline/backfill_qe_thematiques.py --input questions_ecrites_schaerbeek.json --force

Après exécution : réindexer Pinecone pour que les thématiques remontent
aussi dans les sources du chat (voir index_qe.py) :
    python3 index_qe.py
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from pv_extraction_pipeline import _clean_str_list
from questions_ecrites_extraction_pipeline import get_client

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 512          # classification courte, pas un texte intégral
API_DELAY_SEC = 0.5
MAX_RETRIES = 3

SYSTEM_PROMPT = """Tu es un expert en classification thématique des questions écrites \
adressées au Collège des Bourgmestre et Échevins d'une commune belge (Schaerbeek).

Étant donné le titre, la question et la réponse d'une question écrite, identifie 1 à 4 \
thématiques pertinentes, sous forme de tags courts en snake_case français (ex. \
"stationnement", "securite_routiere", "proprete_publique") — même style libre qu'un point \
de procès-verbal, pas de liste fermée. Ne DEVINE JAMAIS un sujet absent du texte.

RÉPONDS UNIQUEMENT en JSON valide, sans markdown, sans texte avant/après :
{"thematiques": [...]}
"""


def classify_thematiques(titre: str, question: str, reponse: str | None) -> list[str]:
    client = get_client()
    text = f"Titre : {titre}\n\nQuestion : {question}\n\nRéponse : {reponse or '(pas de réponse)'}"
    for attempt in range(MAX_RETRIES):
        raw = ""
        try:
            with client.messages.stream(
                model=MODEL, max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT, messages=[{"role": "user", "content": text}],
            ) as stream:
                response = stream.get_final_message()
            raw = next((b.text for b in response.content
                        if getattr(b, "type", None) == "text"), "").strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw).strip()
            data = json.loads(raw)
            return _clean_str_list(data.get("thematiques"))
        except json.JSONDecodeError as e:
            print(f"    JSON invalide (tentative {attempt + 1}) : {e}")
            time.sleep(2 ** attempt)
        except Exception as e:
            print(f"    erreur (tentative {attempt + 1}) : {e}")
            time.sleep(5)
    print("    échec après 3 tentatives — thematiques laissées vides")
    return []


def backfill_thematiques(db: dict, force: bool = False) -> int:
    """Classe en place les questions dont "thematiques" est absent/vide (ou
    toutes si `force`) — mute `db`, ne touche pas au disque. Renvoie le
    nombre de questions classées."""
    targets = [q for q in db.get("questions", []) if force or not q.get("thematiques")]
    for i, q in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {q.get('id')} — {(q.get('titre') or '')[:60]}")
        q["thematiques"] = classify_thematiques(
            q.get("titre") or "", q.get("question") or "", q.get("reponse"),
        )
        print(f"   → {q['thematiques']}")
        time.sleep(API_DELAY_SEC)
    return len(targets)


def main():
    parser = argparse.ArgumentParser(
        description="Reclasse les thématiques des questions écrites déjà publiées",
    )
    parser.add_argument("--input", default="questions_ecrites_schaerbeek.json")
    parser.add_argument("--force", action="store_true",
                        help="Reclasse aussi les questions ayant déjà des thématiques")
    args = parser.parse_args()

    db_path = Path(args.input)
    if not db_path.exists():
        print(f"❌ Fichier introuvable : {db_path}")
        sys.exit(1)
    with open(db_path, encoding="utf-8") as f:
        db = json.load(f)

    print("═" * 60)
    print("  BACKFILL THÉMATIQUES — QUESTIONS ÉCRITES")
    print("═" * 60)
    total = len(db.get("questions", []))
    n_targets = sum(1 for q in db.get("questions", []) if args.force or not q.get("thematiques"))
    print(f"📄 {total} question(s) en base — {n_targets} à (re)classer")
    if not n_targets:
        print("✅ Rien à faire.")
        return

    n_done = backfill_thematiques(db, force=args.force)

    with open(db_path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    print(f"\n✅ {n_done} question(s) classée(s). Base sauvegardée dans {db_path}")
    print("   → Pense à réindexer Pinecone (les métadonnées ont changé) : python3 index_qe.py")


if __name__ == "__main__":
    main()
