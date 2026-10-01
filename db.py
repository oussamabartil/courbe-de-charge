"""
Connexion à la base PostgreSQL/TimescaleDB, partagée par load_to_db.py et queries.py.
"""

import os

import psycopg
from dotenv import load_dotenv

# load_dotenv lit le fichier .env et place son contenu dans les variables
# d'environnement. Le mot de passe n'est donc jamais écrit dans le code
# (qui, lui, peut être publié sur Git), et chaque machine peut avoir le sien.
load_dotenv()


def connect(autocommit=False):
    """Ouvre une connexion à la base décrite dans .env."""
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        # Fuseau de la session : PostgreSQL stocke les TIMESTAMPTZ en UTC mais
        # nous les renvoie en heure de Paris, comme dans le CSV.
        options="-c timezone=Europe/Paris",
        # autocommit=True : chaque commande est validée immédiatement.
        # Nécessaire pour certaines procédures TimescaleDB (refresh, run_job)
        # qui refusent de s'exécuter à l'intérieur d'une transaction.
        autocommit=autocommit,
    )
