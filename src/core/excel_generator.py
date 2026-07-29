"""
[ARCHITECTURE] Génération du format pivot (Rapport_Apave_Corim)

Rôle global :
Ce module est en charge de la conversion de la structure d'objets (JSON/Dict) en un
fichier physique Excel (.xlsx) qui respecte au pixel près le template d'import attendu par l'ERP Corim.

Stratégie métier (Dataframe Alignment) :
L'importation dans l'ERP échouera si une colonne manque ou est mal orthographiée.
La stratégie consiste à définir un "schéma directeur" (la liste des colonnes Corim), 
à intégrer les données trouvées par l'IA, et à remplir automatiquement de vide (vide ou "nan") 
les colonnes exigées par Corim mais non pertinentes pour un rapport Apave.
"""

import pandas as pd
import logging
import os

def generate_corim_excel(data: dict, output_path: str) -> str:
    """
    Génère un fichier Excel d'import Corim à partir des données extraites.
    
    Stratégie :
    Au lieu de créer un Excel "from scratch", on initialise un DataFrame vide contenant
    exhaustivement TOUTES les colonnes du template Corim. On fait ensuite un mapping des
    valeurs extraites, et on réordonne. Cela garantit la compatibilité ascendante avec l'ERP.
    
    Args:
        data (dict): Dictionnaire contenant la clé 'interventions'.
        output_path (str): Le chemin absolu où écrire le fichier Excel.
        
    Returns:
        str: Le chemin du fichier généré.
    """
    logging.info(f"[INFO] Initialisation de la génération de l'Excel vers {output_path}")
    
    # Colonnes standards extraites du modèle d'import Corim
    columns = [
        'INTERVENTION_MERE', 'NUMERO', 'LIBE_INTER', 'APPE_HABIT', 'PARC', 'STATUT', 
        'TYPE_MAINT', 'CODEST_MAINT', 'CODE_SPEC', 'CODE_NATT', 'DEMANDE', 'COMPTE_RENDU', 
        'DEMANDEUR', 'CODE_PRIORITE', 'CODE_RESP', 'NIVEAU_MAINT', 'DUREE_ARRE', 'CODE_SECTBUDG', 
        'AXE_ANALYTIQUE', 'CODE_CONT', 'DATEDEB_PREVU', 'DATEDEB_REEL', 'DATEFIN_PREVU', 'DATEFIN_REEL', 
        'DATE_DEMANDE', 'DATEBUTEE', 'DATEOUVERTURE', 'CHARGE_A_PLANIF', 'DURE_REEL', 'CODE_TAUX', 
        'COUT_MAG', 'COUT_ADI', 'COUT_TVX', 'CODE_INTERV1', 'DUREE1', 'FIGE_DATE1', 'FIGE_INTERV1', 
        'CODE_INTERV2', 'DUREE2', 'FIGE_DATE2', 'FIGE_INTERV2', 'CODE_INTERV3', 'DUREE3', 'FIGE_DATE3', 
        'FIGE_INTERV3', 'CODE_INTERV4', 'DUREE4', 'FIGE_DATE4', 'FIGE_INTERV4', 'CODE_INTERV5', 'DUREE5', 
        'FIGE_DATE5', 'FIGE_INTERV5', 'CODE_INTERV6', 'DUREE6', 'FIGE_DATE6', 'FIGE_INTERV6', 
        'INTERV_ORIG', 'REF_EXTERNE', 'COMMENTAIRE_INTERNE', 'CAUSE'
    ]
    
    try:
        df = pd.DataFrame(columns=columns)
        
        interventions = data.get("interventions", [])
        if interventions:
            df_data = pd.DataFrame(interventions)
            
            # Application de la règle métier : ajout des colonnes manquantes
            for col in columns:
                if col not in df_data.columns:
                    df_data[col] = ""
                    
            # Tri final pour correspondre au format d'ingestion de Corim
            df = df_data[columns]
            
        df.to_excel(output_path, index=False)
        logging.info("[SUCCÈS] Le fichier Excel d'import Corim a été généré.")
        
    except Exception as e:
        logging.error(f"[ERREUR] Échec lors de la génération de l'Excel : {e}", exc_info=True)
        raise
        
    return output_path
