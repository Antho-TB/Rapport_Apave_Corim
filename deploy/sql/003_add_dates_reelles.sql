-- [MIGRATION] apave_corim.interventions_extraites - ajout datedeb_reel/datefin_reel
--
-- Contexte (22/07, suite relecture complete des annotations Maxence) : en ne
-- lisant que les 5 premieres lignes du modele annote au premier passage,
-- les colonnes DATEDEB_REEL et DATEFIN_REEL (jaune = reellement utilisees)
-- avaient ete manquees. Elles portent la date de verification Apave (meme
-- date en debut et fin reel, verification faite en une seule journee).
-- Idempotent, a executer sur dtpf_sylob_prod (meme compte que 001/002).

ALTER TABLE apave_corim.interventions_extraites
    ADD COLUMN IF NOT EXISTS datedeb_reel TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS datefin_reel TIMESTAMPTZ;
