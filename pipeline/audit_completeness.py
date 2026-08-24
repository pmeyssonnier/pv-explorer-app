"""
╔══════════════════════════════════════════════════════════════════════════╗
║  AUDIT DE COMPLÉTUDE HORS-LIGNE — SANS APPEL LLM (donc GRATUIT)          ║
╚══════════════════════════════════════════════════════════════════════════╝

Pour chaque séance DÉJÀ extraite dans la base, re-lit le PDF source et compare
les points ATTENDUS (comptés par regex `SP n.-`, via expected_sp_from_pages)
aux points PRÉSENTS dans le JSON. Révèle les séances incomplètes AVANT de
décider quoi re-extraire — évite de re-payer une extraction complète.

Coût : nul (aucun appel API ; seule la lecture PDF via pdfplumber est utilisée).

USAGE (Colab, PDF dans CONFIG["INPUT_DIR"]) :
    from pv_extraction_pipeline import load_database, CONFIG
    from audit_completeness import audit_completeness, print_audit
    db = load_database()
    report = audit_completeness(db, CONFIG["INPUT_DIR"])
    print_audit(report)   # → liste les séances incomplètes à re-extraire

Deux audits COMPLÉMENTAIRES, tous deux hors-ligne :
  • audit_completeness : SP totalement ABSENTS (compare la base au PDF) ;
  • audit_decisions    : points DÉLIBÉRATIFS présents mais SANS décision
                         (questions/demandes/interpellations exclues — une
                         `decision` vide y est normale). Ne lit même pas le PDF :
        from audit_completeness import audit_decisions, print_decision_audit
        print_decision_audit(audit_decisions(db))
"""
from pathlib import Path
from typing import Optional

from pv_extraction_pipeline import (
    extract_text_from_pdf, expected_sp_from_pages, extract_pdf_metadata,
)


# ── Audit des DÉCISIONS (compteur « sans statut ») ──────────────────────────
# Certains types de points ne donnent JAMAIS lieu à une décision/vote : questions
# orales, demandes d'habitants (interpellations), questions écrites, débats
# filmés. Pour eux, une `decision` vide est NORMALE. Les exclure évite un compteur
# « sans statut » trompeur (une séance riche en interpellations paraîtrait pleine
# de trous alors qu'elle est complète). Frozenset volontairement facile à ajuster.
TYPES_SANS_DECISION = frozenset({
    "question_orale", "demande_habitant", "interpellation",
    "question_ecrite", "debat_filme",
})

# Une décision est considérée MANQUANTE si le champ est absent ou réduit à un
# marqueur vide (mêmes variantes que la récupération déterministe du pipeline).
_DECISION_VIDE = {"", "-", "—", "None"}


def _decision_manquante(point: dict) -> bool:
    d = point.get("decision")
    return d is None or (isinstance(d, str) and d.strip() in _DECISION_VIDE)


def audit_decisions(db: dict, types_sans_decision=TYPES_SANS_DECISION) -> list[dict]:
    """Audit des DÉCISIONS, hors-ligne (sans PDF ni LLM) : par séance, recense les
    points DÉLIBÉRATIFS (hors questions/demandes/interpellations — voir
    types_sans_decision) laissés SANS décision. Complète audit_completeness (qui,
    lui, traque les SP totalement absents). Retourne un rapport, une entrée par
    séance concernée, trié par date."""
    report = []
    for s in db.get("seances", []):
        date = (s.get("seance") or {}).get("date")
        manquants = [
            p for p in s.get("points", [])
            if p.get("type") not in types_sans_decision and _decision_manquante(p)
        ]
        if manquants:
            report.append({
                "date": date,
                "sans_decision": len(manquants),
                "sp": sorted(p.get("sp") for p in manquants if isinstance(p.get("sp"), int)),
            })
    report.sort(key=lambda r: r["date"] or "")
    return report


def print_decision_audit(report: list[dict]) -> dict:
    """Résumé lisible de audit_decisions : total et détail par séance. Retourne un
    dict agrégé (total, séances, dates) — utile pour cibler les ré-extractions."""
    total = sum(r["sans_decision"] for r in report)
    bar = "━" * 56
    print(f"\n{bar}\n  AUDIT DES DÉCISIONS (hors-ligne — questions/demandes exclues)\n{bar}")
    print(f"  Points délibératifs sans décision : {total} "
          f"(sur {len(report)} séance(s))")
    for r in report:
        print(f"    ⚠ {r['date']} : {r['sans_decision']} — SP {r['sp']}")
    print(f"{bar}\n")
    return {"total_sans_decision": total, "seances": len(report),
            "dates": [r["date"] for r in report]}


def _index_pdfs(input_dir: str) -> tuple[dict, dict]:
    """Deux index des PDF présents : par nom de fichier, et par date (déduite du
    nom). Permet de retrouver le PDF d'une séance même sans champ source_file."""
    by_name, by_date = {}, {}
    for p in sorted(Path(input_dir).glob("**/*.pdf")):
        by_name[p.name] = p
        d = extract_pdf_metadata(p).get("date")
        if d:
            by_date.setdefault(d, p)
    return by_name, by_date


def _find_pdf(seance: dict, by_name: dict, by_date: dict) -> Optional[Path]:
    """Retrouve le PDF d'une séance : par source_file (v2.4) d'abord, sinon par
    date (les extractions plus anciennes n'ont pas toujours source_file)."""
    meta = seance.get("seance", {}) or {}
    src = meta.get("source_file")
    if src and src in by_name:
        return by_name[src]
    return by_date.get(meta.get("date"))


def audit_completeness(db: dict, input_dir: str) -> list[dict]:
    """Compare, séance par séance, les SP attendus (regex sur le PDF) aux SP déjà
    présents dans la base. AUCUN appel LLM. Retourne un rapport par séance."""
    by_name, by_date = _index_pdfs(input_dir)
    report = []
    for s in db.get("seances", []):
        meta = s.get("seance", {}) or {}
        date = meta.get("date")
        pdf = _find_pdf(s, by_name, by_date)
        if pdf is None:
            report.append({"date": date, "status": "pdf_introuvable",
                           "source_file": meta.get("source_file")})
            continue
        pages = extract_text_from_pdf(pdf)
        expected = expected_sp_from_pages(pages)
        got = {p["sp"] for p in s.get("points", []) if isinstance(p.get("sp"), int)}
        missing = sorted(set(expected) - got)
        report.append({
            "date": date, "pdf": pdf.name,
            "status": "ok" if not missing else "incomplet",
            "expected": len(expected), "extracted": len(got),
            "missing_sp": missing,
            "missing_pages": sorted({expected[sp] for sp in missing}),
        })
    return report


def print_audit(report: list[dict]) -> dict:
    """Résumé lisible : total attendu vs présent, séances incomplètes (à
    re-extraire en ciblé) et PDF introuvables. Retourne un dict agrégé."""
    incomplete = [r for r in report if r.get("status") == "incomplet"]
    not_found = [r for r in report if r.get("status") == "pdf_introuvable"]
    tot_exp = sum(r.get("expected", 0) for r in report)
    tot_got = sum(r.get("extracted", 0) for r in report)
    bar = "━" * 56
    print(f"\n{bar}\n  AUDIT DE COMPLÉTUDE (hors-ligne, sans LLM)\n{bar}")
    print(f"  Séances auditées         : {len(report)}")
    print(f"  Points attendus (regex)  : {tot_exp}")
    print(f"  Points présents (base)   : {tot_got}")
    print(f"  Séances incomplètes      : {len(incomplete)}")
    for r in incomplete:
        print(f"    ⚠ {r['date']} ({r['pdf']}) : {r['extracted']}/{r['expected']} — "
              f"SP manquants {r['missing_sp']} (pages {r['missing_pages']})")
    if not_found:
        print(f"  PDF introuvables         : {len(not_found)}")
        for r in not_found:
            print(f"    ? {r['date']} : source « {r.get('source_file')} » absente de INPUT_DIR")
    print(f"{bar}\n")
    return {
        "seances": len(report),
        "incompletes": len(incomplete),
        "pdf_introuvables": len(not_found),
        "expected_total": tot_exp,
        "extracted_total": tot_got,
        "seances_a_reextraire": [r["date"] for r in incomplete],
    }
