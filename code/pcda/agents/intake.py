"""Intake agent: does request_text authorize deletion?

Its answer is an input to the solver, not a status. The solver alone maps
it to NOT_APPLICABLE or to an audit.

RuleIntakeAgent is the deterministic baseline. It checks, in order:
  1. an explicit guard against deletion ("don't delete", "keep the account",
     "marketing ... only") -> not a deletion;
  2. a non-deletion request kind (copy, export, correction, contact change,
     login help, marketing opt-out) with no deletion verb -> not a deletion;
  3. a deletion verb in any of the request languages -> deletion;
  4. anything else -> deletion, request_kind AMBIGUOUS (the case is flagged).
The LLM agent replaces it later behind the same IntakeAgent interface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Protocol

from pcda.llm.client import LLMClient


@dataclass(frozen=True)
class IntakeResult:
    case_id: str
    authorizes_deletion: bool | None  # None: undecided, the case is flagged
    language: str | None
    request_kind: str  # e.g. "DELETION", "ACCESS", "CORRECTION", "EXPORT", "MARKETING_OPT_OUT"
    scope_hints: tuple[str, ...] = ()  # e.g. "retain_backups", "anonymous_stats_ok"
    rationale: str = ""

    @property
    def is_ambiguous(self) -> bool:
        return self.request_kind == KIND_AMBIGUOUS


class IntakeAgent(Protocol):
    def classify(self, case_id: str, request_text: str) -> IntakeResult: ...


KIND_DELETION: Final = "DELETION"
KIND_AMBIGUOUS: Final = "AMBIGUOUS"


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


# 1. Explicit guards: the person says the account or data must stay.
_GUARDS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("dont_delete", _rx(
        r"\b(?:don'?t|do not|please don'?t|never)\s+(?:delete|close|remove|erase|change)\b",
        r"\bwithout (?:removing|deleting|closing)\b",
        r"\bnot (?:asking|requesting|a request) (?:for|to) (?:deletion|delete|erase|erasure|removal|remove|close)\b",
        r"\bno (?:borren|eliminen|cierren)\b", r"\bnão (?:excluam|apaguem|encerrem)\b",
        r"\bne (?:supprimez|fermez) pas\b", r"\bnicht (?:löschen|schließen)\b",
        r"削除しないで", r"삭제하지",
    )),
    ("keep_account", _rx(
        r"\bkeep (?:my|the|this) (?:account|profile)\b",
        r"\bleave (?:my|the|this) (?:account|profile)\b[^.]*\b(?:in place|active|as it is|open)\b",
        r"\bleave the account active\b",
        r"\b(?:continue|keep) using (?:the|this) (?:service|account)\b",
        r"\bi still use it\b",
        r"\bi still (?:need|want) (?:the|my|this) account\b",
    )),
    ("marketing_only", _rx(
        r"\b(?:marketing|newsletter|promotional)\b[^.]*\bonly\b",
        r"\bonly\b[^.]*\b(?:marketing|newsletter|promotional)\b",
    )),
)

# 2. Request kinds that are not deletion.
_KINDS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("EXPORT", _rx(r"\bdownload (?:my|the) (?:account|data|information)\b", r"\bportable format\b", r"\bdata portability\b")),
    ("ACCESS", _rx(r"\b(?:send|give|provide|need) (?:me )?a copy\b", r"\bcopy of (?:the|my|last)\b", r"\bwhat (?:personal )?(?:data|information) you hold\b", r"\binformation request\b")),
    ("CORRECTION", _rx(r"\bmisspel+ed\b", r"\b(?:correct|fix|update) (?:it|my (?:name|surname|address))\b")),
    ("EMAIL_CHANGE", _rx(r"\bchange the email\b", r"\bchange my email\b", r"\bupdate (?:my|the) email\b")),
    ("PHONE_CHANGE", _rx(r"\b(?:mobile|phone) number\b[^.]*\b(?:out of date|replace|update|change)\b")),
    ("LOGIN_HELP", _rx(r"\breset(?:ting)? (?:the |my )?(?:login|password)\b", r"\bcan'?t (?:access|log ?in|sign in)\b")),
    ("MARKETING_OPT_OUT", _rx(
        r"\bunsubscribe\b", r"\bremove me from marketing\b", r"\bopt(?:-| )?out\b",
        r"\bstop (?:the |sending )?(?:promotional|marketing)\b",
    )),
)

# 3. Deletion verbs, multilingual where it is cheap.
_DELETION: Final = _rx(
    r"\b(?:delet|eras|remov|clos)\w*",
    r"\bsuppression\b", r"\bsupprim\w*",
    r"\beliminaci[oó]n\b", r"\belimin\w*",
    r"\bexclus[aã]o\b", r"\bexclu[ií]\w*",
    r"\bl[öo]sch\w*",
    r"削除", r"삭제", r"удал\w*",
)

_SCOPE_HINTS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("retain_backups", _rx(r"\bretain backups\b", r"\bkeep backups\b")),
    ("anonymous_stats_ok", _rx(r"\bkeep anonymous statistics\b", r"\banonymous stat")),
    ("tombstone_ok", _rx(r"\bdeleted-account reference\b", r"\bkeep only the reference\b")),
)


def detect_language(text: str) -> str:
    if re.search(r"[぀-ヿ一-鿿]", text):
        return "ja"
    if re.search(r"[가-힯]", text):
        return "ko"
    if re.search(r"[Ѐ-ӿ]", text):
        return "ru"
    lowered = text.lower()
    if re.search(r"\b(?:solicito a|exclusão|conta|vocês|verifiquem)\b", lowered):
        return "pt"
    if re.search(r"\b(?:solicito la|cuenta|eliminación|correo|intenté)\b", lowered):
        return "es"
    if re.search(r"\b(?:je demande|compte|suppression|veuillez)\b", lowered):
        return "fr"
    if re.search(r"\b(?:konto|löschung|bitte)\b", lowered):
        return "de"
    return "en"


class RuleIntakeAgent:
    """Deterministic baseline; no provider calls."""

    def classify(self, case_id: str, request_text: str) -> IntakeResult:
        text = " ".join(request_text.split())
        language = detect_language(text)
        hints = tuple(name for name, rx in _SCOPE_HINTS if rx.search(text))
        for name, rx in _GUARDS:
            m = rx.search(text)
            if m:
                kind = next((k for k, krx in _KINDS if krx.search(text)), "NON_DELETION")
                return IntakeResult(case_id, False, language, kind, hints, f"guard {name}: {m.group(0)!r}")
        deletion = _DELETION.search(text)
        for kind, rx in _KINDS:
            m = rx.search(text)
            if m and not deletion:
                return IntakeResult(case_id, False, language, kind, hints, f"kind {kind}: {m.group(0)!r}")
        if deletion:
            return IntakeResult(case_id, True, language, KIND_DELETION, hints, f"deletion verb {deletion.group(0)!r}")
        return IntakeResult(
            case_id, True, language, KIND_AMBIGUOUS, hints, "no deletion verb and no non-deletion signal"
        )


class LLMIntakeAgent:
    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def classify(self, case_id: str, request_text: str) -> IntakeResult:
        raise NotImplementedError("intake.LLMIntakeAgent.classify")
