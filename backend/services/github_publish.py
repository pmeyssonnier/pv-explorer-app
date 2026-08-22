"""Publie un fichier mis à jour sur GitHub via l'API Git Data (blobs/trees/
commits), pas l'API Contents (PUT .../contents/{path}) : cette dernière a une
limite pratique autour de 1 Mo pour le contenu manipulé côté API, alors que
pv_conseil_schaerbeek.json pèse plusieurs Mo — l'API Git Data est celle
documentée par GitHub pour ce cas.

C'est cette étape — pas une écriture locale — qui PERSISTE réellement un
nouveau PV : le disque du backend Render est éphémère (perdu à chaque
redéploiement/redémarrage), seul git fait foi. Le commit déclenche aussi le
redéploiement automatique (buildFilter backend/** dans render.yaml).
"""
import base64
import os

import httpx

GITHUB_API = "https://api.github.com"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)   # le blob peut peser plusieurs Mo


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _config() -> tuple[str, str, str]:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN manquante côté serveur")
    repo = os.environ.get("GITHUB_REPO", "pmeyssonnier/pv-explorer-app")
    branch = os.environ.get("GITHUB_BRANCH", "main")
    return token, repo, branch


def commit_file(path: str, content: str, message: str) -> str:
    """Remplace le contenu de `path` (chemin dans le dépôt, ex.
    "backend/pv_conseil_schaerbeek.json") par `content` via un nouveau commit
    sur la branche configurée. Retourne le sha du nouveau commit."""
    token, repo, branch = _config()
    with httpx.Client(base_url=GITHUB_API, headers=_headers(token), timeout=_TIMEOUT) as client:
        ref = client.get(f"/repos/{repo}/git/refs/heads/{branch}")
        ref.raise_for_status()
        base_commit_sha = ref.json()["object"]["sha"]

        base_commit = client.get(f"/repos/{repo}/git/commits/{base_commit_sha}")
        base_commit.raise_for_status()
        base_tree_sha = base_commit.json()["tree"]["sha"]

        blob = client.post(f"/repos/{repo}/git/blobs", json={
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "encoding": "base64",
        })
        blob.raise_for_status()
        blob_sha = blob.json()["sha"]

        tree = client.post(f"/repos/{repo}/git/trees", json={
            "base_tree": base_tree_sha,
            "tree": [{"path": path, "mode": "100644", "type": "blob", "sha": blob_sha}],
        })
        tree.raise_for_status()
        new_tree_sha = tree.json()["sha"]

        commit = client.post(f"/repos/{repo}/git/commits", json={
            "message": message,
            "tree": new_tree_sha,
            "parents": [base_commit_sha],
        })
        commit.raise_for_status()
        new_commit_sha = commit.json()["sha"]

        update_ref = client.patch(f"/repos/{repo}/git/refs/heads/{branch}", json={
            "sha": new_commit_sha,
        })
        update_ref.raise_for_status()

        return new_commit_sha
