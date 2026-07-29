# ADR: Pipeline Apave -> Corim, état des lieux

**Date:** 2026-07-23
**Statut:** Accepté (architecture), Poc validé, en attente de recette Maxence/Richard

## Contexte et Problème

Le service Achats/Maintenance a besoin de transformer les rapports de vérification périodique
Apave (PDF) en fichiers d'import Corim (GMAO). Version initiale (avril) : extraction 100% LLM
(Gemini/Vertex AI), qui halluciné les numéros d'intervention Corim (INTERVENTION_MERE, NUMERO,
INTERV_ORIG) puisque ces identifiants n'existent nulle part dans le PDF Apave. Import Corim
cassé silencieusement à chaque tentative réelle.

## Options Considérées

1. Corriger le prompt LLM pour qu'il laisse ces champs vides, et les compléter après coup.
2. Abandonner le tout-LLM pour une extraction déterministe (regex/positionnel), le LLM ne
   restant qu'un repli en cas de format de rapport non reconnu.

## Décision

Option 2. Les rapports Apave utilisent tous le même moteur de template (LearaBIP), avec des
phrases pivots identiques au mot près (vérifié sur 3 rapports de nature différente). Un LLM
n'apporte pas de valeur sur un format aussi stable, et introduit un risque d'hallucination, un
coût, et une dépendance Key Vault/GCP évitables.

## Justification (Signal/Bruit maximal)

- **Fiabilité** : un parseur à base de phrases pivots fixes est déterministe, testable par
  assertions exactes. Le LLM ne restant qu'un repli (`RapportFormatInconnu`), le coût Vertex AI
  et la dépendance Key Vault `kv-tb-ia-agents-secrets` ne sont payés que sur l'exception.
- **Auditabilité** : chaque classification (DEFAUT/CLOTURE/NON_VERIFIE/PARTIEL) est traçable à
  la phrase exacte du PDF qui l'a déclenchée, pas à une décision de modèle opaque. Important
  pour Richard (support Corim) qui doit pouvoir challenger une ligne générée.
- **Séparation des responsabilités** : ce que le parseur/LLM peut lire dans le PDF (défauts,
  dates, désignations) est strictement séparé de ce qui doit venir de Corim lui-même (numéros
  d'ITV existants), résolu par `src/corim_mapping.py` à partir de l'export Excel de Maxence.

## Conséquences

- **Avantages** : zéro hallucination possible sur les champs identifiants Corim, coût
  d'exécution nul sur le cas nominal, pipeline testable sans dépendance réseau.
- **Risques/Inconvénients** : si Apave change de moteur de template, le parseur lève
  `RapportFormatInconnu` et bascule sur Gemini, moins fiable sur les identifiants (mitigé, ce
  chemin ne les renseigne jamais non plus).

---

## Architecture livrée

| Composant | Rôle |
|---|---|
| `src/apave_parser.py` | Extraction déterministe (regex/positionnel), chemin principal |
| `src/ai_processor.py` | Extraction Gemini, repli uniquement si `RapportFormatInconnu` |
| `src/corim_mapping.py` | Résout NUMERO/INTERV_ORIG/TYPE_MAINT à partir de l'export Corim réel |
| `src/excel_generator.py` | Génère le fichier Excel au format pivot d'import Corim (61 colonnes) |
| `src/dwh_loader.py` | Persistance best-effort dans `apave_corim.*` (dtpf_sylob_prod) |
| `batch_processor.py` | Orchestrateur "Dossier Magique" : `A traiter` -> `A importer dans Corim` -> archive |

Schéma DWH `apave_corim` (dtpf_sylob_prod), compte dédié `dtpf_sylob_anthony_bezille_prod`
(même principe d'isolation que `achat`/`appro_raw` sur Data-Achat/FUSEAU : jamais réutiliser un
compte de service partagé pour un pipeline tiers). 4 migrations appliquées au fil de l'eau
(`compte_rendu`, `datedeb_reel`/`datefin_reel`, `datedeb_prevu`/`datefin_prevu`).

## Décisions issues de la recette Maxence (22/07)

Questionnaire de 13 questions envoyé, réponses intégrées au code, dont 3 corrections notables
qui auraient produit un import silencieusement faux si non corrigées :

1. **CODEST_MAINT** : l'alias colonne pointait sur "Code type d'intervention" (valeur observée
   'PREV', sans rapport), corrigé vers "Sous-type de maintenance" (donne 'REGLEMENTAIRE',
   cohérent).
2. **TYPE_MAINT** : piège colonne code/libellé inversé. "Type de maintenance" porte le code brut
   ('PREV'), "Libellé type de maintenance" porte le mot en toutes lettres ('Préventif'), l'
   inverse de ce que les noms suggéraient. Vérifié par impression d'un échantillon réel avant
   de trancher.
3. **Split "une ITV = une ligne"** (Q11) : un équipement à défauts multiples doit produire une
   ligne par défaut, pas un COMPTE_RENDU qui les concatène. Le découpage naïf sur tout `" - "`
   cassait des libellés composés (ex: "CHARPENTE Structure - Tablier - Portillon" = UN défaut,
   pas trois). Correction : découpage sur `". - "` (point suivi d'un tiret), validé contre les
   badges numérotés visibles sur la page de synthèse du PDF (MACH0337 badge 2 = 2 morceaux,
   MACH0105 badge 2 = 2 morceaux, MACH0355 pas de point avant tiret = 1 morceau).

## Dette identifiée (à traiter)

- STATUT ambigu (E/T/H) sur l'ITV mère quand un défaut touche un équipement déjà suivi : on
  propose "En cours" par défaut et on flague `[A CONFIRMER AVEC RICHARD]`, décision finale non
  tranchée.
- Lien mère/filles entre les lignes d'un même équipement à défauts multiples (INTERVENTION_MERE
  vers un NUMERO qui n'existe pas encore avant l'import) : mécanisme Corim inconnu à ce jour,
  question posée à Maxence/Richard, lignes actuellement indépendantes entre elles.
- CODE_NATT : toujours vide, "à voir avec Corim" (réponse Maxence), pas de mapping possible
  aujourd'hui.
- Nature technique "presse à balles" (MACH0535) : toujours en attente côté Richard.
- Nouveau schéma `myreport` demandé par Antho : contenu et compte propriétaire à préciser avant
  exécution (cf. règle "ne jamais deviner un besoin métier DB sans confirmation explicite").

## Bugs corrigés en cours de route (⚠️ gotchas)

- ⚠️ **Clé d'upsert DWH instable** : `batch_processor.py` déduisait `numero_rapport` depuis
  `interventions[0]["COMMENTAIRE_INTERNE"]`, un champ métier enrichi d'un flag variable selon le
  cas. La clé d'upsert changeait donc à chaque run, créant des doublons en base. Corrigé en
  exposant `numero_rapport` explicitement au niveau racine du dict retourné par le parseur.
- ⚠️ **Capture Observations qui déborde sur le pied de page** : quand la section se termine en
  fin de page PDF, le footer ("Version modèle rapport...") et l'en-tête de la page suivante
  restaient collés au texte capturé, générant de faux "défauts" au découpage. Ajout d'un repère
  de coupe supplémentaire.
- 🔍 **Découverte architecture** : l'export Corim natif de Maxence n'est pas le template
  d'import (colonnes en français, ex: "Code parc", "Sous-type de maintenance"), d'où un
  dictionnaire d'alias tolérant plusieurs noms de colonnes pour survivre aux futurs exports.

## Prochaine étape

Démo métier avec Maxence sur les 2 rapports tests (`RA59735423-009-1`, `RA55432737-017-1`),
recette du split multi-défauts et arbitrage Richard sur les points de dette ci-dessus.

## Addendum 2026-07-23 - Compte de service pour le partage réseau maintenance

Décision Antho : remplacer le dépôt manuel des PDF Apave par Richard (dossier
`IA Apave Corim/A traiter`) par une lecture automatique du partage réseau du service
maintenance, via un compte de service dédié. Même pattern que FUSEAU/Data-Achat :
compte AD `svc-data_achat`, créé par Samuel SELLIER (IT Réseau), mot de passe dans
`kv-dtpf-prod` (secret `svc-dataachat-ad-password`), lecture directe des fichiers
sources sur `\\192.168.102.55\partage\ADA\METIER\SUIVI CDES IMPORT\` sans copie
manuelle locale. Pour Apave/Corim : compte `svc-apave_corim` (ou équivalent) à
demander à Samuel, chemin UNC du partage maintenance encore à confirmer avec
Richard/Maxence. Dette ajoutée au plan d'action (voir CLAUDE.md).

## Addendum 2026-07-29 - Rangement, arborescence canonique TB Groupe

Le `src/` "flat" (6 fichiers au même niveau) est remplacé par l'arborescence
canonique des standards Antho : `src/core` (logique métier : parseur, mapping,
générateur Excel, loader DWH), `src/utils` (helpers transverses : extraction PDF),
`src/scripts` (points d'entrée exécutables : `batch_processor.py`, `app.py`),
`src/tests` (vide, prêt pour pytest). `config/` accueille les templates `.env`
(le vrai `.env`, relique pré-Key Vault jamais utilisée par le code depuis le
22/07, a été supprimé). `data/archives_streamlit/` remplace le dossier
`archives/` racine (backups locaux de l'interface Streamlit, distinct de
`IA Apave Corim/Traité, archive/` qui reste le dossier métier du batch).

Tous les imports (`from src.X import`) mis à jour vers `src.core.X`/`src.utils.X`.
Les chemins qui reposaient sur `os.getcwd()` (dossier `IA Apave Corim`, export
Corim, archive Streamlit) sont désormais dérivés de `__file__` : les scripts
fonctionnent quel que soit le répertoire de travail depuis lequel ils sont
lancés, plus seulement depuis la racine du projet. Vérifié par compilation de
tous les modules et exécution du pipeline complet (parse + mapping + génération
Excel) sur un rapport réel après réorganisation.

Junior Tip : un rangement de dossiers n'est jamais "juste du cosmétique" en
Python, chaque déplacement de fichier casse potentiellement un import ou un
chemin relatif calculé au runtime. D'où la vérification systématique (compile
+ exécution réelle) avant de committer, pas seulement un déplacement visuel.
