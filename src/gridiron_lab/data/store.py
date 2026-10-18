"""Append-only Parquet, a rebuildable DuckDB file, and ``as_of``.

Re-ingesting a row whose payload is unchanged appends nothing. ``row_hash``
covers every column except ``ingested_at`` and itself. A changed payload is
a new version. ``as_of`` keeps, per entity key, the latest version whose
``known_as_of`` is strictly before the cutoff.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gridiron_lab.data.errors import IngestError
from gridiron_lab.data.schema import TABLE_NAMES, TableSpec, coerce, require_table, stamp_ok
from gridiron_lab.logging import get_logger

logger = get_logger("data.store")

Clock = Callable[[], datetime]


def _canon(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise IngestError("row values must be finite")
        return value
    return str(value)


def row_hash(row: Mapping[str, object]) -> str:
    payload = {
        key: _canon(value)
        for key, value in row.items()
        if key not in {"row_hash", "ingested_at"}
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parquet_list(files: Sequence[Path]) -> str:
    quoted = ", ".join("'" + str(path).replace("'", "''") + "'" for path in files)
    return f"read_parquet([{quoted}], union_by_name = true)"


class ParquetStore:
    """Write new Parquet parts under ``data_dir/raw/<table>/season=<n>/``."""

    def __init__(self, data_dir: Path, *, clock: Clock | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def duckdb_path(self) -> Path:
        return self.data_dir / "gridiron.duckdb"

    def append(self, table: str, rows: Sequence[Mapping[str, object]]) -> int:
        spec = require_table(table)
        if not rows:
            return 0
        stamped = [self._prepare(spec, row) for row in rows]
        existing = self._hashes(table)
        fresh: list[dict[str, object]] = []
        seen: set[str] = set()
        for row in stamped:
            digest = str(row["row_hash"])
            if digest in existing or digest in seen:
                continue
            seen.add(digest)
            fresh.append(row)
        if not fresh:
            return 0
        by_season: dict[int, list[dict[str, object]]] = {}
        for row in fresh:
            by_season.setdefault(int(row["season"]), []).append(row)
        for season, season_rows in by_season.items():
            directory = self.data_dir / "raw" / table / f"season={season}"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"part-{uuid.uuid4().hex}.parquet"
            self._write(spec, path, season_rows)
        return len(fresh)

    def column_values(self, table: str, column: str) -> list[object]:
        spec = require_table(table)
        if column not in spec.names:
            raise IngestError(f"{table} has no column {column}")
        files = self.files(table)
        if not files:
            return []
        connection = duckdb.connect()
        try:
            found = connection.execute(
                f"SELECT {column} FROM {_parquet_list(files)}"
            ).fetchall()
        finally:
            connection.close()
        return [row[0] for row in found]

    def files(self, table: str) -> list[Path]:
        require_table(table)
        root = self.data_dir / "raw" / table
        if not root.exists():
            return []
        return sorted(path for path in root.rglob("*.parquet") if path.is_file())

    def rebuild(self) -> dict[str, int]:
        """Replace the DuckDB file with a copy of the Parquet parts."""

        if self.duckdb_path.exists():
            self.duckdb_path.unlink()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect(str(self.duckdb_path))
        counts: dict[str, int] = {}
        try:
            for name in TABLE_NAMES:
                spec = require_table(name)
                files = self.files(name)
                if not files:
                    columns = ", ".join(
                        f"{column.name} {self._duck_type(column.kind)}" for column in spec.columns
                    )
                    connection.execute(f"CREATE TABLE {name} ({columns})")
                    counts[name] = 0
                    continue
                relation = _parquet_list(files)
                connection.execute(f"CREATE TABLE {name} AS SELECT * FROM {relation}")
                count = connection.execute(f"SELECT count(*) FROM {name}").fetchone()
                counts[name] = int(count[0]) if count else 0
        finally:
            connection.close()
        logger.info(
            "Rebuilt DuckDB · %s",
            " · ".join(f"{name} {counts[name]}" for name in TABLE_NAMES),
        )
        return counts

    def _hashes(self, table: str) -> set[str]:
        files = self.files(table)
        if not files:
            return set()
        connection = duckdb.connect()
        try:
            found = connection.execute(
                f"SELECT row_hash FROM {_parquet_list(files)}"
            ).fetchall()
        finally:
            connection.close()
        return {str(row[0]) for row in found}

    def _prepare(self, spec: TableSpec, row: Mapping[str, object]) -> dict[str, object]:
        unknown = sorted(set(row) - spec.names - {"row_hash", "ingested_at"})
        if unknown:
            raise IngestError(f"{spec.name} has unknown columns {unknown}")
        prepared: dict[str, object] = {}
        for column in spec.columns:
            if column.name == "ingested_at":
                prepared[column.name] = _format_now(self.clock())
                continue
            if column.name == "row_hash":
                continue
            if column.name not in row:
                raise IngestError(f"{spec.name} is missing {column.name}")
            prepared[column.name] = coerce(row[column.name], column.kind)
        self._validate(spec, prepared)
        prepared["row_hash"] = row_hash(prepared)
        return prepared

    def _validate(self, spec: TableSpec, row: Mapping[str, object]) -> None:
        if not stamp_ok(row.get("known_as_of")):
            raise IngestError(f"{spec.name} known_as_of must be a UTC stamp")
        if not stamp_ok(row.get("ingested_at")):
            raise IngestError(f"{spec.name} ingested_at must be a UTC stamp")
        method = row.get("known_as_of_method")
        if not isinstance(method, str) or not method:
            raise IngestError(f"{spec.name} known_as_of_method is required")
        if row.get("source") != "nflverse":
            raise IngestError(f"{spec.name} source must be nflverse")
        if spec.dimension and row.get("season") != 0:
            raise IngestError(f"{spec.name} dimension rows use season 0")
        for key in spec.keys:
            value = row.get(key)
            if value is None or value == "":
                raise IngestError(f"{spec.name} key {key} is empty")

    def _write(self, spec: TableSpec, path: Path, rows: Sequence[Mapping[str, object]]) -> None:
        arrays = []
        for column in spec.columns:
            values = [row[column.name] for row in rows]
            arrays.append(pa.array(values, type=spec.arrow_schema().field(column.name).type))
        try:
            pq.write_table(pa.Table.from_arrays(arrays, schema=spec.arrow_schema()), path)
        except (OSError, pa.ArrowInvalid) as exc:
            raise IngestError(f"Could not write {path.name}") from exc

    @staticmethod
    def _duck_type(kind: str) -> str:
        if kind == "int":
            return "BIGINT"
        if kind == "float":
            return "DOUBLE"
        if kind == "bool":
            return "BOOLEAN"
        return "VARCHAR"


def _format_now(value: datetime) -> str:
    from gridiron_lab.data.clock import format_utc

    return format_utc(value)


class Database:
    """Point-in-time reads. ``as_of`` scans Parquet, not a stale DuckDB file."""

    def __init__(self, data_dir: Path) -> None:
        self.store = ParquetStore(data_dir)

    def as_of(self, table: str, ts: datetime) -> list[dict[str, object]]:
        """Latest row per key with ``known_as_of`` strictly before ``ts``."""

        spec = require_table(table)
        files = self.store.files(table)
        if not files:
            return []
        cutoff = _format_now(ts)
        keys = ", ".join(spec.keys)
        relation = _parquet_list(files)
        sql = f"""
            WITH ranked AS (
                SELECT
                    *,
                    row_number() OVER (
                        PARTITION BY {keys}
                        ORDER BY known_as_of DESC, ingested_at DESC, row_hash DESC
                    ) AS _rn
                FROM {relation}
                WHERE known_as_of < ?
            )
            SELECT * EXCLUDE (_rn) FROM ranked WHERE _rn = 1
        """
        connection = duckdb.connect()
        try:
            cursor = connection.execute(sql, [cutoff])
            names = [item[0] for item in cursor.description]
            return [dict(zip(names, record, strict=True)) for record in cursor.fetchall()]
        finally:
            connection.close()

    def rebuild(self) -> dict[str, int]:
        return self.store.rebuild()


def open_db(data_dir: Path) -> Database:
    return Database(data_dir)
