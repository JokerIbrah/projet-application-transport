"""Point d'entrée HTTP. Rien de métier ici : on branche, on traduit.

    uvicorn app.main:app --reload
"""

import os
from contextlib import asynccontextmanager
from datetime import date

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles

from app.gtfs.models import parse_time
from app.realtime.poller import RealtimePoller
from app.routing.graph import NoRouteFound, TransitGraph
from app.schemas import (DepartureOut, NetworkInfo, RouteDetail,
                         RouteResponse, RouteSummary, StopSummary)

load_dotenv()
DB_PATH = os.getenv("GTFS_DB", "data/gtfs.sqlite")


# Cycle de vie de l'application ---------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Au démarrage : on charge le graphe UNE fois. Le parsing du GTFS est
    # donc garanti terminé avant la première requête.
    app.state.graph = TransitGraph.load(DB_PATH)
    app.state.poller = RealtimePoller(app.state.graph)
    await app.state.poller.start()

    yield  # l'application tourne

    # À l'arrêt : on coupe proprement la tâche de fond.
    await app.state.poller.stop()


app = FastAPI(
    title="Metz Transit Router",
    description="Calcul d'itinéraires sur le réseau Le Met'.",
    version="0.2.0",
    lifespan=lifespan,
)


def _seconds(value: str) -> int:
    """'08:30' -> 30600. Une heure illisible est une erreur de requête."""
    try:
        return parse_time(value)
    except ValueError:
        raise HTTPException(422, f"Heure illisible : {value!r} (attendu HH:MM)")


# API -----------------------------------------------------------------
@app.get("/api/stops", response_model=list[StopSummary],
         summary="Recherche d'arrêts par nom")
def search_stops(request: Request, q: str = Query(min_length=2)):
    return [StopSummary.of(stop)
            for stop in request.app.state.graph.search_stops(q, limit=10)]


@app.get("/api/stops/{stop_id}/departures",
         response_model=list[DepartureOut],
         summary="Prochains passages à un arrêt")
def stop_departures(
    request: Request,
    stop_id: str,
    after: str = Query("00:00", description="À partir de cette heure, HH:MM"),
    day: date | None = Query(None, description="Jour consulté, AAAA-MM-JJ"),
    limit: int = Query(12, ge=1, le=50),
    realtime: bool = Query(False),
):
    graph = request.app.state.graph
    delays = request.app.state.poller.delays if realtime else {}
    try:
        departures = graph.departures(stop_id, day, _seconds(after),
                                      limit, delays)
    except KeyError as exc:
        raise HTTPException(404, f"Arrêt inconnu : {exc}")
    return [DepartureOut.of(d) for d in departures]


@app.get("/api/routes", response_model=list[RouteSummary],
         summary="Lignes du réseau")
def list_routes(request: Request):
    return [RouteSummary.of(route)
            for route in request.app.state.graph.routes()]


@app.get("/api/routes/{route_id}", response_model=RouteDetail,
         summary="Détail d'une ligne et arrêts desservis")
def route_detail(request: Request, route_id: str):
    graph = request.app.state.graph
    try:
        stops = graph.route_stops(route_id)
    except KeyError:
        raise HTTPException(404, f"Ligne inconnue : {route_id}")
    return RouteDetail(
        **RouteSummary.of(graph.route(route_id)).model_dump(),
        destinations=graph.route_destinations(route_id),
        service_days=graph.route_days(route_id),
        stops=[StopSummary.of(stop) for stop in stops],
    )


@app.get("/api/route", response_model=RouteResponse,
         summary="Itinéraire au plus tôt")
def compute_route(
    request: Request,
    origin: str = Query(description="stop_id de départ"),
    destination: str = Query(description="stop_id d'arrivée"),
    departure: str = Query(description="Heure de départ, HH:MM"),
    day: date | None = Query(None, description="Jour du trajet, AAAA-MM-JJ"),
    realtime: bool = Query(False, description="Appliquer les retards observés"),
):
    graph = request.app.state.graph
    delays = request.app.state.poller.delays if realtime else {}

    try:
        journey = graph.find_route(origin, destination, _seconds(departure),
                                   delays, day)
    except KeyError as exc:
        raise HTTPException(404, f"Arrêt inconnu : {exc}")
    except NoRouteFound as exc:
        raise HTTPException(404, str(exc))

    return RouteResponse.of(journey)


@app.get("/api/vehicles", summary="Positions temps réel des véhicules")
def live_vehicles(request: Request):
    return request.app.state.poller.vehicles


@app.get("/api/network", response_model=NetworkInfo,
         summary="Contenu du GTFS chargé et état du temps réel")
def network(request: Request):
    graph = request.app.state.graph
    poller = request.app.state.poller
    period = graph.service_period()
    return NetworkInfo(
        stations=graph.station_count,
        stops=graph.stop_count,
        routes=graph.route_count,
        trips=graph.trip_count,
        connections=graph.connection_count,
        first_day=period[0].isoformat() if period else None,
        last_day=period[1].isoformat() if period else None,
        realtime=poller.enabled,
        realtime_age_s=poller.age_seconds,
    )


# Frontend ------------------------------------------------------------
# Les routes sont testées dans l'ordre de déclaration : ce montage en
# dernier attrape tout ce qui n'est pas une route d'API.
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
