"""Log evidence. Six styles; identifiers and resource_ids can sit on different lines.

Lines are grouped by trace_id before any fact is emitted: in service-events
logs the identifier is on `profile_lookup_started` (lookup_key=) and the
resource_id on the later `..._completed` line (case_17: account_4092 via
tr-4708586). 10 of 26 logs carry no trace_id; their lines are read one by one.

Lookup outcome decides the fact kind: `rows>=1` is PRESENCE, `rows=0` or
`http_status=404` is ABSENCE of that resource for that identifier. Either way
the trace also yields a LINK fact at tier log_trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from pcda.facts import Fact
from pcda.ingest.base import ManifestRow


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


def detect_style(lines: list[str]) -> LogStyle:
    raise NotImplementedError("log_adapter.detect_style")


def parse_line(style: LogStyle, line_no: int, raw: str) -> LogLine | None:
    raise NotImplementedError("log_adapter.parse_line")


def group_by_trace(lines: list[LogLine]) -> dict[str | None, list[LogLine]]:
    """trace_id -> ordered lines. Key None holds lines without a trace_id."""
    raise NotImplementedError("log_adapter.group_by_trace")


class LogAdapter:
    def parse(self, path: Path, row: ManifestRow) -> list[Fact]:
        raise NotImplementedError("log_adapter.LogAdapter.parse")

    def _facts_for_trace(self, trace_id: str, lines: list[LogLine], row: ManifestRow) -> list[Fact]:
        raise NotImplementedError("log_adapter._facts_for_trace")

    def _facts_for_line(self, line: LogLine, row: ManifestRow) -> list[Fact]:
        raise NotImplementedError("log_adapter._facts_for_line")
