"""Deletion requests and the evidence-id clock used for citation cutoffs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pcda import paths
from pcda.facts import parse_ts


@dataclass(frozen=True)
class CaseRequest:
    case_id: str
    email: str
    requested_at: datetime
    reported_status: str
    reported_at: datetime
    audit_at: datetime
    request_text: str


SPLIT_NAMES: tuple[str, ...] = ("all", "sample", "test")


def split_sources(split: str) -> tuple[Path, ...]:
    """Request files for a split, resolved at call time (paths.use_dataset may move them)."""
    return {
        "sample": (paths.SAMPLE_REQUESTS,),
        "test": (paths.DELETION_REQUESTS,),
        "all": (paths.SAMPLE_REQUESTS, paths.DELETION_REQUESTS),
    }[split]


def load_requests(path: Path) -> list[CaseRequest]:
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    cases = [
        CaseRequest(
            case_id=r["case_id"],
            email=r["email"],
            requested_at=parse_ts(r["requested_at"]),
            reported_status=r["reported_status"],
            reported_at=parse_ts(r["reported_at"]),
            audit_at=parse_ts(r["audit_at"]),
            request_text=r["request_text"],
        )
        for r in rows
    ]
    cases.sort(key=lambda c: c.case_id)
    return cases


def load_evidence_times() -> dict[str, datetime]:
    """Time at which each citable id became visible: file, activity, audit row."""
    times: dict[str, datetime] = {}
    sources = (
        (paths.EVIDENCE_MANIFEST, "file_id", "captured_at"),
        (paths.SYSTEM_ACTIVITY, "activity_id", "event_time"),
        (paths.USER_AUDIT_LOG, "audit_id", "event_time"),
    )
    for path, id_col, ts_col in sources:
        with path.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                times[r[id_col]] = parse_ts(r[ts_col])
    return times
