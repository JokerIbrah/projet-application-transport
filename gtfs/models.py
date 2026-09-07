"""Objets de domaine du réseau. Aucune dépendance à FastAPI ni à SQLite."""

from dataclasses import dataclass
from datetime import date
from enum import IntEnum
from typing import NewType

# Des identifiants distincts, pour que le typeur attrape
# une inversion d'arguments find_route(stop, trip) au lieu de l'inverse.
StopId = NewType("StopId", str)
TripId = NewType("TripId", str)
StationId = NewType("StationId", str)

Seconds = int  # depuis minuit du jour de service ; peut dépasser 86400


class RouteType(IntEnum):
    """Valeurs normalisées GTFS. Metz utilise BUS et TRAM."""
    TRAM = 0
    METRO = 1
    RAIL = 2
    BUS = 3


# Entités statiques 
@dataclass(frozen=True, slots=True) #rend les instances immuables et plus légères
class Stop:
    id: StopId
    name: str
    lat: float
    lon: float
    station_id: StationId  # quais regroupés sous un arrêt logique


@dataclass(frozen=True, slots=True)
class Trip:
    id: TripId
    route_short_name: str   # « Mettis A », « L2 »…
    headsign: str           # destination affichée sur le bus
    route_type: RouteType


# Résultat d'un calcul 
@dataclass(frozen=True, slots=True)
class Leg:
    """Un segment du trajet : soit un trajet en véhicule, soit une marche."""
    from_stop: Stop
    to_stop: Stop
    departure: Seconds
    arrival: Seconds
    trip: Trip | None = None       # None => segment à pied
    delay: Seconds = 0             # issu du GTFS-RT, 0 en théorique

    @property
    def is_walking(self) -> bool:
        return self.trip is None

    @property
    def duration(self) -> Seconds:
        return self.arrival - self.departure


@dataclass(frozen=True, slots=True)
class Journey:
    legs: tuple[Leg, ...]
    service_day: date
    realtime: bool

    @property
    def departure(self) -> Seconds:
        return self.legs[0].departure

    @property
    def arrival(self) -> Seconds:
        return self.legs[-1].arrival

    @property
    def duration(self) -> Seconds:
        return self.arrival - self.departure

    @property
    def transfers(self) -> int:
        """Nombre de changements de véhicule, marche exclue."""
        rides = [leg for leg in self.legs if not leg.is_walking]
        return max(0, len(rides) - 1)


# 3. Formatage -------------------------------------------------------
def format_seconds(value: Seconds) -> str:
    """91800 -> '01:30 (+1j)'. Gère les horaires après minuit."""
    day_offset, rest = divmod(value, 86400)
    hours, remainder = divmod(rest, 3600)
    suffix = f" (+{day_offset}j)" if day_offset else ""
    return f"{hours:02d}:{remainder // 60:02d}{suffix}"