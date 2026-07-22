"""
[ARCHITECTURE] LLM & Extraction Sémantique (Rapport_Apave_Corim)

Rôle global :
Ce module fait le pont entre le backend applicatif et le Large Language Model (Gemini Pro via Vertex AI).
Il est responsable de traduire le texte non structuré (le rapport PDF brut) en une structure
de données rigide (Pydantic Model) exploitable par l'ERP Corim.

Stratégie métier (Pourquoi utiliser Pydantic + Gemini-1.5-Pro) :
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

    Junior Tip (correction du 22/07, suite export réel de Maxence) :
    L'IA ne DOIT PLUS deviner INTERVENTION_MERE, NUMERO ou INTERV_ORIG. Ces numéros
    sont des identifiants internes Corim (base de GMAO) qui n'existent nulle part
    dans le rapport PDF Apave. Les demander à l'IA revenait à lui faire halluciner
    un numéro qui, dans 100% des cas, ne matchera pas la vraie base Corim.
    La bonne source pour ces numéros est l'export Corim fourni par Maxence
    (voir src/corim_mapping.py), croisé après coup sur APPE_HABIT. On garde donc
    ces trois champs à vide ici, et CAS_PDF sert de aiguillage pour le mapping.
    """
    LIBE_INTER: str = Field(description="Titre court de l'intervention, ex: 'Correction équipement X suite rapport Apave'")
    DEMANDE: str = Field(description="Description détaillée de la non-conformité relevée, ou motif de non-vérification")
    APPE_HABIT: str = Field(description="Nom ou référence de l'équipement, toujours préfixé par MACH0 (ex: 'MACH0347')", default="")
    PARC: str = Field(description="Référence Parc si disponible (souvent la même valeur que APPE_HABIT)", default="")
    STATUT: str = Field(description="'A' pour une création (cas défaut ou non vérifié), 'H' pour une clôture (cas sans observation, logo vert)", default="A")
    TYPE_MAINT: str = Field(description="Toujours 'CO' pour Correctif (création) ou 'PR' pour les clôtures/mises à jour", default="CO")
    DEMANDEUR: str = Field(description="Toujours 'utilisateur batch'", default="utilisateur batch")
    COMMENTAIRE_INTERNE: str = Field(description="Numéro du rapport Apave", default="")
    CODE_NATT: str = Field(
        description="Nature technique Corim. Champ propre à chaque client (table Administration/Activité/Natures techniques). "
                     "Ne JAMAIS inventer un code : laisser vide, il sera complété manuellement ou via mapping.",
        default="",
    )
    CODEST_MAINT: str = Field(
        description="Sous-type de maintenance Corim. Champ propre à chaque client (table Administration/Activité/Sous-types de maintenance). "
                     "Ne JAMAIS inventer un code : laisser vide, il sera complété manuellement ou via mapping.",
        default="",
    )
    CAS_PDF: str = Field(
        description="Classification du cas Apave, utilisée ensuite par corim_mapping.py pour choisir entre INTERVENTION_MERE, "
                     "NUMERO et INTERV_ORIG : 'DEFAUT' (avec observation, logo croix), 'CLOTURE' (sans observation, logo vert), "
                     "'NON_VERIFIE' (panne/hors service, logo orange), 'PARTIEL' (vérification partielle, cas particulier a traiter manuellement)",
        default="DEFAUT",
    )
    INTERVENTION_MERE: str = Field(description="Laissé vide par l'IA, recalculé par corim_mapping.py", default="")
    NUMERO: str = Field(description="Laissé vide par l'IA, recalculé par corim_mapping.py", default="")
    INTERV_ORIG: str = Field(description="Laissé vide par l'IA, recalculé par corim_mapping.py", default="")

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
        
        Ta tâche est d'analyser les équipements audités et leurs statuts.

        Règles d'extraction (TRÈS IMPORTANT) :
        1. APPE_HABIT : Doit TOUJOURS commencer par "MACH0". Exemples : si l'équipement est "347", tu extrais "MACH0347".
        2. Gestion des statuts et interventions (classification CAS_PDF) :
           - Cas "DEFAUT" (logo croix / avec observation) : il y a une non-conformité.
             * STATUT="A" (Créé), TYPE_MAINT="CO".
             * LIBE_INTER : un résumé court de la remarque Apave. DEMANDE : la remarque complète (le défaut).
             * DEMANDEUR : "utilisateur batch".
           - Cas "CLOTURE" (logo vert / sans observation) : l'équipement est conforme, rien à faire dessus.
             * STATUT="H" (Clôturé), TYPE_MAINT="PR".
             * LIBE_INTER : "Clôture VGP Apave [APPE_HABIT]". DEMANDE : résumé de la conformité constatée.
           - Cas "NON_VERIFIE" (logo orange / panne, hors service, non vérifié) :
             * STATUT="A" (Créé), TYPE_MAINT="CO".
             * DEMANDE : le motif de non-vérification tel que rédigé par Apave.
           - Cas "PARTIEL" (vérification partielle sur un équipement à nature technique non standard, cas rare) :
             * STATUT="A", TYPE_MAINT="CO". Cette ligne sera de toute façon isolée pour traitement manuel, ne cherche pas à la deviner finement.
        3. IMPORTANT : NE JAMAIS renseigner INTERVENTION_MERE, NUMERO, INTERV_ORIG, CODE_NATT ou CODEST_MAINT.
           Ce sont des identifiants et codes internes à la base Corim, invisibles depuis le PDF Apave.
           Les inventer produirait un import qui échoue silencieusement chez Corim. Laisse-les à leur valeur par défaut (vide) :
           ils seront complétés après coup à partir d'un export Corim réel (voir src/corim_mapping.py).
        4. COMMENTAIRE_INTERNE : Le numéro du rapport Apave (ex: A59735423-009-1) si tu le trouves au début du document.
        """
        
        response = client.models.generate_content(
            model='gemini-1.5-pro',
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
