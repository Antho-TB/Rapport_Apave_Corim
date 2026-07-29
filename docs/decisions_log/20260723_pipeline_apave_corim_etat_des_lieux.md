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

## Addendum 2026-07-29 - Vérification pipeline suite mise à jour du 610 et annotations Antho

Revue croisée du modèle annoté (`610 - Modèle d'import interventions Corim.xlsx`, feuille
"Modèle") et d'une régénération réelle de l'Excel sur `RA55432737-017-1` (export test
`Export ITV tests pour Anthony.xlsx`, 5 équipements : MACH0535/0252/0376/0334/0337).

**2 bugs confirmés et corrigés :**

1. ⚠️ **Zero-padding APPE_HABIT** (`apave_parser._extraire_code_client`) : l'ancien code
   faisait `"MACH0" + repère brut`, correct uniquement si le repère fait déjà 3 chiffres
   (ex: "337" -> "MACH0337"). Pour un repère à 2 chiffres ("84", "74", "76"), ça produisait
   "MACH084" (3 chiffres) au lieu de "MACH0084" (4 chiffres, convention confirmée par
   l'export Corim réel, zero-paddé sur 4 partout). Corrigé par un zero-padding explicite
   (`f"MACH{int(...):04d}"`). Impact : ces équipements ne pouvaient JAMAIS matcher l'export
   Corim, même quand ils y étaient référencés, à cause du mauvais nombre de zéros.
2. ⚠️ **CODEST_MAINT jamais appliqué pour le cas PARTIEL** (`corim_mapping.enrich_...`) :
   le cas PARTIEL (nature technique non standard, ex: MACH0535 presse à balles) sautait
   tout l'enrichissement Corim (NUMERO/INTERV_ORIG/CODEST_MAINT/TYPE_MAINT), alors que
   l'équipement est bien présent dans l'export (MACH0535 -> Numéro 31357, sous-type
   REGLEMENTAIRE). Corrigé : seule la nature technique (CODE_NATT) reste un point manuel
   pour Richard, le reste (NUMERO/INTERV_ORIG/CODEST_MAINT/TYPE_MAINT) suit désormais le
   même enrichissement que les autres cas. CODEST_MAINT est aussi maintenant auto-appliqué
   partout (plus seulement loggé en candidat) : le modèle annoté valide en vert
   REGLEMENTAIRE et CORR SUITE CTRL, les 2 seules valeurs vues à ce jour dans un export réel.

**Déjà corrigé, pas encore revalidé par Maxence :** le split "une ITV = une ligne" (Q11 du
22/07) fonctionne bien sur le cas signalé (MACH0337, "Etat général des éléments..." /
"Mécanismes d'embrayage..." bien séparés en 2 lignes en relançant le pipeline actuel). La
feuille Google Sheets annotée montrait encore 1 seule ligne pour ce cas : elle a été générée
avant le commit `c15c1d0` (split multi-défauts), donc avant ce correctif. À refaire tourner
sur un export frais avant la prochaine revue avec Maxence pour éviter de rejuger un bug déjà
réglé.

**Déjà OK, confirmé par le test :** NUMERO (colonne B import) = Numéro (colonne A export)
pour les cas CLOTURE/NON_VERIFIE (MACH0252 -> 31356). INTERV_ORIG (BF) = Numéro export pour
les cas DEFAUT/PARTIEL (MACH0337 -> 31353, MACH0535 -> 31357 après correctif).

**Nouveau bug détecté, non corrigé (nécessite un chantier dédié) :** 🔍 sur MACH0074/MACH0076,
le texte extrait par pdfplumber mélange l'ordre de 2 colonnes du PDF (rapport en mise en page
tabulaire 2 colonnes, ex: "EQUIPEMENT HYDRAULIQUE" et "PNEUMATIQUE / AUTRES" sont deux
catégories différentes lues côte à côte, mais pdfplumber les linéarise entrelacées). Résultat :
`_decouper_defauts` sépare un fragment "FLUIDES." tout seul, qui n'est pas un défaut mais un
morceau d'en-tête de catégorie mal recollé par l'extraction. Le split au sens strict marche
comme prévu, le problème est en amont, dans l'ordre de lecture du texte brut. Pas de fix
tenté ici (nécessiterait une extraction par coordonnées/colonnes avec `pdfplumber.extract_words`,
plus un vrai changement d'approche) ; à traiter comme un chantier séparé si ce type de mise
en page est fréquent sur les rapports réels.

**Toujours en dette, non tranché (mère/fille)** : le modèle annoté (lignes 5 à 7 de la feuille
"Modèle") montre bien le pattern mère/fille demandé (STATUT mère = E, STATUT fille = A,
INTERV_ORIG fille = NUMERO mère), mais avec un NUMERO mère qui existe déjà côté Corim
(27931), pas un NUMERO généré dans le même import. Le pipeline actuel applique déjà cette
règle quand une intervention Corim existe dans l'export (INTERV_ORIG = numéro existant,
STATUT = A). Ce qui reste non résolu : le cas où AUCUNE intervention "mère" n'existe encore
côté Corim pour l'équipement au moment de l'import (visite Apave sur un équipement neuf ou
jamais suivi) — faut-il créer une ligne mère dans le même fichier (NUMERO vide, STATUT E) que
les lignes filles référenceraient, et Corim sait-il résoudre ce genre de référence intra-fichier
au moment de l'import ? Question à reposer explicitement à Richard/le support Corim, le modèle
annoté ne tranche que le cas "mère déjà existante".

## Addendum 2026-07-29 (bis) - Durcissement avant démo Maxence

Passage du pipeline sur les 3 rapports réels disponibles (et non plus 2) avant la démo
métier de 16h. Le 3e, `RA59215196-010-1` (LEVAGE 2026), n'avait jamais été testé : il
sort 33 interventions et révèle 3 cas limites de repère machine absents des rapports
MACHINES, tous silencieux jusqu'ici.

⚠️ **Codes équipement non conformes non détectés.** `APPE_HABIT` est obligatoire côté
Corim et suit la convention MACH + 4 chiffres. Sur ce rapport, trois blocs y échappent :
un repère à 5 chiffres ("Client 80005" -> `MACH80005`, 5 chiffres, hors convention) et
deux blocs sans aucun repère numérique ("Client ETIQUETAGE", "Client Bâtiment Service
Mezzanine") qui produisent un `APPE_HABIT` vide. Ces lignes partaient à l'import telles
quelles : soit rejet Corim, soit pire, rattachement au mauvais équipement. Décision : ne
JAMAIS deviner un code (pas de troncature du 5 chiffres, pas de code inventé pour les
blocs sans repère), mais marquer explicitement la ligne dans `COMMENTAIRE_INTERNE`
(`[CODE EQUIPEMENT NON CONFORME: ... - A CORRIGER MANUELLEMENT AVANT IMPORT]`), visible
directement dans l'Excel. Reprise manuelle assumée, sur une ligne signalée, plutôt qu'un
import faux et silencieux.

⚠️ **Bug attrapé dans le correctif lui-même, au test.** Le marquage ci-dessus était
écrasé pour les lignes DEFAUT : le flag `[STATUT ITV MERE PROPOSE: E]` posé juste après
faisait une réassignation de `commentaire_interne` au lieu d'une concaténation. Résultat
au premier test : 3 lignes non conformes, 2 seulement marquées, la ligne DEFAUT perdait
son flag en silence. Corrigé par accumulation.

Junior Tip : dès que deux règles indépendantes peuvent écrire dans le même champ (ici un
commentaire de suivi), il faut accumuler, jamais réassigner. Ce genre de bug ne se voit
pas à la lecture du diff (les deux blocs sont corrects pris séparément), uniquement en
comptant le résultat réel sur un jeu de données qui déclenche les deux règles à la fois.
D'où le contrôle automatique ajouté au test de non-régression : `nombre de lignes non
conformes == nombre de lignes marquées`, plutôt qu'une simple inspection visuelle.

**État vérifié avant démo** (compilation complète + exécution des 3 rapports, zéro
exception) : `A55432737-017-1` 20 ITV / 0 anomalie de code, `A59735423-009-1` 8 ITV /
0 anomalie, `A59215196-010-1` 33 ITV / 3 anomalies toutes marquées. Excel régénérés dans
`IA Apave Corim/A importer dans Corim/`.

**Volontairement PAS traité avant la démo** : le bug d'ordre de lecture pdfplumber sur
les mises en page à 2 colonnes (fragment "FLUIDES." sur MACH0074/0076, addendum
précédent). Le fix propre passe par une extraction par coordonnées
(`pdfplumber.extract_words` + clustering des mots par colonne sur `x0`), pas par un
moteur OCR type Tesseract : le PDF Apave est natif, sa couche texte est exacte, seul
l'ordre de lecture pose problème. Passer par de l'OCR reviendrait à rasteriser puis
re-reconnaître des caractères déjà connus, en ajoutant un risque de confusion de
caractères et d'accents, pour un gain nul sur la segmentation en colonnes. Chantier
reporté après la démo : il touche le coeur de l'extraction, donc à ne pas livrer à
quelques heures d'une présentation métier.
