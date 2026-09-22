"""Metrics for one sweep.

The headline pair is SC-001 and SC-002: auto-approve rate, subject to false-approve rate
at or below 1%. Accuracy is deliberately absent — it averages over the one failure that
destroys the ROI model, which is why brief.md S5 does not use it.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field

from capstone.src.schemas import IssueCode, RunResult, VerdictType

# SC-002. Not a target — a constraint. A sweep that breaches it has failed regardless of
# what its approve rate says.
FALSE_APPROVE_LIMIT = 0.01

# SC-001.
APPROVE_RATE_TARGET = 0.60


@dataclass(frozen=True)
class CodeRecall:
    code: IssueCode
    detected: int
    injected: int

    @property
    def recall(self) -> float | None:
        return self.detected / self.injected if self.injected else None


@dataclass
class SweepReport:
    """What one run of the eval set produced."""

    arm: str
    n_cases: int
    n_clean: int
    n_defective: int

    approved: int
    request_fix: int
    escalated: int

    approved_clean: int
    approved_defective: int
    rejected_clean: int

    per_issue: dict[IssueCode, CodeRecall] = field(default_factory=dict)
    borderline_detected: int = 0
    borderline_injected: int = 0

    total_cost_usd: float = 0.0
    latencies_ms: list[int] = field(default_factory=list)
    cache_read_tokens: int = 0
    billed_input_tokens: int = 0
    crash_count: int = 0

    # --- the headline pair ------------------------------------------------------------

    @property
    def auto_approve_rate(self) -> float:
        """SC-001. Share of genuinely clean files cleared without a human."""
        return self.approved_clean / self.n_clean if self.n_clean else 0.0

    @property
    def false_approve_rate(self) -> float:
        """SC-002. Share of approvals that were wrong.

        Denominator is *approvals*, not defective files, because the question this
        answers is "how much can I trust an APPROVE" — which is what decides whether the
        human can be taken out of the loop. Using total defectives instead would let an
        agent that approves almost nothing report a flattering number.
        """
        return self.approved_defective / self.approved if self.approved else 0.0

    @property
    def meets_constraint(self) -> bool:
        return self.false_approve_rate <= FALSE_APPROVE_LIMIT

    @property
    def meets_target(self) -> bool:
        return self.auto_approve_rate >= APPROVE_RATE_TARGET

    @property
    def passes(self) -> bool:
        return self.meets_constraint and self.meets_target and self.crash_count == 0

    # --- secondary --------------------------------------------------------------------

    @property
    def false_reject_rate(self) -> float:
        """Clean files sent for a fix or escalated. Costly but recoverable."""
        return self.rejected_clean / self.n_clean if self.n_clean else 0.0

    @property
    def escalation_rate(self) -> float:
        return self.escalated / self.n_cases if self.n_cases else 0.0

    @property
    def cost_per_file_usd(self) -> float:
        return self.total_cost_usd / self.n_cases if self.n_cases else 0.0

    @property
    def p95_latency_ms(self) -> int:
        if not self.latencies_ms:
            return 0
        ordered = sorted(self.latencies_ms)
        idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return ordered[idx]

    @property
    def mean_latency_ms(self) -> int:
        return int(statistics.fmean(self.latencies_ms)) if self.latencies_ms else 0

    @property
    def borderline_recall(self) -> float | None:
        """Recall restricted to defects within 10% of the threshold.

        Reported separately because it is the number that predicts SC-002 in production.
        A system that scores well overall and badly here is one that has only learned the
        easy cases, and real files cluster near limits, not far past them.
        """
        if not self.borderline_injected:
            return None
        return self.borderline_detected / self.borderline_injected

    @property
    def cache_hit_rate(self) -> float:
        billed = self.billed_input_tokens + self.cache_read_tokens
        return self.cache_read_tokens / billed if billed else 0.0

    @property
    def cache_suspect(self) -> bool:
        """Zero cache reads across a whole sweep means a silent invalidator.

        Constitution Principle VI treats caching as load-bearing rather than as an
        optimisation, so this is surfaced as a defect rather than a curiosity.
        """
        return self.billed_input_tokens > 0 and self.cache_read_tokens == 0

    # --- presentation -----------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "arm": self.arm,
            "n_cases": self.n_cases,
            "n_clean": self.n_clean,
            "n_defective": self.n_defective,
            "auto_approve_rate": round(self.auto_approve_rate, 4),
            "false_approve_rate": round(self.false_approve_rate, 4),
            "false_reject_rate": round(self.false_reject_rate, 4),
            "escalation_rate": round(self.escalation_rate, 4),
            "approved": self.approved,
            "approved_clean": self.approved_clean,
            "approved_defective": self.approved_defective,
            "request_fix": self.request_fix,
            "escalated": self.escalated,
            "borderline_recall": (
                round(self.borderline_recall, 4) if self.borderline_recall is not None else None
            ),
            "per_issue_recall": {
                str(code): round(r.recall, 4) if r.recall is not None else None
                for code, r in sorted(self.per_issue.items(), key=lambda kv: str(kv[0]))
            },
            "cost_per_file_usd": round(self.cost_per_file_usd, 6),
            "total_cost_usd": round(self.total_cost_usd, 4),
            "p95_latency_ms": self.p95_latency_ms,
            "mean_latency_ms": self.mean_latency_ms,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "cache_suspect": self.cache_suspect,
            "crash_count": self.crash_count,
            "meets_constraint_sc002": self.meets_constraint,
            "meets_target_sc001": self.meets_target,
            "passes": self.passes,
        }

    def render(self) -> str:
        ok = "PASS" if self.passes else "FAIL"
        lines = [
            f"--- {self.arm}  [{ok}] ---",
            f"  cases              {self.n_cases}  ({self.n_clean} clean / {self.n_defective} defective)",
            f"  auto-approve rate  {self.auto_approve_rate:6.1%}   (SC-001 target >= {APPROVE_RATE_TARGET:.0%})",
            f"  FALSE-APPROVE rate {self.false_approve_rate:6.1%}   (SC-002 limit  <= {FALSE_APPROVE_LIMIT:.0%})"
            f"  {'OK' if self.meets_constraint else '*** BREACH ***'}",
            f"  false-reject rate  {self.false_reject_rate:6.1%}",
            f"  escalation rate    {self.escalation_rate:6.1%}",
            f"  verdicts           APPROVE {self.approved} / REQUEST_FIX {self.request_fix} / ESCALATE {self.escalated}",
        ]
        if self.borderline_recall is not None:
            lines.append(
                f"  borderline recall  {self.borderline_recall:6.1%} "
                f"({self.borderline_detected}/{self.borderline_injected} within 10% of threshold)"
            )
        lines.append(f"  cost/file          ${self.cost_per_file_usd:.4f}   total ${self.total_cost_usd:.2f}")
        lines.append(f"  latency p95        {self.p95_latency_ms} ms   mean {self.mean_latency_ms} ms")
        if self.billed_input_tokens:
            flag = "  *** zero cache reads: silent invalidator ***" if self.cache_suspect else ""
            lines.append(f"  cache hit rate     {self.cache_hit_rate:6.1%}{flag}")
        if self.crash_count:
            lines.append(f"  CRASHES            {self.crash_count}  (SC-007 requires 0)")
        lines.append("  per-issue recall:")
        for code, r in sorted(self.per_issue.items(), key=lambda kv: str(kv[0])):
            if not r.injected:
                continue
            lines.append(f"    {str(code):26s} {r.detected:3d}/{r.injected:<3d} {r.recall:6.1%}")
        return "\n".join(lines)


def score(results: list[RunResult], arm: str = "default") -> SweepReport:
    """Aggregate per-case results into a sweep report."""
    per_issue_detected: Counter[IssueCode] = Counter()
    per_issue_injected: Counter[IssueCode] = Counter()
    borderline_detected = borderline_injected = 0

    approved = request_fix = escalated = 0
    approved_clean = approved_defective = rejected_clean = 0
    total_cost = 0.0
    latencies: list[int] = []
    cache_read = billed_input = 0
    crashes = 0
    n_clean = n_defective = 0

    for r in results:
        label, verdict, trace = r.label, r.verdict, r.trace

        if label.is_clean:
            n_clean += 1
        else:
            n_defective += 1

        if verdict.verdict is VerdictType.APPROVE:
            approved += 1
            if label.is_clean:
                approved_clean += 1
            else:
                approved_defective += 1
        else:
            if verdict.verdict is VerdictType.REQUEST_FIX:
                request_fix += 1
            else:
                escalated += 1
            if label.is_clean:
                rejected_clean += 1

        found = r.detected_codes
        for perturbation in label.defects:
            per_issue_injected[perturbation.code] += 1
            hit = perturbation.code in found
            if hit:
                per_issue_detected[perturbation.code] += 1
            if perturbation.borderline:
                borderline_injected += 1
                if hit:
                    borderline_detected += 1

        total_cost += trace.cost_usd
        latencies.append(trace.latency_ms)
        cache_read += trace.cache_read_tokens
        billed_input += trace.input_tokens
        if trace.termination == "error":
            crashes += 1

    per_issue = {
        code: CodeRecall(
            code=code,
            detected=per_issue_detected.get(code, 0),
            injected=per_issue_injected.get(code, 0),
        )
        for code in IssueCode
    }

    return SweepReport(
        arm=arm,
        n_cases=len(results),
        n_clean=n_clean,
        n_defective=n_defective,
        approved=approved,
        request_fix=request_fix,
        escalated=escalated,
        approved_clean=approved_clean,
        approved_defective=approved_defective,
        rejected_clean=rejected_clean,
        per_issue=per_issue,
        borderline_detected=borderline_detected,
        borderline_injected=borderline_injected,
        total_cost_usd=total_cost,
        latencies_ms=latencies,
        cache_read_tokens=cache_read,
        billed_input_tokens=billed_input,
        crash_count=crashes,
    )


__all__ = [
    "APPROVE_RATE_TARGET",
    "FALSE_APPROVE_LIMIT",
    "CodeRecall",
    "SweepReport",
    "score",
]
