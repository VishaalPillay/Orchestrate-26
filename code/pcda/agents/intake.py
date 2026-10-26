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


# Request kinds the LLM may answer with: the rule agent's kinds, minus AMBIGUOUS.
LLM_KINDS: Final[tuple[str, ...]] = (
    KIND_DELETION, "ACCESS", "EXPORT", "CORRECTION", "EMAIL_CHANGE", "PHONE_CHANGE",
    "LOGIN_HELP", "MARKETING_OPT_OUT", "NON_DELETION",
)
INTAKE_TASK: Final = "intake.batch"
INTAKE_MODEL_ENV: Final = "INTAKE_MODEL"
BATCH_SIZE: Final = 20
INTAKE_SCHEMA: Final[dict[str, object]] = {"type": "object", "required": ["results"]}


@dataclass(frozen=True)
class LLMIntakeAnswer:
    """One validated LLM answer. Only answers whose evidence_span is verbatim in the text survive."""

    case_id: str
    authorizes_deletion: bool
    request_kind: str
    language: str | None
    confidence: float | None
    evidence_span: str
    rationale: str

    def as_result(self) -> IntakeResult:
        return IntakeResult(
            self.case_id, self.authorizes_deletion, self.language, self.request_kind, (),
            f"llm: {self.rationale} [span {self.evidence_span!r}]",
        )


def _span_in(span: str, text: str) -> bool:
    if len(span.strip()) < 3:
        return False
    if span in text:
        return True
    return " ".join(span.split()) in " ".join(text.split())  # whitespace-only differences


def _block(case_id: str, text: str) -> str:
    # The request is data: fenced, and any closing fence inside it is broken up.
    return f'<request id="{case_id}">\n{text.replace("</request", "</ request")}\n</request>'


class LLMIntakeAgent:
    """Batch LLM intake. `prepare` must see every case first; `answer` then serves one.

    Batches are fixed by sorted case_id, so the cache keys do not depend on
    which split is run. An answer is discarded (with the reason kept) when its
    evidence_span is not a substring of that request's text, when a field is
    outside the allowed enum, or when the case is missing from the reply.
    """

    def __init__(self, client: LLMClient, batch_size: int = BATCH_SIZE, model_env: str = INTAKE_MODEL_ENV) -> None:
        self._client = client
        self._batch_size = batch_size
        self._model_env = model_env
        self.answers: dict[str, LLMIntakeAnswer] = {}
        self.rejected: dict[str, str] = {}  # case_id -> why the LLM answer was not used
        self.calls = 0

    def prepare(self, cases: list[tuple[str, str]]) -> None:
        """cases: (case_id, request_text). One call per batch of `batch_size`."""
        from pcda.agents import prompts
        from pcda.llm.client import LLMRequest, LLMUnavailable

        system, user = prompts.load(INTAKE_TASK)
        ordered = sorted(cases)
        for i in range(0, len(ordered), self._batch_size):
            batch = ordered[i:i + self._batch_size]
            blocks = "\n\n".join(_block(cid, text) for cid, text in batch)
            request = LLMRequest(
                task=INTAKE_TASK, prompt=user.replace("{{REQUESTS}}", blocks), system=system,
                model_env=self._model_env, schema=INTAKE_SCHEMA,
            )
            try:
                resp = self._client.complete(request)
            except LLMUnavailable as exc:  # live: fall back to rules; replay misses propagate (CacheMiss)
                for cid, _ in batch:
                    self.rejected[cid] = f"llm unavailable: {exc}"[:200]
                continue
            self.calls += 0 if resp.cached else 1
            self._ingest(batch, resp.parsed)

    def _ingest(self, batch: list[tuple[str, str]], parsed: object) -> None:
        texts = dict(batch)
        rows = parsed.get("results") if isinstance(parsed, dict) else None
        seen: set[str] = set()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or row.get("case_id") not in texts:
                continue
            cid = str(row["case_id"])
            if cid in seen:
                self.rejected[cid] = "duplicate answer in batch"
                self.answers.pop(cid, None)
                continue
            seen.add(cid)
            span = str(row.get("evidence_span") or "")
            kind = str(row.get("request_kind") or "")
            auth = row.get("authorizes_deletion")
            if not isinstance(auth, bool):
                self.rejected[cid] = f"authorizes_deletion not a boolean: {auth!r}"
            elif kind not in LLM_KINDS:
                self.rejected[cid] = f"request_kind {kind!r} not allowed"
            elif auth != (kind == KIND_DELETION):
                self.rejected[cid] = f"authorizes_deletion={auth} contradicts request_kind {kind}"
            elif not _span_in(span, texts[cid]):
                self.rejected[cid] = f"evidence_span not in request text: {span!r}"
            else:
                conf = row.get("confidence")
                self.answers[cid] = LLMIntakeAnswer(
                    cid, auth, kind, str(row.get("language") or "") or None,
                    float(conf) if isinstance(conf, (int, float)) else None,
                    span, str(row.get("rationale") or "")[:300],
                )
        for cid in texts:
            if cid not in seen and cid not in self.rejected:
                self.rejected[cid] = "no answer for this case in the batch reply"

    def answer(self, case_id: str) -> LLMIntakeAnswer | None:
        return self.answers.get(case_id)

    def classify(self, case_id: str, request_text: str) -> IntakeResult:
        a = self.answers.get(case_id)
        if a is None:
            from pcda.llm.client import LLMUnavailable

            raise LLMUnavailable(f"{case_id}: {self.rejected.get(case_id, 'not prepared')}")
        return a.as_result()


# A non-deletion answer overrides the rules only when its span itself refuses or
# limits deletion. "Can you verify that my account was deleted?" is not a negation.
_NEGATION: Final = _rx(
    r"\b(?:not|no|never|don'?t|doesn'?t|without|instead|rather than|only|keep|stop)\b",
    r"\bne\b.*\bpas\b", r"\bnicht\b", r"\bnão\b", r"\bno\s+\w+", r"しない", r"않",
)


def is_negation(span: str) -> bool:
    return bool(_NEGATION.search(span))


@dataclass(frozen=True)
class IntakeDecision:
    result: IntakeResult
    source: str  # "agree" | "rules" | "corroboration" | "llm_safety" | "rules_only"
    flags: tuple[str, ...]
    rule: IntakeResult
    llm: LLMIntakeAnswer | None
    llm_rejected: str = ""
    corroboration: tuple[str, ...] = ()

    @property
    def disagreement(self) -> bool:
        return self.llm is not None and self.llm.authorizes_deletion != self.rule.authorizes_deletion


def decide_intake(
    rule: IntakeResult,
    llm: LLMIntakeAnswer | None,
    corroboration: tuple[str, ...],
    llm_rejected: str = "",
) -> IntakeDecision:
    """Combine rules, the validated LLM answer and CLASSIFY_REQUEST corroboration.

    * no usable LLM answer (none, or its span failed validation): rules win;
    * agree: done;
    * disagree: flag intake_conflict:llm; a CLASSIFY_REQUEST non-deletion row
      on the subject's resources decides (NOT_APPLICABLE);
    * otherwise an LLM non-deletion answer wins only when its validated span
      is itself a negation ("not asking for deletion", "keep my account"),
      because an unauthorized irreversible deletion is the worse failure;
      a non-negating span ("verify that my account was deleted") leaves the
      rules in charge;
    * a rule non-deletion answer (an explicit guard) is never overridden by
      an LLM deletion answer.
    """
    if llm is None:
        flags = ("intake_llm_rejected",) if llm_rejected and not llm_rejected.startswith("llm unavailable") else ()
        return IntakeDecision(rule, "rules_only", flags, rule, None, llm_rejected, corroboration)
    if llm.authorizes_deletion == rule.authorizes_deletion:
        return IntakeDecision(rule, "agree", (), rule, llm, "", corroboration)
    flags = ("intake_conflict:llm",)
    if corroboration:
        chosen = rule if rule.authorizes_deletion is False else llm.as_result()
        return IntakeDecision(chosen, "corroboration", flags, rule, llm, "", corroboration)
    if llm.authorizes_deletion is False and is_negation(llm.evidence_span):
        return IntakeDecision(llm.as_result(), "llm_safety", flags, rule, llm, "", corroboration)
    return IntakeDecision(rule, "rules", flags, rule, llm, "", corroboration)
