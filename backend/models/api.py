"""Schémas Pydantic de l'API (requêtes/réponses). Contrat HTTP du backend."""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from config import MAX_QUESTION_LEN


class QuestionRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "question": "Quelles décisions sur le parc Josaphat en 2023 ?",
            "commune": "schaerbeek",
            "top_k": 30,
            "max_sources": 15,
        }]
    })

    # Longueur bornée : protège le budget API (une question démesurée serait
    # envoyée telle quelle à Claude). Pydantic renvoie 422 hors bornes.
    question: str = Field(
        min_length=3, max_length=MAX_QUESTION_LEN,
        description="Question en langage naturel sur les délibérations du Conseil communal.",
    )
    # Commune optionnelle. Absente ou "toutes" → recherche croisée (aucun
    # filtre, comportement historique). Sinon → filtre sur cette commune.
    commune: Optional[str] = Field(
        default=None, max_length=50,
        description='Commune à filtrer (ex. "schaerbeek"). Absente ou "toutes" : '
                     "recherche sur toutes les communes indexées.",
    )
    # Overrides optionnels du menu « Options » (front). Re-bornés côté serveur
    # dans rag.answer() ; ces bornes larges ne sont qu'une 1re barrière (422).
    top_k: Optional[int] = Field(
        default=None, ge=1, le=100,
        description="Nombre de passages recherchés dans l'index (recadré côté serveur).",
    )
    max_sources: Optional[int] = Field(
        default=None, ge=1, le=50,
        description="Nombre maximum de sources renvoyées dans la réponse.",
    )
    score_min: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Score de pertinence minimal pour qu'une source soit incluse (0 = désactivé).",
    )
    model: Optional[str] = Field(
        default=None, max_length=60,
        description="Modèle Claude à utiliser. Retombe sur le modèle par défaut si non reconnu.",
    )


class Source(BaseModel):
    date: str
    sp: int
    titre: str
    decision: str
    score: float
    # Étiquettes de thématique (ex. « Mobilité », « Finances ») — même liste,
    # même normalisation d'affichage que les onglets Statistiques/Séances/Par
    # élu·e (voir utils.text._thematique_label). Vide pour un débat filmé ou
    # une question écrite (aucune thématique indexée pour ces types de source).
    thematiques: list[str] = Field(default_factory=list, description="Étiquettes de thématique du point.")
    # Lien : PDF officiel du PV (résolu par date) OU deep-link vidéo pour un débat
    # filmé (métadonnée) OU PDF de la question écrite sur 1030.be (métadonnée,
    # souvent absent — voir services/questions_ecrites*.py) — None si absent.
    url: Optional[str] = Field(default=None, description="Lien vers le PV (PDF), la vidéo du débat, ou la question écrite (PDF).")
    # Type de source : "pv" (délibération, défaut), "video_conseil" (débat
    # filmé) ou "question_ecrite" (question écrite hors séance).
    source_type: str = Field(
        default="pv",
        description='"pv" (délibération), "video_conseil" (débat filmé) ou "question_ecrite" (question écrite).',
    )
    # Lien vers la vidéo de la séance (début) quand elle a été filmée — pour un
    # « ▶ voir la séance » sur une délibération, même sans chapitrage. None pour
    # un débat vidéo (déjà un deep-link précis) ou une question écrite (jamais
    # liée à une séance).
    video_url: Optional[str] = Field(
        default=None,
        description="Lien vers le début de la vidéo de la séance, si elle a été filmée.",
    )
    # Nombre d'extraits de transcript disponibles pour ce point (débat vidéo
    # uniquement, None sinon) — donne une idée de la longueur du débat. PAS un
    # nombre d'intervenant·e·s : aucune diarisation, un extrait est un simple
    # découpage par tranche de texte, indépendant des changements de personne.
    n_extraits: Optional[int] = Field(
        default=None,
        description="Nombre d'extraits de transcript pour ce point (débat vidéo uniquement).",
    )
    # Texte intégral de la réponse du Collège (question écrite uniquement,
    # None sinon — y compris une question écrite sans réponse publiée) :
    # affiché dans un accordéon repliable côté frontend, comme dans l'onglet
    # Par élu·e (voir services/questions_ecrites*.py).
    reponse: Optional[str] = Field(
        default=None,
        description="Texte intégral de la réponse du Collège (question écrite uniquement).",
    )
    # Trois rôles DISTINCTS autour d'un point, tels que le procès-verbal les
    # nomme : qui l'a déposé, qui a pris la parole, qui a répondu au nom du
    # Collège. Jamais déduits l'un de l'autre (voir index_pv.pv_point_to_chunk
    # et pipeline/pv_extraction_pipeline._normalize_point). Un même nom peut
    # figurer dans plusieurs listes — intervenir dans un débat auquel on répond
    # est courant. Vides pour un point délibératif que personne n'a déposé, et
    # pour les sources vidéo/question écrite, qui portent leur propre contexte
    # dans `decision`.
    auteurs: list[str] = Field(default_factory=list, description="Qui a déposé le point (motion, demande, question orale).")
    intervenants: list[str] = Field(default_factory=list, description="Qui a pris la parole dans le débat.")
    repondants: list[str] = Field(default_factory=list, description="Qui a répondu au nom du Collège.")


class AnswerResponse(BaseModel):
    answer: str = Field(description="Réponse générée par Claude, citée à partir des sources.")
    sources: list[Source] = Field(description="Sources ayant servi à générer la réponse.")


class AdminLoginRequest(BaseModel):
    # Bornes larges (pas de contrainte métier sur la forme des identifiants) —
    # juste un garde-fou anti-abus avant même d'atteindre verify_admin_credentials.
    username: str = Field(min_length=1, max_length=100, description="Identifiant administrateur.")
    password: str = Field(min_length=1, max_length=200, description="Mot de passe administrateur.")


class SeancePublishRequest(BaseModel):
    # `seance` reste un dict libre (pas un sous-modèle Pydantic dupliquant le
    # schéma du pipeline d'extraction) : c'est EXACTEMENT ce que /admin/seances/
    # extract a renvoyé, l'admin n'en modifie que le contenu, pas la forme.
    # La validation métier (date/points présents) vit dans services/pv_integration.py.
    seance: dict = Field(
        description="Structure de séance extraite (retournée par /admin/seances/extract).",
    )
    source_url: Optional[str] = Field(
        default=None, max_length=500,
        description="URL du PV (PDF) sur 1030.be, si connue.",
    )


class QuestionEcritePublishRequest(BaseModel):
    # Même raisonnement que SeancePublishRequest ci-dessus : dict libre,
    # exactement ce que /admin/questions-ecrites/extract a renvoyé.
    question: dict = Field(
        description="Question écrite extraite (retournée par /admin/questions-ecrites/extract).",
    )
    source_url: Optional[str] = Field(
        default=None, max_length=500,
        description="URL de la question écrite sur 1030.be, si connue.",
    )


class MandatSaveRequest(BaseModel):
    # Plages "AAAA-AAAA" / "AAAA-présent" (validées côté serveur, voir
    # services.people.mandats.save_mandat) — jamais de jour/mois, le fichier
    # source n'a que la granularité année.
    nom: str = Field(min_length=1, max_length=100, description="Nom complet affiché.")
    conseiller_communal: Optional[str] = Field(default=None, max_length=200)
    echevin: Optional[str] = Field(default=None, max_length=200)
    bourgmestre: Optional[str] = Field(default=None, max_length=200)
    statut: Optional[str] = Field(default=None, max_length=200)
    nom_original: Optional[str] = Field(
        default=None, max_length=100,
        description="Nom AVANT modification (retrouve l'entrée même si le nom lui-même est corrigé).",
    )
