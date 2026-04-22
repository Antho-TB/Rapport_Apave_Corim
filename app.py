import streamlit as st
import os
import shutil
from datetime import datetime
from dotenv import load_dotenv

from src.pdf_extractor import extract_text_from_pdf
from src.ai_processor import parse_apave_text_to_corim_json
from src.excel_generator import generate_corim_excel

# Apprentissage : On charge les variables d'environnement (comme la clé API) depuis le fichier .env
load_dotenv()

# Configuration de la page Streamlit. J'ai enlevé les emojis pour respecter la sobriété demandée.
st.set_page_config(page_title="Import Apave vers Corim", layout="centered")

st.title("Outil d'Import Apave vers Corim via IA")

st.write("""
Cet outil simplifie l'intégration des rapports Apave.
Glissez-déposez votre rapport PDF ci-dessous pour générer automatiquement le fichier Excel compatible avec Corim.
""")

# Création du dossier archives si inexistant
ARCHIVE_DIR = os.path.join(os.getcwd(), "archives")
if not os.path.exists(ARCHIVE_DIR):
    os.makedirs(ARCHIVE_DIR)

# Composant Streamlit pour télécharger un fichier, on restreint aux PDF
uploaded_file = st.file_uploader("Importer le rapport Apave (Format PDF)", type="pdf")

if uploaded_file is not None:
    st.info("Fichier reçu. Traitement en cours...")
    
    # 1. Sauvegarder le fichier PDF téléchargé temporairement
    temp_pdf_path = os.path.join(os.getcwd(), uploaded_file.name)
    with open(temp_pdf_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
        
    try:
        # 2. Extraire le texte
        with st.spinner("Extraction du texte PDF..."):
            text = extract_text_from_pdf(temp_pdf_path)
            
        # 3. Analyser par IA
        with st.spinner("Analyse par l'IA (Gemini)... Cela peut prendre une minute."):
            structured_data = parse_apave_text_to_corim_json(text)
            
        # 4. Générer l'Excel
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        excel_filename = f"Import_Corim_{timestamp}.xlsx"
        excel_path = os.path.join(os.getcwd(), excel_filename)
        
        with st.spinner("Génération du fichier Excel..."):
            generate_corim_excel(structured_data, excel_path)
            
        # 5. Archivage
        archive_pdf_path = os.path.join(ARCHIVE_DIR, f"{timestamp}_{uploaded_file.name}")
        shutil.move(temp_pdf_path, archive_pdf_path)
        
        # Succès
        st.success(f"Traitement terminé avec succès ! {len(structured_data.get('interventions', []))} intervention(s) extraite(s).")
        
        # Apprentissage : Afficher un aperçu visuel (tableau) directement dans l'interface Web 
        # pour permettre à l'utilisateur de vérifier rapidement l'extraction.
        import pandas as pd
        st.subheader("Aperçu des interventions détectées :")
        if structured_data.get("interventions"):
            st.dataframe(pd.DataFrame(structured_data["interventions"]))
        else:
            st.warning("Aucune intervention (défaut) n'a été détectée dans ce rapport.")
            
        # Proposer le téléchargement
        with open(excel_path, "rb") as f:
            st.download_button(
                label="Télécharger le fichier d'import Corim (.xlsx)",
                data=f,
                file_name=excel_filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
    except Exception as e:
        st.error(f"Une erreur est survenue lors du traitement : {str(e)}")
        # Nettoyage en cas d'erreur
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)
