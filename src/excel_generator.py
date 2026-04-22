# Module de génération du fichier Excel
# Apprentissage : Ce module utilise Pandas ou OpenPyXL pour créer un fichier tableur.
# Il est important de bien respecter l'ordre et le nom des colonnes attendues par Corim
# pour que l'import manuel se déroule sans erreur.
import pandas as pd
import os

def generate_corim_excel(data: dict, output_path: str) -> str:
    """
    Génère le fichier Excel d'import Corim à partir des données structurées.
    data doit être un dictionnaire contenant une clé 'interventions' (liste de dictionnaires).
    """
    # Colonnes standards extraites du modèle Corim
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
    
    # Création d'un DataFrame vide avec les bonnes colonnes
    df = pd.DataFrame(columns=columns)
    
    # Si nous avons des données, nous les ajoutons
    interventions = data.get("interventions", [])
    if interventions:
        df_data = pd.DataFrame(interventions)
        # On s'assure que les colonnes manquantes dans data mais présentes dans le template sont rajoutées
        for col in columns:
            if col not in df_data.columns:
                df_data[col] = ""
        # On réordonne les colonnes selon le template
        df = df_data[columns]
        
    df.to_excel(output_path, index=False)
    return output_path
