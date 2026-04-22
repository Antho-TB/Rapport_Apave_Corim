# Module d'interaction avec l'IA
# Apprentissage : Ce module sert à faire le pont entre notre application et l'API LLM (Gemini ou Claude).
# Il prend du texte brut en entrée et utilise un 'Prompt' (une instruction) pour forcer l'IA
# à renvoyer des données structurées (comme du JSON).

import os
import json
from google import genai
from pydantic import BaseModel, Field

class Intervention(BaseModel):
    LIBE_INTER: str = Field(description="Titre court de l'intervention, ex: 'Correction équipement X suite rapport Apave'")
    DEMANDE: str = Field(description="Description détaillée de la non-conformité relevée")
    APPE_HABIT: str = Field(description="Nom ou référence de l'équipement (ex: '348', 'Porte à commande semi-automatique')", default="")
    PARC: str = Field(description="Référence Parc si disponible, sinon vide", default="")
    STATUT: str = Field(description="Toujours 'CREEE'", default="CREEE")
    TYPE_MAINT: str = Field(description="Toujours 'CORRECTIVE'", default="CORRECTIVE")
    DEMANDEUR: str = Field(description="Toujours 'APAVE'", default="APAVE")
    COMMENTAIRE_INTERNE: str = Field(description="Numéro du rapport Apave", default="")

class CorimImport(BaseModel):
    interventions: list[Intervention] = Field(description="Liste des interventions à importer dans Corim")

def parse_apave_text_to_corim_json(text: str) -> dict:
    """
    Envoie le texte à l'API Gemini (via Vertex AI sur GCP) pour extraire et structurer 
    les données sous le format attendu par Corim.
    """
    project_id = os.getenv("GEMINI_PROJECT_ID", "tb-ai-platform")
    location = os.getenv("GEMINI_LOCATION", "europe-west9")
    
    # Client Vertex AI (utilise automatiquement GOOGLE_APPLICATION_CREDENTIALS)
    client = genai.Client(vertexai=True, project=project_id, location=location)

    
    prompt = f"""
    Tu es un assistant expert en maintenance industrielle.
    Voici le texte extrait d'un rapport de vérification périodique (Apave) :
    
    {text[:30000]} # On limite à 30k caractères pour éviter de saturer le modèle
    
    Ta tâche est d'extraire toutes les "observations" ou anomalies ("défauts", "non-conformités") 
    relevées sur les équipements.
    Pour chaque observation trouvée, crée une intervention pour Corim avec :
    - LIBE_INTER : Un titre explicite du défaut
    - DEMANDE : Le détail exact du défaut tel qu'écrit dans le rapport
    - APPE_HABIT : L'identifiant ou nom de l'équipement
    - COMMENTAIRE_INTERNE : Le numéro du rapport si tu le trouves au début.
    
    Ne crée des interventions QUE pour les équipements où il y a une action/défaut identifié.
    Ignore les équipements "Sans observation".
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-pro', # Utilisation du modèle Pro (plus adapté et performant)
        contents=prompt,
        config={
            'response_mime_type': 'application/json',
            'response_schema': CorimImport,
        },
    )
    
    return json.loads(response.text)
