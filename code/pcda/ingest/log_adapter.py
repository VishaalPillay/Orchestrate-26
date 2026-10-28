"""Log evidence. Six styles; identifiers and resource_ids can sit on different lines.

Lines are grouped by trace_id before any fact is emitted: in service-events
logs the identifier is on `profile_lookup_started` (lookup_key=) and the
resource_id on the later `..._completed` line (case_17: account_4092 via
tr-4708586). 10 of 26 logs carry no trace_id; their lines are read one by one.

Lookup outcome decides the fact kind: `rows>=1` is PRESENCE, `rows=0` or
`http_status=404` is ABSENCE of that resource for that identifier. Either way
a traced lookup also yields a LINK fact (identifiers <-> resource via trace).

Identifiers for a resource come from every line of its trace that either has
no resource_id of its own (the `_started` line) or has this resource's id.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Final

from pcda.category import CATEGORIES, DATA_FLOW
from pcda.facts import (
    ATTR_ID_FIELDS,
    ATTR_LINKED,
    ATTR_PII_FIELDS,
    ATTR_STATE,
    Fact,
    FactKind,
    parse_ts,
)
from pcda.ingest.base import IngestReport, ManifestRow
from pcda.ingest.common import Emitter, result_to_kind, to_bool, view_record


class LogStyle(str, Enum):
    KEY_VALUE = "key_value"
    JSON_PREFIXED = "json_prefixed"
    SYSLOG = "syslog"
    BRACKETED_TRACE = "bracketed_trace"
    PIPE_DELIMITED = "pipe_delimited"
    CLOUD_EXPORT = "cloud_export"


@dataclass(frozen=True)
class LogLine:
    line_no: int
    timestamp: datetime
    fields: dict[str, str] = field(default_factory=dict)
    raw: str = ""


_PREFIX = re.compile(r"^(\d{4}-\d\d-\d\dT[\d:.]+Z)\s+(.*)$")
_KV = re.compile(r'([A-Za-z_][\w.]*)=("(?:[^"\\]|\\.)*"|[^\s"]+)')
_BRACKET = re.compile(r"^\[(\w+)\]\s+\[([^\]]+)\]\s+\[([^\]]+)\]\s+(\w+):\s*(.*)$")
_SYSLOG = re.compile(r"^<\d+>\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s+\S+\s+(.*)$")
_TRACE = re.compile(r"^(?:tr|rb)-[\w-]+$")


def _style_of(rest: str) -> LogStyle:
    if rest.startswith("{"):
        return LogStyle.JSON_PREFIXED
    if rest.startswith("["):
        return LogStyle.BRACKETED_TRACE
    if rest.startswith("requestId="):
        return LogStyle.CLOUD_EXPORT
    if re.match(r"^<\d+>\d+\s", rest):
        return LogStyle.SYSLOG
    if rest.startswith("| "):
        return LogStyle.PIPE_DELIMITED
    return LogStyle.KEY_VALUE


def detect_style(lines: list[str]) -> LogStyle:
    for raw in lines:
        m = _PREFIX.match(raw.strip())
        if m:
            return _style_of(m.group(2))
    raise ValueError("no timestamped log line found")


def _kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in _KV.findall(text):
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"')
        out[key] = value
    return out


def _put_trace(fields: dict[str, str], candidate: str | None) -> None:
    """A trace id is tr-/rb- prefixed; collector-N and similar ids are infra, not traces."""
    if candidate and _TRACE.match(candidate):
        fields["trace_id"] = candidate
    elif candidate:
        fields.setdefault("source_id", candidate)


def parse_line(style: LogStyle, line_no: int, raw: str) -> LogLine | None:
    m = _PREFIX.match(raw.strip())
    if not m:
        return None
    ts, rest = parse_ts(m.group(1)), m.group(2)
    fields: dict[str, str] = {}
    if style is LogStyle.JSON_PREFIXED:
        doc = json.loads(rest)
        fields = {k: "" if v is None else str(v) for k, v in doc.items()}
        fields["level"] = fields.pop("severity", fields.get("level", ""))
        _put_trace(fields, fields.pop("trace_id", None))
    elif style is LogStyle.BRACKETED_TRACE:
        b = _BRACKET.match(rest)
        if not b:
            return None
        fields = _kv(b.group(5))
        fields.update(level=b.group(1), service=b.group(2), event=b.group(4))
        _put_trace(fields, b.group(3))
    elif style is LogStyle.PIPE_DELIMITED:
        parts = rest[2:].split(" | ", 4)
        if len(parts) < 5:
            return None
        fields = _kv(parts[4])
        fields.update(level=parts[0], service=parts[1], event=parts[3])
        _put_trace(fields, parts[2])
    elif style is LogStyle.CLOUD_EXPORT:
        outer = _kv(rest)
        fields = _kv(outer.pop("message", ""))
        fields["level"] = outer.get("level", "")
        fields["service"] = outer.get("function", "")
        _put_trace(fields, outer.get("requestId"))
    elif style is LogStyle.SYSLOG:
        s = _SYSLOG.match(rest)
        if not s:
            return None
        fields = _kv(s.group(4))
        fields["service"] = s.group(2)
        _put_trace(fields, s.group(3))
    else:
        first, _, remainder = rest.partition(" ")
        if "=" not in first:
            fields["level"] = first
            rest = remainder
        fields.update(_kv(rest))
        _put_trace(fields, fields.pop("trace_id", None))
    return LogLine(line_no=line_no, timestamp=ts, fields=fields, raw=raw.rstrip("\n"))


def group_by_trace(lines: list[LogLine]) -> dict[str | None, list[LogLine]]:
    """trace_id -> ordered lines. Key None holds lines without a trace_id."""
    groups: dict[str | None, list[LogLine]] = {}
    for line in lines:
        groups.setdefault(line.fields.get("trace_id"), []).append(line)
    for members in groups.values():
        members.sort(key=lambda m: (m.timestamp, m.line_no))
    return groups


# ---------------------------------------------------------------------------
# What a line says
# ---------------------------------------------------------------------------

# QUERY keys say what was looked up; CONTENT keys are what the record holds
# (only these count as exposed PII on the fact).
_QUERY_KEYS: Final = (
    "lookup_key", "match_key", "scan_key", "subject_key", "owner_key", "subject_ref",
    "correlation_key",
)
_CONTENT_KEYS: Final = (
    "email", "phone", "email_hash", "customer_email", "customer_reference", "requester_email",
    "requester_name", "requester_phone", "owner_email", "subject_reference", "matched_value",
    "message_text",
)
_ATTR_KEYS: Final = (
    "rows", "http_status", "result", "total_hits", "index", "enabled", "last_delivery_status",
    "connector_name", "archive_present", "restore_allowed", "event_code", "retention_until",
    "audience_cohort", "file", "available", "object_key", "archive_key", "email_opt_in",
    "decision", "policy", "eligible", "deletion_block", "marker_found", "last_write", "reason",
    "access", "detector", "direct_identifiers", "scheduler_registered", "integrity_check",
)
_PII_SCANS: Final = frozenset({"message_pii_scan", "attachment_pii_scan", "analytics_identity_scan"})
_LABELS: Final = frozenset(CATEGORIES) | {DATA_FLOW}
_PRESENCE_EVENTS: Final = frozenset(
    {
        "analytics_profile_read_completed",
        "analytics_profile_read",
        "support_ticket_read_completed",
        "retained_order_loaded",
    }
)
_NO_STATE_EVENTS: Final = frozenset({"thumbnail_failed"})  # has a resource_id but no state


@dataclass(frozen=True)
class Outcome:
    kind: FactKind
    state: str


_FOUND: Final = Outcome(FactKind.PRESENCE, "FOUND")
_NOT_FOUND: Final = Outcome(FactKind.ABSENCE, "NOT_FOUND")


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def outcome_of(f: Mapping[str, str]) -> Outcome | None:
    """What a line says about its resource, or None for context/noise lines."""
    event = f.get("event", "")
    if "resource_id" not in f:
        return None
    if event == "record_lookup":
        mapped = result_to_kind(f.get("record_type"), f.get("result"))
        return Outcome(*mapped) if mapped else None
    if event in _PII_SCANS:
        return Outcome(FactKind.PII_EXPOSED, "IDENTIFIABLE")
    if event == "message_read":
        if f.get("message_text", "").strip() == "[REDACTED]":
            return Outcome(FactKind.PII_CLEARED, "REDACTED")
        return _FOUND
    if event == "restore_policy_read_completed":
        if to_bool(f.get("archive_present")) is False:
            return _NOT_FOUND
        allowed = to_bool(f.get("restore_allowed"))
        if allowed is None:
            return None
        return Outcome(FactKind.RESTORE_STATE, "ACTIVE" if allowed else "BLOCKED")
    if event == "restore_candidate_evaluated":
        open_ = bool(to_bool(f.get("eligible"))) and not to_bool(f.get("deletion_block"))
        return Outcome(FactKind.RESTORE_STATE, "ACTIVE" if open_ else "BLOCKED")
    if event == "restore_authorization_completed":
        allowed = f.get("decision", "").lower() in ("allow", "allowed", "grant")
        return Outcome(FactKind.RESTORE_STATE, "ACTIVE" if allowed else "BLOCKED")
    enabled = to_bool(f.get("enabled"))
    if enabled is not None:
        return Outcome(FactKind.FLOW_STATE, "ACTIVE" if enabled else "DISABLED")
    if event == "deletion_marker_check":
        marked = to_bool(f.get("marker_found"))
        if marked is None:
            return None
        return _NOT_FOUND if marked else _FOUND
    if event == "index_query_completed" and f.get("result") in ("hit", "miss"):
        return _FOUND if f["result"] == "hit" else _NOT_FOUND
    available = to_bool(f.get("available"))
    if available is not None:
        return _FOUND if available else _NOT_FOUND
    rows, status = _int(f.get("rows")), _int(f.get("http_status"))
    if status == 404 or rows == 0:
        return _NOT_FOUND
    if (rows is not None and rows >= 1) or status == 200:
        return _FOUND
    if event in _PRESENCE_EVENTS:
        return _FOUND
    return None


class LogAdapter:
    def __init__(self, report: IngestReport | None = None) -> None:
        self._report = report

    def parse(self, path: Path, row: ManifestRow) -> list[Fact]:
        raw = path.read_text(encoding="utf-8").splitlines()
        style = detect_style(raw)
        lines: list[LogLine] = []
        for n, text in enumerate(raw, start=1):
            if not text.strip():
                continue
            line = parse_line(style, n, text)
            if line is None:
                Emitter(row, self._report).warn(f"line {n} did not parse as {style.value}")
                continue
            lines.append(line)
        groups = group_by_trace(lines)
        facts: list[Fact] = []
        # Deterministic: traces by first line, then trace-less lines one by one.
        traces = sorted((k for k in groups if k is not None), key=lambda k: groups[k][0].line_no)
        for trace_id in traces:
            facts.extend(self._facts_for_trace(trace_id, groups[trace_id], row))
        for line in groups.get(None, []):
            facts.extend(self._facts_for_line(line, row))
        return _renumber(facts, row)

    def _facts_for_trace(self, trace_id: str, lines: list[LogLine], row: ManifestRow) -> list[Fact]:
        return self._emit(lines, row, trace_id)

    def _facts_for_line(self, line: LogLine, row: ManifestRow) -> list[Fact]:
        return self._emit([line], row, None)

    def _emit(self, lines: list[LogLine], row: ManifestRow, trace_id: str | None) -> list[Fact]:
        em = Emitter(row, self._report, src="log")
        for line in lines:
            f = line.fields
            event = f.get("event", "")
            out = outcome_of(f)
            if out is None:
                if "resource_id" in f and event not in _NO_STATE_EVENTS and "started" not in event:
                    em.warn(f"line {line.line_no}: event {event!r} on {f['resource_id']} has no state")
                continue
            rid = f["resource_id"]
            context = [ln.fields for ln in lines if ln.fields.get("resource_id") in (None, rid)]
            content = view_record({k: f[k] for k in _CONTENT_KEYS if k in f})
            queried = view_record({k: c[k] for c in context for k in _QUERY_KEYS if k in c})
            joined = view_record(
                {k: c[k] for c in context for k in _CONTENT_KEYS if k in c and k != "message_text"}
            )
            identifiers = content.identifiers | queried.identifiers | joined.identifiers
            record_type = f.get("record_type")
            system = f.get("system") or f.get("service")
            attrs: dict[str, object] = {k: f[k] for k in _ATTR_KEYS if k in f}
            attrs.update(
                {
                    ATTR_STATE: out.state,
                    ATTR_PII_FIELDS: ",".join(content.pii_fields) or None,
                    ATTR_ID_FIELDS: ",".join(content.id_fields + queried.id_fields) or None,
                    "trace_id": trace_id,
                    "level": f.get("level"),
                }
            )
            if event == "record_lookup":
                attrs["src"] = "ledger"  # explicit resource/record_type/result entry
            em.emit(
                out.kind,
                line.timestamp,
                system_id=system,
                resource_id=rid,
                category=record_type if record_type in _LABELS else None,
                identifiers=identifiers,
                table=event,
                source_line=line.line_no,
                attrs=attrs,
            )
            if trace_id is not None and identifiers:
                em.emit(
                    FactKind.LINK,
                    line.timestamp,
                    system_id=system,
                    resource_id=rid,
                    identifiers=identifiers,
                    table=event,
                    source_line=line.line_no,
                    attrs={ATTR_LINKED: trace_id, "via": "trace_id"},
                )
        return em.facts


def _renumber(facts: list[Fact], row: ManifestRow) -> list[Fact]:
    """Each group counts from 1; rebuild with file-unique ids in emission order."""
    return [
        Fact(
            fact_id=f"{row.file_id}:{n:05d}",
            evidence_id=f.evidence_id,
            captured_at=f.captured_at,
            observed_at=f.observed_at,
            system_id=f.system_id,
            resource_id=f.resource_id,
            category=f.category,
            kind=f.kind,
            identifiers=f.identifiers,
            attrs=f.attrs,
            topology=f.topology,
        )
        for n, f in enumerate(facts, start=1)
    ]
