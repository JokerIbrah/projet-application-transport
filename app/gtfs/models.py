"""Objets métier partagés. Aucune dépendance : ni FastAPI, ni SQLite."""

from dataclasses import dataclass
from datetime import date

Seconds = int
StopId = str
TripId = str


@dataclass(frozen=True, slots=True)
class Stop:
    """Un point d'arrêt physique (un quai)."""
    id: StopId
    name: str
    lat: float | None = None
    lon: float | None = None
    station_id: str | None = None


@dataclass(frozen=True, slots=True)
class Route:
    """Une ligne commerciale : ce que l'usager appelle « la ligne A »."""
    id: str
    short_name: str     # ce qui est écrit sur le bus, ex. "MA", "L1"
    long_name: str      # ex. "BORNY / HOPITAL SCHUMAN - WOIPPY ST-ELOY"
    color: str = ""     # hexadécimal sans le '#', tel que fourni par le GTFS
    text_color: str = ""
    route_type: int = 3  # 0 tram, 3 bus (spécification GTFS)

    @property
    def name(self) -> str:
        return self.short_name or self.long_name

    @property
    def mode(self) -> str:
        # Codes de la spécification GTFS. Le Met' en utilise deux : 3 pour
        # ses bus, 4 pour les navettes fluviales de la Moselle.
        return {0: "tram", 1: "métro", 2: "train", 3: "bus",
                4: "navette fluviale", 5: "tramway historique",
                6: "téléphérique", 7: "funiculaire", 11: "trolleybus",
                12: "monorail"}.get(self.route_type, "transport")


@dataclass(frozen=True, slots=True)
class Trip:
    """Une course : un véhicule qui parcourt une ligne à une heure donnée."""
    id: TripId
    route_id: str       # renvoie vers Route
    headsign: str       # destination affichée en girouette
    service_id: str     # renvoie vers le calendrier


@dataclass(frozen=True, slots=True)
class Leg:
    """Un segment de trajet : soit un tronçon à bord, soit une marche."""
    mode: str           # "ride" ou "walk"
    from_stop: Stop
    to_stop: Stop
    departure: Seconds
    arrival: Seconds
    route: str | None = None
    headsign: str | None = None
    color: str = ""
    delay: Seconds = 0

    @property
    def duration(self) -> Seconds:
        return self.arrival - self.departure


@dataclass(frozen=True, slots=True)
class Departure:
    """Un passage à venir, tel qu'affiché sur une fiche horaire."""
    time: Seconds       # heure théorique
    stop: Stop          # le quai précis, qui peut différer d'un passage à l'autre
    route: str
    headsign: str
    color: str = ""
    delay: Seconds = 0

    @property
    def expected(self) -> Seconds:
        """Heure réellement attendue, retard compris."""
        return self.time + self.delay


@dataclass(frozen=True, slots=True)
class Journey:
    """Le résultat d'un calcul d'itinéraire."""
    legs: tuple[Leg, ...]
    day: date
    realtime: bool = False

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
        """Nombre de changements de véhicule."""
        return max(sum(1 for leg in self.legs if leg.mode == "ride") - 1, 0)


def format_time(value: Seconds) -> str:
    """91800 -> '25:30'. Le GTFS autorise les heures au-delà de 24h."""
    return f"{value // 3600:02d}:{value % 3600 // 60:02d}"


def parse_time(value: str) -> Seconds:
    """'25:30' ou '25:30:00' -> 91800."""
    parts = [int(p) for p in value.strip().split(":")]
    if len(parts) == 2:
        parts.append(0)
    h, m, s = parts
    return h * 3600 + m * 60 + s
