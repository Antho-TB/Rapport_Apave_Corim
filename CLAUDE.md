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

## Structure
```
src/     # flat
├── ai_processor.py      # Extraction LLM (Gemini/Vertex AI) du PDF Apave vers JSON Corim
├── corim_mapping.py     # Alignement des numéros Corim (NUMERO/INTERV_ORIG) depuis l'export réel Maxence
├── pdf_extractor.py     # ⚠️ DOUBLON avec fiche_de_controle/src/pdf_extractor.py
└── excel_generator.py   # Génération Excel (format pivot d'import Corim)
app.py                   # Entry point Streamlit
batch_processor.py       # Traitement batch ("Dossier Magique")
```

## Credentials (.env)
`.env.template` créé le 22/07 (secrets réels en Azure Key Vault `kv-tb-ia-agents-secrets`,
ce fichier ne sert qu'en fallback local hors-ligne).
Variables : `GEMINI_PROJECT_ID`, `GEMINI_LOCATION`, `CORIM_EXPORT_PATH`.

## Logique métier Corim (mise à jour 22/07)
Le LLM ne renseigne JAMAIS `INTERVENTION_MERE`/`NUMERO`/`INTERV_ORIG` (impossible à
deviner depuis le PDF Apave, confirmé par le test du 28/04 où ces colonnes
ressortaient vides sur les 8 lignes générées). `src/corim_mapping.py` les recalcule
après coup à partir de l'export Corim réel de Maxence, selon la règle validée par
Richard/le support Corim (mi-juin) : colonnes A et BF mutuellement exclusives.
`CODE_NATT` et `CODEST_MAINT` restent volontairement vides tant que Richard n'a pas
confirmé les codes (nature technique / sous-type de maintenance propres à TB Groupe).

## ⚠️ Alertes actives
- `pdf_extractor.py` = doublon avec `fiche_de_controle/src/pdf_extractor.py`
  → Candidat extraction en lib partagée `tb_document_ai`
- `CODE_NATT`/`CODEST_MAINT` non fiabilisés : à valider avec Richard Berthet avant mise en prod
- Un fichier `.env` réel traîne encore à la racine (relique pré-migration Key Vault) : à supprimer manuellement, plus utilisé par le code
- Interlocuteur projet : m.houdelot@tb-groupe.fr (Maxence Houdelot), r.berthet@tb-groupe.fr (Richard Berthet, contact Corim)

## Standards TB Groupe
- Python 3.11, type hints partout
- Config centralisée via classe `Config`
- `logger = logging.getLogger(__name__)` — jamais `print()`
- Créer `.env.template` avec toutes les variables requises (valeurs vides)
- Fonctions max 50 lignes
