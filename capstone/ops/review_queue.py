"""The human-review queue: where every non-approval goes, with its reasoning attached.

brief.md §7: "The agent may never silently approve. Every non-approval carries its
reasoning and the evidence into the queue, because an escalation without reasoning is
just a slower version of doing it by hand."

    python -m capstone.ops.review_queue stats
    python -m capstone.ops.review_queue next  --reviewer ana
    python -m capstone.ops.review_queue resolve <item-id> --reviewer ana --decision approve
    python -m capstone.ops.review_queue export labels.jsonl

Design decisions:

- **SQLite, standard library.** limits.md §9 chose JSONL over a database, and for eval
  runs that still holds. A queue is different: two reviewers must never claim the same
  file, and "take the next open item" has to be one atomic step. SQLite gives that in one
  file with no server.
- **Idempotent enqueue.** An item's id is a hash of the order id and the file's bytes. The
  same upload processed twice is one item (PLAN.md §6.6); a new upload for the same order
  is a new item, as it should be.
- **Claims expire.** A reviewer who opens a file and walks away does not strand it: claims
  older than CLAIM_TIMEOUT return to the queue.
- **Reviewer decisions are the product's future labels.** Every resolution records what a
  person decided about a file the pipeline could not settle. `export` writes them in the
  eval harness's label format: the real-data set this project has never had (limits.md
  §10) accumulates as a side effect of doing the job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from capstone.src.schemas import PreflightCase, Verdict, VerdictType

DEFAULT_DB = Path(__file__).resolve().parents[1] / "ops" / "review_queue.sqlite3"
CLAIM_TIMEOUT = timedelta(minutes=30)

Decision = Literal["approve", "request_fix", "reject"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id               TEXT PRIMARY KEY,
    order_id         TEXT NOT NULL,
    case_id          TEXT NOT NULL,
    file_path        TEXT NOT NULL,
    file_sha256      TEXT NOT NULL,
    verdict          TEXT NOT NULL,          -- REQUEST_FIX or ESCALATE
    reason           TEXT,                   -- escalation reason, if escalated
    issues_json      TEXT NOT NULL,          -- codes, messages, measured evidence
    customer_message TEXT,                   -- REQUEST_FIX: the draft an artist approves
    priority         INTEGER NOT NULL,       -- lower is sooner
    status           TEXT NOT NULL DEFAULT 'open',   -- open | claimed | resolved
    claimed_by       TEXT,
    claimed_at       TEXT,
    decision         TEXT,                   -- approve | request_fix | reject
    resolved_by      TEXT,
    resolved_at      TEXT,
    note             TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_open ON items (status, priority, created_at);
"""


class QueueItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    order_id: str
    case_id: str
    file_path: str
    verdict: str
    reason: str | None
    issues: list[dict[str, object]]
    customer_message: str | None
    priority: int
    status: str
    claimed_by: str | None = None
    decision: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _priority(verdict: Verdict) -> int:
    """Escalations first: they need judgement. A fix request only needs its drafted
    message checked, which is the one-click path brief.md §7 describes."""
    return 0 if verdict.verdict is VerdictType.ESCALATE else 1


class ReviewQueue:
    def __init__(self, path: Path | str = DEFAULT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, isolation_level=None)  # explicit transactions
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # -- writing -----------------------------------------------------------------------

    def enqueue(self, case: PreflightCase, verdict: Verdict) -> str | None:
        """Queue a non-approval. Returns its id, or None for an approval (never queued).

        Raises if a non-approval carries no reasoning at all — an empty escalation is the
        failure brief.md §7 names, and it should be loud, not queued.
        """
        if verdict.verdict is VerdictType.APPROVE:
            return None
        if not verdict.issues and verdict.escalation_reason is None:
            raise ValueError(f"{case.case_id}: non-approval with no issues and no reason")

        sha = _file_sha256(case.image_path)
        item_id = hashlib.sha256(f"{case.order.order_id}:{sha}".encode()).hexdigest()[:16]
        issues = [
            {
                "code": i.code.value,
                "severity": i.severity.value,
                "message": i.message,
                "evidence": i.evidence.model_dump(mode="json", exclude_none=True),
            }
            for i in verdict.issues
        ]
        self._db.execute(
            """INSERT OR IGNORE INTO items
               (id, order_id, case_id, file_path, file_sha256, verdict, reason,
                issues_json, customer_message, priority, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id,
                case.order.order_id,
                case.case_id,
                str(case.image_path),
                sha,
                verdict.verdict.value,
                verdict.escalation_reason.value if verdict.escalation_reason else None,
                json.dumps(issues),
                verdict.customer_message,
                _priority(verdict),
                _now().isoformat(),
            ),
        )
        return item_id

    def claim_next(self, reviewer: str) -> QueueItem | None:
        """Atomically take the most urgent open item (or one whose claim went stale)."""
        stale = (_now() - CLAIM_TIMEOUT).isoformat()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                """SELECT id FROM items
                   WHERE status = 'open' OR (status = 'claimed' AND claimed_at < ?)
                   ORDER BY priority, created_at LIMIT 1""",
                (stale,),
            ).fetchone()
            if row is None:
                self._db.execute("COMMIT")
                return None
            self._db.execute(
                "UPDATE items SET status='claimed', claimed_by=?, claimed_at=? WHERE id=?",
                (reviewer, _now().isoformat(), row["id"]),
            )
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise
        return self.get(row["id"])

    def resolve(self, item_id: str, reviewer: str, decision: Decision, note: str = "") -> None:
        if decision not in ("approve", "request_fix", "reject"):
            raise ValueError(f"unknown decision {decision!r}")
        updated = self._db.execute(
            """UPDATE items SET status='resolved', decision=?, resolved_by=?, resolved_at=?,
                                note=?
               WHERE id=? AND status != 'resolved'""",
            (decision, reviewer, _now().isoformat(), note, item_id),
        ).rowcount
        if updated == 0:
            raise KeyError(f"{item_id}: no such open item")

    # -- reading -----------------------------------------------------------------------

    def get(self, item_id: str) -> QueueItem | None:
        row = self._db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return None
        return QueueItem(
            id=row["id"],
            order_id=row["order_id"],
            case_id=row["case_id"],
            file_path=row["file_path"],
            verdict=row["verdict"],
            reason=row["reason"],
            issues=json.loads(row["issues_json"]),
            customer_message=row["customer_message"],
            priority=row["priority"],
            status=row["status"],
            claimed_by=row["claimed_by"],
            decision=row["decision"],
        )

    def stats(self) -> dict[str, int]:
        rows = self._db.execute(
            "SELECT status, verdict, COUNT(*) AS n FROM items GROUP BY status, verdict"
        ).fetchall()
        return {f"{r['status']}:{r['verdict']}": r["n"] for r in rows}

    def export_labels(self) -> list[dict[str, object]]:
        """Resolved items as labelled data: what a person decided, and what the pipeline
        had flagged. The seed of a real-upload eval set."""
        rows = self._db.execute(
            "SELECT * FROM items WHERE status='resolved' ORDER BY resolved_at"
        ).fetchall()
        return [
            {
                "case_id": r["case_id"],
                "order_id": r["order_id"],
                "file_sha256": r["file_sha256"],
                "pipeline_verdict": r["verdict"],
                "pipeline_issues": [i["code"] for i in json.loads(r["issues_json"])],
                "reviewer_decision": r["decision"],
                "reviewer": r["resolved_by"],
                "note": r["note"],
            }
            for r in rows
        ]


def triage_and_route(
    case: PreflightCase, queue: ReviewQueue, triage: object | None = None
) -> Verdict:
    """Run the shipped pipeline on one upload; queue it unless it was approved."""
    if triage is None:
        from capstone.src.deciders import make_cv_decider_triage

        triage = make_cv_decider_triage()
    verdict = triage(case)  # type: ignore[operator]
    queue.enqueue(case, verdict)
    return verdict


def main() -> None:
    ap = argparse.ArgumentParser(description="Human-review queue.")
    ap.add_argument("--db", type=str, default=str(DEFAULT_DB))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    nxt = sub.add_parser("next")
    nxt.add_argument("--reviewer", required=True)
    res = sub.add_parser("resolve")
    res.add_argument("item_id")
    res.add_argument("--reviewer", required=True)
    res.add_argument("--decision", required=True, choices=["approve", "request_fix", "reject"])
    res.add_argument("--note", default="")
    exp = sub.add_parser("export")
    exp.add_argument("out")
    ing = sub.add_parser("ingest", help="run a manifest through the pipeline into the queue")
    ing.add_argument("manifest")
    ing.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    queue = ReviewQueue(args.db)
    if args.cmd == "stats":
        print(json.dumps(queue.stats(), indent=2))
    elif args.cmd == "next":
        item = queue.claim_next(args.reviewer)
        print("queue empty" if item is None else item.model_dump_json(indent=2))
    elif args.cmd == "resolve":
        queue.resolve(args.item_id, args.reviewer, args.decision, args.note)
        print(f"resolved {args.item_id}: {args.decision}")
    elif args.cmd == "ingest":
        from capstone.evals.harness import load_cases
        from capstone.src.deciders import make_cv_decider_triage

        triage = make_cv_decider_triage()
        counts: dict[str, int] = {}
        for case, _label in load_cases(Path(args.manifest), None)[: args.limit]:
            verdict = triage_and_route(case, queue, triage)
            counts[verdict.verdict.value] = counts.get(verdict.verdict.value, 0) + 1
        print(json.dumps({"routed": counts, "queue": queue.stats()}, indent=2))
    else:
        labels = queue.export_labels()
        Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in labels), encoding="utf-8")
        print(f"wrote {len(labels)} reviewer decisions -> {args.out}")


if __name__ == "__main__":
    main()


__all__ = ["CLAIM_TIMEOUT", "QueueItem", "ReviewQueue", "triage_and_route"]
