"""Spot-check the ownership exclusivity rule before it is applied.

    python code/tools/ownership_spotcheck.py [--seed 20261025] [--n 10]

Lists every activity-derived pick (activity_temporal or activity_joint) of the
first pass for which identity.conflicting_owner finds an ownership-eligible
fact that resolves to another user, then prints, for a seeded random sample of
them plus case_107 and case_108: the picked resource, the conflicting fact
(evidence_id, observed and captured time, table, which identifier kinds it
carries), the user those identifiers resolve to, and the checks that this user
is not the subject under any historical identifier (disjoint identifier sets,
no closure conflict, no identifier of the subject on the fact). Run it before
the rule is switched on: it reads the first pass without the rule.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from pcda import paths  # noqa: E402
from pcda.cases import load_requests  # noqa: E402
from pcda.identity import LinkTier, conflicting_owner  # noqa: E402
from pcda.llm.client import RunMode  # noqa: E402
from pcda.pipeline import build_context, run_case  # noqa: E402

ACTIVITY_TIERS = (LinkTier.ACTIVITY_TEMPORAL, LinkTier.ACTIVITY_JOINT)


def kind_of(identifier: str) -> str:
    if "@" in identifier:
        return "email"
    if len(identifier) == 64:
        return "email_hash"
    if identifier.startswith("user_"):
        return "user_id"
    return "phone"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261025)
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()
    load_dotenv(paths.ENV_FILE)
    ctx = build_context(RunMode.REPLAY)
    sample_ids = {c.case_id for c in load_requests(paths.SAMPLE_REQUESTS)}
    cases = load_requests(paths.SAMPLE_REQUESTS) + load_requests(paths.DELETION_REQUESTS)
    found: list[tuple[str, str]] = []
    per_split = {"sample": 0, "test": 0, "sample picks": 0, "test picks": 0}
    detail: dict[tuple[str, str], tuple[str, object]] = {}
    subjects: dict[str, str] = {}
    for case in cases:
        res = run_case(case, ctx, adjust=False)
        subject = res.certificate.subject
        part = "sample" if case.case_id in sample_ids else "test"
        for a in res.attributions:
            if a.tier not in ACTIVITY_TIERS or subject is None:
                continue
            per_split[f"{part} picks"] += 1
            hit = conflicting_owner(a.resource_id, subject, ctx.closure, ctx.store)
            if hit is not None:
                per_split[part] += 1
                found.append((case.case_id, a.resource_id))
                detail[(case.case_id, a.resource_id)] = hit
                subjects[case.case_id] = subject
    print(f"activity picks: {per_split['sample picks']} sample, {per_split['test picks']} test")
    print(f"picks with an ownership-eligible conflicting fact: {per_split['sample']} sample, {per_split['test']} test")
    test_found = sorted(x for x in found if x[0] not in sample_ids)
    chosen = random.Random(args.seed).sample(test_found, min(args.n, len(test_found)))
    chosen += [x for x in test_found if x[0] in ("case_107", "case_108") and x not in chosen]
    print(f"closure conflicts (identifier claimed by two users): {len(ctx.closure.conflicts)}")
    wrong = 0
    for cid, rid in chosen:
        other, fact = detail[(cid, rid)]  # type: ignore[misc]
        subject = subjects[cid]
        users = {ctx.closure.subject_of(i) for i in fact.identifiers}  # type: ignore[attr-defined]
        subj_ids = ctx.closure.identifiers_of(subject)
        other_ids = ctx.closure.identifiers_of(other)
        checks = {
            "fact resolves to a user": other is not None and None not in users or other is not None,
            "that user is not the subject": other != subject,
            "subject not among the fact's users": subject not in users,
            "subject and user share no identifier": not (subj_ids & other_ids),
            "no subject identifier on the fact": not (set(fact.identifiers) & subj_ids),  # type: ignore[attr-defined]
        }
        ok = all(checks.values())
        wrong += int(not ok)
        kinds = sorted({kind_of(i) for i in fact.identifiers})  # type: ignore[attr-defined]
        print(
            f"\n{cid} subject={subject} pick={rid}\n"
            f"  fact {fact.fact_id} evidence={fact.evidence_id} table={fact.attrs.get('table')} "  # type: ignore[attr-defined]
            f"kind={fact.kind.value} src={fact.attrs.get('src')}\n"  # type: ignore[attr-defined]
            f"  observed {fact.observed_at:%Y-%m-%d %H:%M} captured {fact.captured_at:%Y-%m-%d %H:%M}; "  # type: ignore[attr-defined]
            f"identifier kinds {kinds} resolve to {sorted(u for u in users if u)}\n"
            f"  checks: {'; '.join(f'{k}={v}' for k, v in checks.items())} -> {'OK' if ok else 'WRONG'}"
        )
    print(f"\nspot-checked {len(chosen)}; wrong: {wrong}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
