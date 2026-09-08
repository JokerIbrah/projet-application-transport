"""Moteur de calcul d'itinéraires. Connection Scan Algorithm.

Aucune dépendance à FastAPI. Testable en isolation.

Le CSA repose sur une idée simple : une fois toutes les connexions du
réseau triées par heure de départ, un unique balayage linéaire suffit à
connaître l'heure d'arrivée au plus tôt à chaque arrêt. Pas de tas, pas
de file de priorité — juste une boucle et un tableau.

La même structure sert à la simple consultation : lignes, arrêts
desservis, prochains passages. Tout est déjà en mémoire.
"""

import sqlite3
import unicodedata
from array import array
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.gtfs.models import (Departure, Journey, Leg, Route, Seconds, Stop,
                             StopId, Trip)

INFINITY = 1 << 30
MIN_TRANSFER = 120  # secondes pour changer de quai au sein d'un arrêt


@dataclass(frozen=True, slots=True)
class Connection:
    """Un tronçon élémentaire : un véhicule entre deux arrêts consécutifs."""
    departure: Seconds
    arrival: Seconds
    from_idx: int      # indice interne, pas un stop_id
    to_idx: int
    trip_idx: int


@dataclass(frozen=True, slots=True)
class Walk:
    """Une correspondance à pied, utilisée comme trace de reconstruction."""
    from_idx: int
    departure: Seconds
    arrival: Seconds


class NoRouteFound(Exception):
    """Aucun itinéraire ne relie les deux arrêts ce jour-là."""


class TransitGraph:
    # Construction -----------------------------------------------------
    def __init__(self, stops, routes, trips, connections, footpaths,
                 service_days, patterns):
        self._stops: list[Stop] = stops
        self._index: dict[StopId, int] = {s.id: i for i, s in enumerate(stops)}
        self._routes: dict[str, Route] = routes
        self._trips: list[Trip] = trips
        self._connections: list[Connection] = connections   # trié par départ
        self._departures = array("i", (c.departure for c in connections))
        self._footpaths: dict[int, list[tuple[int, Seconds]]] = footpaths
        self._service_days: dict[str, set[date]] = service_days
        self._patterns: dict[str, tuple[int, ...]] = patterns

        # Quais d'un même arrêt logique, et courses d'une même ligne :
        # deux regroupements que la consultation demande sans arrêt.
        self._quais: dict[str, list[int]] = {}
        for i, stop in enumerate(stops):
            self._quais.setdefault(stop.station_id or stop.id, []).append(i)

        self._trips_by_route: dict[str, list[int]] = {}
        for i, trip in enumerate(trips):
            self._trips_by_route.setdefault(trip.route_id, []).append(i)

        # Départs indexés par quai : une liste de pointeurs, déjà triée
        # puisque `connections` l'est.
        self._leaving: dict[int, list[Connection]] = {}
        for conn in connections:
            self._leaving.setdefault(conn.from_idx, []).append(conn)

    @classmethod
    def load(cls, db_path: str | Path) -> "TransitGraph":
        """Reconstruit le graphe depuis le SQLite produit par loader.py."""
        db_path = Path(db_path)
        if not db_path.exists():
            raise FileNotFoundError(
                f"Base introuvable : {db_path}. "
                "Lancez d'abord : python -m gtfs_metz.loader"
            )
        conn = sqlite3.connect(db_path)
        try:
            stops = cls._read_stops(conn)
            routes = cls._read_routes(conn)
            trips = cls._read_trips(conn)
            index = {s.id: i for i, s in enumerate(stops)}
            trip_index = {t.id: i for i, t in enumerate(trips)}
            connections = cls._read_connections(conn, index, trip_index)
            services = cls._read_services(conn)
        finally:
            conn.close()

        # L'ordre de lecture est celui des dessertes : on en tire les
        # itinéraires des lignes AVANT de trier pour le CSA.
        patterns = cls._build_patterns(connections, trips)
        connections.sort(key=lambda c: c.departure)   # invariant du CSA

        return cls(stops, routes, trips, connections,
                   cls._build_footpaths(stops), services, patterns)

    @staticmethod
    def _read_stops(conn: sqlite3.Connection) -> list[Stop]:
        rows = conn.execute(
            "SELECT stop_id, stop_name, stop_lat, stop_lon, station_id "
            "FROM stops ORDER BY stop_id"
        )
        return [Stop(*row) for row in rows]

    @staticmethod
    def _read_routes(conn: sqlite3.Connection) -> dict[str, Route]:
        rows = conn.execute(
            "SELECT route_id, short_name, long_name, color, text_color, "
            "route_type FROM routes"
        )
        return {row[0]: Route(*row) for row in rows}

    @staticmethod
    def _read_trips(conn: sqlite3.Connection) -> list[Trip]:
        rows = conn.execute(
            "SELECT trip_id, route_id, headsign, service_id FROM trips "
            "ORDER BY trip_id"
        )
        return [Trip(*row) for row in rows]

    @staticmethod
    def _read_connections(conn, index, trip_index) -> list[Connection]:
        """Deux passages consécutifs d'une même course = une connexion.

        Les lignes arrivent triées par course puis par ordre de desserte :
        il suffit de garder le passage précédent pour former les paires.
        """
        rows = conn.execute(
            "SELECT trip_id, stop_id, arrival_s, departure_s FROM stop_times "
            "ORDER BY trip_id, stop_sequence"
        )
        connections: list[Connection] = []
        previous_trip = None
        previous_stop = previous_departure = None

        for trip_id, stop_id, arrival_s, departure_s in rows:
            idx = index.get(stop_id)
            if idx is None:                      # arrêt absent de stops.txt
                previous_trip = None
                continue
            if trip_id == previous_trip and arrival_s is not None \
                    and previous_departure is not None:
                connections.append(Connection(
                    departure=previous_departure,
                    arrival=arrival_s,
                    from_idx=previous_stop,
                    to_idx=idx,
                    trip_idx=trip_index[trip_id],
                ))
            previous_trip = trip_id if trip_id in trip_index else None
            previous_stop, previous_departure = idx, departure_s

        return connections

    @staticmethod
    def _read_services(conn: sqlite3.Connection) -> dict[str, set[date]]:
        services: dict[str, set[date]] = {}
        for service_id, day in conn.execute(
                "SELECT service_id, day FROM service_dates"):
            services.setdefault(service_id, set()).add(date.fromisoformat(day))
        return services

    @staticmethod
    def _build_patterns(connections, trips) -> dict[str, tuple[int, ...]]:
        """L'itinéraire type de chaque ligne : sa course la plus longue.

        Un GTFS ne décrit pas le tracé d'une ligne, seulement des milliers
        de courses. Retenir la plus desservie donne une liste d'arrêts
        représentative, ce qui suffit à afficher une fiche de ligne.
        """
        patterns: dict[str, tuple[int, ...]] = {}

        def keep(trip_idx: int | None, stops: list[int]) -> None:
            if trip_idx is None:
                return
            route_id = trips[trip_idx].route_id
            if len(stops) > len(patterns.get(route_id, ())):
                patterns[route_id] = tuple(stops)

        current_trip: int | None = None
        current: list[int] = []
        for conn in connections:
            if conn.trip_idx != current_trip:
                keep(current_trip, current)
                current_trip, current = conn.trip_idx, [conn.from_idx]
            current.append(conn.to_idx)
        keep(current_trip, current)

        return patterns

    @staticmethod
    def _build_footpaths(stops) -> dict[int, list[tuple[int, Seconds]]]:
        """Relie entre eux les quais d'un même arrêt logique.

        Volontairement limité : pas de correspondance entre arrêts voisins
        distincts. C'est la version minimale qui reste juste.
        """
        by_station: dict[str, list[int]] = {}
        for i, stop in enumerate(stops):
            by_station.setdefault(stop.station_id or stop.id, []).append(i)

        footpaths: dict[int, list[tuple[int, Seconds]]] = {}
        for quais in by_station.values():
            if len(quais) < 2:
                continue
            for a in quais:
                footpaths[a] = [(b, MIN_TRANSFER) for b in quais if b != a]
        return footpaths

    # Lecture ----------------------------------------------------------
    @property
    def stop_count(self) -> int:
        return len(self._stops)

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @property
    def route_count(self) -> int:
        return len(self._routes)

    @property
    def trip_count(self) -> int:
        return len(self._trips)

    @property
    def station_count(self) -> int:
        return len(self._quais)

    def service_period(self) -> tuple[date, date] | None:
        """Premier et dernier jour couverts par le GTFS chargé."""
        days = [day for days in self._service_days.values() for day in days]
        return (min(days), max(days)) if days else None

    def search_stops(self, query: str, limit: int = 10) -> list[Stop]:
        """Recherche par sous-chaîne, insensible à la casse et aux accents.

        Un seul résultat par arrêt logique : l'usager cherche « GARE »,
        pas les six quais qui la composent.
        """
        needle = _normalize(query)
        seen: set[str] = set()
        hits: list[Stop] = []
        for stop in self._stops:
            station = stop.station_id or stop.id
            if station in seen or needle not in _normalize(stop.name):
                continue
            seen.add(station)
            hits.append(stop)
        hits.sort(key=lambda s: (not _normalize(s.name).startswith(needle),
                                 len(s.name), s.name))
        return hits[:limit]

    def stop(self, stop_id: StopId) -> Stop:
        return self._stops[self._index[stop_id]]

    def route_of(self, trip: Trip) -> Route:
        """Ligne d'une course. Un GTFS incomplet ne doit pas faire tomber
        l'application : on retombe sur une ligne anonyme."""
        return self._routes.get(trip.route_id) or Route(trip.route_id, "", "")

    # Consultation du réseau -------------------------------------------
    def route(self, route_id: str) -> Route:
        return self._routes[route_id]

    def routes(self) -> list[Route]:
        """Toutes les lignes, dans un ordre lisible par un humain."""
        return sorted(self._routes.values(), key=lambda r: _route_key(r.name))

    def route_stops(self, route_id: str) -> list[Stop]:
        """Arrêts desservis par une ligne, dans l'ordre du parcours.

        Un seul quai par arrêt logique : la fiche de ligne se lit comme un
        plan, pas comme une liste de poteaux.
        """
        if route_id not in self._routes:
            raise KeyError(route_id)
        seen: set[str] = set()
        stops: list[Stop] = []
        for idx in self._patterns.get(route_id, ()):
            stop = self._stops[idx]
            station = stop.station_id or stop.id
            if station not in seen:
                seen.add(station)
                stops.append(stop)
        return stops

    def route_destinations(self, route_id: str) -> list[str]:
        """Girouettes distinctes de la ligne, soit ses terminus usuels."""
        seen = {self._trips[i].headsign
                for i in self._trips_by_route.get(route_id, ())
                if self._trips[i].headsign}
        return sorted(seen)

    def route_days(self, route_id: str) -> int:
        """Nombre de jours où la ligne circule, sur la période chargée."""
        days: set[date] = set()
        for i in self._trips_by_route.get(route_id, ()):
            days |= self._service_days.get(self._trips[i].service_id, set())
        return len(days)

    def departures(self, stop_id: StopId, day: date | None = None,
                   after: Seconds = 0, limit: int = 12,
                   delays=None) -> list[Departure]:
        """Prochains passages à un arrêt, tous quais confondus.

        Le terminus d'une course n'y figure pas : on ne « part » pas d'un
        arrêt où le véhicule s'arrête définitivement.
        """
        day = day or date.today()
        delays = delays or {}
        origin = self._stops[self._index[stop_id]]
        station = origin.station_id or origin.id

        found: list[Departure] = []
        for idx in self._quais.get(station, ()):
            # Chaque quai fournit au plus `limit` passages : les premiers
            # de l'arrêt ne peuvent pas venir de plus loin que ça.
            taken = 0
            for conn in self._leaving.get(idx, ()):
                if conn.departure < after:
                    continue  # listes triées : on avance jusqu'à l'heure voulue
                if taken >= limit:
                    break
                if not self._runs_on(conn.trip_idx, day):
                    continue
                taken += 1
                trip = self._trips[conn.trip_idx]
                route = self.route_of(trip)
                found.append(Departure(
                    time=conn.departure,
                    stop=self._stops[idx],
                    route=route.name,
                    headsign=trip.headsign,
                    color=route.color,
                    delay=delays.get(trip.id, 0),
                ))

        found.sort(key=lambda d: d.expected)
        return found[:limit]

    # Le cœur ----------------------------------------------------------
    def find_route(self, origin: StopId, destination: StopId,
                   departure: Seconds, delays=None, day=None) -> Journey:
        """Heure d'arrivée au plus tôt, et le chemin qui y mène.

        `delays` associe un trip_id au retard observé, en secondes.
        """
        src = self._index[origin]      # lève KeyError -> 404 dans main.py
        dst = self._index[destination]
        day = day or date.today()
        delays = delays or {}

        # Meilleure heure d'arrivée connue pour chaque arrêt.
        earliest = [INFINITY] * len(self._stops)
        earliest[src] = departure
        # Trace de reconstruction : par quoi est-on arrivé ici.
        incoming: list[Connection | Walk | None] = [None] * len(self._stops)

        self._relax_footpaths(src, departure, earliest, incoming)

        # On démarre le balayage à la première connexion utile.
        start = bisect_left(self._departures, departure)

        for conn in self._connections[start:]:
            # Élagage : partir après la meilleure arrivée connue à
            # destination ne peut plus rien améliorer. Suppose des retards
            # positifs, ce qui est le cas en pratique.
            if conn.departure > earliest[dst]:
                break
            if not self._runs_on(conn.trip_idx, day):
                continue  # course qui ne circule pas ce jour-là

            delay = delays.get(self._trips[conn.trip_idx].id, 0)
            if earliest[conn.from_idx] > conn.departure + delay:
                continue  # on n'était pas là à temps

            arrival = conn.arrival + delay
            if arrival < earliest[conn.to_idx]:
                earliest[conn.to_idx] = arrival
                incoming[conn.to_idx] = conn
                self._relax_footpaths(conn.to_idx, arrival, earliest, incoming)

        if earliest[dst] == INFINITY:
            raise NoRouteFound(
                f"Aucun itinéraire de {self._stops[src].name} vers "
                f"{self._stops[dst].name} le {day}."
            )
        return self._rebuild(src, dst, earliest, incoming, delays, day)

    # Auxiliaires ------------------------------------------------------
    def _runs_on(self, trip_idx: int, day: date) -> bool:
        return day in self._service_days.get(self._trips[trip_idx].service_id, ())

    def _relax_footpaths(self, idx, arrival, earliest, incoming) -> None:
        """Propage aux quais voisins accessibles à pied."""
        for neighbour, walk in self._footpaths.get(idx, ()):
            if arrival + walk < earliest[neighbour]:
                earliest[neighbour] = arrival + walk
                incoming[neighbour] = Walk(idx, arrival, arrival + walk)

    def _rebuild(self, src, dst, earliest, incoming, delays, day) -> Journey:
        """Remonte les traces et fabrique les objets métier, une seule fois.

        Les connexions successives d'une même course sont agrégées en un
        seul segment : l'usager veut lire « ligne A, 6 arrêts », pas six
        lignes de tableau.
        """
        legs: list[Leg] = []
        cursor = dst

        while cursor != src:
            step = incoming[cursor]
            if step is None:                       # trace incohérente
                raise NoRouteFound("Chemin non reconstructible.")

            if isinstance(step, Walk):
                legs.append(Leg(
                    mode="walk",
                    from_stop=self._stops[step.from_idx],
                    to_stop=self._stops[cursor],
                    departure=step.departure,
                    arrival=step.arrival,
                ))
                cursor = step.from_idx
                continue

            # Remonter jusqu'à l'arrêt où l'on est monté à bord.
            boarding = step
            while True:
                previous = incoming[boarding.from_idx]
                if isinstance(previous, Connection) \
                        and previous.trip_idx == boarding.trip_idx:
                    boarding = previous
                else:
                    break

            trip = self._trips[step.trip_idx]
            route = self.route_of(trip)
            delay = delays.get(trip.id, 0)
            legs.append(Leg(
                mode="ride",
                from_stop=self._stops[boarding.from_idx],
                to_stop=self._stops[cursor],
                departure=boarding.departure + delay,
                arrival=earliest[cursor],
                route=route.name,
                headsign=trip.headsign,
                color=route.color,
                delay=delay,
            ))
            cursor = boarding.from_idx

        return Journey(tuple(reversed(legs)), day, realtime=bool(delays))


def _normalize(text: str) -> str:
    """« Gare Routière » -> « gare routiere ». Comparaison tolérante."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _route_key(name: str):
    """Trie « L2 » avant « L10 » : le chiffre compte comme un nombre."""
    digits = "".join(c for c in name if c.isdigit())
    return (int(digits) if digits else 0, name)
