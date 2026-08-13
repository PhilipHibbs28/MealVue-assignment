from __future__ import annotations

import sqlite3
from collections.abc import Callable

Migration = tuple[int, Callable[[sqlite3.Connection], None]]


def migration_001(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE device_boots (
            device_id TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            registered_at TEXT NOT NULL,
            PRIMARY KEY (device_id, boot_id),
            UNIQUE (device_id, generation)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE telemetry_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            device_time TEXT NOT NULL,
            received_at TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            UNIQUE (device_id, sequence),
            FOREIGN KEY (device_id, boot_id)
                REFERENCES device_boots (device_id, boot_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE current_state (
            device_id TEXT NOT NULL,
            metric TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            device_time TEXT NOT NULL,
            received_at TEXT NOT NULL,
            value REAL NOT NULL,
            PRIMARY KEY (device_id, metric),
            FOREIGN KEY (device_id, boot_id)
                REFERENCES device_boots (device_id, boot_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX telemetry_events_received_at_idx
        ON telemetry_events (received_at DESC)
        """
    )


def migration_002(connection: sqlite3.Connection) -> None:
    """Scope event identity to (device_id, boot_id, sequence).

    Migration 001 uniqued telemetry_events on (device_id, sequence), which
    let a new boot's sequence numbers collide with an older boot's rows and
    be silently swallowed as duplicates. The protocol defines logical event
    identity as (deviceId, bootId, sequence).
    """
    connection.execute(
        """
        CREATE TABLE telemetry_events_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            device_time TEXT NOT NULL,
            received_at TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            UNIQUE (device_id, boot_id, sequence),
            FOREIGN KEY (device_id, boot_id)
                REFERENCES device_boots (device_id, boot_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO telemetry_events_v2
            (id, device_id, boot_id, generation, sequence, device_time,
             received_at, metric, value)
        SELECT id, device_id, boot_id, generation, sequence, device_time,
               received_at, metric, value
        FROM telemetry_events
        """
    )
    connection.execute("DROP TABLE telemetry_events")
    connection.execute(
        "ALTER TABLE telemetry_events_v2 RENAME TO telemetry_events"
    )
    connection.execute(
        """
        CREATE INDEX telemetry_events_received_at_idx
        ON telemetry_events (received_at DESC)
        """
    )


MIGRATIONS: list[Migration] = [(1, migration_001), (2, migration_002)]


def apply_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = {
        row[0]
        for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    }

    for version, migration in MIGRATIONS:
        if version in applied:
            continue

        connection.execute("BEGIN IMMEDIATE")
        try:
            migration(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
                (version,),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
