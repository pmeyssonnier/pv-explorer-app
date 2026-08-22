"""Tests de services/jobs.py en isolation (pas via les routes /admin/*, déjà
couvertes par test_admin_seances.py) : statut pending→done/error, éviction
FIFO au-delà de _MAX_JOBS."""
import time

import services.jobs as jobs


def _wait_done(job_id, max_wait=2.0):
    deadline = time.time() + max_wait
    job = jobs.get_job(job_id)
    while time.time() < deadline and job["status"] == "pending":
        time.sleep(0.02)
        job = jobs.get_job(job_id)
    return job


def test_start_job_returns_immediately_and_completes():
    def slow():
        time.sleep(0.05)
        return {"ok": True}

    started = time.time()
    job_id = jobs.start_job(slow)
    elapsed = time.time() - started
    assert elapsed < 0.05
    assert jobs.get_job(job_id)["status"] in ("pending", "done")

    job = _wait_done(job_id)
    assert job == {"status": "done", "result": {"ok": True}}


def test_job_captures_value_error():
    job_id = jobs.start_job(lambda: (_ for _ in ()).throw(ValueError("bad input")))
    job = _wait_done(job_id)
    assert job == {"status": "error", "code": 422, "detail": "bad input"}


def test_job_captures_runtime_error():
    job_id = jobs.start_job(lambda: (_ for _ in ()).throw(RuntimeError("missing config")))
    job = _wait_done(job_id)
    assert job == {"status": "error", "code": 500, "detail": "missing config"}


def test_job_flattens_unexpected_exception_to_generic_500():
    job_id = jobs.start_job(lambda: (_ for _ in ()).throw(KeyError("oops")))
    job = _wait_done(job_id)
    assert job["status"] == "error"
    assert job["code"] == 500
    assert job["detail"] == "Erreur interne inattendue."


def test_get_job_unknown_returns_none():
    assert jobs.get_job("does-not-exist") is None


def test_progress_field_present_while_pending_then_absent_once_done():
    import threading
    release = threading.Event()

    def fn(progress_cb=None):
        progress_cb({"stage": "working", "chunk": 1, "total_chunks": 5})
        release.wait(timeout=2.0)
        return "ok"

    job_id = jobs.start_job(fn)
    deadline = time.time() + 2.0
    job = jobs.get_job(job_id)
    while time.time() < deadline and job.get("progress") is None:
        time.sleep(0.01)
        job = jobs.get_job(job_id)
    assert job["status"] == "pending"
    assert job["progress"] == {"stage": "working", "chunk": 1, "total_chunks": 5}

    release.set()
    done = _wait_done(job_id)
    assert done == {"status": "done", "result": "ok"}
    assert "progress" not in done   # le dict "done" ne garde plus la trace d'avancement


def test_progress_not_injected_when_function_does_not_accept_it():
    # Ne doit pas planter : start_job détecte l'absence de `progress_cb` dans
    # la signature et ne le passe simplement pas.
    job_id = jobs.start_job(lambda: "no progress param here")
    job = _wait_done(job_id)
    assert job == {"status": "done", "result": "no progress param here"}


def test_fifo_eviction_caps_job_count():
    # L'éviction se décide à l'INSERTION (taille du dict), indépendamment de
    # l'état "pending"/"done" — pas besoin d'attendre les threads de fond ici.
    ids = [jobs.start_job(lambda: None) for _ in range(jobs._MAX_JOBS + 10)]
    with jobs._lock:
        remaining = len(jobs._jobs)
    assert remaining <= jobs._MAX_JOBS
    # Les tout premiers jobs doivent avoir été évincés, les plus récents gardés.
    assert jobs.get_job(ids[0]) is None
    assert _wait_done(ids[-1])["status"] == "done"
