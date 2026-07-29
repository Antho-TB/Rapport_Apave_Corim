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


def _extraire_date_rapport(texte: str) -> tuple[str, str, str] | None:
    """
    Récupère la date de couverture du rapport (ex: "Date : 30/01/2026" en page
    de garde), à ne pas confondre avec "Date de la vérification JJ/MM/AAAA" qui
    est propre à chaque équipement.

    Junior Tip (réponse de Maxence au questionnaire du 22/07, Q7) :
    DATEDEB_PREVU/DATEFIN_PREVU attendent cette date de couverture, pas la date
    de vérification par équipement (qui va dans DATEDEB_REEL/DATEFIN_REEL). La
    regex "Date\\s*:" (avec le double-point) ne matche jamais "Date de la
    vérification" (qui n'a pas de double-point), donc pas d'ambiguïté entre les
    deux occurrences dans le texte du PDF.
    """
    match = re.search(r"Date\s*:\s*(\d{2})/(\d{2})/(\d{4})", texte)
    return match.groups() if match else None


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
    if not nombres:
        # Cas découvert le 29/07 sur le rapport LEVAGE (RA59215196-010-1) : certains
        # blocs n'ont pas de repère numérique du tout ("Client ETIQUETAGE Bâtiment
        # Service", "Client Bâtiment Service Mezzanine"). On ne devine PAS un code :
        # APPE_HABIT reste vide et l'appelant marque la ligne pour reprise manuelle
        # (voir parse_apave_report). Inventer un code ici ferait échouer l'import
        # Corim silencieusement, ou pire, rattacherait l'ITV au mauvais équipement.
        return ""
    # Bug corrigé le 29/07 (revue Antho, comparaison avec l'export Corim réel) :
    # un ancien essai faisait "MACH0" + nombre brut, ce qui ne donne 4 chiffres
    # qu'à condition que le repère machine fasse déjà 3 chiffres (ex: "337" ->
    # "MACH0337"). Pour un repère à 2 chiffres ("84") ça produisait "MACH084"
    # (3 chiffres) au lieu de "MACH0084" (4 chiffres), cassant tout matching
    # avec l'export Corim qui zero-pad systématiquement sur 4 chiffres (vérifié
    # sur les 5 équipements de l'export test : MACH0535, MACH0252, MACH0376,
    # MACH0334, MACH0337, tous à 4 chiffres). On zero-pad explicitement sur 4,
    # quelle que soit la longueur du repère brut.
    #
    # Cas 5 chiffres (découvert le 29/07 sur le rapport LEVAGE, repère "80005") :
    # le zero-padding ne s'applique pas (le nombre dépasse déjà 4 chiffres), on
    # renvoie le code tel quel. Il ne respecte alors pas la convention MACH+4,
    # d'où le marquage pour reprise manuelle côté parse_apave_report : soit ce
    # repère appartient à une autre famille de codes Corim, soit c'est une
    # coquille du rapport Apave. À confirmer avec Richard, ne pas tronquer.
    return f"MACH{int(nombres[-1]):04d}"


def _extraire_designation(bloc: str) -> str:
    """Récupère le libellé de l'équipement, entre le split et 'Date de la vérification'."""
    avant_date = bloc.split("Date de la vérification", 1)[0]
    lignes = [l.strip() for l in avant_date.splitlines() if l.strip() and not l.strip().isdigit()]
    return lignes[0] if lignes else "Équipement non identifié"


def _extraire_observations(bloc: str) -> str:
    """
    Récupère le texte des observations (défauts relevés), entre 'Observations'
    et le premier repère de fin : 'Liste des points vérifiés', ou le pied de
    page qui suit ('Version modèle rapport...', répété en bas de chaque page).

    Piège corrigé le 22/07 (suite au split multi-défauts, voir Q11) : quand la
    section Observations se termine en fin de page, le pied de page ('Date :
    JJ/MM/AAAA - Version modèle rapport LearaBIP_x.x.x Page N / M') ET l'en-tête
    de la page suivante ('RAPPORT - EQUIPEMENTS MECANIQUES N° DE RAPPORT : ...')
    restaient collés au texte capturé, tant que 'Liste des points vérifiés'
    n'apparaissait pas avant la fin du bloc. Ce bruit de mise en page se
    faisait alors découper en faux "défauts" supplémentaires par
    _decouper_defauts. "Version modèle rapport" sert de repère de coupe
    supplémentaire, plus fiable qu'une simple recherche de fin de bloc.
    """
    match = re.search(
        r"Observations\s*\n(.*?)(?=Liste des points vérifiés|Date\s*:\s*\d{2}/\d{2}/\d{4}\s*-\s*Version modèle rapport|\Z)",
        bloc, re.DOTALL,
    )
    if not match:
        return ""
    # Nettoyage : on recolle les lignes coupées par la mise en page PDF et on
    # retire les tirets de fin de paragraphe laissés par l'extraction.
    texte = " ".join(l.strip() for l in match.group(1).splitlines() if l.strip())
    return texte.rstrip("- ").strip()


def _decouper_defauts(texte_observations: str) -> list[str]:
    """
    Découpe un texte d'observations en défauts individuels.

    Junior Tip (réponse Maxence Q11 du 22/07, "une ITV = une ligne") : sur la
    page de synthèse du rapport Apave, chaque équipement porte un badge numéroté
    (1, 2...) qui correspond au nombre de blocs "catégorie + description"
    présents dans sa page Observations (ex: MACH0337 badge "2" = "ÉLÉMENTS
    CONSTITUTIFS" + "ÉLÉMENTS MÉCANIQUES").

    Piège corrigé le 22/07 : un premier essai découpait sur TOUT " - ", mais un
    même défaut peut contenir des tirets internes qui n'ont rien à voir avec une
    séparation entre défauts (ex: MACH0355 "CHARPENTE Structure - Tablier -
    Portillon : Remettre en état le panneau inferieur." = UN SEUL défaut dont le
    libellé énumère 3 sous-éléments avec des tirets, PAS 3 défauts). Découper
    plutôt sur ". - " (point suivi d'un tiret) est plus sûr : c'est la marque
    d'une phrase déjà terminée avant un nouveau bloc catégorie/description.
    Contrepartie assumée : un cas où deux défauts s'enchaînent SANS point avant
    le tiret (observé une fois sur MACH0103, "...mission complémentaire -
    Protecteurs : ...") reste fusionné en une seule ligne au lieu de deux. C'est
    un sous-découpage (comportement identique à avant cette fonctionnalité), pas
    un sur-découpage qui inventerait des lignes fausses : le compromis est
    délibéré, on préfère rater un découpage que produire un défaut fantôme.
    """
    morceaux = [m.strip() for m in re.split(r"\.\s*-\s*", texte_observations)]
    return [m if m.endswith(".") else f"{m}." for m in morceaux if m]


def _extraire_defauts(bloc: str) -> list[str]:
    """
    Retourne un défaut par ligne d'ITV à créer (voir _decouper_defauts).

    Se rabat sur "Par ailleurs" si la section Observations est vide (cas d'une
    clôture reclassée en DEFAUT à cause d'une remarque complémentaire, ex:
    MACH0370) : dans ce cas il n'y a qu'un seul défaut, pas de découpage à faire.
    """
    observations = _extraire_observations(bloc)
    if observations:
        defauts = _decouper_defauts(observations)
        if defauts:
            return defauts
    par_ailleurs = _extraire_par_ailleurs(bloc)
    return [par_ailleurs] if par_ailleurs else []


def _extraire_elements_non_verifies(bloc: str) -> str:
    """Récupère le détail des points non contrôlés lors d'une vérification partielle."""
    match = re.search(
        r"[eé]l[ée]ments non v[ée]rifi[ée]s\s*\n(.*?)(?=\nDate\s*:|\Z)", bloc, re.DOTALL | re.IGNORECASE
    )
    if not match:
        return ""
    return " ".join(l.strip() for l in match.group(1).splitlines() if l.strip())


def _extraire_date_verification(bloc: str) -> str:
    """
    Récupère la date de vérification Apave du bloc (ex: '28/01/2026'), présente
    systématiquement sous la forme "Date de la vérification JJ/MM/AAAA".

    Junior Tip (correction du 22/07, suite relecture des annotations Maxence) :
    dans le modèle d'import annoté, les colonnes DATEDEB_REEL et DATEFIN_REEL
    (jaunes, donc réellement utilisées) contiennent toutes deux le même
    placeholder "[DATE ITV APAVE]" : Corim veut cette date de vérification en
    début ET fin réelle (la vérification se fait en une seule journée, pas de
    plage horaire précise dans le rapport Apave). On ne connaît pas l'heure
    exacte : on retient 00:00 par convention, à confirmer avec Richard si Corim
    a besoin d'une heure réelle.
    """
    match = re.search(r"Date de la vérification\s+(\d{2})/(\d{2})/(\d{4})", bloc)
    return match.groups() if match else None


def _formater_date_corim(jour: str, mois: str, annee: str) -> str:
    """Formate JJ/MM/AAAA en AAAAMMJJ HH:mm (format Corim imposé), heure conventionnelle 00:00."""
    return f"{annee}{mois}{jour} 00:00"


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

    # Date de couverture du rapport (DATEDEB_PREVU/DATEFIN_PREVU, réponse Maxence
    # Q7 du 22/07) : une seule fois pour tout le document, pas par équipement.
    date_rapport = _extraire_date_rapport(texte)
    date_prevu_corim = _formater_date_corim(*date_rapport) if date_rapport else ""

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
        statut_a_confirmer = False

        date_verif = _extraire_date_verification(bloc)
        if date_verif:
            jour, mois, annee = date_verif
            mois_annee = f"{mois}/{annee}"
            date_corim = _formater_date_corim(jour, mois, annee)
        else:
            # Pas de date trouvée dans ce bloc : on ne l'invente pas, on laisse
            # vide plutôt que de faire échouer tout le rapport pour ça (rare,
            # jamais observé sur les 2 rapports réels testés).
            mois_annee = ""
            date_corim = ""

        # Réponse Maxence Q11 du 22/07 ("une ITV = une ligne") : le cas DEFAUT
        # peut produire PLUSIEURS comptes-rendus (un par défaut individuel
        # relevé sur l'équipement, voir _extraire_defauts) -> une intervention
        # par défaut, pas une seule ligne qui les concatène tous. Les autres cas
        # (CLOTURE/NON_VERIFIE/PARTIEL) restent une seule ligne, il n'y a
        # jamais plusieurs constats distincts pour ces cas-là.
        if cas == "DEFAUT":
            comptes_rendus = _extraire_defauts(bloc) or [f"Anomalie relevée sur {designation}."]
            libe_inter = f"Défaut relevé {appe_habit}"
            statut, codest_maint = "A", "CORR SUITE CTRL"
        elif cas == "CLOTURE":
            comptes_rendus = ["Clôture de l'intervention suite au rapport de vérification périodique Apave, aucune anomalie détectée."]
            libe_inter = f"Clôture VGP Apave {appe_habit}"
            statut, codest_maint = "H", "REGLEMENTAIRE"
        elif cas == "NON_VERIFIE":
            comptes_rendus = ["Équipement non vérifié : en panne ou hors service lors de la vérification Apave. Intervention complémentaire à prévoir."]
            libe_inter = f"Équipement non vérifié {appe_habit}"
            statut, codest_maint = "A", "CORR SUITE CTRL"
        else:  # PARTIEL
            detail = _extraire_elements_non_verifies(bloc)
            comptes_rendus = [(
                f"Vérification partielle ({detail})." if detail else "Vérification partielle."
            ) + " Cas particulier, nature technique à confirmer avec Richard avant import."]
            # LIBE_INTER (réponse Maxence Q9 du 22/07) : libellé fixe "A vérifier
            # manuellement" pour ce cas, le détail va dans COMPTE_RENDU (déjà fait
            # ci-dessus), pas de mois/année ajouté ensuite pour ce cas particulier.
            libe_inter = "A vérifier manuellement"
            statut, codest_maint = "A", ""

        # TYPE_MAINT : vide par défaut ici (le parseur ne lit pas l'export Corim).
        # Réponse Maxence Q1 du 22/07 : "Ils sont dans l'export CORIM" -> la vraie
        # valeur (PR/CO/AM/FA/AU) vient de la colonne "Type de maintenance" de
        # l'export, appliquée par corim_mapping.py quand l'équipement y est trouvé.
        # Ce champ reste vide seulement en fallback (équipement absent de l'export).
        type_maint = ""

        # Modèle annoté Maxence (610 - Modèle d'import.xlsx, C5/C6) : LIBE_INTER
        # se termine par le mois/année de l'intervention Apave (ex: "01/2026").
        # Le préfixe (désignation équipement) est ensuite éventuellement remplacé
        # par corim_mapping.py avec le "Libellé parc" de l'export Corim, plus
        # fiable que ce texte générique (voir MOIS_ANNEE ci-dessous, conservé
        # pour permettre cette réécriture après coup). Cas PARTIEL exclu (libellé
        # fixe ci-dessus).
        if mois_annee and cas != "PARTIEL":
            libe_inter = f"{libe_inter} {mois_annee}"

        # Réponse Maxence Q13 du 22/07 : LIBE_INTER limité à 60 caractères côté
        # Corim (voir doc colonne du template, "60 c max") -> troncature simple,
        # pas de gestion spéciale demandée.
        libe_inter = libe_inter[:60]

        # Cas signalé par Maxence lui-même dans son modèle annoté (610 - Modèle
        # d'import.xlsx, ligne 6, cellule STATUT en rouge "E ou T ou H ? Voir
        # Richard") : quand un défaut est relevé sur un équipement qui a déjà une
        # ITV mère, le statut à donner à CETTE ITV mère (En cours/Terminée/
        # Clôturée) n'est pas tranché. Réponse Maxence Q8 du 22/07 : "pas défaut :
        # En cours", mais "à valider avec Richard" -> on propose E par défaut tout
        # en gardant le flag explicite (pas encore une décision définitive).
        if cas == "DEFAUT":
            statut_a_confirmer = True

        # Contrôle de conformité du code équipement (ajouté le 29/07, découverte
        # sur le rapport LEVAGE RA59215196-010-1) : APPE_HABIT est obligatoire
        # côté Corim et doit suivre la convention MACH + 4 chiffres. Trois écarts
        # réels observés sur ce rapport : repère à 5 chiffres ("80005"), et deux
        # blocs sans aucun repère numérique ("ETIQUETAGE", "Mezzanine"). Plutôt
        # que de laisser passer une ligne silencieusement invalide (import Corim
        # en échec, ou pire, rattachée au mauvais équipement), on la marque
        # explicitement dans le commentaire, visible directement dans l'Excel.
        code_non_conforme = not re.fullmatch(r"MACH\d{4}", appe_habit)

        commentaire_interne = numero_rapport
        if code_non_conforme:
            detail = f"'{appe_habit}'" if appe_habit else "ABSENT DU RAPPORT"
            commentaire_interne = (
                f"{commentaire_interne} [CODE EQUIPEMENT NON CONFORME: {detail}, "
                "ATTENDU MACH+4 CHIFFRES - A CORRIGER MANUELLEMENT AVANT IMPORT]"
            )
        if statut_a_confirmer:
            # Visible directement dans l'Excel final (colonne conservée par
            # excel_generator), pas seulement dans un champ interne qui serait
            # perdu au tri des colonnes du template Corim.
            #
            # Junior Tip : on CONCATÈNE au commentaire déjà construit, on ne le
            # réassigne pas. Une réassignation ici écrasait le flag de code
            # équipement non conforme posé juste au-dessus (bug attrapé le 29/07
            # au test : 3 lignes non conformes sur le rapport LEVAGE, mais 2
            # seulement marquées, la ligne DEFAUT perdait son flag). Dès que
            # deux règles peuvent marquer le même champ, il faut accumuler.
            commentaire_interne = (
                f"{commentaire_interne} "
                "[STATUT ITV MERE PROPOSE: E (EN COURS) - A CONFIRMER AVEC RICHARD]"
            )

        # Une intervention par compte-rendu (voir comptes_rendus ci-dessus) :
        # pour CLOTURE/NON_VERIFIE/PARTIEL, c'est toujours une seule ligne ;
        # pour DEFAUT, autant de lignes que de défauts individuels détectés,
        # toutes identiques hors COMPTE_RENDU (même équipement, même dates,
        # même statut : Corim les distinguera par son propre NUMERO généré).
        for compte_rendu in comptes_rendus:
            interventions.append({
                "LIBE_INTER": libe_inter,
                "DEMANDE": "",
                "COMPTE_RENDU": compte_rendu,
                "APPE_HABIT": appe_habit,
                "PARC": appe_habit,
                "STATUT": statut,
                "TYPE_MAINT": type_maint,
                "DATEDEB_PREVU": date_prevu_corim,
                "DATEFIN_PREVU": date_prevu_corim,
                "DATEDEB_REEL": date_corim,
                "DATEFIN_REEL": date_corim,
                "DEMANDEUR": "utilisateur batch",
                "COMMENTAIRE_INTERNE": commentaire_interne,
                "CODE_NATT": "",
                "CODEST_MAINT": codest_maint,
                "CAS_PDF": cas,
                "MOIS_ANNEE": mois_annee,
                "STATUT_A_CONFIRMER": statut_a_confirmer,
                "INTERVENTION_MERE": "",
                "NUMERO": "",
                "INTERV_ORIG": "",
            })

    logging.info(f"[SUCCES] {len(interventions)} intervention(s) extraite(s) sans appel LLM.")

    # Bug corrigé le 22/07 : avant cette clé explicite, batch_processor.py
    # devait reconstituer numero_rapport à partir de interventions[0]["COMMENTAIRE_INTERNE"],
    # qui porte désormais le suffixe "[STATUT ITV MERE A CONFIRMER AVEC RICHARD]"
    # pour les cas DEFAUT. Résultat : la clé d'upsert DWH (numero_rapport, nom_fichier)
    # changeait à chaque run selon la première intervention du bloc, créant un
    # doublon en base au lieu de rafraîchir le rapport existant. On expose ici la
    # valeur propre, déjà calculée en tête de fonction, pour que l'appelant n'ait
    # plus jamais besoin de la déduire d'un champ métier annexe.
    return {"numero_rapport": numero_rapport, "interventions": interventions}
