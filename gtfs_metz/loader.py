"""Charge un GTFS statique dans une base SQLite exploitable.

    python -m gtfs_metz.loader gtfs_metz/LEMET-gtfs.zip data/gtfs.sqlite

Le GTFS contient beaucoup de champs dont l'application n'a pas l'usage
(shapes, couleurs, accessibilité...). On ne retient que le strict
nécessaire au calcul d'itinéraire : où, quand, quelle course, quel jour.

Décision assumée : on privilégie la rapidité de lecture à l'espace disque.
Le calendrier est déplié — une ligne par (service, jour) — plutôt que
recalculé à chaque requête à partir des règles hebdomadaires.
"""

import argparse
import csv
import io
import sqlite3
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday")


# Schéma ---------------------------------------------------------------

def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        DROP TABLE IF EXISTS stops;
        DROP TABLE IF EXISTS routes;
        DROP TABLE IF EXISTS trips;
        DROP TABLE IF EXISTS stop_times;
        DROP TABLE IF EXISTS service_dates;

        CREATE TABLE stops (
            stop_id TEXT PRIMARY KEY,
            stop_name TEXT NOT NULL,
            stop_lat REAL, stop_lon REAL,
            station_id TEXT          -- arrêt logique regroupant les quais
        );
        CREATE TABLE routes (
            route_id TEXT PRIMARY KEY,
            short_name TEXT,         -- 'MA', 'L1' : ce qui est écrit sur le bus
            long_name TEXT,          -- 'BORNY - WOIPPY ST-ELOY'
            color TEXT,              -- hexadécimal, sans le '#'
            text_color TEXT,
            route_type INTEGER       -- 0 tram, 3 bus (spécification GTFS)
        );
        CREATE TABLE trips (
            trip_id TEXT PRIMARY KEY,
            route_id TEXT NOT NULL,
            headsign TEXT,
            service_id TEXT NOT NULL
        );
        CREATE TABLE stop_times (
            trip_id TEXT NOT NULL,
            stop_id TEXT NOT NULL,
            stop_sequence INTEGER NOT NULL,
            arrival_s INTEGER,       -- secondes depuis minuit
            departure_s INTEGER
        );
        CREATE TABLE service_dates (
            service_id TEXT NOT NULL,
            day TEXT NOT NULL        -- 'YYYY-MM-DD'
        );
    """)


# Helpers --------------------------------------------------------------

def parse_time(value: str) -> int | None:
    """'25:30:00' -> 91800. Les heures supérieures à 24 sont légales."""
    if not value:
        return None
    h, m, s = (int(p) for p in value.split(":"))
    return h * 3600 + m * 60 + s


def parse_day(value: str) -> date:
    """'20260908' -> date(2026, 9, 8)."""
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def read_table(zf: zipfile.ZipFile, name: str):
    """Générateur de dicts. utf-8-sig gère le BOM, très fréquent.

    Renvoie une séquence vide si le fichier est absent : calendar.txt et
    transfers.txt sont optionnels dans la spécification GTFS.
    """
    if f"{name}.txt" not in zf.namelist():
        return
    with zf.open(f"{name}.txt") as raw:
        stream = io.TextIOWrapper(raw, encoding="utf-8-sig")
        yield from csv.DictReader(stream)


# Chargement -----------------------------------------------------------

def load_stops(conn: sqlite3.Connection, zf: zipfile.ZipFile) -> None:
    """Un quai par ligne. station_id regroupe les quais d'un même arrêt."""
    rows = (
        (r["stop_id"], r["stop_name"],
         float(r["stop_lat"]) if r.get("stop_lat") else None,
         float(r["stop_lon"]) if r.get("stop_lon") else None,
         r.get("parent_station") or r["stop_id"])
        for r in read_table(zf, "stops")
    )
    conn.executemany("INSERT OR REPLACE INTO stops VALUES (?, ?, ?, ?, ?)", rows)


def load_routes(conn: sqlite3.Connection, zf: zipfile.ZipFile) -> None:
    """Les lignes commerciales, couleurs de la charte comprises."""
    rows = (
        (r["route_id"], r.get("route_short_name") or "",
         r.get("route_long_name") or "", (r.get("route_color") or "").strip(),
         (r.get("route_text_color") or "").strip(),
         int(r.get("route_type") or 3))
        for r in read_table(zf, "routes")
    )
    conn.executemany(
        "INSERT OR REPLACE INTO routes VALUES (?, ?, ?, ?, ?, ?)", rows)


def load_trips(conn: sqlite3.Connection, zf: zipfile.ZipFile) -> None:
    """Une course par ligne, rattachée à sa ligne commerciale."""
    rows = (
        (r["trip_id"], r["route_id"], r.get("trip_headsign") or "",
         r["service_id"])
        for r in read_table(zf, "trips")
    )
    conn.executemany("INSERT OR REPLACE INTO trips VALUES (?, ?, ?, ?)", rows)


def load_stop_times(conn: sqlite3.Connection, zf: zipfile.ZipFile) -> None:
    """Le gros morceau : plusieurs centaines de milliers de lignes.

    On passe par un générateur pour ne jamais tout charger en mémoire.
    """
    rows = (
        (r["trip_id"], r["stop_id"], int(r["stop_sequence"]),
         parse_time(r["arrival_time"]), parse_time(r["departure_time"]))
        for r in read_table(zf, "stop_times")
    )
    conn.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", rows)


def expand_calendar(conn: sqlite3.Connection, zf: zipfile.ZipFile) -> None:
    """Déplie calendar.txt (règles hebdomadaires) puis applique
    calendar_dates.txt (exceptions : 1 = ajout, 2 = suppression).

    Le GTFS du Met' n'utilise que les exceptions, mais les deux sources
    sont gérées : un autre réseau ne se comporterait pas pareil.
    """
    active: dict[str, set[date]] = {}

    for r in read_table(zf, "calendar"):
        days = {i for i, name in enumerate(WEEKDAYS) if r[name] == "1"}
        if not days:
            continue
        start, end = parse_day(r["start_date"]), parse_day(r["end_date"])
        bucket = active.setdefault(r["service_id"], set())
        day = start
        while day <= end:
            if day.weekday() in days:
                bucket.add(day)
            day += timedelta(days=1)

    for r in read_table(zf, "calendar_dates"):
        day = parse_day(r["date"])
        bucket = active.setdefault(r["service_id"], set())
        if r["exception_type"] == "1":
            bucket.add(day)
        else:
            bucket.discard(day)

    conn.executemany(
        "INSERT INTO service_dates VALUES (?, ?)",
        ((sid, d.isoformat()) for sid, days in active.items() for d in days),
    )


# Post-traitement ------------------------------------------------------

def build_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE INDEX idx_st_trip ON stop_times(trip_id, stop_sequence);
        CREATE INDEX idx_st_stop ON stop_times(stop_id, departure_s);
        CREATE INDEX idx_sd ON service_dates(service_id, day);
        CREATE INDEX idx_stops_station ON stops(station_id);
        CREATE INDEX idx_trips_route ON trips(route_id);
    """)


def summary(conn: sqlite3.Connection) -> str:
    def count(table: str) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    return (f"{count('stops')} arrêts, {count('routes')} lignes, "
            f"{count('trips')} courses, {count('stop_times')} passages, "
            f"{count('service_dates')} jours de service")


def main(gtfs_zip: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    # Base reconstruite à volonté : on peut se passer du journal et de la
    # synchronisation disque, ce qui divise le temps de chargement.
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")

    with zipfile.ZipFile(gtfs_zip) as zf:
        create_schema(conn)
        load_stops(conn, zf)
        load_routes(conn, zf)
        load_trips(conn, zf)
        load_stop_times(conn, zf)
        expand_calendar(conn, zf)
        build_indexes(conn)

    conn.commit()
    print(f"{db_path} : {summary(conn)}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Charge un GTFS en SQLite.")
    parser.add_argument("gtfs_zip", nargs="?", type=Path,
                        default=Path("gtfs_metz/LEMET-gtfs.zip"))
    parser.add_argument("db_path", nargs="?", type=Path,
                        default=Path("data/gtfs.sqlite"))
    args = parser.parse_args()
    if not args.gtfs_zip.exists():
        sys.exit(f"Archive GTFS introuvable : {args.gtfs_zip}")
    main(args.gtfs_zip, args.db_path)
