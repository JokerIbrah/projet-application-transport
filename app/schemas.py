"""Contrats d'entrée/sortie de l'API.

Les objets métier (`app.gtfs.models`) ignorent tout du web ; ces modèles
les traduisent en JSON stable et documentent l'API automatiquement.
"""

from pydantic import BaseModel, Field

from app.gtfs.models import (Departure, Journey, Leg, Route, Stop,
                             format_time)


def _hex(color: str) -> str | None:
    """Le GTFS donne 'B22E75' ; le web attend '#B22E75'."""
    return f"#{color}" if color else None


class StopSummary(BaseModel):
    id: str = Field(description="Identifiant GTFS du quai")
    name: str
    lat: float | None = None
    lon: float | None = None

    @classmethod
    def of(cls, stop: Stop) -> "StopSummary":
        return cls(id=stop.id, name=stop.name, lat=stop.lat, lon=stop.lon)


class RouteSummary(BaseModel):
    id: str
    name: str = Field(description="Nom affiché sur le véhicule")
    long_name: str = ""
    mode: str = Field(description="tram, bus…")
    color: str | None = None
    text_color: str | None = None

    @classmethod
    def of(cls, route: Route) -> "RouteSummary":
        return cls(
            id=route.id,
            name=route.name,
            long_name=route.long_name,
            mode=route.mode,
            color=_hex(route.color),
            text_color=_hex(route.text_color),
        )


class RouteDetail(RouteSummary):
    destinations: list[str] = Field(description="Girouettes rencontrées")
    service_days: int = Field(description="Jours de circulation chargés")
    stops: list[StopSummary]


class DepartureOut(BaseModel):
    time: str = Field(description="Heure théorique, HH:MM")
    expected: str = Field(description="Heure attendue, retard compris")
    route: str
    headsign: str
    stop: str = Field(description="Quai précis du départ")
    color: str | None = None
    delay_min: int = 0

    @classmethod
    def of(cls, departure: Departure) -> "DepartureOut":
        return cls(
            time=format_time(departure.time),
            expected=format_time(departure.expected),
            route=departure.route,
            headsign=departure.headsign,
            stop=departure.stop.name,
            color=_hex(departure.color),
            delay_min=round(departure.delay / 60),
        )


class LegOut(BaseModel):
    mode: str = Field(description="'ride' à bord, 'walk' à pied")
    from_stop: str
    to_stop: str
    departure: str = Field(description="HH:MM, heure locale du réseau")
    arrival: str
    duration_min: int
    route: str | None = None
    headsign: str | None = None
    color: str | None = None
    delay_min: int = 0

    @classmethod
    def of(cls, leg: Leg) -> "LegOut":
        return cls(
            mode=leg.mode,
            from_stop=leg.from_stop.name,
            to_stop=leg.to_stop.name,
            departure=format_time(leg.departure),
            arrival=format_time(leg.arrival),
            duration_min=round(leg.duration / 60),
            route=leg.route,
            headsign=leg.headsign,
            color=_hex(leg.color),
            delay_min=round(leg.delay / 60),
        )


class RouteResponse(BaseModel):
    departure: str
    arrival: str
    duration_min: int
    transfers: int
    realtime: bool
    legs: list[LegOut]

    @classmethod
    def of(cls, journey: Journey) -> "RouteResponse":
        return cls(
            departure=format_time(journey.departure),
            arrival=format_time(journey.arrival),
            duration_min=round(journey.duration / 60),
            transfers=journey.transfers,
            realtime=journey.realtime,
            legs=[LegOut.of(leg) for leg in journey.legs],
        )


class NetworkInfo(BaseModel):
    """Ce que contient le GTFS chargé, en un coup d'œil."""
    stations: int
    stops: int
    routes: int
    trips: int
    connections: int
    first_day: str | None = None
    last_day: str | None = None
    realtime: bool = False
    realtime_age_s: float | None = None
