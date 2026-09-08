"""Rafraîchissement du GTFS-RT en tâche de fond.

Le GTFS statique dit ce qui *devrait* passer ; le GTFS-RT dit ce qui
passe vraiment. Le poller récupère les flux à intervalle régulier et
garde en mémoire deux choses seulement :

* `delays`   : trip_id -> retard en secondes, injecté dans le calcul ;
* `vehicles` : dernière position connue de chaque véhicule.

Toute panne du flux est non bloquante : l'application retombe alors sur
l'horaire théorique, ce qui vaut mieux que de ne rien afficher.
"""

import asyncio
import os
import time
from pathlib import Path

DEFAULT_INTERVAL = 30  # secondes entre deux rafraîchissements


class RealtimePoller:
    def __init__(self, graph=None, trip_updates: str | None = None,
                 vehicle_positions: str | None = None,
                 interval: int = DEFAULT_INTERVAL):
        self._graph = graph
        self._trip_updates = trip_updates or os.getenv("GTFS_RT_TRIP_UPDATES")
        self._vehicle_positions = (vehicle_positions
                                   or os.getenv("GTFS_RT_VEHICLE_POSITIONS"))
        self._interval = int(os.getenv("GTFS_RT_INTERVAL", interval))
        self._task: asyncio.Task | None = None
        self._updated_at: float | None = None

        self.delays: dict[str, int] = {}
        self.vehicles: list[dict] = []

    # Cycle de vie -----------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self._trip_updates or self._vehicle_positions)

    @property
    def age_seconds(self) -> float | None:
        """Fraîcheur des données. None tant que rien n'a été reçu."""
        if self._updated_at is None:
            return None
        return round(time.monotonic() - self._updated_at, 1)

    async def start(self) -> None:
        if not self.enabled:
            return  # aucune source configurée : mode horaire théorique
        await self.refresh()          # un premier passage synchrone
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.refresh()

    # Rafraîchissement -------------------------------------------------
    async def refresh(self) -> None:
        try:
            if self._trip_updates:
                feed = await self._fetch(self._trip_updates)
                self.delays = _parse_delays(feed)
            if self._vehicle_positions:
                feed = await self._fetch(self._vehicle_positions)
                self.vehicles = _parse_vehicles(feed)
            self._updated_at = time.monotonic()
        except Exception as exc:  # réseau, protobuf, source absente...
            # On garde le dernier état connu : dégradation, pas panne.
            print(f"[realtime] flux indisponible ({exc.__class__.__name__}: {exc})")

    @staticmethod
    async def _fetch(source: str) -> bytes:
        """Accepte une URL http(s) ou un chemin de fichier local.

        Le fichier local sert à travailler hors connexion, avec les
        échantillons rangés dans gtfs_metz/.
        """
        if source.startswith(("http://", "https://")):
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(source)
                response.raise_for_status()
                return response.content
        return Path(source).read_bytes()


# Décodage protobuf ----------------------------------------------------

def _feed(payload: bytes):
    """Import tardif : l'application démarre même sans les bindings."""
    from google.transit import gtfs_realtime_pb2

    message = gtfs_realtime_pb2.FeedMessage()
    message.ParseFromString(payload)
    return message


def _parse_delays(payload: bytes) -> dict[str, int]:
    """Un retard par course. On retient la dernière prévision connue."""
    delays: dict[str, int] = {}
    for entity in _feed(payload).entity:
        if not entity.HasField("trip_update"):
            continue
        update = entity.trip_update
        delay = None
        for stop_time in update.stop_time_update:
            if stop_time.HasField("departure") and stop_time.departure.delay:
                delay = stop_time.departure.delay
            elif stop_time.HasField("arrival") and stop_time.arrival.delay:
                delay = stop_time.arrival.delay
        if delay:
            delays[update.trip.trip_id] = int(delay)
    return delays


def _parse_vehicles(payload: bytes) -> list[dict]:
    """Positions instantanées, telles quelles, pour affichage."""
    vehicles = []
    for entity in _feed(payload).entity:
        if not entity.HasField("vehicle"):
            continue
        vehicle = entity.vehicle
        vehicles.append({
            "id": entity.id,
            "trip_id": vehicle.trip.trip_id or None,
            "route": vehicle.trip.route_id or None,
            "lat": round(vehicle.position.latitude, 6),
            "lon": round(vehicle.position.longitude, 6),
            "bearing": round(vehicle.position.bearing) or None,
            "timestamp": vehicle.timestamp or None,
        })
    return vehicles
