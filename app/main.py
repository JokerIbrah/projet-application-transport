from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles

from app.routing.graph import TransitGraph
from app.realtime.poller import RealtimePoller
from app.schemas import RouteResponse, StopSummary


# Cycle de vie de l'application 
@asynccontextmanager
async def lifespan(app: FastAPI):
    #garantir que le parsing du GTFS est fait avant de lancer l'application
    # Au démarrage : on charge le graphe UNE fois
    app.state.graph = TransitGraph.load("data/gtfs.sqlite")
    app.state.poller = RealtimePoller(app.state.graph)
    await app.state.poller.start()

    yield  # l'application tourne

    # À l'arrêt : on coupe proprement la tâche de fond
    await app.state.poller.stop()


app = FastAPI(
    title="Metz Transit Router",
    description="Calcul d'itinéraires sur le réseau Le Met'",
    lifespan=lifespan,
)


# Endpoints de l'API
@app.get("/api/stops", response_model=list[StopSummary])
def search_stops(request: Request, q: str = Query(min_length=2)):
    return request.app.state.graph.search_stops(q, limit=10)


@app.get("/api/route", response_model=RouteResponse) #valide la sortie de la fonction compute_route pour alimenter la doc
def compute_route(
    request: Request,
    origin: str,
    destination: str,
    departure: str,
    realtime: bool = False,
):
    graph = request.app.state.graph
    delays = request.app.state.poller.delays if realtime else {}
    try:
        return graph.find_route(origin, destination, departure, delays)
    except KeyError as exc:
        raise HTTPException(404, f"Arrêt inconnu : {exc}")


@app.get("/api/vehicles")
def live_vehicles(request: Request):
    return request.app.state.poller.vehicles


#  Santé 
@app.get("/health")
def health(request: Request):
    return {
        "status": "ok",
        "stops": request.app.state.graph.stop_count,
        "realtime_age_s": request.app.state.poller.age_seconds,
    }


#Frontend
#Test des routes dans l'ordre des déclarations, si aucune ne correspond, on renvoie le front
app.mount("/", StaticFiles(directory="app/static", html=True))