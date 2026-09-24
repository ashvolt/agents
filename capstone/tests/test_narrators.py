"""Narrator backends, exercised against a real HTTP server standing in for Ollama.

The mock speaks Ollama's /api/chat and /api/tags wire format, so the request the backend
builds and the response it parses are the real ones; only the model is scripted. The
live-model test at the bottom runs when an Ollama server with the model is reachable.

Offline otherwise. No API, no spend.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from capstone.src.narrate import narrate
from capstone.src.narrators import NARRATION_SCHEMA, OllamaBackend
from capstone.tests.test_scene_fixes_narrate import ESCALATE, planned


class FakeOllama:
    """A tiny HTTP server answering like Ollama with a scripted `message.content`."""

    def __init__(self) -> None:
        self.reply: str = "{}"
        self.requests: list[dict] = []
        self.models = ["qwen2.5:3b"]
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers["Content-Length"])
                fake.requests.append(json.loads(self.rfile.read(length)))
                self._send({"message": {"role": "assistant", "content": fake.reply}})

            def do_GET(self) -> None:  # noqa: N802
                self._send({"models": [{"name": m} for m in fake.models]})

            def _send(self, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def ollama() -> Iterator[FakeOllama]:
    fake = FakeOllama()
    yield fake
    fake.server.shutdown()


def draft(**fields: object) -> str:
    base = {"customer_message": "", "reviewer_note": "", "cited_elements": [], "cited_fixes": []}
    return json.dumps({**base, **fields})


def test_request_is_schema_constrained_text_only(ollama: FakeOllama, tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    ollama.reply = draft(customer_message="Please approve the proof.")
    narrate(OllamaBackend("qwen2.5:3b", ollama.url), scene, plan, ESCALATE)
    (sent,) = ollama.requests
    assert sent["format"] == NARRATION_SCHEMA
    assert sent["options"]["temperature"] == 0
    assert sent["stream"] is False
    assert all("images" not in m for m in sent["messages"])  # measurements, never pixels
    assert '"elements"' in sent["messages"][1]["content"]


def test_honest_local_model_narration_ships(ollama: FakeOllama, tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    ollama.reply = draft(
        customer_message="We moved your design away from the cut. Please approve the proof.",
        reviewer_note="F1 verified; E1 was in the margin.",
        cited_elements=["E1"],
        cited_fixes=["F1"],
    )
    shipped, problems = narrate(OllamaBackend("qwen2.5:3b", ollama.url), scene, plan, ESCALATE)
    assert problems == []
    assert shipped.source == "ollama:qwen2.5:3b"


def test_local_model_inventing_a_number_falls_back(ollama: FakeOllama, tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    ollama.reply = draft(customer_message="Your logo sat 0.31 in from the edge.")
    shipped, problems = narrate(OllamaBackend("qwen2.5:3b", ollama.url), scene, plan, ESCALATE)
    assert shipped.source == "template"
    assert "unsupported number 0.31" in problems


def test_non_json_output_falls_back(ollama: FakeOllama, tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    ollama.reply = "Sure! Here is the explanation you asked for."
    shipped, problems = narrate(OllamaBackend("qwen2.5:3b", ollama.url), scene, plan, ESCALATE)
    assert shipped.source == "template"
    assert "not JSON" in problems[0]


def test_missing_field_falls_back(ollama: FakeOllama, tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    ollama.reply = json.dumps({"customer_message": "ok"})
    shipped, problems = narrate(OllamaBackend("qwen2.5:3b", ollama.url), scene, plan, ESCALATE)
    assert shipped.source == "template"
    assert problems[0].startswith("schema")


def test_server_down_is_a_fallback_not_an_outage(tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    dead = OllamaBackend("qwen2.5:3b", "http://127.0.0.1:9", timeout_s=2)
    shipped, problems = narrate(dead, scene, plan, ESCALATE)
    assert shipped.source == "template"
    assert "unreachable" in problems[0]


def test_available_checks_the_model_is_pulled(ollama: FakeOllama) -> None:
    assert OllamaBackend("qwen2.5:3b", ollama.url).available()
    assert not OllamaBackend("llama3.2:3b", ollama.url).available()


def test_host_and_model_come_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "gpu-box:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "phi4-mini")
    backend = OllamaBackend()
    assert backend.host == "http://gpu-box:11434"
    assert backend.name == "ollama:phi4-mini"


_live = OllamaBackend()


@pytest.mark.integration
@pytest.mark.skipif(not _live.available(), reason=f"no Ollama with {_live.model} reachable")
def test_live_local_model_narration(tmp_path: Path) -> None:
    scene, plan = planned(tmp_path)
    shipped, problems = narrate(_live, scene, plan, ESCALATE)
    assert problems == [], problems
    assert shipped.source == _live.name
