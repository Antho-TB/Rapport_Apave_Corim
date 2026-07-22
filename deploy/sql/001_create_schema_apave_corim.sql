-- [SCHEMA] apave_corim - Rapport_Apave_Corim
--
-- Role global :
-- Remplace le fichier Excel comme source de verite pour l'historique des
-- interventions generees depuis les rapports Apave. Suit le pattern
-- land-raw + profiling-first deja utilise pour l'ingestion Colibri
-- (cf. decisions_log/20260624_appro_ingestion_colibri.md).
--
-- A executer manuellement (psql ou DBeaver) sur dtpf_sylob_prod, VPN Stormshield
-- actif, avec le compte dtpf_sylob_anthony_bezille_prod (pas myreport : meme
-- principe d'isolation que achat.* et appro_raw sur Data-Achat/FUSEAU, cf. ADR
-- ERP Achat du 10/06). Schema bootstrap hors Terraform, comme achat et appro_raw
-- (ADR Colibri du 24/06). Idempotent : peut etre rejoue sans casser l'existant
-- (IF NOT EXISTS).

CREATE SCHEMA IF NOT EXISTS apave_corim;

-- Une ligne par PDF traite : trace d'audit, permet de savoir si un rapport a
-- deja ete ingere (evite les doublons si un PDF est redepose par erreur) et
-- quelle methode d'extraction a ete utilisee (deterministe ou repli LLM).
CREATE TABLE IF NOT EXISTS apave_corim.rapports_traites (
    id                  BIGSERIAL PRIMARY KEY,
    numero_rapport      TEXT NOT NULL,
    nom_fichier_pdf     TEXT NOT NULL,
    methode_extraction  TEXT NOT NULL CHECK (methode_extraction IN ('DETERMINISTE', 'LLM_GEMINI')),
    nb_interventions    INTEGER NOT NULL DEFAULT 0,
    statut              TEXT NOT NULL CHECK (statut IN ('SUCCES', 'ECHEC')),
    message_erreur      TEXT,
    traite_le           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (numero_rapport, nom_fichier_pdf)
);

-- Une ligne par intervention generee, alignee sur le schema d'import Corim.
-- Remplace le tableur Excel volatil : ici l'historique est interrogeable
-- (ex: "quel PDF a produit telle ligne", "quelles lignes restent en cas
-- particulier a traiter avec Richard").
CREATE TABLE IF NOT EXISTS apave_corim.interventions_extraites (
    id                  BIGSERIAL PRIMARY KEY,
    rapport_id          BIGINT NOT NULL REFERENCES apave_corim.rapports_traites(id) ON DELETE CASCADE,
    appe_habit          TEXT NOT NULL,
    cas_pdf             TEXT NOT NULL CHECK (cas_pdf IN ('DEFAUT', 'CLOTURE', 'NON_VERIFIE', 'PARTIEL')),
    libe_inter          TEXT NOT NULL,
    demande             TEXT,
    compte_rendu        TEXT,
    statut              TEXT NOT NULL,
    type_maint          TEXT NOT NULL,
    datedeb_reel        TIMESTAMPTZ,
    datefin_reel        TIMESTAMPTZ,
    intervention_mere   TEXT,
    numero              TEXT,
    interv_orig         TEXT,
    code_natt           TEXT,
    codest_maint        TEXT,
    a_traiter_manuellement  BOOLEAN NOT NULL DEFAULT false,
    importe_dans_corim  BOOLEAN NOT NULL DEFAULT false,
    cree_le             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_interventions_appe_habit ON apave_corim.interventions_extraites (appe_habit);
CREATE INDEX IF NOT EXISTS idx_interventions_a_traiter ON apave_corim.interventions_extraites (a_traiter_manuellement) WHERE a_traiter_manuellement = true;
