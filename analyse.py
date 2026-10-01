"""
Analyse la courbe de charge load_curve.csv : énergie par jour et par mois,
pic de puissance, répartition heures pleines / heures creuses.
"""

import pandas as pd

# Le pas de temps vaut 30 minutes = 0,5 heure.
# Énergie (kWh) = puissance (kW) × durée (h)  ->  kWh = kW × 0,5
PAS_EN_HEURES = 0.5


def charger_donnees(chemin="load_curve.csv"):
    """Lit le CSV et renvoie un DataFrame avec des dates en heure de Paris."""
    # read_csv lit un fichier CSV et le transforme en DataFrame.
    #
    # Normalement, on écrirait read_csv(chemin, parse_dates=["time"]) :
    # parse_dates demande à pandas de convertir la colonne "time" (du texte)
    # en vraies dates, sur lesquelles on peut calculer (extraire l'heure,
    # regrouper par jour...). Sans ça, "2024-01-01 08:00" n'est qu'une chaîne.
    #
    # PIÈGE : notre fichier contient deux décalages horaires, "+01:00"
    # (heure d'hiver) et "+02:00" (heure d'été après le 31 mars).
    # Face à ce mélange, parse_dates abandonne et laisse du texte.
    # Solution robuste : convertir nous-mêmes en UTC (un temps universel,
    # sans changement d'heure), puis ramener en heure de Paris.
    df = pd.read_csv(chemin)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Europe/Paris")

    # Énergie de chaque pas : on la calcule une seule fois ici, ensuite il
    # suffit de faire des sommes. (Sommer des kW n'aurait pas de sens.)
    df["energy_kwh"] = df["power_kw"] * PAS_EN_HEURES

    # L'accesseur .dt donne accès aux "morceaux" d'une colonne de dates.
    # - dt.hour      : l'heure (0 à 23)
    # - dt.dayofweek : le jour de la semaine, 0 = lundi ... 6 = dimanche
    df["heure"] = df["time"].dt.hour
    df["jour_semaine"] = df["time"].dt.dayofweek
    return df


def energie_par_jour(df):
    """Énergie (kWh) consommée chaque jour, par compteur."""
    # set_index("time") fait de la colonne time l'"index" du tableau
    # (l'étiquette de chaque ligne). resample a besoin d'un index de dates
    # pour savoir comment découper le temps.
    #
    # groupby("meter_id") sépare les données en un groupe par compteur :
    # sinon on additionnerait les deux compteurs ensemble.
    #
    # resample("D") regroupe les pas de 30 min par jour ("D" = Day), comme un
    # groupby mais sur le temps. .sum() additionne l'énergie de chaque jour.
    # Comme l'index est en heure de Paris, une "journée" = minuit-minuit à Paris.
    return (
        df.set_index("time")
          .groupby("meter_id")["energy_kwh"]
          .resample("D")
          .sum()
    )


def energie_par_mois(df):
    """Énergie (kWh) consommée chaque mois, par compteur."""
    # Même principe, avec "MS" = Month Start (une ligne par mois, datée du 1er).
    # unstack("meter_id") transforme les compteurs en colonnes : plus lisible.
    return (
        df.set_index("time")
          .groupby("meter_id")["energy_kwh"]
          .resample("MS")
          .sum()
          .unstack("meter_id")
    )


def pics_de_puissance(df):
    """Puissance maximale atteinte par compteur, avec sa date et son heure."""
    serie = df.set_index("time").groupby("meter_id")["power_kw"]
    # max() donne la valeur du pic ; idxmax() donne l'ÉTIQUETTE (ici la date,
    # puisque time est l'index) de la ligne où ce maximum est atteint.
    # C'est ce qui permet de répondre à "quand ?" et pas seulement "combien ?".
    return pd.DataFrame({"pic_kw": serie.max(), "date_du_pic": serie.idxmax()})


def heures_pleines_creuses(df):
    """Répartition de l'énergie entre heures pleines et heures creuses."""
    # Heures pleines (HP) : du lundi au vendredi, de 6h à 22h.
    # Un pas qui commence à 21h30 couvre 21h30-22h, il est donc bien en HP ;
    # celui de 22h00 est en heures creuses (HC). D'où le "< 22".
    est_hp = (df["jour_semaine"] < 5) & (df["heure"] >= 6) & (df["heure"] < 22)

    # On crée une colonne texte "HP"/"HC" puis on somme l'énergie par compteur
    # et par période. unstack met HP et HC en colonnes, côte à côte.
    periode = est_hp.map({True: "HP", False: "HC"})
    table = df.groupby(["meter_id", periode])["energy_kwh"].sum().unstack()
    table.columns.name = None

    # Pourcentages : chaque période divisée par le total du compteur.
    total = table["HP"] + table["HC"]
    table["HP_%"] = 100 * table["HP"] / total
    table["HC_%"] = 100 * table["HC"] / total
    return table[["HP", "HC", "HP_%", "HC_%"]]


def titre(texte):
    print(f"\n{'=' * 60}\n{texte}\n{'=' * 60}")


def main():
    df = charger_donnees()

    titre("Aperçu des données")
    print(df.head())
    print(f"\n{len(df)} lignes, du {df['time'].min()} au {df['time'].max()}")

    titre("Consommation par jour (kWh) - 7 premiers jours")
    # unstack pour avoir un compteur par colonne, round pour l'affichage.
    jours = energie_par_jour(df).unstack("meter_id").round(1)
    print(jours.head(7))
    print("\nMoyenne journalière (kWh) :")
    print(jours.mean().round(1).to_string())

    titre("Consommation par mois (kWh)")
    mois = energie_par_mois(df).round(0)
    mois.index = mois.index.strftime("%Y-%m")  # "2024-01" plutôt qu'une date complète
    print(mois)

    titre("Pic de puissance par compteur")
    for meter_id, ligne in pics_de_puissance(df).iterrows():
        print(f"{meter_id} : {ligne['pic_kw']:.2f} kW "
              f"le {ligne['date_du_pic']:%d/%m/%Y à %Hh%M}")

    titre("Heures pleines (lun-ven 6h-22h) / heures creuses")
    hphc = heures_pleines_creuses(df)
    for meter_id, ligne in hphc.iterrows():
        print(f"{meter_id} : HP = {ligne['HP']:8.0f} kWh ({ligne['HP_%']:.1f} %)   "
              f"HC = {ligne['HC']:8.0f} kWh ({ligne['HC_%']:.1f} %)")


# Ce bloc ne s'exécute que si on lance "python analyse.py" directement,
# pas quand plot.py fait "from analyse import charger_donnees".
if __name__ == "__main__":
    main()
