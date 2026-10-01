-- =============================================================================
-- Schéma de la base de démonstration : courbes de charge dans TimescaleDB.
-- Le script peut être relancé sans erreur (IF NOT EXISTS / if_not_exists).
-- =============================================================================

-- TimescaleDB est une EXTENSION de PostgreSQL : il faut l'activer dans chaque
-- base qui l'utilise. Elle ajoute les hypertables, time_bucket, les agrégats
-- continus, la compression... Tout le reste reste du PostgreSQL standard.
CREATE EXTENSION IF NOT EXISTS timescaledb;


-- -----------------------------------------------------------------------------
-- 1. Les compteurs (table PostgreSQL classique)
-- -----------------------------------------------------------------------------
-- Peu de lignes, rarement modifiées : pas besoin d'hypertable ici.
CREATE TABLE IF NOT EXISTS meters (
    meter_id             TEXT PRIMARY KEY,  -- identifiant PRM du compteur
    name                 TEXT,              -- nom lisible du site
    subscribed_power_kva INT                -- puissance souscrite dans le contrat
);


-- -----------------------------------------------------------------------------
-- 2. Les mesures (la courbe de charge)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS load_curve (
    -- TIMESTAMPTZ = date + heure + fuseau. PostgreSQL stocke l'instant en UTC
    -- et le réaffiche dans le fuseau de la session : pas d'ambiguïté au
    -- changement d'heure (contrairement à TIMESTAMP sans fuseau).
    time      TIMESTAMPTZ      NOT NULL,
    -- REFERENCES meters : clé étrangère, on ne peut pas insérer une mesure
    -- pour un compteur qui n'existe pas dans la table meters.
    meter_id  TEXT             NOT NULL REFERENCES meters (meter_id),
    power_kw  DOUBLE PRECISION NOT NULL
);

-- On transforme la table en HYPERTABLE : vue de l'extérieur c'est toujours
-- une table normale (INSERT, SELECT...), mais TimescaleDB la découpe en
-- morceaux (chunks) de 7 jours de données. Une requête sur une semaine ne lit
-- alors qu'un ou deux chunks au lieu de toute la table.
-- 7 jours car c'est petit pour notre démo de 3 mois (≈ 13 chunks) ; en
-- production on choisit la taille pour qu'un chunk récent tienne en mémoire.
SELECT create_hypertable(
    'load_curve',
    by_range('time', INTERVAL '7 days'),
    if_not_exists => TRUE
);

-- Index pour la requête la plus fréquente : "les mesures d'UN compteur sur une
-- période", triées de la plus récente à la plus ancienne.
-- On le déclare UNIQUE : un compteur ne peut pas avoir deux mesures au même
-- instant. Cela permet aussi à load_to_db.py d'utiliser ON CONFLICT DO NOTHING
-- et donc d'être relancé sans créer de doublons.
CREATE UNIQUE INDEX IF NOT EXISTS load_curve_meter_time_idx
    ON load_curve (meter_id, time DESC);


-- -----------------------------------------------------------------------------
-- 3. Compression des données de plus de 30 jours
-- -----------------------------------------------------------------------------
-- Les vieilles mesures ne changent plus et sont lues en bloc (sommes par jour,
-- par mois) : on peut les compresser. TimescaleDB les range alors par colonne
-- (toutes les puissances ensemble, toutes les dates ensemble), ce qui prend
-- beaucoup moins de place. Les versions récentes de TimescaleDB appellent
-- cela le "columnstore" ; "compression" reste le nom historique.
-- - segmentby = 'meter_id' : les données de chaque compteur sont compressées
--   séparément, donc filtrer sur un compteur reste rapide.
-- - orderby = 'time DESC' : dans chaque segment, les mesures sont triées par
--   date, ce qui compresse mieux (valeurs voisines proches).
ALTER TABLE load_curve SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'meter_id',
    timescaledb.compress_orderby   = 'time DESC'
);

-- Tâche de fond : compresse automatiquement chaque chunk dès que toutes ses
-- données ont plus de 30 jours.
SELECT add_compression_policy('load_curve', INTERVAL '30 days', if_not_exists => TRUE);


-- -----------------------------------------------------------------------------
-- 4. Agrégat continu : consommation et pic par jour et par compteur
-- -----------------------------------------------------------------------------
-- Un agrégat continu est une vue matérialisée (résultat stocké sur disque)
-- que TimescaleDB met à jour au fil de l'eau, en ne recalculant que les
-- périodes où des données ont changé. Lire 1 ligne par jour au lieu de
-- 48 mesures par jour rend les tableaux de bord beaucoup plus rapides.
CREATE MATERIALIZED VIEW IF NOT EXISTS daily_consumption
WITH (timescaledb.continuous) AS
SELECT
    -- time_bucket découpe le temps en tranches d'un jour. Le 3e argument est
    -- important : sans lui, les journées seraient découpées en UTC (minuit UTC
    -- = 1h ou 2h à Paris). Avec 'Europe/Paris', une journée = minuit-minuit
    -- heure française, et le 31 mars ne compte bien que 23 h.
    time_bucket(INTERVAL '1 day', time, 'Europe/Paris') AS day,
    meter_id,
    -- Pas de 30 min : énergie (kWh) = puissance (kW) × 0,5 h.
    SUM(power_kw) * 0.5 AS energy_kwh,
    MAX(power_kw)       AS peak_kw,
    COUNT(*)            AS nb_points   -- 48 attendus (46 ou 50 au changement d'heure)
FROM load_curve
GROUP BY day, meter_id
-- WITH NO DATA : on crée la vue vide, le remplissage se fait après le chargement
-- (voir load_to_db.py). Sinon la création calculerait tout d'un coup.
WITH NO DATA;

-- Politique de rafraîchissement : toutes les heures, recalcule la fenêtre
-- [maintenant - 3 jours ; maintenant - 1 heure].
-- - start_offset 3 jours : rattrape les données arrivées en retard (Enedis
--   publie les courbes avec un décalage).
-- - end_offset 1 heure : laisse de côté l'heure en cours, encore incomplète.
-- Les données plus anciennes que la fenêtre ne sont pas recalculées
-- automatiquement : c'est pour ça que load_to_db.py fait un rafraîchissement
-- manuel après avoir chargé un historique.
SELECT add_continuous_aggregate_policy(
    'daily_consumption',
    start_offset      => INTERVAL '3 days',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists     => TRUE
);
