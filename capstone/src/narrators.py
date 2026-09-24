"""Narrator backends: the same job, done by a hosted model or a local one.

Each backend turns (system prompt, facts) into a draft with four fields. It does not
check anything — `narrate.narrate` does that for every backend identically — so a local
3B model and Claude are judged by exactly the same rule: every id and number it writes
must exist in the engine's output.

`OllamaBackend` talks to a local Ollama server over its HTTP API with the standard
library, so running a local model adds no Python dependency. It asks Ollama for
schema-constrained output (`format` = the JSON schema), which Ollama supports from 0.5.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol

NARRATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["customer_message", "reviewer_note", "cited_elements", "cited_fixes"],
    "properties": {
        "customer_message": {"type": "string"},
        "reviewer_note": {"type": "string"},
        "cited_elements": {"type": "array", "items": {"type": "string"}},
        "cited_fixes": {"type": "array", "items": {"type": "string"}},
    },
}

TOOL_NAME = "write_explanation"


class NoDraft(Exception):
    """The backend produced nothing usable. The message says why; it becomes the reason
    the template shipped instead."""


class Backend(Protocol):
    name: str  # what `Narration.source` records, e.g. "ollama:qwen2.5:3b"

    def draft(self, system: str, user: str) -> dict[str, Any]: ...


# --------------------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------------------


class AnthropicBackend:
    """Claude via a strict tool. `tool_choice` is auto with the tool named in the prompt,
    because newer models reject forced tool choice."""

    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model
        self.name = model

    def draft(self, system: str, user: str) -> dict[str, Any]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system + f"\n\nCall the {TOOL_NAME} tool with your answer. "
            "Do not reply with plain text.",
            tools=[
                {
                    "name": TOOL_NAME,
                    "description": "Submit the customer message and reviewer note.",
                    "strict": True,
                    "input_schema": NARRATION_SCHEMA,
                }
            ],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": user}],
        )
        for block in response.content:
            if getattr(block, "type", "") == "tool_use" and block.name == TOOL_NAME:
                return dict(block.input)
        raise NoDraft(f"no {TOOL_NAME} call (stop_reason={response.stop_reason})")


# --------------------------------------------------------------------------------------
# Ollama
# --------------------------------------------------------------------------------------

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:3b"


class OllamaBackend:
    """A local model through Ollama's /api/chat.

    Host and model come from `OLLAMA_HOST` / `OLLAMA_MODEL` unless passed. Temperature is
    0 and the context is widened to 8192: the facts run to ~1,100 tokens and Ollama's
    default context is small enough on some versions to truncate them silently, which
    would read as the model inventing numbers when it had simply lost the input.
    """

    def __init__(
        self, model: str | None = None, host: str | None = None, timeout_s: float = 120.0
    ) -> None:
        self.model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        self.host = (host or os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)).rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self.timeout_s = timeout_s
        self.name = f"ollama:{self.model}"

    def draft(self, system: str, user: str) -> dict[str, Any]:
        body = {
            "model": self.model,
            "stream": False,
            "format": NARRATION_SCHEMA,
            "options": {"temperature": 0, "num_ctx": 8192},
            "messages": [
                {
                    "role": "system",
                    "content": system + "\n\nAnswer with a single JSON object with the "
                    "fields customer_message, reviewer_note, cited_elements, cited_fixes.",
                },
                {"role": "user", "content": user},
            ],
        }
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NoDraft(f"ollama unreachable at {self.host}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise NoDraft(f"ollama returned non-JSON: {exc}") from exc

        content = (payload.get("message") or {}).get("content", "")
        try:
            draft = json.loads(content)
        except json.JSONDecodeError as exc:
            raise NoDraft(f"model output is not JSON: {content[:80]!r}") from exc
        if not isinstance(draft, dict):
            raise NoDraft("model output is not a JSON object")
        return draft

    def available(self) -> bool:
        """True if the server answers and has this model pulled."""
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=5) as response:
                tags = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return False
        names = {m.get("name", "") for m in tags.get("models", [])}
        return self.model in names or f"{self.model}:latest" in names


__all__ = [
    "AnthropicBackend",
    "Backend",
    "NARRATION_SCHEMA",
    "NoDraft",
    "OllamaBackend",
    "TOOL_NAME",
]
