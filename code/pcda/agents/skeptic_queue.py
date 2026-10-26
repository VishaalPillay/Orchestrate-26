"""The skeptic's risk-ordered queue.

Targets are decisions the deterministic system itself marks as low-confidence.
Order (lower priority number first; inside a priority the larger risk first,
then case_id, so the queue is deterministic):

  0  activity pick whose row lies beyond the sample timing envelope, or whose resource
     facts attribute to another user (the skeptic's tools show the same facts)
  1  the no-provable-obligation fallback / an unknown category
  2  COMPLETE verdict that rests on a single discharged obligation
  3  activity pick inside the envelope, widest gap first
  4  contested account: candidates remain but none was assigned
  5  intake conflict or unknown category name (advisory: the gate has no action for these)
"""

from __future__ import annotations

from typing import Final

from pcda.agents.skeptic import Target
from pcda.agents.skeptic_gate import CHALLENGEABLE
from pcda.agents.skeptic_tools import SkepticWorld
from pcda.cases import CaseRequest
from pcda.solver import ObligationStatus, Verdict

# Largest |event - reported_at| among the 12 correct picks on the public samples (minutes).
SAMPLE_ENVELOPE_MIN: Final = 74.1


def pick_gap_minutes(world: SkepticWorld, case: CaseRequest, evidence_ids: tuple[str, ...]) -> float:
    gaps = [
        abs((c.event_time - case.reported_at).total_seconds()) / 60
        for c in (world.lineage.claim(e) for e in evidence_ids)
        if c is not None
    ]
    return min(gaps) if gaps else 0.0


def build_queue(world: SkepticWorld, cases: list[CaseRequest]) -> list[Target]:
    found: dict[str, Target] = {}

    def add(t: Target) -> None:
        kept = found.get(t.key)
        if kept is None or (t.priority, -t.risk) < (kept.priority, -kept.risk):
            found[t.key] = t

    for case in cases:
        res = world.results.get(case.case_id)
        if res is None:
            continue
        cert = res.certificate
        for a in res.attributions:
            if a.tier not in CHALLENGEABLE:
                continue
            gap = pick_gap_minutes(world, case, a.evidence_ids)
            outlier = gap > SAMPLE_ENVELOPE_MIN
            others = sorted(world.owners_by_resource.get(a.resource_id, set()) - {cert.subject})
            why = (
                f"{a.tier.name.lower()} pick {a.resource_id}: its deletion_worker row is {gap:.0f} min from "
                f"reported_at (sample picks were at most {SAMPLE_ENVELOPE_MIN:.0f})"
            )
            if others:
                # Cheap, code-side: facts name another user as this resource's owner.
                why += f"; evidence facts carry identifiers of {', '.join(others[:3])}, not of this subject"
            add(Target(case.case_id, "PICK", a.resource_id, 0 if outlier or others else 3, gap + (1000 if others else 0), why))
        unknown = [f.split(":", 1)[1] for f in cert.flags if f.startswith("category_unknown:")]
        if "fallback_no_provable_obligation" in cert.flags or unknown:
            add(
                Target(
                    case.case_id, "FALLBACK", unknown[0] if unknown else None, 1, 0.0,
                    "no obligation could be proved for this case, or a resource has no known category",
                )
            )
        discharged = [o for o in cert.obligations if o.status is ObligationStatus.DISCHARGED]
        if cert.verdict is Verdict.COMPLETE and len(cert.obligations) == 1 and len(discharged) == 1:
            add(
                Target(
                    case.case_id, "SINGLE_FACT", discharged[0].resource_id, 2, 0.0,
                    f"COMPLETE verdict resting on one fact about {discharged[0].resource_id}",
                )
            )
        for flag in cert.flags:
            if flag.startswith("joint_none:account_service:") and "0 candidate" not in flag:
                add(
                    Target(
                        case.case_id, "CONTESTED", None, 4, 0.0,
                        f"no account was assigned: {flag.split(':', 2)[2][:90]}",
                    )
                )
            elif flag.startswith("intake_conflict:") or flag == "intake_ambiguous":
                add(Target(case.case_id, "FLAG", None, 5, 0.0, f"intake doubt: {flag[:60]}"))
            elif flag.startswith("category_unknown_name:"):
                add(Target(case.case_id, "FLAG", None, 5, 0.0, f"unknown category name: {flag[:80]}"))
    return sorted(found.values(), key=lambda t: (t.priority, -t.risk, t.case_id, t.resource_id or ""))
