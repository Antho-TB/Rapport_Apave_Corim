# Rapport_Apave_Corim

Pipeline de conformité TB Groupe : transforme les rapports de vérification périodique
Apave (PDF) en fichiers Excel prêts à importer dans Corim (GMAO du service maintenance).

## Pourquoi ce projet existe

Richard Berthet (support Corim) recevait un rapport Apave par équipement vérifié
(porte, presse, levage...) et devait ressaisir à la main chaque défaut, clôture ou
équipement non vérifié dans Corim. Ce pipeline automatise cette ressaisie tout en
laissant la décision finale au métier : rien n'est importé directement dans Corim,
le fichier Excel généré reste soumis à vérification humaine avant import.

## Comment ça marche (vue d'ensemble)

```
PDF Apave (dossier "A traiter")
        v
Extraction déterministe (regex/positionnel, src/apave_parser.py)
        v  [repli si format non reconnu]
Extraction LLM Gemini (src/ai_processor.py)
        v
Alignement avec l'export Corim réel de Maxence (src/corim_mapping.py)
        v
Génération Excel format import Corim (src/excel_generator.py)
        v
Excel dans "A importer dans Corim"  +  trace optionnelle dans le DWH (best-effort)
        v
PDF source archivé automatiquement
```

**Pourquoi pas 100% LLM ?** Les rapports Apave utilisent tous le même moteur de
template (LearaBIP), avec des phrases identiques au mot près d'un rapport à l'autre.
Un LLM a halluciné les numéros d'intervention Corim lors des premiers tests (ces
numéros n'existent nulle part dans le PDF, impossible à deviner). L'extraction
déterministe élimine ce risque sur le cas nominal ; Gemini ne sert plus que de
filet de sécurité si Apave change un jour de format de rapport. Détail de cette
décision : `docs/decisions_log/20260723_pipeline_apave_corim_etat_des_lieux.md`.

## Structure du repo

```
src/
├── apave_parser.py      # Extraction déterministe (regex) du PDF Apave, chemin principal
├── ai_processor.py      # Extraction LLM (Gemini/Vertex AI), repli si format PDF inconnu
├── corim_mapping.py     # Résout NUMERO/INTERV_ORIG/TYPE_MAINT depuis l'export Corim réel
├── dwh_loader.py         # Écriture optionnelle vers apave_corim.* (dtpf_sylob_prod)
├── pdf_extractor.py     # Extraction texte brut du PDF (pdfplumber)
└── excel_generator.py   # Génération Excel (format pivot d'import Corim, 61 colonnes)
app.py                   # Interface Streamlit (dépôt manuel d'un PDF, aperçu, téléchargement)
batch_processor.py       # Orchestrateur batch "Dossier Magique" (traitement par lot)
deploy/sql/              # DDL + migrations du schéma apave_corim (dtpf_sylob_prod)
docs/decisions_log/      # ADR datés : pourquoi chaque décision technique a été prise
IA Apave Corim/           # Dossiers métier : A traiter, A importer dans Corim, archives
```

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Copier `.env.template` vers `.env` et renseigner les variables (Key Vault en
production, `.env` local en repli uniquement, jamais de secret en dur) :

| Variable | Rôle |
|---|---|
| `GEMINI_PROJECT_ID`, `GEMINI_LOCATION` | Repli Gemini (Key Vault `kv-tb-ia-agents-secrets`) |
| `CORIM_EXPORT_PATH` | Chemin de l'export Corim fourni par Maxence (`.xlsx`) |
| `DWH_KEY_VAULT_NAME`, `PG_HOST`, `PG_PORT`, `PG_DB` | Persistance DWH (Key Vault `kv-dtpf-prod`) |
| `PG_USER`, `PG_PASSWORD` | Repli local uniquement (jamais renseigné en prod) |

## Usage

**Traitement par lot (production)** : déposer les PDF dans
`IA Apave Corim/A traiter/`, puis :

```bash
python batch_processor.py
```

Génère un Excel par PDF dans `A importer dans Corim/`, écrit dans le DWH si le VPN
Stormshield est actif (best-effort, ne bloque jamais l'Excel), archive le PDF
source dans `Traité, archive/<année>/`.

**Traitement manuel (ponctuel, démo)** :

```bash
streamlit run app.py
```

## Statut et points ouverts

Voir `CLAUDE.md` (section Plan d'action) pour la liste à jour des décisions en
attente : arbitrage STATUT ambigu, lien mère/filles entre ITV, nature technique
des équipements atypiques, et le passage prévu d'un dépôt manuel des PDF à un
accès direct au partage réseau du service maintenance via compte de service dédié.

## Contacts métier

- Maxence Houdelot (m.houdelot@tb-groupe.fr) : export Corim, données de mapping.
- Richard Berthet (r.berthet@tb-groupe.fr) : support Corim, arbitrage des cas ambigus.
