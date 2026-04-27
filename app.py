"""
[ARCHITECTURE] Interface Utilisateur & Orchestration (Rapport_Apave_Corim)

Rôle global :
Ce script est le point d'entrée de l'application Streamlit. Son rôle est d'orchestrer le pipeline
de transformation des données (Extraction PDF -> Analyse LLM -> Génération Excel).
Il gère l'interface avec l'utilisateur métier, l'orchestration des états de chargement (spinners),
et la politique de rétention (archivage des PDF originaux).

Stratégie métier :
L'objectif est d'absorber la complexité de l'extraction de l'information non structurée (les rapports
PDF Apave) pour exposer un flux de travail transparent à l'utilisateur final. L'Excel généré sert
ensuite de format pivot pour l'import final dans l'ERP (Corim).
"""

import streamlit as st
import os
import shutil
from datetime import datetime
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
import logging
import pandas as pd

from src.pdf_extractor import extract_text_from_pdf
from src.ai_processor import parse_apave_text_to_corim_json
from src.excel_generator import generate_corim_excel

# --- Configuration Log & Securité ---
# Stratégie : Centralisation des secrets via Azure Key Vault pour empêcher toute compromission 
# de compte de service GCP en local ou sur GitHub.
try:
    vault_url = "https://kv-tb-ia-agents-secrets.vault.azure.net/"
    credential = DefaultAzureCredential()
    secret_client = SecretClient(vault_url=vault_url, credential=credential)
    
    os.environ["GEMINI_PROJECT_ID"] = secret_client.get_secret("GEMINI-PROJECT-ID").value
    os.environ["GEMINI_LOCATION"] = secret_client.get_secret("GEMINI-LOCATION").value
    
    # GCP nécessite physiquement un fichier pour `GOOGLE_APPLICATION_CREDENTIALS` par défaut.
    # Nous le récupérons du Key Vault et le provisionnons de manière éphémère.
    gcp_json_content = secret_client.get_secret("GCP-CREDENTIALS-JSON").value
    temp_gcp_path = os.path.join(os.getcwd(), "gcp_credentials_temp.json")
    with open(temp_gcp_path, "w", encoding="utf-8") as f:
        f.write(gcp_json_content)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_gcp_path

except Exception as e:
    logging.warning(f"[NUBO SEC] Impossible de charger les secrets depuis le Key Vault : {e}")

st.set_page_config(page_title="Import Apave vers Corim", layout="centered")
st.title("Outil d'Import Apave vers Corim via IA")
st.write("""
Cet outil simplifie l'intégration des rapports Apave.
Glissez-déposez votre rapport PDF ci-dessous pour générer automatiquement le fichier Excel compatible avec Corim.
""")

ARCHIVE_DIR = os.path.join(os.getcwd(), "archives")
if not os.path.exists(ARCHIVE_DIR):
    os.makedirs(ARCHIVE_DIR)

uploaded_file = st.file_uploader("Importer le rapport Apave (Format PDF)", type="pdf")

if uploaded_file is not None:
    st.info("Fichier reçu. Traitement en cours...")
    
    # Stratégie I/O : Streamlit conserve le fichier en RAM via BytesIO.
    # On le matérialise sur le disque temporairement car PyPDF2/PDFPlumber ont besoin d'un path physique.
    temp_pdf_path = os.path.join(os.getcwd(), uploaded_file.name)
    with open(temp_pdf_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
        
    try:
        with st.spinner("Extraction du texte PDF..."):
            text = extract_text_from_pdf(temp_pdf_path)
            
        with st.spinner("Analyse par l'IA (Gemini)... Cela peut prendre une minute."):
            structured_data = parse_apave_text_to_corim_json(text)
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        excel_filename = f"Import_Corim_{timestamp}.xlsx"
        excel_path = os.path.join(os.getcwd(), excel_filename)
        
        with st.spinner("Génération du fichier Excel..."):
            generate_corim_excel(structured_data, excel_path)
            
        # Archivage de la source pour garder une trace auditable de ce qui a nourri l'ERP
        archive_pdf_path = os.path.join(ARCHIVE_DIR, f"{timestamp}_{uploaded_file.name}")
        shutil.move(temp_pdf_path, archive_pdf_path)
        
        st.success(f"Traitement terminé avec succès ! {len(structured_data.get('interventions', []))} intervention(s) extraite(s).")
        
        # Feedback visuel direct : On affiche la donnée parsée pour que l'utilisateur valide 
        # la cohérence métier avant l'import dans Corim.
        st.subheader("Aperçu des interventions détectées :")
        if structured_data.get("interventions"):
            st.dataframe(pd.DataFrame(structured_data["interventions"]))
        else:
            st.warning("Aucune intervention (défaut) n'a été détectée dans ce rapport.")
            
        with open(excel_path, "rb") as f:
            st.download_button(
                label="Télécharger le fichier d'import Corim (.xlsx)",
                data=f,
                file_name=excel_filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
    except Exception as e:
        # Fallback : en cas d'échec du traitement IA ou Excel, on nettoie le disque pour éviter d'accumuler des déchets.
        st.error(f"Une erreur est survenue lors du traitement : {str(e)}")
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)
