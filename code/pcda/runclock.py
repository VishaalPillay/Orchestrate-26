"""Run clock: how the deletion worker's rows of one run sit in time, learned from its own pinned runs.

A deletion_worker run writes an account DELETE row and then the rows for the
person's other resources. This module measures, on the evaluation requests whose
account is attributed by identifiers (never by activity), three things:

  * the band of the account row around `reported_at`;
  * the horizon: how long after the account row the run's other rows appear;
  * the residue (seconds modulo 60) of each later row's offset from the account
    row, per row template.

Nothing is typed in: every value comes from `learn`, which reads participant
files only and never `sample_output.csv`. The clock is used to *link* activity
claims to a request (candidate filtering), never to decide any state.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from pcda.cases import CaseRequest
from pcda.facts import FactStore
from pcda.identity import IdentityClosure, ResourceAttribution, normalize_email
from pcda.lineage import Claim, LineageGraph

ACCOUNT_SYSTEM = "account_service"
BASE_PERIOD_S = 60
FOLD_SHARE = 0.9  # fold to half the period when this share of pairs has its half-period partner observed
MIN_TEMPLATE_PAIRS = 30  # a template with fewer pinned pairs is left unconstrained
HORIZON_STEP = timedelta(minutes=30)  # the learned maximum is rounded up to this

_ID = re.compile(r"\b\w+_\d+\b")
_FROM = re.compile(r" from [a-z_]+")


def template(details: str) -> str:
    """A row's message with resource ids and the reporting system removed."""
    return _FROM.sub(" from S", _ID.sub("X", details))


@dataclass(frozen=True)
class RunClock:
    band_min: timedelta  # earliest observed (account row - reported_at)
    band_max: timedelta  # latest observed
    horizon: timedelta  # latest observed (later run row - account row), rounded up
    residues: dict[str, frozenset[int]]  # template -> observed offset residues (seconds mod `period_s`)
    period_s: int = BASE_PERIOD_S
    # template -> (earliest, latest) offset of a later run row after the account row, over pinned runs
    windows: dict[str, tuple[timedelta, timedelta]] = field(default_factory=dict)
    pins: int = 0
    pairs: int = 0
    delta_hist: dict[int, int] = field(default_factory=dict)  # 30-minute bin -> pairs

    def in_band(self, row: Claim, case: CaseRequest) -> bool:
        return self.band_min <= row.event_time - case.reported_at <= self.band_max

    def consistent(self, anchor: Claim, run_rows: list[Claim]) -> bool:
        """True when every later row of the run (within the horizon) has an offset residue seen in pinned runs."""
        for r in run_rows:
            d = (r.event_time - anchor.event_time).total_seconds()
            if 0 < d <= self.horizon.total_seconds():
                allowed = self.residues.get(template(r.details))
                if allowed is not None and int(d) % self.period_s not in allowed:
                    return False
        return True

    def fits(self, anchor: Claim, row: Claim) -> bool:
        """True when `row` could belong to the run whose account row is `anchor`.

        Its template must have been seen in pinned runs, with the row's offset
        inside that template's learned window and on its learned residue.
        """
        d = row.event_time - anchor.event_time
        key = template(row.details)
        window = self.windows.get(key)
        if window is None or not window[0] <= d <= window[1]:
            return False
        return int(d.total_seconds()) % self.period_s in self.residues[key]

    def describe(self) -> list[str]:
        mins = lambda t: f"{t.total_seconds() / 60:+.1f}"  # noqa: E731
        lines = [
            f"run clock learned from {self.pins} identifier-pinned account runs ({self.pairs} later run rows):",
            f"  band: account row {mins(self.band_min)} .. {mins(self.band_max)} min from reported_at",
            f"  horizon: {self.horizon.total_seconds() / 3600:.1f} h after the account row "
            f"(rows per 30-min bin: {dict(sorted(self.delta_hist.items()))})",
        ]
        for t, r in sorted(self.residues.items()):
            lo, hi = self.windows[t]
            lines.append(
                f"  {t[:56]:<56} residues mod {self.period_s} s {sorted(r)}; "
                f"window {lo.total_seconds() / 60:.1f} .. {hi.total_seconds() / 60:.1f} min after the account row"
            )
        return lines


def deletion_worker_rows(lineage: LineageGraph) -> dict[str, list[Claim]]:
    by: dict[str, list[Claim]] = defaultdict(list)
    for c in lineage.claims:
        if c.source_system == "deletion_worker":
            by[c.resource_id].append(c)
    return by


def pinned_runs(
    cases: list[CaseRequest], closure: IdentityClosure, store: FactStore, lineage: LineageGraph
) -> list[tuple[CaseRequest, Claim, list[Claim]]]:
    """(case, account row, later rows of its other identifier-attributed resources) for each pinned run.

    A pin is an account attributed by identifiers (an attribution that is not an
    activity pick) with exactly one deletion_worker DELETE row in
    [requested_at, audit_at].
    """
    rows = deletion_worker_rows(lineage)
    out: list[tuple[CaseRequest, Claim, list[Claim]]] = []
    for case in cases:
        user = closure.subject_of(normalize_email(case.email))
        if user is None:
            continue
        att = [a for a in ResourceAttribution.for_case(case, user, closure, store, lineage) if not a.needs_skeptic]
        for acc in (a for a in att if a.system_id == ACCOUNT_SYSTEM or a.resource_id.startswith("account_")):
            anchors = [
                r
                for r in rows.get(acc.resource_id, [])
                if r.event_type == "DELETE_RECORD"
                and r.target_system == ACCOUNT_SYSTEM
                and case.requested_at <= r.event_time <= case.audit_at
            ]
            if len(anchors) != 1:
                continue
            later = [
                r
                for b in att
                if b.resource_id != acc.resource_id
                for r in rows.get(b.resource_id, [])
                if r.event_time > anchors[0].event_time
            ]
            out.append((case, anchors[0], later))
    return out


def account_anchors(
    cases: list[CaseRequest],
    closure: IdentityClosure,
    store: FactStore,
    lineage: LineageGraph,
    account_picks: dict[str, str],
) -> dict[str, Claim]:
    """case_id -> the account DELETE row of the case's run: its identifier pin, else its joint account pick.

    `account_picks` maps case_id to the activity id the account joint pass cited.
    """
    by_id = {c.activity_id: c for c in lineage.claims}
    pins = {case.case_id: anchor for case, anchor, _ in pinned_runs(cases, closure, store, lineage)}
    out = dict(pins)
    for cid, activity_id in account_picks.items():
        if cid not in out and activity_id in by_id:
            out[cid] = by_id[activity_id]
    return out


def learn(cases: list[CaseRequest], closure: IdentityClosure, store: FactStore, lineage: LineageGraph) -> RunClock | None:
    """The clock measured on `cases` (the evaluation requests); None when there is no pinned run."""
    runs = pinned_runs(cases, closure, store, lineage)
    if not runs:
        return None
    offsets = [anchor.event_time - case.reported_at for case, anchor, _ in runs]
    deltas = [(r.event_time - anchor.event_time, r) for _, anchor, later in runs for r in later]
    longest = max((d for d, _ in deltas), default=timedelta(0))
    steps = -(-longest // HORIZON_STEP)
    horizon = HORIZON_STEP * max(steps, 1)
    seen: dict[str, Counter[int]] = defaultdict(Counter)
    for d, r in deltas:
        seen[template(r.details)][int(d.total_seconds()) % BASE_PERIOD_S] += 1
    usable = {t: c for t, c in seen.items() if sum(c.values()) >= MIN_TEMPLATE_PAIRS}
    period = _period(usable)
    residues = {t: frozenset(r % period for r in c) for t, c in usable.items()}
    windows: dict[str, tuple[timedelta, timedelta]] = {}
    for t in usable:
        ds = [d for d, r in deltas if template(r.details) == t]
        pad = (max(ds) - min(ds)) / len(ds)  # as for the band: one mean spacing
        windows[t] = (min(ds) - pad, max(ds) + pad)
    hist = Counter(int(d // HORIZON_STEP) for d, _ in deltas)
    # The sample extremes sit about one mean spacing (range / n) inside the true edges; widen by that.
    pad = (max(offsets) - min(offsets)) / len(offsets)
    return RunClock(
        min(offsets) - pad,
        max(offsets) + pad,
        horizon,
        residues,
        period_s=period,
        windows=windows,
        pins=len(runs),
        pairs=len(deltas),
        delta_hist=dict(hist),
    )


def _period(seen: dict[str, Counter[int]]) -> int:
    """Half the base period when the pinned residues repeat after half of it, else the base period.

    Residues come in pairs r and r + 30 s (a run's rows start in either half
    minute). With a few hundred pinned rows a pair's second member can be
    unobserved, which would reject a genuine row; folding removes that.
    """
    half = BASE_PERIOD_S // 2
    mass = sum(sum(c.values()) for c in seen.values())
    paired = sum(n for c in seen.values() for r, n in c.items() if (r + half) % BASE_PERIOD_S in c)
    return half if mass and paired / mass >= FOLD_SHARE else BASE_PERIOD_S
