"""
Requêtes d'analyse sur la base TimescaleDB, comparées au calcul pandas.
"""

import statistics
import time

import numpy as np
import pandas as pd

from analyse import charger_donnees, energie_par_jour
from db import connect

COMPTEUR = "PRM_001"

# Seuil choisi pour la démo (pas une règle officielle) : si le pic n'atteint
# même pas 70 % de la puissance souscrite, l'abonnement est probablement trop gros.
SEUIL_SURDIMENSIONNEMENT = 0.70


def requete(conn, sql, params=None):
    """Exécute une requête et renvoie le résultat sous forme de DataFrame pandas."""
    cur = conn.execute(sql, params)
    # cur.description contient le nom des colonnes renvoyées par la requête.
    colonnes = [col.name for col in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=colonnes)


def titre(texte):
    print(f"\n{'=' * 70}\n{texte}\n{'=' * 70}")


# -----------------------------------------------------------------------------
# 1. Consommation par jour, lue dans l'agrégat continu
# -----------------------------------------------------------------------------
def conso_par_jour(conn, meter_id):
    # daily_consumption contient déjà une ligne par jour et par compteur :
    # la base n'a rien à recalculer, elle lit seulement ~91 lignes.
    return requete(conn, """
        SELECT day, energy_kwh, peak_kw, nb_points
        FROM daily_consumption
        WHERE meter_id = %s
        ORDER BY day
    """, (meter_id,))


# -----------------------------------------------------------------------------
# 2. Consommation par mois avec time_bucket
# -----------------------------------------------------------------------------
def conso_par_mois(conn):
    # time_bucket('1 month', ...) range chaque mesure dans le mois auquel elle
    # appartient (comme resample("MS") en pandas). Le fuseau 'Europe/Paris'
    # fait commencer le mois à minuit heure française, pas à minuit UTC.
    return requete(conn, """
        SELECT time_bucket(INTERVAL '1 month', time, 'Europe/Paris') AS month,
               meter_id,
               ROUND((SUM(power_kw) * 0.5)::numeric, 1) AS energy_kwh
        FROM load_curve
        GROUP BY month, meter_id
        ORDER BY month, meter_id
    """)


# -----------------------------------------------------------------------------
# 3. Pic sur 12 mois comparé à la puissance souscrite
# -----------------------------------------------------------------------------
def pics_vs_puissance_souscrite(conn):
    # "12 mois" = les 12 mois précédant la DERNIÈRE mesure en base, et non
    # now() : nos données datent de 2024, now() - 12 mois ne trouverait rien.
    #
    # DISTINCT ON (meter_id) est propre à PostgreSQL : garde la PREMIÈRE ligne
    # de chaque compteur selon l'ORDER BY. En triant par puissance décroissante,
    # cette première ligne est le pic, avec sa date : l'équivalent de idxmax.
    #
    # On compare des kW à des kVA en supposant un cos φ proche de 1
    # (simplification de démo : en réalité kW = kVA × cos φ).
    return requete(conn, """
        WITH fenetre AS (
            SELECT MAX(time) - INTERVAL '12 months' AS debut FROM load_curve
        ),
        pics AS (
            SELECT DISTINCT ON (meter_id)
                   meter_id, power_kw AS peak_kw, time AS peak_time
            FROM load_curve, fenetre
            WHERE load_curve.time > fenetre.debut
            ORDER BY meter_id, power_kw DESC, time
        )
        SELECT p.meter_id, m.name, p.peak_kw, p.peak_time, m.subscribed_power_kva
        FROM pics p
        JOIN meters m USING (meter_id)
        ORDER BY p.meter_id
    """)


def verdict(peak_kw, souscrite_kva):
    ratio = peak_kw / souscrite_kva
    if ratio > 1:
        return f"DÉPASSEMENT ({ratio:.0%}) : puissance souscrite trop basse"
    if ratio < SEUIL_SURDIMENSIONNEMENT:
        return f"pic à {ratio:.0%} de la souscription : puissance souscrite probablement TROP HAUTE"
    return f"pic à {ratio:.0%} de la souscription : puissance souscrite adaptée"


# -----------------------------------------------------------------------------
# 4. Même calcul en pandas et en SQL
# -----------------------------------------------------------------------------
def comparer_pandas_sql(conn):
    # Côté pandas : on refait le calcul depuis le CSV, avec analyse.py.
    df = charger_donnees("load_curve.csv")
    pandas_kwh = energie_par_jour(df).rename("kwh_pandas").reset_index()
    pandas_pic = (df.set_index("time").groupby("meter_id")["power_kw"]
                    .resample("D").max().rename("pic_pandas").reset_index())
    cote_pandas = pandas_kwh.merge(pandas_pic, on=["meter_id", "time"])

    # Côté SQL : l'agrégat continu, qui fait le même calcul dans la base.
    cote_sql = requete(conn, """
        SELECT meter_id, day AS time, energy_kwh AS kwh_sql, peak_kw AS pic_sql
        FROM daily_consumption
    """)
    # Même type de date des deux côtés pour pouvoir faire la jointure.
    cote_sql["time"] = pd.to_datetime(cote_sql["time"], utc=True).dt.tz_convert("Europe/Paris")
    cote_pandas["time"] = cote_pandas["time"].dt.tz_convert("Europe/Paris").astype(cote_sql["time"].dtype)

    # how="outer" garde aussi les jours présents d'un seul côté : un jour
    # manquant serait une différence, il ne faut pas le cacher.
    comp = cote_pandas.merge(cote_sql, on=["meter_id", "time"], how="outer")

    # np.isclose et pas == : les sommes de nombres décimaux peuvent différer à la
    # 12e décimale selon l'ordre des additions (arrondis des flottants).
    kwh_ok = np.isclose(comp["kwh_pandas"], comp["kwh_sql"], rtol=1e-9)
    pic_ok = np.isclose(comp["pic_pandas"], comp["pic_sql"], rtol=1e-9)

    print(f"Jours comparés : {len(comp)} (pandas : {len(cote_pandas)}, SQL : {len(cote_sql)})")
    print(f"Énergie journalière identique : {kwh_ok.sum()}/{len(comp)}")
    print(f"Pic journalier identique      : {pic_ok.sum()}/{len(comp)}")
    print(f"Écart maximal sur l'énergie   : "
          f"{(comp['kwh_pandas'] - comp['kwh_sql']).abs().max():.2e} kWh")
    if kwh_ok.all() and pic_ok.all():
        print("=> Les deux méthodes donnent les mêmes résultats.")
    else:
        print("=> DIFFÉRENCES détectées :")
        print(comp[~(kwh_ok & pic_ok)])


# -----------------------------------------------------------------------------
# 5. Temps d'exécution : table brute vs agrégat continu
# -----------------------------------------------------------------------------
def chronometrer(conn, sql, params, repetitions=30):
    """Médiane du temps d'exécution en millisecondes."""
    conn.execute(sql, params).fetchall()  # 1er appel "à blanc" : met les données en cache
    durees = []
    for _ in range(repetitions):
        debut = time.perf_counter()  # horloge précise, faite pour mesurer des durées
        conn.execute(sql, params).fetchall()
        durees.append((time.perf_counter() - debut) * 1000)
    # La médiane est moins sensible qu'une moyenne à un appel ralenti par hasard.
    return statistics.median(durees)


def comparer_temps(conn, meter_id):
    # La même question ("kWh et pic par jour pour un compteur") posée deux fois.
    sur_table_brute = """
        SELECT time_bucket(INTERVAL '1 day', time, 'Europe/Paris') AS day,
               SUM(power_kw) * 0.5 AS energy_kwh, MAX(power_kw) AS peak_kw
        FROM load_curve
        WHERE meter_id = %s
        GROUP BY day ORDER BY day
    """
    sur_agregat = """
        SELECT day, energy_kwh, peak_kw
        FROM daily_consumption
        WHERE meter_id = %s
        ORDER BY day
    """
    t_brut = chronometrer(conn, sur_table_brute, (meter_id,))
    t_agr = chronometrer(conn, sur_agregat, (meter_id,))
    nb_brut = conn.execute("SELECT count(*) FROM load_curve WHERE meter_id = %s",
                           (meter_id,)).fetchone()[0]
    nb_agr = conn.execute("SELECT count(*) FROM daily_consumption WHERE meter_id = %s",
                          (meter_id,)).fetchone()[0]

    print(f"Table brute load_curve   : {t_brut:6.2f} ms  ({nb_brut} mesures lues et agrégées)")
    print(f"Agrégat daily_consumption: {t_agr:6.2f} ms  ({nb_agr} lignes déjà calculées)")
    print(f"=> l'agrégat est {t_brut / t_agr:.1f} fois plus rapide (médiane de 30 exécutions)")


def main():
    with connect() as conn:
        titre(f"1. Consommation par jour de {COMPTEUR} (agrégat continu daily_consumption)")
        jours = conso_par_jour(conn, COMPTEUR)
        print(jours.head(8).round({"energy_kwh": 2, "peak_kw": 2}).to_string(index=False))
        print(f"... {len(jours)} jours au total. "
              f"Remarquez le 31/03 : {jours['nb_points'].iloc[-1]} points (changement d'heure).")

        titre("2. Consommation par mois (time_bucket sur la table brute)")
        mois = conso_par_mois(conn)
        mois["month"] = mois["month"].map(lambda d: d.strftime("%Y-%m"))
        print(mois.pivot(index="month", columns="meter_id", values="energy_kwh").to_string())

        titre("3. Pic de puissance sur 12 mois vs puissance souscrite")
        for p in pics_vs_puissance_souscrite(conn).itertuples():
            print(f"{p.meter_id} ({p.name}) : pic {p.peak_kw:.2f} kW le "
                  f"{p.peak_time:%d/%m/%Y à %Hh%M}, souscrit {p.subscribed_power_kva} kVA")
            print(f"    -> {verdict(p.peak_kw, p.subscribed_power_kva)}")

        titre("4. Vérification : kWh et pic par jour, pandas (CSV) vs SQL (base)")
        comparer_pandas_sql(conn)

        titre(f"5. Temps d'exécution de la même requête ({COMPTEUR})")
        comparer_temps(conn, COMPTEUR)

        titre("Bonus : effet de la compression")
        stats = conn.execute("""
            SELECT total_chunks, number_compressed_chunks,
                   pg_size_pretty(before_compression_total_bytes),
                   pg_size_pretty(after_compression_total_bytes)
            FROM hypertable_compression_stats('load_curve')
        """).fetchone()
        print(f"{stats[1]}/{stats[0]} chunks compressés : {stats[2]} avant -> {stats[3]} après")


if __name__ == "__main__":
    main()
