"""Réindexation ciblée : n'envoyer à Pinecone que ce qui a réellement changé.

Une correction de données ne touche parfois que dix points sur dix mille. Le
quota d'embedding étant limité, la question « lesquels ré-embedder ? » doit
avoir une réponse CALCULÉE — pas une liste recopiée à la main, qui vieillit mal
et ne dit jamais si elle est complète.

Les fonctions testées ici sont pures : elles ne touchent pas à Pinecone. Ce qui
en dépend (l'upsert, la relecture de l'index) n'est pas couvert par la suite —
c'est l'API distante, elle demande une clé.
"""
import json
import subprocess

from index_pv import load_chunks
from reindex_points import _FETCH_BATCH, _lire_index, chunks_depuis_git, ids_modifies


def _chunk(id_, texte):
    return {"id": id_, "metadata": {"chunk_text": texte}}


def test_seuls_les_textes_qui_changent_sont_rendus():
    anciens = [_chunk("A", "Décision : DÉBAT"), _chunk("B", "inchangé")]
    actuels = [_chunk("A", "Décision : REJETÉ"), _chunk("B", "inchangé")]
    assert ids_modifies(actuels, anciens) == ["A"]


def test_un_point_ajoute_compte_comme_a_indexer():
    # Il n'existe pas encore dans l'index : ne pas l'envoyer le laisserait
    # introuvable à la recherche.
    assert ids_modifies([_chunk("A", "x"), _chunk("C", "neuf")], [_chunk("A", "x")]) == ["C"]


def test_un_point_supprime_n_est_pas_rendu():
    """Ce script n'efface rien : rendre un ID disparu ferait planter l'envoi."""
    assert ids_modifies([_chunk("A", "x")], [_chunk("A", "x"), _chunk("B", "parti")]) == []


def test_rien_a_faire_quand_la_base_n_a_pas_bouge():
    memes = [_chunk("A", "x"), _chunk("B", "y")]
    assert ids_modifies(memes, list(memes)) == []


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_chunks_depuis_git_relit_l_etat_precedent_de_la_base(tmp_path):
    """La révision nomme l'état d'AVANT sans sauvegarde à ranger ensuite — et le
    code appliqué est celui d'aujourd'hui : on compare deux états des données à
    travers la même moulinette."""
    repo = tmp_path / "depot"
    (repo / "backend").mkdir(parents=True)
    base = repo / "backend" / "pv.json"

    def ecrire(decision):
        base.write_text(json.dumps({"seances": [{
            "seance": {"date": "1999-01-07", "id": "PV1999"},
            "points": [{"sp": 1, "type": "motion", "titre": "Motion",
                        "decision": decision, "vote": None}],
        }]}), encoding="utf-8")

    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    ecrire("DÉBAT")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "avant")
    ecrire("REJETÉ")

    avant = chunks_depuis_git("HEAD", base, "schaerbeek")
    assert "Décision : DÉBAT" in avant[0]["metadata"]["chunk_text"]
    # Et la comparaison désigne bien le seul point touché.
    assert ids_modifies(load_chunks(base, "schaerbeek"), avant) == ["PV1999_SP1"]


# ── _lire_index : découpage en lots ──────────────────────────────────────────
# `fetch()` passe les ID en query params d'un GET (voir pinecone.Index.fetch,
# pas de endpoint batch) — au-delà de _FETCH_BATCH, un seul appel dépasse la
# limite d'URL du serveur (414, observé en pratique avec ~2 000 ID). Ces deux
# tests, eux, restent purs (faux client, aucun réseau) : seul le découpage/
# fusion est vérifié, jamais l'appel Pinecone réel.
class _FauxIndex:
    def __init__(self, texte_par_id, echoue_sur=()):
        self.texte_par_id = texte_par_id
        self.echoue_sur = set(echoue_sur)
        self.lots = []

    def fetch(self, *, ids, namespace):
        self.lots.append(list(ids))
        if self.echoue_sur.intersection(ids):
            raise RuntimeError("boom")
        return {"vectors": {i: {"metadata": {"chunk_text": self.texte_par_id[i]}}
                             for i in ids if i in self.texte_par_id}}


class _FauxPinecone:
    def __init__(self, index):
        self._index = index

    def Index(self, name):
        return self._index


def test_lire_index_decoupe_en_lots_au_dela_de_fetch_batch():
    ids = [f"ID{i}" for i in range(_FETCH_BATCH * 2 + 5)]
    texte = {i: f"texte-{i}" for i in ids}
    index = _FauxIndex(texte)

    out = _lire_index(_FauxPinecone(index), ids)

    assert out == texte
    assert len(index.lots) == 3
    assert all(len(lot) <= _FETCH_BATCH for lot in index.lots)


def test_lire_index_un_lot_en_echec_ne_perd_pas_les_autres():
    ids = [f"ID{i}" for i in range(_FETCH_BATCH + 3)]
    texte = {i: f"texte-{i}" for i in ids}
    # Le 1er lot (qui contient ids[0]) échoue entièrement ; les lots suivants
    # doivent quand même être lus — pas de perte en cascade.
    index = _FauxIndex(texte, echoue_sur={ids[0]})

    out = _lire_index(_FauxPinecone(index), ids)

    assert set(out) == set(ids[_FETCH_BATCH:])
