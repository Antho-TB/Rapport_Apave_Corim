# Module d'extraction de texte PDF
# Apprentissage : Ce module est responsable de la lecture du fichier PDF Apave.
# Comme les PDF sont dits 'natifs' (pas des images scannées), on peut utiliser des bibliothèques
# comme pdfplumber ou PyMuPDF pour récupérer directement le texte et la structure des tableaux.
import pdfplumber
import os

def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extrait le texte d'un rapport PDF Apave natif de manière robuste.
    """
    # Vérification croisée (OS) pour la résilience
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Le fichier PDF n'a pas été trouvé : {pdf_path}")
        
    extracted_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                extracted_text.append(text)
                
    return "\\n".join(extracted_text)
