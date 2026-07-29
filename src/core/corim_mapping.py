"""
[MAPPING] Alignement Apave vers Corim (Rapport_Apave_Corim)

Rôle global :
Ce module résout le blocage identifié le 13/07 par Maxence (mail "Export ITV Corim
fait") : ni le LLM ni le PDF Apave ne peuvent connaître les numéros d'intervention
Corim déjà existants (colonnes NUMERO, INTERVENTION_MERE, INTERV_ORIG). ai_processor.py
laisse volontairement ces champs vides ; ce module les complète à partir d'un export
Corim réel (fichier Excel fourni côté métier par Maxence, un par site/équipement).

Stratégie métier (pourquoi un module séparé) :
Séparer "ce que le LLM peut lire dans le PDF" de "ce qui doit venir de Corim" évite
deux écueils :
1. Le LLM qui hallucine un numéro d'ITV plausible mais faux (l'import Corim échoue
   sans message clair, ou pire, écrase la mauvaise intervention).
2. Un couplage fort entre ai_processor.py et le format d'export Corim, qui change
   selon les demandes de Richard/Corim (on a déjà eu 3 clarifications de colonnes
   entre avril et juin).

Règle de mapping validée par Richard et le support Corim (échange du 12/06 au 15/06) :
- INTERVENTION_MERE (colonne A) et INTERV_ORIG (colonne BF) sont mutuellement exclusifs.
- Cas CLOTURE ou NON_VERIFIE : on referme/complète l'ITV existante par son propre
  numéro -> colonne NUMERO. Colonnes A et BF restent vides.
- Cas DEFAUT : on crée une ITV de suite qui pointe vers l'ITV d'origine -> colonne
  INTERV_ORIG (BF). Colonne A (INTERVENTION_MERE) reste vide.
- Cas PARTIEL : nature technique non standard (ex: presse à balles), jamais
  automatisé. La ligne est marquée pour reprise manuelle avec Richard, comme convenu
  dans le mail de Maxence du 13/07.

Junior Tip (correction du 22/07, après réception du vrai fichier de Maxence) :
Le premier jet de ce module supposait, à tort, que l'export de Maxence suivrait le
même schéma que le TEMPLATE D'IMPORT Corim (colonnes APPE_HABIT, NUMERO...). En
réalité, Maxence exporte un extrait NATIF de Corim (un export de consultation, pas
un fichier d'import), avec des colonnes en français : "Code parc", "Numéro", "Code
nature d'intervention"... Les deux formats se ressemblent mais ne sont pas les
mêmes. D'où le dictionnaire COLONNES_ALIAS ci-dessous : plutôt que de figer un nom
de colonne, on tente plusieurs alias connus, pour survivre au prochain export que
Maxence nous enverra sans nous prévenir du changement de forme.
"""

import logging

import pandas as pd

# Alias de colonnes tolérés, du plus spécifique (export natif Corim) au plus
# générique (template d'import Corim). On prend le premier qui matche dans le
# fichier lu, pour ne pas casser si Maxence change de format d'export.
COLONNES_ALIAS: dict[str, list[str]] = {
    "code_parc": ["Code parc", "APPE_HABIT"],
    "numero_itv": ["Numéro", "NUMERO"],
    "code_natt": ["Code nature d'intervention", "CODE_NATT"],
    # Correction du 22/07 (réponse Maxence Q5) : la colonne candidate était
    # "Code type d'intervention" (valeur observée 'PREV', qui ne correspond à
    # aucun code CODEST_MAINT connu), alors que "Sous-type de maintenance" porte
    # bien des valeurs comme 'REGLEMENTAIRE' qui matchent notre convention.
    "codest_maint": ["Sous-type de maintenance", "CODEST_MAINT"],
    # Découvert le 22/07 en relisant les annotations du modèle Maxence : le
    # placeholder "[A RECUPERER SUR EXPORT CORIM]" sur LIBE_INTER correspond à
    # ce libellé (ex: "Presse à balles HSM"), pas à un texte à générer nous-même.
    "libelle_parc": ["Libellé parc", "LIBE_PARC"],
    # Réponse Maxence Q1 du 22/07 ("Ils sont dans l'export CORIM") : TYPE_MAINT
    # vient de cette colonne. Attention piège vérifié le 22/07 : "Type de
    # maintenance" porte le CODE brut (ex: 'PREV'), et c'est "Libellé type de
    # maintenance" qui porte le mot en toutes lettres ('Préventif') - l'inverse
    # de ce qu'on pourrait supposer au nom des colonnes. On reprend donc "Type
    # de maintenance" tel quel (comme codest_maint/libelle_parc), sans essayer
    # de traduire un mot vers un code PR/CO/AM/FA/AU du template générique : ce
    # code ('PREV') ne correspond à aucune valeur de cette doc générique, mais
    # c'est le code réellement utilisé par CETTE instance Corim (même logique
    # que REGLEMENTAIRE/CORR SUITE CTRL pour CODEST_MAINT, qui ne sont pas non
    # plus dans la doc générique du template).
    "type_maintenance": ["Type de maintenance", "TYPE_MAINT"],
}


class EquipementInconnuCorim(Exception):
    """Levée (en mode strict) quand un équipement du rapport Apave n'a pas de correspondance dans l'export Corim."""


def _resoudre_colonne(df: pd.DataFrame, alias: list[str]) -> str | None:
    """Retourne le premier nom de colonne de `alias` présent dans `df`, sinon None."""
    for nom in alias:
        if nom in df.columns:
            return nom
    return None


def load_corim_export(export_path: str) -> dict[str, dict]:
    """
    Charge l'export Corim fourni par Maxence et construit un index par équipement.

    Junior Tip : ce fichier n'est PAS le rapport Apave, c'est un extrait de la base
    Corim. Sans lui, impossible de savoir quel numéro d'ITV existe déjà pour une
    machine donnée : le PDF Apave ne contient jamais de numéro Corim, uniquement des
    repères machine (ex: "347").

    CODE_NATT (nature technique) et CODEST_MAINT (sous-type de maintenance) sont
    récupérés seulement si la colonne correspondante existe ET contient une valeur
    exploitable. Sur le premier export réel reçu le 13/07, "Code nature
    d'intervention" est systématiquement vide : ces deux champs resteront donc vides
    la plupart du temps, à compléter manuellement dans Corim après import. C'est un
    choix assumé (voir décision du 22/07) plutôt que de propager une valeur ambiguë
    (le couple "Sous-type de maintenance" / "Libellé sous-type de maintenance" de cet
    export ne correspond pas clairement aux codes attendus par le template d'import,
    Richard avait déjà signalé cette confusion colonne G/H mi-juin).

    Args:
        export_path: chemin absolu vers l'export Excel transmis par Maxence.

    Returns:
        Un dictionnaire {code_parc: {"numero_itv": str, "cas_particulier": bool,
        "code_natt": str, "codest_maint": str}}.

    Raises:
        FileNotFoundError: si l'export n'existe pas à l'emplacement indiqué.
        ValueError: si aucune colonne connue ne permet d'identifier l'équipement
            (Code parc / APPE_HABIT) ou le numéro d'ITV (Numéro / NUMERO).
    """
    logging.info(f"[INFO] Chargement de l'export Corim : {export_path}")

    df = pd.read_excel(export_path)

    col_parc = _resoudre_colonne(df, COLONNES_ALIAS["code_parc"])
    col_numero = _resoudre_colonne(df, COLONNES_ALIAS["numero_itv"])
    if col_parc is None or col_numero is None:
        raise ValueError(
            f"Export Corim illisible : colonnes équipement/numéro introuvables parmi {df.columns.tolist()}. "
            "Vérifier avec Maxence si le format d'export a encore changé."
        )

    col_natt = _resoudre_colonne(df, COLONNES_ALIAS["code_natt"])
    col_codest = _resoudre_colonne(df, COLONNES_ALIAS["codest_maint"])
    col_libelle = _resoudre_colonne(df, COLONNES_ALIAS["libelle_parc"])
    col_type_maint = _resoudre_colonne(df, COLONNES_ALIAS["type_maintenance"])

    index: dict[str, dict] = {}

    for _, row in df.iterrows():
        code_parc = str(row.get(col_parc, "")).strip()
        if not code_parc or code_parc.lower() == "nan":
            continue

        numero_itv = str(row.get(col_numero, "")).strip()
        code_natt = str(row.get(col_natt, "") or "").strip() if col_natt else ""
        codest_maint = str(row.get(col_codest, "") or "").strip() if col_codest else ""
        libelle_parc = str(row.get(col_libelle, "") or "").strip() if col_libelle else ""
        type_maintenance = str(row.get(col_type_maint, "") or "").strip() if col_type_maint else ""

        index[code_parc] = {
            "numero_itv": "" if numero_itv.lower() == "nan" else numero_itv,
            # Pas de colonne dédiée dans l'export natif Corim reçu le 13/07 :
            # ce signal reste porté par la classification CAS_PDF du LLM (voir ai_processor.py),
            # pas par ce fichier.
            "cas_particulier": False,
            "code_natt": "" if code_natt.lower() == "nan" else code_natt,
            "codest_maint": "" if codest_maint.lower() == "nan" else codest_maint,
            "libelle_parc": "" if libelle_parc.lower() == "nan" else libelle_parc,
            "type_maintenance": "" if type_maintenance.lower() == "nan" else type_maintenance,
        }

    logging.info(f"[SUCCES] {len(index)} équipement(s) indexé(s) depuis l'export Corim.")
    return index


def enrich_interventions_with_corim_numbers(
    interventions: list[dict],
    corim_index: dict[str, dict],
    strict: bool = False,
) -> list[dict]:
    """
    Complète chaque intervention extraite par le LLM avec le bon numéro Corim.

    Args:
        interventions: interventions issues de ai_processor (colonnes NUMERO,
            INTERVENTION_MERE, INTERV_ORIG vides par construction).
        corim_index: index issu de load_corim_export.
        strict: si True, lève EquipementInconnuCorim au lieu de logguer un warning
            quand un équipement du PDF est absent de l'export. À activer une fois
            le POC validé, pour ne pas laisser passer silencieusement un import
            partiel en production.

    Returns:
        La liste d'interventions enrichie, prête pour excel_generator.generate_corim_excel.
    """
    enriched: list[dict] = []

    for itv in interventions:
        appe_habit = itv.get("APPE_HABIT", "")
        match = corim_index.get(appe_habit)
        cas = itv.get("CAS_PDF", "DEFAUT")

        if match is None:
            message = f"Aucune correspondance Corim pour {appe_habit} (export incomplet ou machine non référencée)."
            if strict:
                raise EquipementInconnuCorim(message)
            logging.warning(f"[ATTENTION] {message} Ligne laissée brute pour vérification manuelle.")
            enriched.append(itv)
            continue

        if match["cas_particulier"] or cas == "PARTIEL":
            logging.warning(
                f"[ATTENTION] {appe_habit} : cas particulier (nature technique non standard), "
                "à traiter manuellement avec Richard, comme convenu le 13/07."
            )
            note = itv.get("COMMENTAIRE_INTERNE", "")
            itv["COMMENTAIRE_INTERNE"] = f"{note} [CAS PARTICULIER, VOIR RICHARD]".strip()
            enriched.append(itv)
            continue

        if cas in ("CLOTURE", "NON_VERIFIE"):
            # On referme/complète l'ITV existante : colonne NUMERO uniquement.
            itv["NUMERO"] = match["numero_itv"]
            itv["INTERVENTION_MERE"] = ""
            itv["INTERV_ORIG"] = ""
        else:
            # Cas DEFAUT : ITV de suite. Colonne A vide, BF = numéro d'origine
            # (règle validée par Richard et le support Corim le 15/06).
            itv["INTERV_ORIG"] = match["numero_itv"]
            itv["INTERVENTION_MERE"] = ""
            itv["NUMERO"] = ""

        # CODE_NATT : complété seulement si l'export fournit une vraie valeur.
        # Sur l'export du 13/07 il est systématiquement vide, donc en pratique ce
        # champ reste vide pour ce POC (assumé, cf décision du 22/07).
        if match.get("code_natt"):
            itv["CODE_NATT"] = match["code_natt"]

        # CODEST_MAINT : toujours PAS auto-rempli, même après correction de la
        # colonne source (22/07, Sous-type de maintenance). Réponse Maxence Q5 :
        # "peut y en avoir d'autres, liste à faire par Richard" -> pas encore un
        # feu vert pour appliquer automatiquement, on continue de log un candidat.
        if match.get("codest_maint"):
            logging.info(
                f"[INFO] {appe_habit} : code candidat pour CODEST_MAINT détecté ('{match['codest_maint']}') "
                "mais non appliqué automatiquement, à confirmer avec Richard avant réutilisation."
            )

        # TYPE_MAINT : réponse Maxence Q1 du 22/07 ("Ils sont dans l'export CORIM")
        # -> auto-appliqué, contrairement à CODEST_MAINT (pas la même réserve de
        # sa part). Valeur reprise telle quelle (voir commentaire COLONNES_ALIAS
        # sur le piège colonne code/libellé inversé).
        if match.get("type_maintenance"):
            itv["TYPE_MAINT"] = match["type_maintenance"]

        # LIBE_INTER : le modèle annoté par Maxence (610 - Modèle d'import.xlsx,
        # C5/C6) attend "[libellé équipement issu de l'export Corim] [mois/année
        # intervention Apave]", pas un texte générique inventé par le parseur/le
        # LLM. Si l'export fournit ce libellé, on l'utilise ; sinon on garde le
        # LIBE_INTER généré en amont (fallback, jamais pire que l'existant). Cas
        # PARTIEL exclu : Maxence veut le libellé fixe "A vérifier manuellement"
        # (Q9 du 22/07), pas le libellé équipement.
        if match.get("libelle_parc") and cas != "PARTIEL":
            mois_annee = itv.get("MOIS_ANNEE", "")
            itv["LIBE_INTER"] = f"{match['libelle_parc']} {mois_annee}".strip()

        # Troncature 60 caractères (réponse Maxence Q13) : refaite ici car cette
        # réécriture de LIBE_INTER peut dépasser la limite même si le parseur
        # d'origine tronquait déjà son propre texte de repli.
        itv["LIBE_INTER"] = itv.get("LIBE_INTER", "")[:60]

        enriched.append(itv)

    return enriched
