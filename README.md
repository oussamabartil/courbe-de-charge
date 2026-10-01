# Courbe de charge – analyse avec pandas et TimescaleDB

Projet d'entraînement personnel : analyse de courbes de charge électriques (pas de 30 min, format proche des données Enedis) pour deux compteurs fictifs, `PRM_001` et `PRM_002`. Les données sont analysées en pandas puis stockées et requêtées dans PostgreSQL + TimescaleDB.

## Prérequis

- Python 3.10+
- Docker avec Docker Compose

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # puis choisir un mot de passe dans .env
```

## Partie 1 : analyse avec pandas

```bash
python generate_data.py   # crée load_curve.csv (3 mois, 2 compteurs)
python analyse.py         # affiche kWh/jour, kWh/mois, pics, HP/HC
python plot.py            # crée conso_journaliere.png et journee_type.png
```

## Partie 2 : base de données TimescaleDB

```bash
# 1. Démarrer la base (--wait attend qu'elle soit prête)
docker compose up -d --wait

# 2. Créer le schéma (tables, hypertable, agrégat continu, compression)
docker compose exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/schema.sql

# 3. Charger le CSV dans la base (relançable sans créer de doublons)
python load_to_db.py

# 4. Lancer les requêtes d'analyse et les comparaisons pandas / SQL
python queries.py
```

Pour ouvrir une console SQL :

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Pour arrêter la base : `docker compose down` (les données sont conservées dans le volume `pgdata`). `docker compose down -v` supprime aussi les données.

## Fichiers

| Fichier | Rôle |
|---|---|
| `generate_data.py` | Génère des données réalistes (activité en semaine de 8h à 18h, bruit, pics) |
| `analyse.py` | Calculs avec pandas (`groupby`, `resample`, `idxmax`, `.dt`) |
| `plot.py` | Graphiques matplotlib |
| `docker-compose.yml` | Conteneur PostgreSQL 16 + TimescaleDB |
| `.env.example` | Modèle du fichier `.env` (identifiants, non versionné) |
| `sql/schema.sql` | Tables, hypertable, index, agrégat continu, compression |
| `db.py` | Connexion à la base (lit `.env`) |
| `load_to_db.py` | Chargement en masse du CSV (COPY) |
| `queries.py` | Requêtes SQL, vérification pandas = SQL, mesure des temps |
