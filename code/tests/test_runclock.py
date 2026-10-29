"""Run clock, matching rule, and the guarantee that gold is never an input."""

from __future__ import annotations

import builtins
from datetime import timedelta
from pathlib import Path

import pytest

from pcda import paths
from pcda.cases import CaseRequest, load_requests
from pcda.facts import parse_ts
from pcda.identity import forced_by_matching, propagate
from pcda.lineage import Claim
from pcda.runclock import RunClock, template


def claim(activity_id: str, ts: str, details: str = "Deletion worker received the final result for search_1 from search_service.") -> Claim:
    return Claim(activity_id, parse_ts(ts), "DELETE_RECORD", "deletion_worker", "search_service", "search_1", "FAILED", details)


def clock(period: int = 30) -> RunClock:
    return RunClock(
        timedelta(minutes=-75), timedelta(minutes=-44), timedelta(hours=3, minutes=30),
        {template("Deletion worker received the final result for search_1 from search_service."): frozenset({20, 21, 22})},
        period_s=period,
    )


def windowed() -> RunClock:
    t = template("Deletion worker received the final result for search_1 from search_service.")
    base = clock()
    return RunClock(base.band_min, base.band_max, base.horizon, base.residues, period_s=30,
                    windows={t: (timedelta(minutes=80), timedelta(minutes=180))})


def test_fits_needs_the_learned_window_and_residue() -> None:
    anchor = claim("a0", "2026-09-05T11:00:00Z")
    assert windowed().fits(anchor, claim("a1", "2026-09-05T12:40:21Z"))  # +100 min, residue 21
    assert not windowed().fits(anchor, claim("a2", "2026-09-05T11:30:21Z"))  # +30 min: before the window
    assert not windowed().fits(anchor, claim("a3", "2026-09-05T12:40:05Z"))  # right window, wrong residue
    assert not windowed().fits(anchor, claim("a4", "2026-09-05T12:40:21Z", "Some unseen message for X."))  # unseen template


def test_template_strips_ids_and_reporting_system() -> None:
    a = template("Deletion worker received the final result for search_12 from search_service.")
    b = template("Deletion worker received the final result for export_9 from export_service.")
    assert a == b


def test_phase_accepts_the_learned_residue_and_rejects_another() -> None:
    anchor = claim("a0", "2026-09-05T11:00:00Z")
    on_phase = claim("a1", "2026-09-05T12:00:21Z")  # +3621 s: residue 21 mod 30
    off_phase = claim("a2", "2026-09-05T12:00:05Z")  # residue 5
    assert clock().consistent(anchor, [on_phase])
    assert not clock().consistent(anchor, [on_phase, off_phase])


def test_rows_outside_the_horizon_or_before_the_anchor_do_not_constrain() -> None:
    anchor = claim("a0", "2026-09-05T11:00:00Z")
    assert clock().consistent(anchor, [claim("a1", "2026-09-06T12:00:05Z"), claim("a2", "2026-09-05T10:00:05Z")])


def test_band_is_measured_from_reported_at() -> None:
    case = CaseRequest("c", "x@y.z", parse_ts("2026-09-04T12:00:00Z"), "COMPLETE", parse_ts("2026-09-05T12:00:00Z"),
                       parse_ts("2026-09-15T12:00:00Z"), "")
    assert clock().in_band(claim("a", "2026-09-05T11:00:00Z"), case)  # -60 min
    assert not clock().in_band(claim("a", "2026-09-05T11:30:00Z"), case)  # -30 min


def test_matching_forces_only_edges_of_every_maximum_matching() -> None:
    # r1 can take a or b, r2 only a: r2 -> a is proven, then r1 -> b follows.
    assert forced_by_matching({"r1": frozenset({"a", "b"}), "r2": frozenset({"a"})}) == {"r1": "b", "r2": "a"}
    # A symmetric pair has two perfect matchings: nothing is forced.
    assert forced_by_matching({"r1": frozenset({"a", "b"}), "r2": frozenset({"a", "b"})}) == {}
    # A spare resource means the request is not forced either.
    assert forced_by_matching({"r1": frozenset({"a", "b"})}) == {}


def test_matching_is_independent_of_request_order() -> None:
    graph = {"r1": frozenset({"a", "b"}), "r2": frozenset({"a"}), "r3": frozenset({"b", "c"}), "r4": frozenset({"c", "d"})}
    assert forced_by_matching(graph) == forced_by_matching(dict(reversed(list(graph.items()))))


def test_propagate_reports_what_it_could_not_decide() -> None:
    result = propagate({"r1": frozenset({"x1", "x2"})}, {"x1": "a", "x2": "b"}, {"r1": "u1"})
    assert result.remaining == {"r1": frozenset({"a", "b"})}


def test_pipeline_never_opens_sample_output_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gold is for score.py only: build the context and run cases with its path made unreadable."""
    from pcda.llm.client import RunMode
    from pcda.pipeline import build_context, run_case

    forbidden = paths.SAMPLE_OUTPUT.resolve()
    real_open = builtins.open
    real_path_open = Path.open

    def guard(path: object) -> None:
        if isinstance(path, (str, Path)) and Path(path).resolve() == forbidden:
            raise AssertionError("the pipeline opened sample_output.csv")

    def open_guard(file: object, *args: object, **kwargs: object):
        guard(file)
        return real_open(file, *args, **kwargs)  # type: ignore[arg-type]

    def path_open_guard(self: Path, *args: object, **kwargs: object):
        guard(self)
        return real_path_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "open", open_guard)
    monkeypatch.setattr(Path, "open", path_open_guard)
    ctx = build_context(RunMode.DETERMINISTIC)
    for case in load_requests(paths.SAMPLE_REQUESTS)[:5]:
        run_case(case, ctx)
