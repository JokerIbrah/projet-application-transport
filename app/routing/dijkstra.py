"""Dijkstra dépendant du temps — implémentation de référence.

Volontairement naïve et lente. Sert d'oracle pour valider le CSA
dans les tests, pas à répondre aux requêtes de l'API.
"""

import heapq
from datetime import date

from app.gtfs.models import Seconds, StopId

INFINITY = 1 << 30


def earliest_arrival(graph, origin: StopId, destination: StopId,
                     departure: Seconds, day: date) -> Seconds:
    """Heure d'arrivée au plus tôt. Retourne INFINITY si injoignable.

    Ne reconstruit pas le chemin : on ne compare que les heures.
    """
    src = graph._index[origin]
    dst = graph._index[destination]

    best = [INFINITY] * graph.stop_count
    best[src] = departure
    queue = [(departure, src)]          # (heure d'arrivée, arrêt)

    while queue:
        arrival, stop = heapq.heappop(queue)

        if stop == dst:
            return arrival
        if arrival > best[stop]:
            continue                     # entrée périmée

        # Correspondances à pied.
        for neighbour, walk in graph._footpaths.get(stop, ()):
            if arrival + walk < best[neighbour]:
                best[neighbour] = arrival + walk
                heapq.heappush(queue, (arrival + walk, neighbour))

        # Départs en véhicule : balayage linéaire assumé.
        for conn in graph._connections:
            if conn.from_idx != stop or conn.departure < arrival:
                continue
            if not graph._runs_on(conn.trip_idx, day):
                continue
            if conn.arrival < best[conn.to_idx]:
                best[conn.to_idx] = conn.arrival
                heapq.heappush(queue, (conn.arrival, conn.to_idx))

    return best[dst]