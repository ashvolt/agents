"""Metrics for one sweep.

The headline pair is SC-001 and SC-002: auto-approve rate, subject to false-approve rate
at or below 1%. Accuracy is deliberately absent — it averages over the one failure that
destroys the ROI model, which is why brief.md S5 does not use it.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass, field

from capstone.src.schemas import IssueCode, RunResult, VerdictType

# SC-002. Not a target — a constraint. A sweep that breaches it has failed regardless of
# what its approve rate says.
FALSE_APPROVE_LIMIT = 0.01

# SC-001.
APPROVE_RATE_TARGET = 0.60


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p), summed in log space."""
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    total = 0.0
    for i in range(k + 1):
        log_term = (
            math.lgamma(n + 1)
            - math.lgamma(i + 1)
            - math.lgamma(n - i + 1)
            + i * log_p
            + (n - i) * log_q
        )
        total += math.exp(log_term)
    return min(1.0, total)


def clopper_pearson_upper(k: int, n: int, alpha: float = 0.05) -> float:
    """Exact one-sided upper confidence bound on a binomial rate, k events in n."""
    if n <= 0:
        return 1.0
    if k >= n:
        return 1.0
    lo, hi = k / n, 1.0
    for _ in range(100):  # bisection; the CDF falls monotonically in p
        mid = (lo + hi) / 2
        if _binom_cdf(k, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


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
    def false_approve_ci_upper(self) -> float:
        """95% upper bound on the true false-approve rate (exact, one-sided).

        Clopper-Pearson: the largest rate at which seeing this few wrong approvals would
        still happen 5% of the time. With none it is about 3/n (the rule of three).

        This exists because the point estimate is misleading at small n. With 45
        approvals, one wrong approval scores 2.2% and zero score 0% — the rate cannot
        land on 1% at all. Reporting "0%" as a pass would be claiming a precision the
        sample does not have.

        Until 2026-09-24 this used a normal approximation when k > 0, which is too
        optimistic for a handful of events: 2 of 479 read 1.0%, the exact bound is 1.3%.
        """
        n = self.approved
        if n == 0:
            return 1.0
        return clopper_pearson_upper(self.approved_defective, n)

    @property
    def false_approve_resolution(self) -> float:
        """Smallest non-zero false-approve rate this many approvals can report."""
        return 1.0 / self.approved if self.approved else 1.0

    @property
    def can_measure_constraint(self) -> bool:
        """Whether the sample is large enough for SC-002 to be a meaningful test.

        False means a single wrong approval already scores above the limit, so the only
        outcomes are "zero observed" and "fail". That is a statement about the dataset,
        not about the agent.
        """
        return self.false_approve_resolution <= FALSE_APPROVE_LIMIT

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
            "false_approve_ci_upper_95": round(self.false_approve_ci_upper, 4),
            "false_approve_resolution": round(self.false_approve_resolution, 4),
            "can_measure_constraint": self.can_measure_constraint,
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
        breach = "OK" if self.meets_constraint else "*** BREACH ***"
        mix = f"({self.n_clean} clean / {self.n_defective} defective)"
        verdicts = (
            f"APPROVE {self.approved} / REQUEST_FIX {self.request_fix} / ESCALATE {self.escalated}"
        )
        lines = [
            f"--- {self.arm}  [{ok}] ---",
            f"  cases              {self.n_cases}  {mix}",
            f"  auto-approve rate  {self.auto_approve_rate:6.1%}"
            f"   (SC-001 target >= {APPROVE_RATE_TARGET:.0%})",
            f"  FALSE-APPROVE rate {self.false_approve_rate:6.1%}"
            f"   (SC-002 limit  <= {FALSE_APPROVE_LIMIT:.0%})  {breach}",
            f"      95% upper bound {self.false_approve_ci_upper:6.1%}"
            f"   resolution {self.false_approve_resolution:.1%} "
            f"({self.approved} approvals)",
            f"  false-reject rate  {self.false_reject_rate:6.1%}",
            f"  escalation rate    {self.escalation_rate:6.1%}",
            f"  verdicts           {verdicts}",
        ]
        if self.borderline_recall is not None:
            lines.append(
                f"  borderline recall  {self.borderline_recall:6.1%} "
                f"({self.borderline_detected}/{self.borderline_injected} within 10% of threshold)"
            )
        lines.append(
            f"  cost/file          ${self.cost_per_file_usd:.4f}   total ${self.total_cost_usd:.2f}"
        )
        lines.append(
            f"  latency p95        {self.p95_latency_ms} ms   mean {self.mean_latency_ms} ms"
        )
        if self.billed_input_tokens:
            flag = "  *** zero cache reads ***" if self.cache_suspect else ""
            lines.append(f"  cache hit rate     {self.cache_hit_rate:6.1%}{flag}")
        if not self.can_measure_constraint:
            lines.append(
                f"  !! SC-002 NOT MEASURABLE at n={self.approved} approvals: one wrong "
                f"approval scores {self.false_approve_resolution:.1%}, already over the "
                f"{FALSE_APPROVE_LIMIT:.0%} limit."
            )
            lines.append(
                "     This run can only report 'zero observed' or 'fail'. "
                f"Measuring {FALSE_APPROVE_LIMIT:.0%} needs ~{round(3 / FALSE_APPROVE_LIMIT)} "
                "approvals."
            )
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
