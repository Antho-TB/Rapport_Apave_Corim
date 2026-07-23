-- [MIGRATION] apave_corim.interventions_extraites - ajout datedeb_prevu/datefin_prevu
--
-- Contexte (22/07, reponses Maxence au questionnaire, Q7) : DATEDEB_PREVU et
-- DATEFIN_PREVU portent la date de couverture du rapport Apave (premiere
-- page, "Date : JJ/MM/AAAA"), distincte de DATEDEB_REEL/DATEFIN_REEL (date de
-- verification par equipement, migration 003). Idempotent, meme compte que
-- les migrations precedentes.

ALTER TABLE apave_corim.interventions_extraites
    ADD COLUMN IF NOT EXISTS datedeb_prevu TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS datefin_prevu TIMESTAMPTZ;
