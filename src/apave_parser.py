"""
[ETL] Extraction déterministe des rapports Apave (Rapport_Apave_Corim)

Rôle global :
Remplace l'appel LLM (ai_processor.py) pour le cas nominal. Les rapports Apave
utilisent tous le même moteur de template (LearaBIP, vérifié sur 3 rapports de
sites/natures différents : machines, portes/levage, presses) avec des phrases
pivots identiques au mot près. Ce n'est donc pas un cas d'usage justifiant un LLM :
c'est une extraction positionnelle classique, comme sur les ETL Data-Achat/FUSEAU.

Stratégie métier (pourquoi on abandonne le tout-LLM ici) :
1. Fiabilité : un LLM peut halluciner (déjà vécu sur ce projet, cf. bug du 22/07
   sur les numéros Corim). Un parseur à base de phrases pivots fixes est
   déterministe : la même entrée produit toujours la même sortie, testable avec
   des assertions exactes.
2. Coût et dépendances : plus d'appel Vertex AI, plus de dépendance Key Vault/GCP
   pour ce module. Uniquement pdfplumber (déjà utilisé) et de la regex.
3. Auditabilité pour Richard/Maxence : chaque classification est traçable à la
   phrase exacte du PDF qui l'a déclenchée, pas à une décision de modèle opaque.

Limite assumée : si Apave change de moteur de template (nouvelle version
LearaBIP, ou un autre bureau de contrôle avec un autre gabarit), ce parseur ne
reconnaîtra plus les phrases pivots et lèvera RapportFormatInconnu. C'est le
signal explicite pour retomber sur ai_processor.py (LLM) en mode dégradé, selon
le principe de résilience déjà écrit dans SYSTEM_PROMPT.md (section 9).
"""

import logging
import re

# Phrases pivots du moteur de template Apave (LearaBIP), identiques sur tous les
# rapports vérifiés à ce jour (machines, portes/levage, presses).
PHRASE_CLOTURE = "n'ont pas fait apparaître"
PHRASE_DEFAUT = "ont fait apparaître des"
# Panne totale : l'équipement entier n'a pas pu être vérifié (ex: MACH0353, rapport A59735423-009-1).
PHRASE_NON_VERIFIE = "en panne ou hors service"
# Vérification partielle : résultat global conforme MAIS certains points précis n'ont
# pas pu être contrôlés (ex: presses à balles MACH0535/MACH0191, obstruction physique).
# C'est le "cas particulier" décrit par Maxence le 13/07 (nature technique manquante).
# Piège : le PDF écrit tantôt "Eléments", tantôt "Éléments" (accent absent selon la
# page), d'où le regex plutôt qu'une simple sous-chaîne.
MOTIF_PARTIEL = re.compile(r"[eé]l[ée]ments non v[ée]rifi[ée]s", re.IGNORECASE)

# Découpe le texte en un bloc par équipement : chaque bloc démarre au marqueur
# "N° Ordre X" suivi immédiatement de "Rapport de vérification" (les pages
# "Observations" et "Liste des points vérifiés" du même équipement réutilisent
# "N° Ordre X" mais sans ce second marqueur juste après, donc elles restent
# rattachées au bloc précédent lors du split).
DECOUPE_BLOC = re.compile(r"N°\s*Ordre\s+(\d+)\s*\nRapport de vérification\s*\n")


class RapportFormatInconnu(Exception):
    """
    Levée quand le texte du PDF ne contient pas les marqueurs attendus du
    moteur de template Apave (LearaBIP). Signal explicite pour retomber sur
    l'extraction LLM (ai_processor.py) en mode dégradé.
    """


def _extraire_numero_rapport(texte: str) -> str:
    """Récupère le numéro de rapport Apave (ex: A55432737-017-1) en tête de document."""
    match = re.search(r"N°\s*de rapport\s*:\s*(\S+)", texte)
    return match.group(1) if match else ""


def _extraire_code_client(bloc: str) -> str:
    """
    Récupère le code équipement (repère "Client") et le formate en APPE_HABIT.

    Junior Tip : le champ "Client" du PDF est le repère interne TB Groupe de la
    machine (ex: "347"), pas un nom de client au sens commercial. C'est ce code
    qui doit devenir "MACH0347" côté Corim (convention confirmée par Maxence).

    Piège rencontré le 22/07 : quand le champ "Bâtiment" est vide, la mise en
    page PDF glisse tout vers la droite et "Client" se retrouve collé à
    "Bâtiment" sans numéro ("Client Bâtiment Service 337"). Le numéro
    n'apparaît alors qu'après "Service". Plutôt que de gérer chaque variante de
    mise en page, on prend le DERNIER nombre trouvé dans le bloc "Repères :",
    qui correspond dans tous les cas observés au code équipement (le champ
    "Service" reprend systématiquement ce même code quand "Client" est vide).
    """
    match = re.search(r"Repères\s*:\s*\n(.*?)\nFabricant", bloc, re.DOTALL)
    if not match:
        return ""
    nombres = re.findall(r"\d+", match.group(1))
    return f"MACH0{nombres[-1]}" if nombres else ""


def _extraire_designation(bloc: str) -> str:
    """Récupère le libellé de l'équipement, entre le split et 'Date de la vérification'."""
    avant_date = bloc.split("Date de la vérification", 1)[0]
    lignes = [l.strip() for l in avant_date.splitlines() if l.strip() and not l.strip().isdigit()]
    return lignes[0] if lignes else "Équipement non identifié"


def _extraire_observations(bloc: str) -> str:
    """
    Récupère le texte des observations (défauts relevés), entre 'Observations'
    et 'Liste des points vérifiés' (ou la fin du bloc si la coupure est absente).
    """
    match = re.search(r"Observations\s*\n(.*?)(?=Liste des points vérifiés|\Z)", bloc, re.DOTALL)
    if not match:
        return ""
    # Nettoyage : on recolle les lignes coupées par la mise en page PDF et on
    # retire les tirets de fin de paragraphe laissés par l'extraction.
    texte = " ".join(l.strip() for l in match.group(1).splitlines() if l.strip())
    return texte.rstrip("- ").strip()


def _extraire_elements_non_verifies(bloc: str) -> str:
    """Récupère le détail des points non contrôlés lors d'une vérification partielle."""
    match = re.search(
        r"[eé]l[ée]ments non v[ée]rifi[ée]s\s*\n(.*?)(?=\nDate\s*:|\Z)", bloc, re.DOTALL | re.IGNORECASE
    )
    if not match:
        return ""
    return " ".join(l.strip() for l in match.group(1).splitlines() if l.strip())


def _extraire_par_ailleurs(bloc: str) -> str:
    """Récupère le texte d'une remarque complémentaire ('Par ailleurs'), si présente."""
    match = re.search(r"Par ailleurs\s*\n(.*?)(?=\nDate\s*:|\Z)", bloc, re.DOTALL)
    if not match:
        return ""
    return " ".join(l.strip() for l in match.group(1).splitlines() if l.strip())


def _classifier_bloc(bloc: str) -> str:
    """
    Détermine le cas Apave (CAS_PDF) à partir des phrases pivots du bloc.

    Ordre de priorité : PARTIEL et NON_VERIFIE d'abord (l'équipement n'a alors
    pas de section "Résultat de la vérification" classique), puis DEFAUT/CLOTURE.
    Une clôture (logo vert) accompagnée d'une remarque "Par ailleurs" est
    reclassée en DEFAUT : Apave signale alors une anomalie secondaire malgré un
    résultat global conforme (cas observé sur MACH0370, confirmé par le test du
    28/04 où cette ligne avait bien été traitée comme un défaut, pas une clôture).
    """
    bloc_lower = bloc.lower()

    if MOTIF_PARTIEL.search(bloc):
        return "PARTIEL"
    if PHRASE_NON_VERIFIE in bloc_lower:
        return "NON_VERIFIE"

    a_clôture = PHRASE_CLOTURE in bloc_lower
    a_defaut = PHRASE_DEFAUT in bloc_lower
    a_par_ailleurs = bool(_extraire_par_ailleurs(bloc))

    if a_clôture and a_par_ailleurs:
        return "DEFAUT"
    if a_defaut:
        return "DEFAUT"
    if a_clôture:
        return "CLOTURE"

    raise RapportFormatInconnu(
        "Aucune phrase pivot connue (clôture/défaut/non vérifié/partiel) trouvée dans ce bloc. "
        "Format de rapport probablement différent : basculer sur l'extraction LLM (ai_processor.py)."
    )


def parse_apave_report(texte: str) -> dict:
    """
    Extrait les interventions d'un rapport Apave de façon déterministe.

    Args:
        texte: texte brut du PDF (sortie de pdf_extractor.extract_text_from_pdf).

    Returns:
        Un dictionnaire {"interventions": [...]}, même contrat que
        ai_processor.parse_apave_text_to_corim_json, directement compatible avec
        corim_mapping.enrich_interventions_with_corim_numbers et
        excel_generator.generate_corim_excel.

    Raises:
        RapportFormatInconnu: si aucun bloc équipement n'est reconnu, ou si un
            bloc ne contient aucune phrase pivot connue. Signal pour basculer
            sur l'extraction LLM en mode dégradé (voir SYSTEM_PROMPT.md section 9).
    """
    logging.info("[INFO] Extraction déterministe du rapport Apave (sans LLM).")

    numero_rapport = _extraire_numero_rapport(texte)
    morceaux = DECOUPE_BLOC.split(texte)

    if len(morceaux) < 3:
        raise RapportFormatInconnu(
            "Aucun bloc 'N° Ordre / Rapport de vérification' reconnu : "
            "le format du PDF ne correspond pas au moteur de template Apave connu."
        )

    # re.split avec groupes capturants alterne [avant, num1, bloc1, num2, bloc2, ...]
    blocs = list(zip(morceaux[1::2], morceaux[2::2]))
    interventions: list[dict] = []

    for _, bloc in blocs:
        appe_habit = _extraire_code_client(bloc)
        designation = _extraire_designation(bloc)
        cas = _classifier_bloc(bloc)

        if cas == "DEFAUT":
            observation = _extraire_observations(bloc) or _extraire_par_ailleurs(bloc)
            demande = observation or f"Anomalie relevée sur {designation}."
            libe_inter = f"Défaut relevé {appe_habit}"
            statut, type_maint = "A", "CO"
        elif cas == "CLOTURE":
            demande = "Clôture de l'intervention suite au rapport de vérification périodique Apave, aucune anomalie détectée."
            libe_inter = f"Clôture VGP Apave {appe_habit}"
            statut, type_maint = "H", "PR"
        elif cas == "NON_VERIFIE":
            demande = "Équipement non vérifié : en panne ou hors service lors de la vérification Apave. Intervention complémentaire à prévoir."
            libe_inter = f"Équipement non vérifié {appe_habit}"
            statut, type_maint = "A", "CO"
        else:  # PARTIEL
            detail = _extraire_elements_non_verifies(bloc)
            demande = (
                f"Vérification partielle ({detail})." if detail else "Vérification partielle."
            ) + " Cas particulier, nature technique à confirmer avec Richard avant import."
            libe_inter = f"Vérification partielle {appe_habit}"
            statut, type_maint = "A", "CO"

        interventions.append({
            "LIBE_INTER": libe_inter,
            "DEMANDE": demande,
            "APPE_HABIT": appe_habit,
            "PARC": appe_habit,
            "STATUT": statut,
            "TYPE_MAINT": type_maint,
            "DEMANDEUR": "utilisateur batch",
            "COMMENTAIRE_INTERNE": numero_rapport,
            "CODE_NATT": "",
            "CODEST_MAINT": "",
            "CAS_PDF": cas,
            "INTERVENTION_MERE": "",
            "NUMERO": "",
            "INTERV_ORIG": "",
        })

    logging.info(f"[SUCCES] {len(interventions)} intervention(s) extraite(s) sans appel LLM.")
    return {"interventions": interventions}
