"""
Charge load_curve.csv dans la base TimescaleDB.

Choix de la bibliothèque : psycopg (version 3) plutôt que SQLAlchemy.
- On écrit le SQL nous-mêmes : on voit exactement ce qui est envoyé à la base,
  c'est plus formateur et plus simple à déboguer.
- psycopg donne accès à COPY, la commande d'import en masse de PostgreSQL,
  bien plus rapide que des milliers d'INSERT. (Avec SQLAlchemy, on ferait
  df.to_sql(...) : une seule ligne, mais plus lent et sans gestion des doublons.)
"""

from analyse import charger_donnees
from db import connect

# Le CSV ne contient que les mesures : les informations "contrat" des compteurs
# (nom du site, puissance souscrite) sont décrites ici. Valeurs fictives.
METERS = [
    # (meter_id,  name,                    subscribed_power_kva)
    ("PRM_001", "Bureau - démo",          108),
    ("PRM_002", "Atelier/commerce - démo", 96),
]


def inserer_compteurs(cur):
    # ON CONFLICT ... DO UPDATE ("upsert") : si le compteur existe déjà, on met
    # à jour son nom et sa puissance au lieu de planter. Le script peut donc
    # être relancé autant de fois qu'on veut.
    # %s sont des paramètres : psycopg les transmet séparément de la requête,
    # ce qui évite les injections SQL (ne jamais construire le SQL avec des f-strings).
    cur.executemany(
        """
        INSERT INTO meters (meter_id, name, subscribed_power_kva)
        VALUES (%s, %s, %s)
        ON CONFLICT (meter_id) DO UPDATE
            SET name = EXCLUDED.name,
                subscribed_power_kva = EXCLUDED.subscribed_power_kva
        """,
        METERS,
    )


def inserer_mesures(cur, df):
    """Insère les mesures en masse ; renvoie le nombre de lignes réellement ajoutées."""
    # Étape 1 : une table temporaire (elle disparaît à la fin de la session).
    # COPY est très rapide mais ne sait pas ignorer les doublons ; on copie donc
    # d'abord dans cette table intermédiaire ("staging"), sans contrainte.
    cur.execute("""
        CREATE TEMP TABLE staging_load_curve
            (LIKE load_curve INCLUDING DEFAULTS) ON COMMIT DROP
    """)

    # Étape 2 : COPY ... FROM STDIN envoie toutes les lignes en un seul flux,
    # au lieu d'un aller-retour réseau par ligne comme avec INSERT.
    with cur.copy("COPY staging_load_curve (time, meter_id, power_kw) FROM STDIN") as copy:
        # itertuples parcourt le DataFrame ligne par ligne (plus rapide qu'iterrows).
        # Les dates pandas sont "conscientes" du fuseau : psycopg les convertit
        # correctement en TIMESTAMPTZ.
        for ligne in df[["time", "meter_id", "power_kw"]].itertuples(index=False):
            copy.write_row(ligne)

    # Étape 3 : on transfère vers la vraie table. ON CONFLICT DO NOTHING ignore
    # les mesures déjà présentes (grâce à l'index unique (meter_id, time)).
    cur.execute("""
        INSERT INTO load_curve (time, meter_id, power_kw)
        SELECT time, meter_id, power_kw FROM staging_load_curve
        ON CONFLICT DO NOTHING
    """)
    # rowcount = nombre de lignes réellement insérées (hors doublons ignorés).
    return cur.rowcount


def rafraichir_et_compresser():
    # Ces deux procédures TimescaleDB gèrent elles-mêmes leurs transactions :
    # elles doivent être appelées en mode autocommit.
    with connect(autocommit=True) as conn:
        # La politique de rafraîchissement automatique ne recalcule que les
        # 3 derniers jours. Nos données datent de 2024 : il faut donc remplir
        # l'agrégat continu à la main (NULL, NULL = toute la période).
        conn.execute("CALL refresh_continuous_aggregate('daily_consumption', NULL, NULL)")

        # La compression tourne normalement en tâche de fond, à son rythme.
        # Pour la démo, on lance la tâche tout de suite afin de voir le résultat.
        job = conn.execute("""
            SELECT job_id FROM timescaledb_information.jobs
            WHERE hypertable_name = 'load_curve'
              AND proc_name = 'policy_compression'
        """).fetchone()
        if job:
            conn.execute("CALL run_job(%s)", (job[0],))


def main():
    df = charger_donnees("load_curve.csv")
    print(f"CSV lu : {len(df)} lignes, {df['meter_id'].nunique()} compteurs.")

    # "with connect() as conn" : à la sortie du bloc, la transaction est validée
    # (commit) si tout s'est bien passé, annulée (rollback) en cas d'erreur, et la
    # connexion est fermée. On ne se retrouve jamais avec une moitié de données.
    with connect() as conn, conn.cursor() as cur:
        inserer_compteurs(cur)
        print(f"Compteurs insérés ou mis à jour : {len(METERS)}")
        nb_insertions = inserer_mesures(cur, df)
        print(f"Mesures insérées : {nb_insertions} "
              f"(déjà présentes et ignorées : {len(df) - nb_insertions})")

    rafraichir_et_compresser()

    # Petit bilan lu directement dans la base.
    with connect() as conn:
        total = conn.execute("SELECT count(*) FROM load_curve").fetchone()[0]
        nb_jours = conn.execute("SELECT count(*) FROM daily_consumption").fetchone()[0]
        chunks = conn.execute("""
            SELECT count(*), count(*) FILTER (WHERE is_compressed)
            FROM timescaledb_information.chunks
            WHERE hypertable_name = 'load_curve'
        """).fetchone()
    print(f"\nDans la base : {total} mesures, {nb_jours} lignes dans daily_consumption, "
          f"{chunks[0]} chunks dont {chunks[1]} compressés.")


if __name__ == "__main__":
    main()
