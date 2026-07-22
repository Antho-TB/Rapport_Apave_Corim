-- [MIGRATION] apave_corim.interventions_extraites - ajout compte_rendu
--
-- Contexte (22/07, correction "Fais maintenant, indispensable") : le modele
-- d'import Corim annote par Maxence (610 - Modele d'import interventions
-- Corim.xlsx) montre que le detail de l'anomalie/cloture va dans la colonne
-- COMPTE_RENDU, pas DEMANDE (colonne inutilisee en pratique). Le schema
-- 001 avait ete cree AVANT cette decouverte et n'a qu'une colonne demande.
-- On garde demande (compat, restera vide en pratique) et on ajoute
-- compte_rendu. Idempotent, a executer sur dtpf_sylob_prod (meme compte
-- dtpf_sylob_anthony_bezille_prod que 001).

ALTER TABLE apave_corim.interventions_extraites
    ADD COLUMN IF NOT EXISTS compte_rendu TEXT;
