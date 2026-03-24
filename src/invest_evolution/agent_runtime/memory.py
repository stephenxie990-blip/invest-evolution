"""Persistent memory store for commander runtime."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None


def _bounded_tail(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    normalized_limit = int(limit)
    if normalized_limit <= 0:
        return []
    return rows[-normalized_limit:]


@dataclass
class MemoryRecord:
    id: str
    ts_ms: int
    kind: str
    session_key: str
    content: str
    metadata: dict[str, Any]


class MemoryStore:
    def __init__(self, path: Path, max_records: int = 10000, create: bool = True):
        self.path = Path(path)
        self.max_records = max(100, int(max_records))
        self.audit_path = self.path.with_name(self.path.stem + "_audit.jsonl")
        self.meta_path = self.path.with_name(self.path.stem + "_meta.json")
        self.lock_path = self.path.with_name(self.path.stem + ".lock")
        self._last_load_warning_signature: tuple[str, int, str] | None = None
        self._record_count: int | None = None
        self._record_count_signature: tuple[int, int] | None = None
        self._thread_lock = threading.RLock()
        if create:
            self.ensure_storage()

    def ensure_storage(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")
            self._set_record_count_cache(0)

    def append(self, kind: str, session_key: str, content: str, metadata: dict[str, Any] | None = None) -> MemoryRecord:
        with self._exclusive_access():
            current_count = self._refresh_record_count_from_storage()
            rec = MemoryRecord(
                id=uuid.uuid4().hex[:12],
                ts_ms=int(time.time() * 1000),
                kind=str(kind),
                session_key=str(session_key),
                content=str(content),
                metadata=metadata or {},
            )
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
            self._set_record_count_cache(current_count + 1)
            self._truncate_if_needed_locked()
            return rec

    def recent(self, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        normalized_limit = int(limit)
        if normalized_limit <= 0:
            return []
        if kind is None:
            return self._tail_rows(normalized_limit)
        rows: deque[dict[str, Any]] = deque(maxlen=normalized_limit)
        for row in self._iter_rows():
            if kind and row.get("kind") != kind:
                continue
            rows.append(row)
        return list(rows)

    def append_audit(self, event: str, session_key: str, payload: dict[str, Any] | None = None) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "id": uuid.uuid4().hex[:12],
            "ts_ms": int(time.time() * 1000),
            "event": str(event),
            "session_key": str(session_key),
            "payload": payload or {},
            "pid": os.getpid(),
        }
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        q = str(query or "").strip().lower()
        if not q:
            return self.recent(limit=limit)
        normalized_limit = int(limit)
        if normalized_limit <= 0:
            return []
        hits: deque[dict[str, Any]] = deque(maxlen=normalized_limit)
        for row in self._iter_rows():
            if self._matches_query(row, q):
                hits.append(row)
        return list(hits)

    def get(self, record_id: str) -> dict[str, Any] | None:
        target = str(record_id or "").strip()
        if not target:
            return None
        found: dict[str, Any] | None = None
        for row in self._iter_rows():
            if str(row.get("id") or "") == target:
                found = row
        return found

    def stats(self) -> dict[str, Any]:
        audit_records = self._count_nonempty_lines(self.audit_path, warning_label="memory audit log")
        return {
            "path": str(self.path),
            "audit_path": str(self.audit_path),
            "records": self._resolve_record_count(),
            "audit_records": audit_records,
            "max_records": self.max_records,
        }

    def _matches_query(self, row: dict[str, Any], query: str) -> bool:
        haystacks = [str(row.get("content", ""))]
        metadata = row.get("metadata")
        if isinstance(metadata, dict) and metadata:
            try:
                haystacks.append(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
            except (TypeError, ValueError) as exc:
                logger.debug("Failed to serialize memory metadata for search: %s", exc)
                haystacks.append(str(metadata))
        needle = str(query or "").strip().lower()
        if not needle:
            return True
        return any(needle in str(item).lower() for item in haystacks)

    def _load_all(self) -> list[dict[str, Any]]:
        return list(self._iter_rows())

    def _iter_rows(self):
        if not self.path.exists():
            self._record_count = 0
            self._set_record_count_cache(0)
            return
        invalid_rows = 0
        first_error = ""
        first_line_no = 0
        valid_rows = 0
        with self.path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                s = line.strip()
                if not s:
                    continue
                try:
                    row = json.loads(s)
                except json.JSONDecodeError as exc:
                    invalid_rows += 1
                    if not first_error:
                        first_error = str(exc)
                        first_line_no = line_no
                    continue
                valid_rows += 1
                yield row
        if invalid_rows:
            self._warn_invalid_rows(invalid_rows, first_line_no, first_error)
        self._set_record_count_cache(valid_rows)

    def _warn_invalid_rows(self, invalid_rows: int, first_line_no: int, first_error: str) -> None:
        signature = (str(self.path), invalid_rows, first_error)
        if signature == self._last_load_warning_signature:
            return
        self._last_load_warning_signature = signature
        logger.warning(
            "Skipped %s invalid memory rows in %s; first error at line %s: %s",
            invalid_rows,
            self.path,
            first_line_no,
            first_error,
        )

    def _truncate_if_needed(self) -> None:
        with self._exclusive_access():
            self._truncate_if_needed_locked()

    def _truncate_if_needed_locked(self) -> None:
        record_count = self._refresh_record_count_from_storage()
        overflow_margin = max(1, min(256, self.max_records // 20))
        truncate_threshold = self.max_records + overflow_margin
        if record_count <= truncate_threshold:
            # Recheck with a tail read so stale count caches cannot suppress truncation.
            observed_tail_count = len(self._tail_lines(truncate_threshold + 1))
            if observed_tail_count <= truncate_threshold:
                return
        keep_lines = self._tail_lines(self.max_records)
        if not keep_lines:
            return
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f"{self.path.stem}_",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                for line in keep_lines:
                    handle.write(line + "\n")
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)
        self._set_record_count_cache(len(keep_lines))

    def _resolve_record_count(self) -> int:
        if self._record_count is not None:
            signature = self._path_signature(self.path)
            if signature is not None and signature == self._record_count_signature:
                return int(self._record_count)
        cached_count = self._read_count_cache()
        if cached_count is not None:
            self._record_count = cached_count
            self._record_count_signature = self._path_signature(self.path)
            return int(cached_count)
        self._record_count = self._count_nonempty_lines(self.path)
        self._set_record_count_cache(self._record_count)
        return int(self._record_count)

    def _refresh_record_count_from_storage(self) -> int:
        cached_count = self._read_count_cache()
        if cached_count is not None:
            self._record_count = cached_count
            self._record_count_signature = self._path_signature(self.path)
            return int(cached_count)
        self._record_count = self._count_nonempty_lines(self.path)
        self._set_record_count_cache(self._record_count)
        return int(self._record_count)

    def _set_record_count_cache(self, count: int) -> None:
        self._record_count = int(count)
        self._record_count_signature = self._path_signature(self.path)
        self._write_count_cache(self._record_count)

    @contextmanager
    def _exclusive_access(self):
        with self._thread_lock:
            lock_handle = None
            locker = fcntl
            try:
                if locker is not None:
                    self.lock_path.parent.mkdir(parents=True, exist_ok=True)
                    lock_handle = self.lock_path.open("a+", encoding="utf-8")
                    locker.flock(lock_handle.fileno(), locker.LOCK_EX)
                yield
            finally:
                if lock_handle is not None:
                    try:
                        if locker is not None:
                            locker.flock(lock_handle.fileno(), locker.LOCK_UN)
                    finally:
                        lock_handle.close()

    def _tail_lines(self, limit: int) -> list[str]:
        normalized_limit = max(0, int(limit))
        if normalized_limit <= 0 or not self.path.exists():
            return []
        chunk_size = 8192
        buffer = bytearray()
        newline_count = 0
        with self.path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            while position > 0 and newline_count <= normalized_limit:
                read_size = min(chunk_size, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size)
                if not chunk:
                    break
                buffer[:0] = chunk
                newline_count += chunk.count(b"\n")
        text = buffer.decode("utf-8", errors="ignore")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[-normalized_limit:]

    def _tail_rows(self, limit: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        invalid_rows = 0
        first_error = ""
        first_line_no = 0
        for line in self._tail_lines(limit):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                invalid_rows += 1
                if not first_error:
                    first_error = str(exc)
                    first_line_no = -1
        if invalid_rows:
            self._warn_invalid_rows(invalid_rows, first_line_no, first_error)
        return rows

    def _count_nonempty_lines(self, path: Path, *, warning_label: str = "memory store") -> int:
        if not path.exists():
            return 0
        count = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        count += 1
        except OSError as exc:
            logger.warning("Failed to read %s %s: %s", warning_label, path, exc)
            return 0
        return count

    def _read_count_cache(self) -> int | None:
        if not self.path.exists() or not self.meta_path.exists():
            return None
        signature = self._path_signature(self.path)
        if signature is None:
            return None
        try:
            payload = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        cached_count = payload.get("records")
        cached_size = payload.get("file_size")
        cached_mtime_ns = payload.get("mtime_ns")
        if (
            isinstance(cached_count, int)
            and int(cached_size) == signature[0]
            and int(cached_mtime_ns) == signature[1]
        ):
            return cached_count
        return None

    def _write_count_cache(self, count: int) -> None:
        signature = self._path_signature(self.path)
        if signature is None:
            return
        payload = {
            "records": int(count),
            "file_size": int(signature[0]),
            "mtime_ns": int(signature[1]),
        }
        try:
            self.meta_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            logger.debug("Failed to write memory count cache for %s: %s", self.path, exc)

    def _path_signature(self, path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
        return int(stat.st_size), int(mtime_ns)
