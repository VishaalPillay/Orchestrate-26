"""Does reported_status describe only the account deletion step? No gold is read.

    python code/tools/status_result_check.py

Over all 550 requests, for resources attributed by identifiers (never by
activity rows), tabulate the case's reported_status against the
reported_result of that resource's deletion_worker rows inside the case window
[requested_at, audit_at], separately for account_service DELETE_RECORD and
analytics_platform ANONYMIZE_RECORD.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from pcda import paths  # noqa: E402
from pcda.cases import load_requests  # noqa: E402
from pcda.identity import ResourceAttribution, normalize_email  # noqa: E402
from pcda.llm.client import RunMode  # noqa: E402
from pcda.pipeline import ACTIVITY_TIERS, build_context  # noqa: E402

PASSES = (("account_service", "DELETE_RECORD"), ("analytics_platform", "ANONYMIZE_RECORD"))


def main() -> int:
    load_dotenv(paths.ENV_FILE)  # the perception cache is keyed on PERCEPTION_MODEL
    ctx = build_context(RunMode.DETERMINISTIC)
    every = load_requests(paths.SAMPLE_REQUESTS) + load_requests(paths.DELETION_REQUESTS)
    for system, event_type in PASSES:
        table: Counter[tuple[str, str]] = Counter()
        cases_with: Counter[str] = Counter()
        no_row: Counter[str] = Counter()
        for case in every:
            subject = ctx.closure.subject_of(normalize_email(case.email))
            if subject is None:
                continue
            owned = [
                a.resource_id
                for a in ResourceAttribution.for_case(case, subject, ctx.closure, ctx.store, ctx.lineage)
                if a.system_id == system and a.tier not in ACTIVITY_TIERS
            ]
            if not owned:
                continue
            results = sorted({
                c.reported_result
                for c in ctx.lineage.claims
                if c.resource_id in owned
                and c.source_system == "deletion_worker"
                and c.event_type == event_type
                and case.requested_at <= c.event_time <= case.audit_at
            })
            if not results:
                no_row[case.reported_status] += 1
                continue
            cases_with[case.reported_status] += 1
            table[(case.reported_status, "+".join(results))] += 1
        print(f"\n{system} {event_type} (identifier-attributed resources, rows in [requested_at, audit_at])")
        statuses = sorted({s for s, _ in table} | set(no_row))
        results = sorted({r for _, r in table})
        print("reported_status".ljust(20) + "".join(r.ljust(16) for r in results) + "no row")
        for s in statuses:
            print(s.ljust(20) + "".join(str(table[(s, r)]).ljust(16) for r in results) + str(no_row[s]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
