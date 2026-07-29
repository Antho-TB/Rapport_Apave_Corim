# Rapport_Apave_Corim — Contexte Claude

## Rôle
Conformité / Document AI — traitement automatisé des rapports Apave et Corim.
Pipeline : PDF input → extraction structurée → génération Excel → interface Streamlit.

## Statut
**Actif**

## Stack
- Python 3.11 · Streamlit · PDF extraction · Excel generation (openpyxl)
- Traitement batch (batch_processor.py)
- Pas d'Azure Function — exécution locale ou VM
- Extraction déterministe (regex/positionnel) en chemin principal, Gemini/Vertex AI
  en repli uniquement si le format PDF n'est pas reconnu (décision du 22/07, voir plus bas)
- Persistance DWH optionnelle (schéma `apave_corim` sur `dtpf_sylob_prod`), en plus de
  l'Excel, pas à la place (voir `src/core/dwh_loader.py`)

## Structure

**Rangement du 29/07** : arborescence canonique TB Groupe appliquée (`src/core`,
`src/utils`, `src/scripts`, `config/`, `data/`), l'ancien `src/` "flat" est terminé.
Tous les imports (`from src.X import`) et chemins dérivés de `__file__` ont été
mis à jour en conséquence, testés (compile + exécution pipeline complète).

```
src/
├── core/                  # Logique métier du pipeline
│   ├── apave_parser.py      # Extraction déterministe (regex) du PDF Apave, chemin principal
│   ├── ai_processor.py      # Extraction LLM (Gemini/Vertex AI), repli si format PDF inconnu
│   ├── corim_mapping.py     # Alignement des numéros Corim (NUMERO/INTERV_ORIG/TYPE_MAINT) depuis l'export réel Maxence
│   ├── dwh_loader.py        # Écriture optionnelle vers apave_corim.* (dtpf_sylob_prod)
│   └── excel_generator.py   # Génération Excel (format pivot d'import Corim)
├── utils/
│   └── pdf_extractor.py     # ⚠️ DOUBLON avec fiche_de_controle/src/pdf_extractor.py
├── scripts/                # Points d'entrée exécutables
│   ├── batch_processor.py    # Traitement batch ("Dossier Magique")
│   └── app.py                 # Entry point Streamlit
└── tests/                  # pytest (à peupler)
config/                    # .env.template, .env.example (le vrai .env n'est jamais commité)
data/archives_streamlit/   # Backups locaux des PDF traités via l'interface Streamlit
deploy/sql/                 # DDL + migrations du schéma apave_corim (VPN requis pour exécuter)
docs/decisions_log/         # ADR datés
```

## Credentials (config/.env)
`config/.env.template` créé le 22/07, mis à jour le même jour avec les variables DWH,
déplacé dans `config/` lors du rangement du 29/07 (secrets réels en Azure Key Vault :
`kv-tb-ia-agents-secrets` pour Gemini, `kv-dtpf-prod` pour PostgreSQL — DEUX Key Vault
différents, ne pas confondre). Variables : `GEMINI_PROJECT_ID`, `GEMINI_LOCATION`,
`CORIM_EXPORT_PATH`, `DWH_KEY_VAULT_NAME`, `PG_HOST`, `PG_PORT`, `PG_DB`, `PG_USER`,
`PG_PASSWORD`.

## Décision architecture (22/07) : abandon du tout-LLM pour l'extraction
Les rapports Apave utilisent tous le même moteur de template (LearaBIP) avec des
phrases pivots identiques au mot près (vérifié sur 3 rapports de nature différente).
Un LLM n'apporte donc pas de valeur sur un format aussi stable, et introduit un
risque (hallucination, déjà rencontré), un coût, et une dépendance Key Vault/GCP
évitables. `src/core/apave_parser.py` fait l'extraction par regex/position, testée à
100% de conformité sur les 2 rapports réels disponibles (22 machines, 4 cas :
DEFAUT/CLOTURE/NON_VERIFIE/PARTIEL). `ai_processor.py` (Gemini) reste en repli
uniquement si `RapportFormatInconnu` est levée (ex: nouvelle version de template).

## Persistance DWH (créé le 22/07, en prod)
Schéma `apave_corim` créé sur `dtpf_sylob_prod` (DDL dans `deploy/sql/`, 4 migrations
appliquées à date), avec deux tables : `rapports_traites` (audit par PDF) et
`interventions_extraites` (historique interrogeable, remplace l'Excel comme source
de vérité). Le loader utilise le compte dédié `dtpf_sylob_anthony_bezille_prod`
(secrets `psql-prod-sylob-anthony-bezille-login/-password` dans `kv-dtpf-prod`),
PAS le compte partagé `myreport` : même principe d'isolation que sur FUSEAU/Data-Achat
(ADR ERP Achat du 10/06), un pipeline tiers ne doit jamais réutiliser un compte de
service d'un autre projet. Écriture DWH toujours best-effort (n'interrompt jamais
la génération Excel si le VPN/schéma n'est pas disponible).

## Logique métier Corim (mise à jour 22/07)
Le LLM ne renseigne JAMAIS `INTERVENTION_MERE`/`NUMERO`/`INTERV_ORIG` (impossible à
deviner depuis le PDF Apave, confirmé par le test du 28/04 où ces colonnes
ressortaient vides sur les 8 lignes générées). `src/core/corim_mapping.py` les recalcule
après coup à partir de l'export Corim réel de Maxence, selon la règle validée par
Richard/le support Corim (mi-juin) : colonnes A et BF mutuellement exclusives.
`CODE_NATT` et `CODEST_MAINT` restent volontairement vides tant que Richard n'a pas
confirmé les codes (nature technique / sous-type de maintenance propres à TB Groupe).

## ⚠️ Alertes actives
- `src/utils/pdf_extractor.py` = doublon avec `fiche_de_controle/src/pdf_extractor.py`
  → Candidat extraction en lib partagée `tb_document_ai`
- `CODE_NATT`/`CODEST_MAINT` non fiabilisés, **confirmé volontairement vide pour le moment** (réponse Antho du 22/07) : pas d'enrichissement de l'export prévu à court terme, à revalider avec Richard Berthet quand la nature technique "presse à balles" (cas MACH0535) sera créée dans Corim.
- Interlocuteur projet : m.houdelot@tb-groupe.fr (Maxence Houdelot), r.berthet@tb-groupe.fr (Richard Berthet, contact Corim)

## Workflow confirmé (Antho, 22/07)
- **Export Corim** : régénéré manuellement par Maxence à chaque lot. Pas d'API ni de connexion SQL directe pour l'instant, sujet en discussion côté direction (à suivre, potentiel gain futur pour supprimer cette étape manuelle).
- **Dépôt des PDF Apave** : manuel, au fil de l'eau, fait par Richard Berthet dans `IA Apave Corim/A traiter`. Architecture "Dossier Magique" de `batch_processor.py` confirmée comme bonne cible. **Révisé le 23/07 (voir Plan d'action) : cible désormais un scan réseau automatisé via compte de service, le dépôt manuel devenant une étape intermédiaire à supprimer.**
- **DEMANDE (Corim)** : texte libre accepté côté Corim, pas de contrainte de format à gérer dans `ai_processor.py`.
- **Nature technique équipements atypiques (ex: presse à balles MACH0535)** : statut à reconfirmer avec Richard, pas encore tranché au 22/07.

## Plan d'action

- **[23/07] Compte de service dédié pour le partage réseau maintenance** (Antho) :
  remplacer le dépôt manuel des PDF Apave par Richard par un accès direct de l'ETL
  au dossier du serveur de fichiers du service maintenance, via un compte de service
  AD dédié, même pattern que `svc-data_achat` sur FUSEAU/Data-Achat (compte `svc-`
  créé par Samuel SELLIER, IT Réseau, mot de passe stocké dans `kv-dtpf-prod` sous
  un secret `svc-<projet>-ad-password`, jamais de credentials en dur). Reprend le
  rôle aujourd'hui tenu par `batch_processor.py`/dossier `A traiter`, qui devient
  alors une lecture réseau directe plutôt qu'un dossier local alimenté à la main.
  **Action à faire** : demander la création du compte à Samuel SELLIER, obtenir le
  chemin UNC exact du partage réseau maintenance (à confirmer avec Richard/Maxence,
  inconnu à ce jour), documenter le résultat ici une fois le compte opérationnel.

## Standards TB Groupe
- Python 3.11, type hints partout
- Config centralisée via classe `Config`
- `logger = logging.getLogger(__name__)` — jamais `print()`
- Créer `.env.template` avec toutes les variables requises (valeurs vides)
- Fonctions max 50 lignes
