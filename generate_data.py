"""
Génère un fichier load_curve.csv contenant une fausse courbe de charge.

Une courbe de charge = la puissance moyenne consommée par un compteur
sur chaque pas de temps (ici 30 minutes), comme celles que fournit Enedis.
"""

import numpy as np
import pandas as pd

# On fixe la "graine" du générateur aléatoire : à chaque exécution on obtient
# exactement les mêmes données. Indispensable pour pouvoir comparer des
# résultats ou déboguer (sinon les chiffres changent à chaque lancement).
rng = np.random.default_rng(seed=42)

# --- 1. L'axe du temps -------------------------------------------------------
# pd.date_range crée une suite de dates régulièrement espacées.
# - freq="30min" : un point toutes les 30 minutes (le pas de temps Enedis).
# - tz="Europe/Paris" : les dates sont "conscientes" du fuseau horaire.
#   C'est important car en France l'heure change deux fois par an : le
#   31 mars 2024, on passe de 2h à 3h, donc cette journée ne compte que
#   23 heures (46 pas au lieu de 48). Avec un fuseau, pandas le gère seul.
dates = pd.date_range(
    start="2024-01-01 00:00",
    end="2024-03-31 23:30",
    freq="30min",
    tz="Europe/Paris",
)

# On extrait l'heure (en décimal : 8h30 -> 8.5) et le jour de la semaine
# (0 = lundi ... 6 = dimanche). Ce sont eux qui déterminent le profil.
heure = dates.hour + dates.minute / 60
jour_semaine = dates.dayofweek

# Chaque compteur a sa propre "personnalité" : une puissance de veille
# (nuit, week-end) et une puissance d'activité (heures de bureau).
compteurs = {
    "PRM_001": {"veille_kw": 3.0, "activite_kw": 25.0},   # petit bureau
    "PRM_002": {"veille_kw": 6.0, "activite_kw": 42.0},   # atelier / commerce
}

tableaux = []
for meter_id, params in compteurs.items():
    # --- 2. Le profil "théorique" --------------------------------------------
    # Masque booléen : True quand on est en semaine (lundi-vendredi)
    # ET entre 8h et 18h. On travaille sur tout le tableau d'un coup
    # (calcul "vectorisé") au lieu de faire une boucle sur chaque date :
    # c'est beaucoup plus rapide et c'est la façon idiomatique avec numpy.
    en_activite = (jour_semaine < 5) & (heure >= 8) & (heure < 18)

    # np.where(condition, si_vrai, si_faux) : choisit la puissance selon le masque.
    puissance = np.where(en_activite, params["activite_kw"], params["veille_kw"])

    # Petites rampes réalistes : les gens arrivent vers 7h et partent vers 19h,
    # la consommation ne passe pas brutalement de 3 à 25 kW.
    en_semaine = jour_semaine < 5
    rampe = en_semaine & (((heure >= 7) & (heure < 8)) | ((heure >= 18) & (heure < 19)))
    puissance = np.where(rampe, (params["activite_kw"] + params["veille_kw"]) / 2, puissance)

    # --- 3. Le bruit aléatoire -----------------------------------------------
    # Une vraie courbe n'est jamais parfaitement plate : on multiplie par un
    # facteur proche de 1 (loi normale, écart-type 8 %).
    bruit = rng.normal(loc=1.0, scale=0.08, size=len(dates))
    puissance = puissance * bruit

    # --- 4. Quelques pics ----------------------------------------------------
    # Environ 0,5 % des pas reçoivent un pic (démarrage d'une machine,
    # chauffage d'appoint...). On multiplie la puissance par 1,5 à 2,2.
    est_un_pic = rng.random(len(dates)) < 0.005
    facteur_pic = rng.uniform(1.5, 2.2, size=len(dates))
    puissance = np.where(est_un_pic, puissance * facteur_pic, puissance)

    # Une puissance consommée ne peut pas être négative.
    puissance = np.clip(puissance, 0, None)

    # On range le tout dans un DataFrame (un tableau à colonnes nommées).
    tableaux.append(
        pd.DataFrame({
            "time": dates,
            "meter_id": meter_id,
            "power_kw": puissance.round(2),
        })
    )

# pd.concat empile les tableaux des deux compteurs l'un sous l'autre.
# ignore_index=True renumérote les lignes de 0 à N-1.
df = pd.concat(tableaux, ignore_index=True)

# index=False : on ne veut pas écrire le numéro de ligne dans le CSV.
df.to_csv("load_curve.csv", index=False)

print(f"load_curve.csv généré : {len(df)} lignes, "
      f"{df['meter_id'].nunique()} compteurs, "
      f"du {df['time'].min():%d/%m/%Y} au {df['time'].max():%d/%m/%Y}.")
