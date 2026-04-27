"""
[ARCHITECTURE] Extraction Native I/O (Rapport_Apave_Corim)

Rôle global :
Ce module est dédié à la lecture des fichiers PDF et à l'extraction de leur contenu brut.
C'est le premier maillon de la chaîne d'ingestion des données.

Stratégie métier (Natifs vs Scans) :
Les rapports Apave récents sont "natifs" (le texte est sélectionnable), ce qui nous dispense
de recourir à des systèmes lourds de Computer Vision (OCR type Tesseract).
On utilise `pdfplumber` (ou `PyMuPDF`) qui est optimisé pour récupérer le texte et la géométrie,
ce qui maintient le pipeline d'ingestion rapide et peu coûteux en calculs.
"""

import pdfplumber
import os
import logging

def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extrait l'intégralité du texte d'un rapport PDF Apave de manière robuste.
    
    Stratégie :
    Une vérification croisée de l'existence du fichier est effectuée en premier lieu 
    (Fail-fast) pour éviter d'engager le moteur PDF sur un chemin corrompu.
    Ensuite, on itère sur les pages et on aggrège le texte. Si une page est vide (ou une image),
    elle est silencieusement ignorée pour ne pas casser le flux.
    
    Args:
        pdf_path (str): Le chemin absolu du fichier PDF à parser.
        
    Returns:
        str: Le texte complet concaténé, prêt à être envoyé au LLM.
        
    Raises:
        FileNotFoundError: Si le fichier physique est introuvable.
    """
    logging.info(f"[INFO] Démarrage de l'extraction PDF pour le fichier : {pdf_path}")
    
    # Vérification croisée (OS) pour la résilience
    if not os.path.exists(pdf_path):
        logging.error(f"[ERREUR] Le fichier PDF n'a pas été trouvé à l'emplacement : {pdf_path}")
        raise FileNotFoundError(f"Le fichier PDF n'a pas été trouvé : {pdf_path}")
        
    extracted_text = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    extracted_text.append(text)
                else:
                    logging.debug(f"La page {page_idx} n'a retourné aucun texte (potentiellement une image/plan).")
                    
        logging.info("[SUCCÈS] L'extraction du texte PDF s'est terminée correctement.")
        return "\n".join(extracted_text)
        
    except Exception as e:
        logging.error(f"[ERREUR] Une erreur est survenue lors de l'analyse du PDF avec pdfplumber : {e}", exc_info=True)
        raise
