"""Benchmark narrators: which model writes explanations that survive the claim check?

    # local models through Ollama (pull them first: `ollama pull qwen2.5:3b`)
    python -m capstone.evals.narration_eval --backend ollama \\
        --model qwen2.5:3b --model llama3.2:3b --model phi4-mini --model gemma3:4b

    python -m capstone.evals.narration_eval --backend anthropic   # CHEAP_MODEL from .env
    python -m capstone.evals.narration_eval --backend template    # the floor: always passes

The metric that matters is **pass rate**: the share of files where the model's narration
cleared the claim check and would ship. A model that fails falls back to the template,
so a low pass rate costs polish, never correctness — but it also means the model is not
earning its latency. Latency is reported because a local 3B model on a laptop CPU and one
on a GPU box differ by an order of magnitude, and that decides whether it can sit in the
upload path at all.

Every draft, shipped or not, is written to `--out` as JSONL for a human to read. Pass
rate says the numbers were right; only reading them says the message was good.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from collections import Counter
from pathlib import Path

from capstone.evals.harness import REPO_ROOT, load_cases
from capstone.src.deciders import LogisticModel
from capstone.src.narrate import narrate, template_narration
from capstone.src.narrators import AnthropicBackend, Backend, OllamaBackend
from capstone.src.pipeline import Outcome, run
from capstone.src.schemas import Split, VerdictType

DEFAULT_MANIFEST = REPO_ROOT / "capstone" / "data" / "cases_large.jsonl"


class TemplateBackend:
    """The no-model floor, as a backend, so it runs through the same loop."""

    name = "template"

    def draft(self, system: str, user: str) -> dict:  # pragma: no cover - never called
        raise AssertionError("template narration does not draft")


def _bucket(problem: str) -> str:
    for key in (
        "unreachable",
        "not JSON",
        "schema",
        "unsupported number",
        "unknown id",
        "fidelity",
        "no write_explanation",
    ):
        if key in problem:
            return key
    return "other"


def outcomes(manifest: Path, split: Split | None, limit: int) -> list[Outcome]:
    """Files that need explaining: not approved as uploaded, and decoded."""
    model = LogisticModel.load()
    out_dir = Path(tempfile.mkdtemp(prefix="narration-proofs-"))
    found: list[Outcome] = []
    for case, _label in load_cases(manifest, split):
        outcome = run(case, model, out_dir)
        if outcome.verdict.verdict is VerdictType.APPROVE or outcome.plan is None:
            continue
        found.append(outcome)
        if len(found) >= limit:
            break
    return found


def benchmark(backend: Backend | TemplateBackend, cases: list[Outcome], out) -> dict:  # noqa: ANN001
    passed = 0
    reasons: Counter[str] = Counter()
    latencies: list[float] = []
    for o in cases:
        assert o.scene is not None and o.plan is not None
        t0 = time.perf_counter()
        if isinstance(backend, TemplateBackend):
            shipped, problems = template_narration(o.scene, o.plan, o.verdict), []
            model_passed = True
        else:
            shipped, problems = narrate(backend, o.scene, o.plan, o.verdict)
            model_passed = not problems and shipped.source == backend.name
        latencies.append(time.perf_counter() - t0)
        passed += model_passed
        reasons.update(_bucket(p) for p in problems[:1])
        out.write(
            json.dumps(
                {
                    "backend": backend.name,
                    "case_id": o.case_id,
                    "passed": model_passed,
                    "problems": problems,
                    "customer_message": shipped.customer_message,
                    "reviewer_note": shipped.reviewer_note,
                    "shipped_source": shipped.source,
                }
            )
            + "\n"
        )
    n = len(cases)
    return {
        "backend": backend.name,
        "files": n,
        "pass_rate": round(passed / n, 3) if n else 0.0,
        "fallback_reasons": dict(reasons),
        "latency_p50_s": round(statistics.median(latencies), 2) if latencies else None,
        "latency_p95_s": round(sorted(latencies)[int(0.95 * (n - 1))], 2) if latencies else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark narrators against the claim check.")
    ap.add_argument("--backend", choices=["template", "ollama", "anthropic"], default="template")
    ap.add_argument(
        "--model",
        action="append",
        default=None,
        help="model name; repeat to compare several (ollama)",
    )
    ap.add_argument("--manifest", type=str, default=str(DEFAULT_MANIFEST))
    ap.add_argument("--split", choices=["train", "holdout", "all"], default="train")
    ap.add_argument("--limit", type=int, default=40, help="files to narrate (default 40)")
    ap.add_argument("--out", type=str, default=None, help="JSONL of every draft")
    args = ap.parse_args()

    backends: list[Backend | TemplateBackend]
    if args.backend == "template":
        backends = [TemplateBackend()]
    elif args.backend == "ollama":
        backends = [OllamaBackend(m) for m in (args.model or [None])]
        missing = [b.name for b in backends if not b.available()]  # type: ignore[union-attr]
        if missing:
            raise SystemExit(
                f"not reachable or not pulled: {', '.join(missing)}. Start `ollama serve`, "
                "`ollama pull <model>`, and set OLLAMA_HOST if it is not localhost:11434."
            )
    else:
        import anthropic

        from shared.env import cheap_model

        backends = [
            AnthropicBackend(anthropic.Anthropic(), m) for m in (args.model or [cheap_model()])
        ]

    split = None if args.split == "all" else Split(args.split)
    print(f"preparing up to {args.limit} files that need explaining ...")
    cases = outcomes(Path(args.manifest), split, args.limit)
    print(f"{len(cases)} files\n")

    out_path = Path(args.out) if args.out else Path(tempfile.mkdtemp()) / "narrations.jsonl"
    with out_path.open("w", encoding="utf-8") as out:
        for backend in backends:
            report = benchmark(backend, cases, out)
            print(json.dumps(report))
    print(f"\nevery draft: {out_path}")


if __name__ == "__main__":
    main()
