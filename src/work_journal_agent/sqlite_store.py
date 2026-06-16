from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from .config import default_data_dir


SCHEMA_VERSION = 1
SUMMARY_NAMESPACE = "summary"
MIGRATION_EVENTS = "events-jsonl-v1"
MIGRATION_REQUIREMENTS = "requirements-json-v1"
MIGRATION_AI_SUMMARY = "ai-cache-summary-v1"
MIGRATION_AI_KNOWLEDGE = "ai-cache-knowledge-v1"
MIGRATION_AI_CLUSTER_REVIEW = "ai-cache-cluster-review-v1"


@dataclass(frozen=True)
class LegacyMigrationResult:
    database_path: Path
    events: int = 0
    requirement_threads: int = 0
    requirement_daily: int = 0
    status: int = 0
    ai_summary: int = 0
    ai_knowledge: int = 0
    ai_cluster_review: int = 0


def database_path_for(storage: Any | None = None) -> Path:
    path = getattr(storage, "database_path", None)
    if path:
        return Path(path)
    return default_data_dir() / "work-journal.db"


class WorkJournalStore:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self.ensure_schema(conn)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                occurred_on TEXT NOT NULL,
                cwd TEXT,
                summary TEXT NOT NULL,
                raw_request TEXT,
                decision TEXT,
                files_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                source_event_key TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_events_source_key
                ON events(source_event_key)
                WHERE source_event_key IS NOT NULL AND source_event_key != '';
            CREATE INDEX IF NOT EXISTS idx_events_day ON events(occurred_on);
            CREATE INDEX IF NOT EXISTS idx_events_source_type ON events(source, event_type);
            CREATE TABLE IF NOT EXISTS requirement_threads (
                id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_requirement_threads_updated
                ON requirement_threads(updated_at);
            CREATE TABLE IF NOT EXISTS requirement_daily (
                day TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_status (
                key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ai_cache (
                namespace TEXT NOT NULL,
                day TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(namespace, day)
            );
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    def migration_done(self, conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute("SELECT 1 FROM migrations WHERE name = ?", (name,)).fetchone()
        return row is not None

    def mark_migration_done(self, conn: sqlite3.Connection, name: str) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO migrations(name, applied_at) VALUES(?, ?)",
            (name, now_iso()),
        )

    def insert_event_dict(self, conn: sqlite3.Connection, payload: dict[str, Any]) -> bool:
        event_id = str(payload.get("id") or "").strip()
        summary = str(payload.get("summary") or "").strip()
        occurred_at = str(payload.get("occurred_at") or "").strip()
        if not event_id or not summary or not occurred_at:
            return False
        occurred_on = occurred_at[:10]
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO events(
                id, source, event_type, occurred_at, occurred_on, cwd, summary,
                raw_request, decision, files_json, metadata_json, source_event_key
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                str(payload.get("source") or "manual"),
                str(payload.get("event_type") or "note"),
                occurred_at,
                occurred_on,
                payload.get("cwd"),
                summary,
                payload.get("raw_request"),
                payload.get("decision"),
                json.dumps(payload.get("files") or [], ensure_ascii=False, sort_keys=True),
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                source_event_key(metadata),
            ),
        )
        return cursor.rowcount > 0

    def read_event_dicts(self, conn: sqlite3.Connection, *, day: date | None = None) -> list[dict[str, Any]]:
        params: tuple[str, ...] = ()
        where = ""
        if day is not None:
            where = "WHERE occurred_on = ?"
            params = (day.isoformat(),)
        rows = conn.execute(
            f"""
            SELECT id, source, event_type, occurred_at, cwd, summary, raw_request,
                   decision, files_json, metadata_json
            FROM events
            {where}
            ORDER BY occurred_at, id
            """,
            params,
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "id": row["id"],
                    "source": row["source"],
                    "event_type": row["event_type"],
                    "occurred_at": row["occurred_at"],
                    "cwd": row["cwd"],
                    "summary": row["summary"],
                    "raw_request": row["raw_request"],
                    "decision": row["decision"],
                    "files": json_list(row["files_json"]),
                    "metadata": json_dict(row["metadata_json"]),
                }
            )
        return result

    def migrate_events_from_jsonl(self, conn: sqlite3.Connection, inbox_path: Path) -> int:
        imported = 0
        if inbox_path.exists():
            with inbox_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        payload = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict) and self.insert_event_dict(conn, payload):
                        imported += 1
        self.mark_migration_done(conn, MIGRATION_EVENTS)
        return imported

    def migrate_requirements_from_json(self, conn: sqlite3.Connection, *, root: Path, threads_path: Path, daily_dir: Path, status_path: Path) -> tuple[int, int, int]:
        thread_count = 0
        daily_count = 0
        status_count = 0
        threads_payload = read_json_object(threads_path)
        for item in threads_payload.get("requirements") or []:
            if isinstance(item, dict) and item.get("id"):
                if self.save_requirement_thread(conn, str(item["id"]), item):
                    thread_count += 1
        if daily_dir.exists():
            for path in sorted(daily_dir.glob("*.json")):
                try:
                    day = date.fromisoformat(path.stem)
                except ValueError:
                    continue
                payload = read_json_object(path)
                if payload:
                    if self.save_daily_review(conn, day, payload):
                        daily_count += 1
        status_payload = read_json_object(status_path)
        if status_payload:
            if self.save_status(conn, status_payload):
                status_count = 1
        self.mark_migration_done(conn, MIGRATION_REQUIREMENTS)
        return thread_count, daily_count, status_count

    def load_requirement_threads(self, conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        rows = conn.execute("SELECT id, payload_json FROM requirement_threads ORDER BY updated_at DESC").fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = json_dict(row["payload_json"])
            if payload:
                result[str(row["id"])] = payload
        return result

    def save_requirement_thread(self, conn: sqlite3.Connection, requirement_id: str, payload: dict[str, Any]) -> bool:
        if self.payload_matches(conn, "requirement_threads", "id", requirement_id, payload):
            return False
        conn.execute(
            "INSERT OR REPLACE INTO requirement_threads(id, payload_json, updated_at) VALUES(?, ?, ?)",
            (
                requirement_id,
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                str(payload.get("updated_at") or now_iso()),
            ),
        )
        return True

    def load_daily_review(self, conn: sqlite3.Connection, day: date) -> dict[str, Any]:
        row = conn.execute("SELECT payload_json FROM requirement_daily WHERE day = ?", (day.isoformat(),)).fetchone()
        return json_dict(row["payload_json"]) if row else {}

    def save_daily_review(self, conn: sqlite3.Connection, day: date, payload: dict[str, Any]) -> bool:
        if self.payload_matches(conn, "requirement_daily", "day", day.isoformat(), payload):
            return False
        conn.execute(
            "INSERT OR REPLACE INTO requirement_daily(day, payload_json, updated_at) VALUES(?, ?, ?)",
            (
                day.isoformat(),
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                str(payload.get("updated_at") or now_iso()),
            ),
        )
        return True

    def load_status(self, conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute("SELECT payload_json FROM app_status WHERE key = 'current'").fetchone()
        return json_dict(row["payload_json"]) if row else {}

    def save_status(self, conn: sqlite3.Connection, payload: dict[str, Any]) -> bool:
        if self.payload_matches(conn, "app_status", "key", "current", payload):
            return False
        conn.execute(
            "INSERT OR REPLACE INTO app_status(key, payload_json, updated_at) VALUES('current', ?, ?)",
            (
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                str(payload.get("updated_at") or now_iso()),
            ),
        )
        return True

    def load_ai_cache(self, conn: sqlite3.Connection, namespace: str, day: date) -> dict[str, Any]:
        row = conn.execute(
            "SELECT payload_json FROM ai_cache WHERE namespace = ? AND day = ?",
            (namespace, day.isoformat()),
        ).fetchone()
        return json_dict(row["payload_json"]) if row else {}

    def save_ai_cache(self, conn: sqlite3.Connection, namespace: str, day: date, payload: dict[str, Any]) -> bool:
        row = conn.execute(
            "SELECT payload_json FROM ai_cache WHERE namespace = ? AND day = ?",
            (namespace, day.isoformat()),
        ).fetchone()
        if row and json_dict(row["payload_json"]) == payload:
            return False
        conn.execute(
            "INSERT OR REPLACE INTO ai_cache(namespace, day, payload_json, updated_at) VALUES(?, ?, ?, ?)",
            (
                namespace,
                day.isoformat(),
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                str(payload.get("updated_at") or now_iso()),
            ),
        )
        return True

    def prune_ai_cache(self, conn: sqlite3.Connection, namespace: str, *, cutoff: date) -> None:
        conn.execute("DELETE FROM ai_cache WHERE namespace = ? AND day < ?", (namespace, cutoff.isoformat()))

    def migrate_ai_cache_from_json(self, conn: sqlite3.Connection, *, namespace: str, cache_dir: Path, migration_name: str) -> int:
        imported = 0
        if cache_dir.exists():
            for path in sorted(cache_dir.glob("*.json")):
                try:
                    day = date.fromisoformat(path.stem)
                except ValueError:
                    continue
                payload = read_json_object(path)
                if payload:
                    if self.save_ai_cache(conn, namespace, day, payload):
                        imported += 1
        self.mark_migration_done(conn, migration_name)
        return imported

    def payload_matches(self, conn: sqlite3.Connection, table: str, key_column: str, key_value: str, payload: dict[str, Any]) -> bool:
        row = conn.execute(f"SELECT payload_json FROM {table} WHERE {key_column} = ?", (key_value,)).fetchone()
        return bool(row and json_dict(row["payload_json"]) == payload)


def store_for(storage: Any | None = None) -> WorkJournalStore:
    return WorkJournalStore(database_path_for(storage))


def migrate_legacy_storage(
    storage: Any,
    *,
    legacy_inbox_path: Path,
    legacy_requirements_dir: Path,
    legacy_state_dir: Path,
    legacy_ai_cache_dir: Path,
) -> LegacyMigrationResult:
    store = store_for(storage)
    with store.connect() as conn:
        events = store.migrate_events_from_jsonl(conn, legacy_inbox_path)
        threads, daily, status = store.migrate_requirements_from_json(
            conn,
            root=legacy_requirements_dir,
            threads_path=legacy_requirements_dir / "threads.json",
            daily_dir=legacy_requirements_dir / "daily",
            status_path=legacy_state_dir / "status.json",
        )
        ai_summary = store.migrate_ai_cache_from_json(
            conn,
            namespace=SUMMARY_NAMESPACE,
            cache_dir=legacy_ai_cache_dir,
            migration_name=MIGRATION_AI_SUMMARY,
        )
        ai_knowledge = store.migrate_ai_cache_from_json(
            conn,
            namespace="knowledge",
            cache_dir=legacy_ai_cache_dir / "knowledge",
            migration_name=MIGRATION_AI_KNOWLEDGE,
        )
        ai_cluster_review = store.migrate_ai_cache_from_json(
            conn,
            namespace="cluster-review",
            cache_dir=legacy_ai_cache_dir / "cluster-review",
            migration_name=MIGRATION_AI_CLUSTER_REVIEW,
        )
    return LegacyMigrationResult(
        database_path=store.database_path,
        events=events,
        requirement_threads=threads,
        requirement_daily=daily,
        status=status,
        ai_summary=ai_summary,
        ai_knowledge=ai_knowledge,
        ai_cluster_review=ai_cluster_review,
    )


def is_sqlite_storage(value: Any) -> bool:
    return getattr(value, "database_path", None) is not None


def event_to_dict(event: Any) -> dict[str, Any]:
    if is_dataclass(event):
        payload = asdict(event)
    elif hasattr(event, "to_dict"):
        payload = event.to_dict()
    else:
        payload = dict(event)
    files = payload.get("files")
    if isinstance(files, tuple):
        payload["files"] = list(files)
    occurred_at = payload.get("occurred_at")
    if isinstance(occurred_at, datetime):
        payload["occurred_at"] = occurred_at.isoformat()
    return payload


def source_event_key(metadata: dict[str, Any]) -> str | None:
    for key in ("codex_event_key", "opencode_event_key", "kun_event_key", "zcode_event_key"):
        value = metadata.get(key)
        if value:
            return f"{key}:{value}"
    return None


def json_dict(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def json_list(text: str | None) -> list[Any]:
    if not text:
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()
