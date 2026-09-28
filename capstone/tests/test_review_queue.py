"""The human-review queue. Offline; SQLite in a temp dir."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from capstone.ops import review_queue
from capstone.ops.review_queue import ReviewQueue
from capstone.src.schemas import (
    EscalationReason,
    Evidence,
    Issue,
    IssueCode,
    OrderMetadata,
    PreflightCase,
    Severity,
    Verdict,
    VerdictType,
)

APPROVE = Verdict(verdict=VerdictType.APPROVE, confidence=0.99, checks_completed=["x"])
ESCALATE = Verdict(
    verdict=VerdictType.ESCALATE,
    confidence=0.4,
    escalation_reason=EscalationReason.LOW_CONFIDENCE,
    issues=[
        Issue(
            code=IssueCode.CONTENT_IN_SAFE_ZONE,
            severity=Severity.ADVISORY,
            message="An element reaches into the margin.",
            evidence=Evidence(measured=1.12, required=1.0, unit="safe-zone depth"),
        )
    ],
)
FIX = Verdict(
    verdict=VerdictType.REQUEST_FIX,
    confidence=0.95,
    issues=[
        Issue(
            code=IssueCode.LOW_RESOLUTION,
            severity=Severity.BLOCKING,
            message="Artwork is 75 DPI.",
            evidence=Evidence(measured=75, required=150, unit="dpi"),
        )
    ],
    customer_message="Please send a higher-resolution file.",
)


def case(tmp_path: Path, name: str, order: str = "ORD-1", content: bytes = b"x") -> PreflightCase:
    path = tmp_path / f"{name}.tif"
    path.write_bytes(content)
    return PreflightCase(
        case_id=name,
        image_path=path,
        order=OrderMetadata(
            order_id=order, product_id="die-cut-sticker", width_in=2, height_in=2, quantity=50
        ),
    )


@pytest.fixture
def queue(tmp_path: Path) -> ReviewQueue:
    q = ReviewQueue(tmp_path / "q.sqlite3")
    yield q
    q.close()


def test_approvals_are_never_queued(queue: ReviewQueue, tmp_path: Path) -> None:
    assert queue.enqueue(case(tmp_path, "a"), APPROVE) is None
    assert queue.stats() == {}


def test_escalation_carries_its_reasoning_and_evidence(queue: ReviewQueue, tmp_path: Path) -> None:
    item = queue.get(queue.enqueue(case(tmp_path, "a"), ESCALATE))
    assert item.reason == "LOW_CONFIDENCE"
    (issue,) = item.issues
    assert issue["code"] == "CONTENT_IN_SAFE_ZONE"
    assert issue["evidence"]["measured"] == 1.12


def test_same_upload_twice_is_one_item(queue: ReviewQueue, tmp_path: Path) -> None:
    c = case(tmp_path, "a")
    assert queue.enqueue(c, ESCALATE) == queue.enqueue(c, ESCALATE)
    assert queue.stats() == {"open:ESCALATE": 1}


def test_new_upload_for_the_same_order_is_a_new_item(queue: ReviewQueue, tmp_path: Path) -> None:
    first = queue.enqueue(case(tmp_path, "a", content=b"v1"), ESCALATE)
    second = queue.enqueue(case(tmp_path, "b", content=b"v2"), ESCALATE)
    assert first != second


def test_escalations_are_claimed_before_fix_requests(queue: ReviewQueue, tmp_path: Path) -> None:
    queue.enqueue(case(tmp_path, "fix", "ORD-1", b"1"), FIX)
    queue.enqueue(case(tmp_path, "esc", "ORD-2", b"2"), ESCALATE)
    assert queue.claim_next("ana").case_id == "esc"
    assert queue.claim_next("ana").case_id == "fix"
    assert queue.claim_next("ana") is None


def test_two_reviewers_never_get_the_same_file(queue: ReviewQueue, tmp_path: Path) -> None:
    for i in range(3):
        queue.enqueue(case(tmp_path, f"c{i}", f"ORD-{i}", bytes([i])), ESCALATE)
    claimed = [queue.claim_next("ana").id, queue.claim_next("ben").id, queue.claim_next("ana").id]
    assert len(set(claimed)) == 3


def test_a_stale_claim_returns_to_the_queue(
    queue: ReviewQueue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue.enqueue(case(tmp_path, "a"), ESCALATE)
    first = queue.claim_next("ana")
    assert queue.claim_next("ben") is None  # still fresh
    monkeypatch.setattr(review_queue, "CLAIM_TIMEOUT", timedelta(seconds=-1))
    assert queue.claim_next("ben").id == first.id


def test_resolution_is_recorded_once_and_exported_as_a_label(
    queue: ReviewQueue, tmp_path: Path
) -> None:
    item_id = queue.enqueue(case(tmp_path, "a"), ESCALATE)
    queue.claim_next("ana")
    queue.resolve(item_id, "ana", "approve", note="background bleed, deliberate")
    with pytest.raises(KeyError):
        queue.resolve(item_id, "ben", "reject")
    (label,) = queue.export_labels()
    assert label["reviewer_decision"] == "approve"
    assert label["pipeline_issues"] == ["CONTENT_IN_SAFE_ZONE"]


def test_unknown_decision_is_refused(queue: ReviewQueue, tmp_path: Path) -> None:
    item_id = queue.enqueue(case(tmp_path, "a"), ESCALATE)
    with pytest.raises(ValueError):
        queue.resolve(item_id, "ana", "maybe")  # type: ignore[arg-type]


def test_triage_and_route_queues_only_non_approvals(queue: ReviewQueue, tmp_path: Path) -> None:
    from capstone.ops.review_queue import triage_and_route

    verdicts = iter([APPROVE, ESCALATE])
    triage_and_route(case(tmp_path, "a", "ORD-1", b"1"), queue, lambda c: next(verdicts))
    triage_and_route(case(tmp_path, "b", "ORD-2", b"2"), queue, lambda c: next(verdicts))
    assert queue.stats() == {"open:ESCALATE": 1}
