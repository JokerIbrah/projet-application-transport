# projet-application-transport

# v.0.1

Calculateur d'itinéraires pour le réseau **Le Met'** (Metz), volontairement
minimaliste : une API FastAPI, un moteur de calcul en Python pur, une page
web sans framework ni build.

Projet personnel d'apprentissage du développement. Aucun lien officiel avec
l'exploitant du réseau.

---

# Ce que fait l'application

La page d'accueil tient en trois onglets, tous alimentés par le GTFS chargé.

**Itinéraire** — le trajet arrivant **au plus tôt** entre deux arrêts, pour
un jour et une heure donnés, correspondances à pied comprises.

**Horaires** — les prochains passages à un arrêt, tous quais confondus :
heure, ligne, destination, quai, et le retard s'il est connu.

**Lignes** — toutes les lignes du réseau avec leurs couleurs officielles ;
en sélectionner une affiche ses destinations, ses jours de circulation et
son parcours. Chaque arrêt du parcours renvoie vers sa fiche horaire.

En complément : recherche d'arrêts insensible à la casse et aux accents,
prise en compte optionnelle des **retards observés** (GTFS-RT), et positions
temps réel des véhicules exposées en JSON.

---

## Installation

Prérequis : **Python 3.12** ou plus récent.

### Linux / macOS

```bash
git clone <url-du-depot> && cd projet-application-transport
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirement.txt
```

### Windows (PowerShell)

```powershell
git clone <url-du-depot>; cd projet-application-transport
py -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirement.txt
```

### Utilisation

Il faut ouvrir avec l'IP locale et le port 8000 (exemple <http://127.0.0.1:8000>)

## Docker

```bash
docker build -f Index.dockerfile -t metz-transit .
docker run -p 8000:8000 metz-transit
```

L'image construit la base au moment du `build` : elle démarre sans réseau.

---

## Démarrage

**1. Construire la base à partir du GTFS statique** (une fois, puis à chaque
mise à jour des horaires) :

```bash
python -m gtfs_metz.loader gtfs_metz/LEMET-gtfs.zip data/gtfs.sqlite
```

Sans argument, ces deux chemins sont ceux pris par défaut. Le script affiche
le nombre d'arrêts, de lignes, de courses et de passages chargés.

**2. Lancer le serveur**, depuis la racine du dépôt :

```bash
uvicorn app.main:app --reload
```

L'application est sur <http://127.0.0.1:8000>, la documentation
interactive de l'API sur <http://127.0.0.1:8000/docs>.

---

## Configuration

Copiez `.env.example` en `.env` et adaptez :

| Variable | Rôle | Défaut |
| --- | --- | --- |
| `GTFS_DB` | Base SQLite à charger | `data/gtfs.sqlite` |
| `GTFS_RT_TRIP_UPDATES` | Flux des retards (URL ou fichier) | vide |
| `GTFS_RT_VEHICLE_POSITIONS` | Flux des positions (URL ou fichier) | vide |
| `GTFS_RT_INTERVAL` | Rafraîchissement, en secondes | `30` |

Les deux flux acceptent aussi bien une URL `http(s)` qu'un chemin local :
les échantillons rangés dans `gtfs_metz/` permettent de travailler hors
connexion. Si aucun flux n'est configuré, l'application fonctionne sur les
seuls horaires théoriques — c'est le mode par défaut.

---

## API

| Méthode | Route | Description |
| --- | --- | --- |
| `GET` | `/api/stops?q=gare` | Arrêts dont le nom contient `q` (10 max) |
| `GET` | `/api/stops/{stop_id}/departures` | Prochains passages à un arrêt |
| `GET` | `/api/routes` | Lignes du réseau |
| `GET` | `/api/routes/{route_id}` | Détail d'une ligne et arrêts desservis |
| `GET` | `/api/route` | Itinéraire au plus tôt |
| `GET` | `/api/vehicles` | Positions temps réel connues |
| `GET` | `/api/network` | Contenu du GTFS chargé et fraîcheur du temps réel |

`/api/route` attend `origin`, `destination` (des `stop_id` renvoyés par
`/api/stops`), `departure` au format `HH:MM`, et accepte `day`
(`AAAA-MM-JJ`) et `realtime` (booléen). `/api/stops/{id}/departures` accepte
`after` (`HH:MM`), `day`, `limit` et `realtime`.

```bash
curl "http://127.0.0.1:8000/api/route?origin=0:POMS&destination=0:MUSE02&departure=08:30"
curl "http://127.0.0.1:8000/api/stops/0:POMS/departures?after=08:00"
curl "http://127.0.0.1:8000/api/routes"
```

---

## Organisation du code

```
app/
  main.py            branchement HTTP : routes, cycle de vie, erreurs
  schemas.py         contrats JSON d'entrée et de sortie
  gtfs/models.py     objets métier (Stop, Route, Trip, Leg, Departure…)
  routing/graph.py   le moteur : Connection Scan Algorithm, et la
                     consultation (lignes, parcours, prochains passages)
  routing/dijkstra.py  implémentation de référence, lente, sert d'oracle
  realtime/poller.py rafraîchissement GTFS-RT en tâche de fond
  static/            la page web : un HTML, un CSS, un JS
gtfs_metz/
  loader.py          GTFS statique -> SQLite
  LEMET-gtfs.zip     archive GTFS du réseau
data/                base générée, ignorée par git
```

La dépendance ne va que dans un sens : `routing/` et `gtfs/` ignorent
FastAPI, ce qui les rend testables sans serveur.

---

## Choix techniques

**Connection Scan plutôt que Dijkstra.** Un réseau de transport n'est pas
un graphe ordinaire : le coût d'une arête dépend de l'heure à laquelle on
s'y présente. Le CSA contourne le problème en triant une fois pour toutes
les connexions par heure de départ ; un seul balayage linéaire donne alors
l'heure d'arrivée au plus tôt partout. Pas de tas, pas de file de priorité.
`routing/dijkstra.py` conserve la version naïve : plus lente, mais utile
comme oracle pour vérifier que le CSA donne les mêmes heures.

**Calendrier déplié à l'écriture.** Dans le loader, une décision devait
être prise : privilégier l'espace disque ou la rapidité ? J'ai choisi la
rapidité, avec une table `service_dates` qui contient une ligne par
(service, jour) plutôt que les règles hebdomadaires à réinterpréter à
chaque requête. La base grossit, la lecture devient triviale.

**Correspondances limitées aux quais d'un même arrêt.** Deux quais reliés
par `parent_station` sont joints par un trajet à pied forfaitaire de deux
minutes. Pas de correspondance entre arrêts voisins distincts : c'est la
version minimale qui reste juste.

**Un seul index pour tout.** La consultation ne rouvre pas la base : les
prochains passages sortent des mêmes connexions que le calcul d'itinéraire,
indexées par quai au chargement. Le parcours d'une ligne, lui, est celui de
sa course la plus longue — un GTFS ne décrit pas de tracé, seulement des
milliers de courses, et la plus desservie en donne une image fidèle.

**Retards appliqués par course.** Le GTFS-RT donne un retard par passage ;
on retient la dernière prévision de chaque course et on l'applique
uniformément. Approximation assumée, largement suffisante à l'échelle d'un
réseau urbain.

**Dégradation, pas panne.** Si un flux temps réel est indisponible, le
poller conserve son dernier état connu et l'application continue sur
l'horaire théorique.

---

## Limites connues

- Pas encore de tests automatisés : `dijkstra.py` attend son harnais.
- Le premier critère est l'heure d'arrivée seule ; le nombre de
  correspondances n'est pas optimisé.
- Un seul itinéraire proposé, pas d'alternatives ni de départs suivants.
- Le `index.html` à la racine du dépôt est une ancienne maquette : la page
  réellement servie est `app/static/index.html`.

## Feuille de route

- **v0.1** — chargement du GTFS statique, moteur d'itinéraire, page statique.
- **v0.2** *(en cours)* — API complète, temps réel branché, front minimal :
  itinéraire, fiches horaires et consultation des lignes.
- **v0.3** — tests contre l'oracle Dijkstra, plusieurs itinéraires proposés.
- **v0.4** — perturbations (`service_alerts`), carte des véhicules.

---

## Données

Horaires théoriques (GTFS) et temps réel (GTFS-RT) issus des données
ouvertes publiées par Le Met'. Leur réutilisation reste soumise aux
conditions de leur licence.
