"""Charge un GTFS statique dans une base SQLite exploitable."""

import csv
import io
import sqlite3
import zipfile
from datetime import date, timedelta
from pathlib import Path

# Colonnes retenues par fichier. Tout le reste est ignoré :
# le GTFS contient beaucoup de champs dont tu n'as pas l'usage.
SCHEMA = {
    "stops": ["stop_id", "stop_name", "stop_lat", "stop_lon", "parent_station"],
    "routes": ["route_id", "route_short_name", "route_type"],
    "trips": ["trip_id", "route_id", "service_id", "trip_headsign"],
    "stop_times": ["trip_id", "stop_id", "stop_sequence",
                   "arrival_time", "departure_time"],
}


# Schéma 
def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        DROP TABLE IF EXISTS stops;
        DROP TABLE IF EXISTS stop_times;
        DROP TABLE IF EXISTS service_dates;

        CREATE TABLE stops (
            stop_id TEXT PRIMARY KEY,
            stop_name TEXT NOT NULL,
            stop_lat REAL, stop_lon REAL,
            station_id TEXT          -- arrêt logique regroupé
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


# Helpers 
def parse_time(value: str) -> int | None:
    """'25:30:00' -> 91800. Les heures > 24 sont légales en GTFS."""
    if not value:
        return None
    h, m, s = (int(p) for p in value.split(":"))
    return h * 3600 + m * 60 + s


def read_table(zf: zipfile.ZipFile, name: str):
    """Générateur de dicts. utf-8-sig gère le BOM, très fréquent."""
    with zf.open(f"{name}.txt") as raw:
        stream = io.TextIOWrapper(raw, encoding="utf-8-sig")
        yield from csv.DictReader(stream)


# Chargement 
def load_stop_times(conn, zf) -> None:
    rows = (
        (r["trip_id"], r["stop_id"], int(r["stop_sequence"]),
         parse_time(r["arrival_time"]), parse_time(r["departure_time"]))
        for r in read_table(zf, "calendar_dates"):
        day = date.fromisoformat_compact(r["date"])
        bucket = active.setdefault(r["service_id"], set())
        bucket.add(day) if r["exception_type"] == "1" else bucket.discard(day)

    conn.executemany(
        "INSERT INTO service_dates VALUES (?, ?)",
        ((sid, d.isoformat()) for sid, days in active.items() for d in days),
    )


# Post-traitement 
def build_indexes(conn) -> None:
    conn.executescript("""
        CREATE INDEX idx_st_trip ON stop_times(trip_id, stop_sequence);
        CREATE INDEX idx_st_stop ON stop_times(stop_id, departure_s);
        CREATE INDEX idx_sd ON service_dates(service_id, day);
    """)


def main(gtfs_zip: Path, db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    # désactiver le journal et la synchronisation pour accélérer le chargement
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")

    with zipfile.ZipFile(gtfs_zip) as zf:
        create_schema(conn)
        load_stop_times(conn, zf)
        expand_calendar(conn, zf)
        group_stations(conn) # regroupement des stations et arrêts logiques
        build_indexes(conn)

    conn.commit()
    conn.close()