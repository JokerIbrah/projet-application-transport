"""Moteur de calcul d'itinéraires. Connection Scan Algorithm.

Aucune dépendance à FastAPI. Testable en isolation.
"""

import sqlite3
from array import array
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.gtfs.models import Journey, Leg, Seconds, Stop, StopId, TripId

INFINITY = 1 << 30
MIN_TRANSFER = 120  # secondes pour changer de quai


@dataclass(frozen=True, slots=True)
class Connection:
    """Un tronçon élémentaire : un véhicule entre deux arrêts consécutifs."""
    departure: Seconds
    arrival: Seconds
    from_idx: int      # indice interne, pas un stop_id
    to_idx: int
    trip_idx: int


class TransitGraph:
    # Construction 
    def __init__(self, stops, connections, footpaths, service_days):
        self._stops: list[Stop] = stops
        self._index: dict[StopId, int] = {s.id: i for i, s in enumerate(stops)}
        self._connections: list[Connection] = connections   # trié par départ
        self._departures = array("i", (c.departure for c in connections))
        self._footpaths: dict[int, list[tuple[int, Seconds]]] = footpaths
        self._service_days = service_days  # trip_idx -> set[date]

    @classmethod
    def load(cls, db_path: Path) -> "TransitGraph":
        """Reconstruit le graphe depuis le SQLite produit par loader.py."""
        conn = sqlite3.connect(db_path)
        stops = cls._read_stops(conn)
        index = {s.id: i for i, s in enumerate(stops)}
        connections = cls._read_connections(conn, index)
        connections.sort(key=lambda c: c.departure)   # invariant du CSA
        footpaths = cls._build_footpaths(stops)
        return cls(stops, connections, footpaths, cls._read_services(conn))

    @property
    def stop_count(self) -> int:
        return len(self._stops)

    # Recherche d'arrêts 
    def search_stops(self, query: str, limit: int = 10) -> list[Stop]:
        needle = _normalize(query)
        hits = [s for s in self._stops if needle in _normalize(s.name)]
        hits.sort(key=lambda s: (not _normalize(s.name).startswith(needle),
                                 len(s.name)))
        return hits[:limit]

    # Le cœur 
    def find_route(self, origin, destination, departure, delays=None,
                   day=None) -> Journey:
        src = self._index[origin]      # lève KeyError -> 404 dans main.py
        dst = self._index[destination]
        day = day or date.today()
        delays = delays or {}

        # Meilleure heure d'arrivée connue pour chaque arrêt.
        earliest = [INFINITY] * len(self._stops)
        earliest[src] = departure
        # Pour reconstruire : quelle connexion nous a amenés ici.
        incoming: list[Connection | None] = [None] * len(self._stops)

        self._relax_footpaths(src, departure, earliest, incoming)

        # On démarre le balayage à la première connexion utile.
        start = bisect_left(self._departures, departure)

        for conn in self._connections[start:]:
            # Élagage : plus rien ne peut améliorer la destination.
            if conn.departure > earliest[dst]:
                break
            if not self._runs_on(conn.trip_idx, day):
                continue #écarte les connexions qu'on ne peut pas prendre ce jour-là

            delay = delays.get((conn.trip_idx, conn.from_idx), 0)
            if earliest[conn.from_idx] > conn.departure + delay:
                continue  # on n'était pas là à temps

            if conn.arrival + delay < earliest[conn.to_idx]:
                earliest[conn.to_idx] = conn.arrival + delay
                incoming[conn.to_idx] = conn
                self._relax_footpaths(conn.to_idx, conn.arrival + delay,
                                      earliest, incoming)

        if earliest[dst] == INFINITY:
            raise NoRouteFound(origin, destination, departure)
        return self._rebuild(src, dst, incoming, day, bool(delays))

    # Auxiliaires 
    def _relax_footpaths(self, idx, arrival, earliest, incoming) -> None:
        """Propage aux quais voisins accessibles à pied."""
        for neighbour, walk in self._footpaths.get(idx, ()):
            if arrival + walk < earliest[neighbour]:
                earliest[neighbour] = arrival + walk
                incoming[neighbour] = None  # segment piéton

    def _rebuild(self, src, dst, incoming, day, realtime) -> Journey:
        """Remonte les pointeurs et fabrique les objets métier, une seule fois."""
        legs, cursor = [], dst
        while cursor != src:
            conn = incoming[cursor]
            ...  # à écrire : agréger les connexions d'un même trip en un Leg
        return Journey(tuple(reversed(legs)), day, realtime)


class NoRouteFound(Exception):
    pass