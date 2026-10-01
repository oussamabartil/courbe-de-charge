"""
Génère deux graphiques PNG à partir de la courbe de charge :
1. conso_journaliere.png : énergie consommée chaque jour par un compteur ;
2. journee_type.png      : profil moyen par heure, semaine vs week-end.
"""

import matplotlib
matplotlib.use("Agg")  # pas de fenêtre : on enregistre seulement des fichiers PNG
import matplotlib.pyplot as plt

# On réutilise les fonctions d'analyse.py au lieu de recopier le code :
# une seule façon de lire et de préparer les données = moins d'erreurs.
from analyse import charger_donnees, energie_par_jour

COMPTEUR = "PRM_001"

# Couleurs : fond clair, texte sombre, grille discrète. Le bleu et l'orange
# restent distinguables par les personnes daltoniennes.
FOND = "#fcfcfb"
TEXTE = "#0b0b0b"
TEXTE_SECONDAIRE = "#52514e"
GRILLE = "#e4e3df"
BLEU = "#2a78d6"
ORANGE = "#eb6834"


def style_axes(ax):
    """Style sobre commun aux deux graphiques : c'est la donnée qui doit ressortir."""
    ax.set_facecolor(FOND)
    for cote in ("top", "right"):
        ax.spines[cote].set_visible(False)
    for cote in ("left", "bottom"):
        ax.spines[cote].set_color(GRILLE)
    ax.grid(axis="y", color=GRILLE, linewidth=0.8)
    ax.set_axisbelow(True)  # la grille passe derrière les courbes
    ax.tick_params(colors=TEXTE_SECONDAIRE, labelsize=9)
    ax.yaxis.label.set_color(TEXTE_SECONDAIRE)
    ax.xaxis.label.set_color(TEXTE_SECONDAIRE)


def graphique_conso_journaliere(df):
    # energie_par_jour renvoie une série indexée par (compteur, jour) ;
    # .loc[COMPTEUR] garde uniquement les jours du compteur choisi.
    jours = energie_par_jour(df).loc[COMPTEUR]

    fig, ax = plt.subplots(figsize=(11, 4.5), facecolor=FOND)
    ax.plot(jours.index, jours.values, color=BLEU, linewidth=2)
    style_axes(ax)
    ax.set_ylim(bottom=0)  # partir de 0 évite d'exagérer visuellement les écarts
    ax.set_ylabel("Énergie consommée (kWh / jour)")
    ax.set_title(f"Consommation journalière – {COMPTEUR}",
                 loc="left", color=TEXTE, fontsize=13, pad=22)

    # Les creux réguliers correspondent aux week-ends : on l'indique en
    # sous-titre plutôt que d'étiqueter chaque point.
    ax.text(0, 1.02, "Les creux réguliers sont les week-ends",
            transform=ax.transAxes, color=TEXTE_SECONDAIRE, fontsize=9)

    fig.tight_layout()
    fig.savefig("conso_journaliere.png", dpi=150)
    plt.close(fig)


def graphique_journee_type(df):
    d = df[df["meter_id"] == COMPTEUR].copy()

    # Week-end = samedi (5) ou dimanche (6).
    d["type_jour"] = d["jour_semaine"].map(lambda j: "Week-end" if j >= 5 else "Semaine")

    # Pour chaque type de jour et chaque heure, la puissance moyenne :
    # c'est la "journée type". unstack met Semaine / Week-end en colonnes.
    profil = d.groupby(["heure", "type_jour"])["power_kw"].mean().unstack("type_jour")

    fig, ax = plt.subplots(figsize=(9, 4.5), facecolor=FOND)
    for colonne, couleur in (("Semaine", BLEU), ("Week-end", ORANGE)):
        ax.plot(profil.index, profil[colonne], color=couleur, linewidth=2, label=colonne)
        # Étiquette directe juste au-dessus de la courbe, à midi (là où les deux
        # courbes sont bien séparées) : on lit qui est qui sans chercher la légende.
        ax.annotate(colonne, xy=(12, profil.loc[12, colonne]),
                    xytext=(0, 8), textcoords="offset points",
                    ha="center", color=TEXTE, fontsize=9)

    style_axes(ax)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xticklabels([f"{h}h" for h in range(0, 24, 2)])
    ax.set_xlim(0, 23)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Heure de la journée")
    ax.set_ylabel("Puissance moyenne (kW)")
    ax.set_title(f"Journée type – {COMPTEUR}", loc="left", color=TEXTE, fontsize=13)
    ax.legend(frameon=False, labelcolor=TEXTE, loc="center left")

    fig.tight_layout()
    fig.savefig("journee_type.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    donnees = charger_donnees()
    graphique_conso_journaliere(donnees)
    graphique_journee_type(donnees)
    print("Graphiques enregistrés : conso_journaliere.png, journee_type.png")
