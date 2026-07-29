"""
[ARCHITECTURE] Orchestrateur Batch - Dossier Magique (Rapport_Apave_Corim)

Rôle global :
Ce script remplace l'interface Streamlit par un traitement automatisé en arrière-plan.
Il surveille un répertoire d'entrée ("A traiter"), traite les rapports Apave PDF qui s'y trouvent,
génère les fichiers Excel d'import Corim dans le dossier de sortie ("A importer dans Corim"),
et archive le PDF source de manière structurée ("Traité, archive").

Stratégie métier :
L'objectif est le "Zero Click". L'utilisateur glisse simplement les PDF dans le dossier d'entrée,
et le script s'occupe de tout, incluant l'authentification sécurisée (Azure Key Vault).
"""

import os
import shutil
import time
import logging
from datetime import datetime
from pathlib import Path
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

from src.utils.pdf_extractor import extract_text_from_pdf
from src.core.apave_parser import parse_apave_report, RapportFormatInconnu
from src.core.ai_processor import parse_apave_text_to_corim_json
from src.core.excel_generator import generate_corim_excel
from src.core.corim_mapping import load_corim_export, enrich_interventions_with_corim_numbers

# Chargement DWH optionnel : nécessite le schéma apave_corim (voir deploy/sql/) et
# le VPN Stormshield actif. Import protégé pour ne pas bloquer le pipeline Excel
# si sqlalchemy/psycopg2 ne sont pas encore installés (cf. requirements.txt du 22/07).
try:
    from src.core.dwh_loader import get_engine, enregistrer_rapport
    _DWH_DISPONIBLE = True
except ImportError:
    _DWH_DISPONIBLE = False

# Configuration du Logging façon Lead Data
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# Configuration des dossiers magiques
# Junior Tip : dérivé de __file__ (racine projet = 3 niveaux au-dessus de
# src/scripts/), pas de os.getcwd(). Depuis le passage à l'arborescence
# canonique (src/core, src/scripts...) le script peut être lancé depuis
# n'importe quel répertoire de travail sans casser la localisation du
# dossier métier "IA Apave Corim".
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_DIR = os.path.join(PROJECT_ROOT, "IA Apave Corim")
DIR_INPUT = os.path.join(BASE_DIR, "A traiter")
DIR_OUTPUT = os.path.join(BASE_DIR, "A importer dans Corim")
DIR_ARCHIVE = os.path.join(BASE_DIR, "Traité, archive")

# Export Corim fourni par Maxence (mail du 13/07, "Export ITV Corim fait").
# Sans ce fichier, les colonnes NUMERO/INTERVENTION_MERE/INTERV_ORIG restent vides
# et l'import Corim ne pourra ni clôturer ni chaîner les interventions.
# Junior Tip : valeur en dur pour le POC. À déplacer en Config/Key Vault une fois validé.
CORIM_EXPORT_PATH = os.path.join(BASE_DIR, "Export ITV tests pour Anthony.xlsx")

# Credentials Azure/GCP chargés à la demande uniquement (voir _get_gemini_credentials) :
# depuis le passage à l'extraction déterministe (apave_parser.py), Gemini n'est plus
# sollicité que si le format du PDF n'est pas reconnu. Pas la peine de payer le coût
# d'un appel Key Vault sur un lot qui n'en a pas besoin.
_gemini_credentials_path = None

def setup_directories():
    """Vérifie et crée l'arborescence des dossiers magiques."""
    for directory in [DIR_INPUT, DIR_OUTPUT, DIR_ARCHIVE]:
        if not os.path.exists(directory):
            os.makedirs(directory)
            logging.info(f"[INFO] Dossier créé : {directory}")

def _get_gemini_credentials():
    """
    Récupère (une seule fois par run) les clés Key Vault nécessaires à Gemini.

    Junior Tip : appelée uniquement en repli, quand apave_parser.py lève
    RapportFormatInconnu (PDF qui ne colle plus au moteur de template Apave
    connu). Le cas nominal n'a plus besoin d'Azure Key Vault ni de credentials
    GCP du tout, ce qui supprime une dépendance et un point de défaillance pour
    la majorité des lots traités.
    """
    global _gemini_credentials_path
    if _gemini_credentials_path:
        return _gemini_credentials_path

    try:
        vault_url = "https://kv-tb-ia-agents-secrets.vault.azure.net/"
        credential = DefaultAzureCredential()
        secret_client = SecretClient(vault_url=vault_url, credential=credential)

        os.environ["GEMINI_PROJECT_ID"] = secret_client.get_secret("GEMINI-PROJECT-ID").value
        os.environ["GEMINI_LOCATION"] = secret_client.get_secret("GEMINI-LOCATION").value

        gcp_json_content = secret_client.get_secret("GCP-CREDENTIALS-JSON").value
        temp_gcp_path = os.path.join(os.getcwd(), "gcp_credentials_temp.json")
        with open(temp_gcp_path, "w", encoding="utf-8") as f:
            f.write(gcp_json_content)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_gcp_path
        logging.info("[SUCCÈS] Authentification Azure Key Vault OK (repli Gemini activé).")
        _gemini_credentials_path = temp_gcp_path
        return temp_gcp_path
    except Exception as e:
        logging.error(f"[ERREUR] Échec de récupération des secrets Azure (repli Gemini indisponible) : {e}")
        return None

def process_new_files():
    """Parcourt le dossier d'entrée et traite les PDF."""
    pdf_files = [f for f in os.listdir(DIR_INPUT) if f.lower().endswith(".pdf")]

    if not pdf_files:
        logging.info("[INFO] Aucun fichier à traiter.")
        return

    # Chargement unique de l'export Corim pour tout le lot : évite de relire le
    # fichier à chaque PDF, et permet de dégrader proprement si Maxence n'a pas
    # encore livré d'export à jour (mode POC, pas encore de fallback Config).
    corim_index = {}
    if os.path.exists(CORIM_EXPORT_PATH):
        corim_index = load_corim_export(CORIM_EXPORT_PATH)
    else:
        logging.warning(
            f"[ATTENTION] Export Corim introuvable ({CORIM_EXPORT_PATH}). "
            "Les colonnes NUMERO/INTERV_ORIG resteront vides pour ce lot."
        )

    for filename in pdf_files:
        input_pdf_path = os.path.join(DIR_INPUT, filename)
        logging.info(f"[PROCESS] Début du traitement pour : {filename}")

        try:
            # 1. Extraction du texte
            text = extract_text_from_pdf(input_pdf_path)

            # 2. Structuration : extraction déterministe en priorité (rapide, gratuite,
            # testable), Gemini uniquement en repli si le format n'est pas reconnu
            # (voir apave_parser.py pour le détail de la décision du 22/07).
            try:
                structured_data = parse_apave_report(text)
                methode_extraction = "DETERMINISTE"
                logging.info("[INFO] Extraction déterministe utilisée (pas d'appel LLM).")
            except RapportFormatInconnu as format_error:
                logging.warning(
                    f"[ATTENTION] {format_error} Repli sur l'extraction Gemini pour ce fichier."
                )
                if _get_gemini_credentials():
                    structured_data = parse_apave_text_to_corim_json(text)
                    methode_extraction = "LLM_GEMINI"
                else:
                    logging.error(f"[ECHEC] Ni parseur déterministe ni Gemini disponibles pour {filename}.")
                    continue

            # 2 bis. Alignement des numéros Corim (NUMERO / INTERV_ORIG), à partir
            # de l'export réel : le LLM ne les fournit jamais (voir ai_processor.py).
            if corim_index and structured_data.get("interventions"):
                structured_data["interventions"] = enrich_interventions_with_corim_numbers(
                    structured_data["interventions"], corim_index
                )

            # 3. Génération de l'Excel
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            excel_filename = f"Import_Corim_{timestamp}_{filename.replace('.pdf', '')}.xlsx"
            excel_path = os.path.join(DIR_OUTPUT, excel_filename)
            
            generate_corim_excel(structured_data, excel_path)
            logging.info(f"[SUCCÈS] Fichier d'import généré : {excel_path}")

            # 3 bis. Traçabilité DWH (best-effort) : n'interrompt jamais le pipeline
            # Excel si le schéma apave_corim n'existe pas encore ou si le VPN est
            # coupé. Voir deploy/sql/001_create_schema_apave_corim.sql pour créer
            # le schéma avant la première utilisation.
            if _DWH_DISPONIBLE:
                try:
                    # Bug corrigé le 22/07 : lire COMMENTAIRE_INTERNE de la première
                    # intervention cassait la clé d'upsert dès qu'un flag y était ajouté
                    # (ex: "[STATUT ITV MERE A CONFIRMER AVEC RICHARD]" sur les cas
                    # DEFAUT), créant un doublon en base à chaque run au lieu de
                    # rafraîchir le rapport existant. numero_rapport est maintenant
                    # exposé proprement par parse_apave_report/parse_apave_text_to_corim_json.
                    numero_rapport = structured_data.get("numero_rapport") or filename
                    engine = get_engine()
                    enregistrer_rapport(engine, numero_rapport, filename, methode_extraction, structured_data.get("interventions", []))
                except Exception as dwh_error:
                    logging.warning(f"[ATTENTION] Écriture DWH ignorée pour {filename} (non bloquant) : {dwh_error}")

            # 4. Archivage du PDF
            current_year = str(datetime.now().year)
            archive_year_dir = os.path.join(DIR_ARCHIVE, current_year)
            if not os.path.exists(archive_year_dir):
                os.makedirs(archive_year_dir)
                
            archive_pdf_path = os.path.join(archive_year_dir, filename)
            shutil.move(input_pdf_path, archive_pdf_path)
            logging.info(f"[SUCCÈS] Fichier PDF archivé dans : {archive_year_dir}")
            
        except Exception as e:
            logging.error(f"[ERREUR] Échec du traitement pour {filename} : {e}", exc_info=True)

def main():
    setup_directories()

    try:
        logging.info("[START] Démarrage du traitement par lot (Dossier Magique)...")
        process_new_files()
    finally:
        # Nettoyage sécurisé du token GCP éphémère, seulement s'il a été créé
        # (repli Gemini effectivement déclenché sur ce run).
        if _gemini_credentials_path and os.path.exists(_gemini_credentials_path):
            os.remove(_gemini_credentials_path)
            logging.info("[SEC] Fichier GCP éphémère nettoyé.")

if __name__ == "__main__":
    main()
