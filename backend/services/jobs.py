"""File de tâches en mémoire, ultra-légère (in-process, aucune dépendance
externe) — pour les opérations admin trop longues pour tenir dans UNE seule
requête HTTP synchrone : une extraction dense (plusieurs appels Claude
séquentiels) ou une publication (commit GitHub + upsert Pinecone, avec retry
qui peut attendre jusqu'à 65s sur un 429) dépassent régulièrement le délai
que tolère le proxy Render avant de couper la connexion — observé en
pratique (la requête échoue côté navigateur en "CORS bloqué", faute de toute
réponse à vérifier, alors que ce n'est pas un problème de CORS).

Pas de queue externe (Celery/Redis/RQ) : un seul admin, un seul process
Render — un dict protégé par verrou suffit très largement pour ce volume
d'usage, et évite une dépendance/infra de plus pour un besoin aussi rare.
Les tâches tournent dans un thread réel (pas asyncio) car process_pdf/
commit_file/index_chunks sont des appels bloquants (I/O réseau synchrone).
"""
import threading
import uuid
from typing import Callable, Optional

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_MAX_JOBS = 50   # purge FIFO grossière anti-fuite mémoire (pas de TTL : trop rare pour le justifier)


def start_job(fn: Callable, *args, **kwargs) -> str:
    """Lance `fn(*args, **kwargs)` en tâche de fond, retourne un job_id à
    interroger via get_job(). Le statut passe à "error" si `fn` lève
    ValueError (422 côté route) ou RuntimeError (500 côté route) — toute
    autre exception est aplatie en 500 générique (jamais de traceback
    interne exposé au client)."""
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {"status": "pending"}
        while len(_jobs) > _MAX_JOBS:
            oldest = next(iter(_jobs))
            if oldest == job_id:
                break
            _jobs.pop(oldest, None)

    def run():
        try:
            result = fn(*args, **kwargs)
            with _lock:
                _jobs[job_id] = {"status": "done", "result": result}
        except ValueError as e:
            with _lock:
                _jobs[job_id] = {"status": "error", "code": 422, "detail": str(e)}
        except RuntimeError as e:
            with _lock:
                _jobs[job_id] = {"status": "error", "code": 500, "detail": str(e)}
        except Exception:
            with _lock:
                _jobs[job_id] = {"status": "error", "code": 500, "detail": "Erreur interne inattendue."}

    threading.Thread(target=run, daemon=True, name=f"job-{job_id[:8]}").start()
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        return _jobs.get(job_id)
