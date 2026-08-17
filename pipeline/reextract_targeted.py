"""
╔══════════════════════════════════════════════════════════════════════════╗
║  RE-EXTRACTION CIBLÉE — ne re-traite QUE les séances désignées            ║
╚══════════════════════════════════════════════════════════════════════════╝

Complément de audit_completeness.py : au lieu de re-payer des années entières
(run_pipeline(year=…, force_reprocess=True) re-traite TOUTES les séances de
l'année), on re-traite seulement les quelques séances à trous détectées par
l'audit.

Chaque séance ciblée passe par `process_pdf` (v2.4) → extraction + contrôle de
complétude + **récupération page-par-page des SP manqués** (GREFFE 2), puis
`merge_seance_into_db` **fusionne** le résultat : les points manquants sont
AJOUTÉS à la séance existante, le reste est enrichi, rien n'est écrasé à tort.

Coût : réduit. Les chunks déjà en cache ne re-facturent pas ; seule la
récupération relance le LLM sur les pages manquantes (nouveau prompt → nouvel
appel → le point récupéré porte enfin son `page`). Nécessite ANTHROPIC_API_KEY
et CONFIG["RECOVER_MISSING"]=True (défaut).

USAGE (Colab) :
    from audit_completeness import audit_completeness
    from reextract_targeted import targets_from_audit, reextract_seances
    from pv_extraction_pipeline import load_database, CONFIG

    db = load_database()
    report = audit_completeness(db, CONFIG["INPUT_DIR"])
    # ne cible que les vrais trous (≥ 2 SP manquants ignore les off-by-one/faux positifs)
    cibles = targets_from_audit(report, min_missing=2)
    reextract_seances(cibles)          # re-extrait + fusionne + sauvegarde

    # …ou cibler manuellement par date / nom de PDF :
    reextract_seances(["2016-11-30", "2017-04-26"])
"""
from pathlib import Path
from typing import Optional

from pv_extraction_pipeline import (
    get_logger, CONFIG, load_database, process_pdf,
    merge_seance_into_db, save_database,
)
from audit_completeness import _index_pdfs


def targets_from_audit(report: list[dict], min_missing: int = 1) -> list[str]:
    """Liste des séances à re-extraire d'après l'audit : celles au statut
    « incomplet » avec au moins `min_missing` SP manquants. Mets `min_missing`
    à 2 ou 3 pour ignorer les off-by-one (souvent des faux positifs regex)."""
    return [r["date"] for r in report
            if r.get("status") == "incomplet"
            and len(r.get("missing_sp", [])) >= min_missing]


def _resolve_target(target: str, by_name: dict, by_date: dict) -> Optional[Path]:
    """Retrouve le PDF d'une cible exprimée par nom de fichier OU par date
    (YYYY-MM-DD), sinon par correspondance partielle du nom."""
    if target in by_name:
        return by_name[target]
    if target in by_date:
        return by_date[target]
    for name, p in by_name.items():
        if target in name:
            return p
    return None


def reextract_seances(targets: list[str], save: bool = True) -> dict:
    """Re-traite UNIQUEMENT les séances ciblées (date « YYYY-MM-DD » ou nom de
    PDF) avec le pipeline complet, puis fusionne dans la base. Ne touche pas aux
    autres séances. Retourne la base mise à jour."""
    log = get_logger()
    db = load_database()
    by_name, by_date = _index_pdfs(CONFIG["INPUT_DIR"])

    done, recovered_pts, failed = 0, 0, []
    for t in targets:
        pdf = _resolve_target(t, by_name, by_date)
        if pdf is None:
            log.warning(f"  ? Cible « {t} » : PDF introuvable dans INPUT_DIR")
            failed.append(t)
            continue
        log.info(f"↻ Re-extraction ciblée : {pdf.name}")
        before = _points_for_date(db, _date_of(pdf, by_date))
        seance = process_pdf(pdf)          # extraction + complétude + récupération
        if seance is None:
            log.warning(f"  ✗ Échec sur {pdf.name}")
            failed.append(t)
            continue
        merge_seance_into_db(db, seance)   # ajoute les points manquants
        after = len(seance.get("points", []))
        recovered_pts += max(0, after - before)
        done += 1

    if save and done:
        save_database(db)

    log.info(
        f"\nRe-extraction ciblée terminée : {done} séance(s) re-traitée(s), "
        f"~{recovered_pts} point(s) ajouté(s), {len(failed)} échec(s)."
    )
    if failed:
        log.warning(f"  Cibles non résolues : {failed}")
    log.info("→ Relance audit_completeness() pour confirmer que les trous sont comblés.")
    return db


def _date_of(pdf: Path, by_date: dict) -> Optional[str]:
    """Date associée à un PDF (recherche inverse dans l'index par date)."""
    for d, p in by_date.items():
        if p == pdf:
            return d
    return None


def _points_for_date(db: dict, date: Optional[str]) -> int:
    """Nombre de points déjà présents pour une date (0 si séance absente)."""
    if not date:
        return 0
    for s in db.get("seances", []):
        if (s.get("seance", {}) or {}).get("date") == date:
            return len(s.get("points", []))
    return 0
