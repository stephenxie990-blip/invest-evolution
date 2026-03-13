"""Persistent memory store for commander runtime."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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
        self._last_load_warning_signature: tuple[str, int, str] | None = None
        if create:
            self.ensure_storage()

    def ensure_storage(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def append(self, kind: str, session_key: str, content: str, metadata: dict[str, Any] | None = None) -> MemoryRecord:
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
        self._truncate_if_needed()
        return rec

    def recent(self, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        rows = self._load_all()
        if kind:
            rows = [x for x in rows if x.get("kind") == kind]
        return rows[-max(1, int(limit)):]

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
        rows = self._load_all()
        if not q:
            return rows[-max(1, int(limit)):]
        hits = [r for r in rows if self._matches_query(r, q)]
        return hits[-max(1, int(limit)):]

    def get(self, record_id: str) -> dict[str, Any] | None:
        target = str(record_id or "").strip()
        if not target:
            return None
        for row in reversed(self._load_all()):
            if str(row.get("id") or "") == target:
                return row
        return None

    def stats(self) -> dict[str, Any]:
        rows = self._load_all()
        audit_records = 0
        if self.audit_path.exists():
            try:
                audit_records = len([line for line in self.audit_path.read_text(encoding="utf-8").splitlines() if line.strip()])
            except OSError as exc:
                logger.warning("Failed to read memory audit log %s: %s", self.audit_path, exc)
                audit_records = 0
        return {
            "path": str(self.path),
            "audit_path": str(self.audit_path),
            "records": len(rows),
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
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        invalid_rows = 0
        first_error = ""
        first_line_no = 0
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            s = line.strip()
            if not s:
                continue
            try:
                rows.append(json.loads(s))
            except json.JSONDecodeError as exc:
                invalid_rows += 1
                if not first_error:
                    first_error = str(exc)
                    first_line_no = line_no
                continue
        if invalid_rows:
            self._warn_invalid_rows(invalid_rows, first_line_no, first_error)
        return rows

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
        rows = self._load_all()
        if len(rows) <= self.max_records:
            return
        keep = rows[-self.max_records:]
        with self.path.open("w", encoding="utf-8") as f:
            for r in keep:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
