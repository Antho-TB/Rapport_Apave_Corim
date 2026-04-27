"""
[ARCHITECTURE] LLM & Extraction Sémantique (Rapport_Apave_Corim)

Rôle global :
Ce module fait le pont entre le backend applicatif et le Large Language Model (Gemini Pro via Vertex AI).
Il est responsable de traduire le texte non structuré (le rapport PDF brut) en une structure
de données rigide (Pydantic Model) exploitable par l'ERP Corim.

Stratégie métier (Pourquoi utiliser Pydantic + Gemini-2.5-Pro) :
Les rapports Apave peuvent être extrêmement denses et leur formalisme peut varier. Utiliser des regex
classiques serait fragile. En forçant Gemini à répondre via un `response_schema` (Pydantic),
nous garantissons que le dictionnaire retourné contiendra toujours les clés exigées par Corim
(LIBE_INTER, DEMANDE, APPE_HABIT, etc.), empêchant ainsi les "hallucinations" de casser l'export Excel.
"""

import os
import json
import logging
from google import genai
from pydantic import BaseModel, Field

# --- Définition des schémas de données (Data Contracts) ---

class Intervention(BaseModel):
    """
    Modèle représentant une unique ligne d'intervention pour Corim.
    Les champs 'default' permettent d'assurer une valeur de repli (fallback)
    si l'IA ne trouve pas l'information, garantissant la stabilité du pipeline.
    """
    LIBE_INTER: str = Field(description="Titre court de l'intervention, ex: 'Correction équipement X suite rapport Apave'")
    DEMANDE: str = Field(description="Description détaillée de la non-conformité relevée")
    APPE_HABIT: str = Field(description="Nom ou référence de l'équipement (ex: '348', 'Porte à commande semi-automatique')", default="")
    PARC: str = Field(description="Référence Parc si disponible, sinon vide", default="")
    STATUT: str = Field(description="Toujours 'CREEE'", default="CREEE")
    TYPE_MAINT: str = Field(description="Toujours 'CORRECTIVE'", default="CORRECTIVE")
    DEMANDEUR: str = Field(description="Toujours 'APAVE'", default="APAVE")
    COMMENTAIRE_INTERNE: str = Field(description="Numéro du rapport Apave", default="")

class CorimImport(BaseModel):
    """Conteneur global listant toutes les interventions détectées dans le document."""
    interventions: list[Intervention] = Field(description="Liste des interventions à importer dans Corim")

def parse_apave_text_to_corim_json(text: str) -> dict:
    """
    Orchestre l'appel à l'API Gemini pour effectuer l'extraction de données.
    
    Stratégie :
    1. Instancie le client Vertex AI (qui s'appuie sur le token injecté depuis Azure Key Vault).
    2. Construit un prompt "Zero-Shot" ciblé.
    3. Impose un schéma de réponse JSON strict (CorimImport).
    
    Args:
        text (str): Le texte brut du PDF, potentiellement bruité.
        
    Returns:
        dict: Un dictionnaire contenant la liste des interventions prêtes à être converties en DataFrame.
    """
    logging.info("[INFO] Début de l'analyse sémantique du texte via Gemini Pro.")
    project_id = os.getenv("GEMINI_PROJECT_ID", "tb-ai-platform")
    location = os.getenv("GEMINI_LOCATION", "europe-west9")
    
    try:
        # Le client utilisera implicitement GOOGLE_APPLICATION_CREDENTIALS généré dans app.py
        client = genai.Client(vertexai=True, project=project_id, location=location)

        prompt = f"""
        Tu es un assistant expert en maintenance industrielle.
        Voici le texte extrait d'un rapport de vérification périodique (Apave) :
        
        {text[:30000]} # Coupe de sécurité pour ne pas excéder la fenêtre de contexte
        
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
            model='gemini-2.5-pro',
            contents=prompt,
            config={
                'response_mime_type': 'application/json',
                'response_schema': CorimImport,
            },
        )
        logging.info("[SUCCÈS] Analyse Gemini terminée.")
        return json.loads(response.text)
        
    except Exception as e:
        logging.error(f"[ERREUR] Échec de la requête Gemini : {e}", exc_info=True)
        # Dégradation gracieuse : on retourne un dictionnaire vide plutôt que de faire crasher l'app
        return {"interventions": []}
