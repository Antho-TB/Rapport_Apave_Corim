"""
[ETL] Chargement des interventions vers le DWH Sylob (Rapport_Apave_Corim)

Rôle global :
Écrit dans `apave_corim.rapports_traites` et `apave_corim.interventions_extraites`
(dtpf_sylob_prod) ce que produisent apave_parser.py + corim_mapping.py. Remplace
le fichier Excel comme source de vérité pour l'historique : on peut désormais
retrouver quel PDF a généré quelle ligne, et lister les cas particuliers
toujours en attente de traitement avec Richard.

Stratégie métier (pattern d'écriture) :
- `rapports_traites` : un UPSERT par PDF (clé naturelle numero_rapport +
  nom_fichier_pdf), pour ne pas dupliquer si le même PDF est redéposé.
- `interventions_extraites` : un FULL-REFRESH par rapport (on supprime les
  lignes existantes pour ce rapport_id avant de réinsérer), car la granularité
  est "l'état courant des interventions détectées à ce run", pas un historique
  de versions ligne à ligne (cf. gotchas etl-tb : FULL-REFRESH pour les tables
  de faits à granularité ligne).

Ce module ne génère toujours PAS de fichier Excel : il vient EN PLUS, pas à la
place. L'Excel reste nécessaire tant que Corim n'a pas d'API/connexion SQL
directe (confirmé par Antho le 22/07, sujet en discussion côté direction).
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

logger = logging.getLogger(__name__)

# Bruit Azure SDK : on le baisse pour ne pas noyer les logs [SUCCES]/[ERREUR] du pipeline.
for _noisy in ("azure.core.pipeline", "azure.identity", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class Config:
    """
    Configuration centralisée de connexion au DWH Sylob.

    Junior Tip : ATTENTION, ce projet a DEUX Key Vault différents. Celui du
    pipeline IA (`kv-tb-ia-agents-secrets`, GEMINI_*) sert à Vertex AI. Celui-ci
    (`kv-dtpf-prod`) sert au DWH PostgreSQL.

    Compte utilisé : `dtpf_sylob_anthony_bezille_prod` (secrets
    `psql-prod-sylob-anthony-bezille-login/-password`), PAS le compte partagé
    `myreport`. C'est le même principe d'isolation que sur Data-Achat/FUSEAU,
    où réutiliser le compte MyReport pour un pipeline tiers avait été identifié
    comme une violation à corriger (ADR ERP Achat du 10/06). Le schéma
    `apave_corim` est créé à la main par ce compte (comme `achat` et
    `appro_raw`), hors Terraform : c'est le pattern TB Groupe pour les schémas
    métier bootstrap (voir ADR Colibri du 24/06).
    """

    KEY_VAULT_NAME: str = os.getenv("DWH_KEY_VAULT_NAME", "kv-dtpf-prod")
    PG_HOST: str = os.getenv("PG_HOST", "psql-dtpf-psql-prod.postgres.database.azure.com")
    PG_PORT: int = int(os.getenv("PG_PORT", "5432"))
    PG_DB: str = os.getenv("PG_DB", "dtpf_sylob_prod")
    PG_SCHEMA: str = "apave_corim"
    # Fallback local uniquement (dev hors VPN/Key Vault) : jamais renseigné en prod.
    PG_USER: str = os.getenv("PG_USER", "")
    PG_PASSWORD: str = os.getenv("PG_PASSWORD", "")

    @classmethod
    def get_pg_url(cls) -> URL:
        """Construit l'URL de connexion, Key Vault en priorité, .env en repli."""
        try:
            return cls._from_keyvault()
        except Exception as exc:
            logger.warning(f"[ATTENTION] Key Vault inaccessible ({exc}), repli sur .env local.")
            if not cls.PG_USER or not cls.PG_PASSWORD:
                raise RuntimeError(
                    "Ni Key Vault ni .env local ne fournissent de credentials PostgreSQL. "
                    "Vérifier le VPN Stormshield et/ou PG_USER/PG_PASSWORD dans .env."
                ) from exc
            return URL.create(
                drivername="postgresql+psycopg2",
                username=cls.PG_USER,
                password=cls.PG_PASSWORD,
                host=cls.PG_HOST,
                port=cls.PG_PORT,
                database=cls.PG_DB,
                query={"sslmode": "require"},
            )

    @classmethod
    def _from_keyvault(cls) -> URL:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        client = SecretClient(
            vault_url=f"https://{cls.KEY_VAULT_NAME}.vault.azure.net/",
            credential=DefaultAzureCredential(),
        )
        return URL.create(
            drivername="postgresql+psycopg2",
            username=client.get_secret("psql-prod-sylob-anthony-bezille-login").value,
            password=client.get_secret("psql-prod-sylob-anthony-bezille-password").value,
            host=cls.PG_HOST,
            port=cls.PG_PORT,
            database=cls.PG_DB,
            query={"sslmode": "require"},
        )


def get_engine():
    """Crée le moteur SQLAlchemy vers dtpf_sylob_prod (VPN Stormshield requis)."""
    return create_engine(Config.get_pg_url())


def enregistrer_rapport(
    engine,
    numero_rapport: str,
    nom_fichier_pdf: str,
    methode_extraction: str,
    interventions: list[dict],
    statut: str = "SUCCES",
    message_erreur: str | None = None,
) -> int:
    """
    Upsert la ligne de suivi du rapport, puis full-refresh de ses interventions.

    Args:
        engine: moteur SQLAlchemy (voir get_engine()).
        numero_rapport: numéro Apave (ex: A55432737-017-1).
        nom_fichier_pdf: nom du PDF source, pour traçabilité.
        methode_extraction: 'DETERMINISTE' ou 'LLM_GEMINI'.
        interventions: liste de dicts au format ai_processor/apave_parser.
        statut: 'SUCCES' ou 'ECHEC'.
        message_erreur: détail si statut == 'ECHEC'.

    Returns:
        L'identifiant (rapport_id) de la ligne rapports_traites.
    """
    with engine.begin() as conn:
        rapport_id = conn.execute(
            text("""
                INSERT INTO apave_corim.rapports_traites
                    (numero_rapport, nom_fichier_pdf, methode_extraction, nb_interventions, statut, message_erreur)
                VALUES (:numero_rapport, :nom_fichier_pdf, :methode, :nb, :statut, :message)
                ON CONFLICT (numero_rapport, nom_fichier_pdf) DO UPDATE SET
                    methode_extraction = EXCLUDED.methode_extraction,
                    nb_interventions = EXCLUDED.nb_interventions,
                    statut = EXCLUDED.statut,
                    message_erreur = EXCLUDED.message_erreur,
                    traite_le = now()
                RETURNING id
            """),
            {
                "numero_rapport": numero_rapport,
                "nom_fichier_pdf": nom_fichier_pdf,
                "methode": methode_extraction,
                "nb": len(interventions),
                "statut": statut,
                "message": message_erreur,
            },
        ).scalar_one()

        # Full-refresh : on repart propre pour ce rapport avant de réinsérer.
        conn.execute(
            text("DELETE FROM apave_corim.interventions_extraites WHERE rapport_id = :rid"),
            {"rid": rapport_id},
        )

        for itv in interventions:
            conn.execute(
                text("""
                    INSERT INTO apave_corim.interventions_extraites
                        (rapport_id, appe_habit, cas_pdf, libe_inter, demande, compte_rendu, statut, type_maint,
                         intervention_mere, numero, interv_orig, code_natt, codest_maint,
                         a_traiter_manuellement)
                    VALUES
                        (:rapport_id, :appe_habit, :cas_pdf, :libe_inter, :demande, :compte_rendu, :statut, :type_maint,
                         :intervention_mere, :numero, :interv_orig, :code_natt, :codest_maint,
                         :a_traiter_manuellement)
                """),
                {
                    "rapport_id": rapport_id,
                    "appe_habit": itv.get("APPE_HABIT", ""),
                    "cas_pdf": itv.get("CAS_PDF", "DEFAUT"),
                    "libe_inter": itv.get("LIBE_INTER", ""),
                    "demande": itv.get("DEMANDE", ""),
                    "compte_rendu": itv.get("COMPTE_RENDU", ""),
                    "statut": itv.get("STATUT", ""),
                    "type_maint": itv.get("TYPE_MAINT", ""),
                    "intervention_mere": itv.get("INTERVENTION_MERE") or None,
                    "numero": itv.get("NUMERO") or None,
                    "interv_orig": itv.get("INTERV_ORIG") or None,
                    "code_natt": itv.get("CODE_NATT") or None,
                    "codest_maint": itv.get("CODEST_MAINT") or None,
                    "a_traiter_manuellement": itv.get("CAS_PDF") == "PARTIEL",
                },
            )

    logger.info(f"[SUCCES] Rapport {numero_rapport} enregistré dans apave_corim (id={rapport_id}, {len(interventions)} intervention(s)).")
    return rapport_id
