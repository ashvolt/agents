"""The lettering-review page data: the summary must match what rule-loop.md reports.

Pure logic over the committed label files; no images, no network.
"""

from __future__ import annotations

from capstone.demo.build_lettering import (
    _read,
    category,
    confidence_note,
    items,
    summary,
)

R1 = _read("lettering_round1.jsonl")
R1B = _read("lettering_round1b.jsonl")


def test_summary_matches_the_published_round_results() -> None:
    s = summary(R1, R1B)
    assert (s["round1_controls_right"], s["round1_controls"]) == (17, 20)
    assert (s["round1b_controls_right"], s["round1b_controls"]) == (20, 20)
    assert (s["repeats_same"], s["repeats"]) == (9, 15)
    assert s["repeats_crossed_print_line"] == 3


def test_every_round1_item_and_every_new_control_appears_once() -> None:
    entries = items(R1, R1B)
    new_controls = [r for r in R1B if r["kind"] == "control" and not r.get("round1_item")]
    assert len(entries) == len(R1) + len(new_controls)


def test_a_reshown_item_takes_its_readable_answer() -> None:
    for e in items(R1, R1B):
        if e["round1b_kind"] == "reshown":
            assert e["answer"] == e["round1b"]
        elif e["kind"] != "control" or e["round1"]:
            assert e["answer"] == e["round1"]


def test_changed_repeats_are_flipped_and_nothing_else_is() -> None:
    entries = items(R1, R1B)
    flipped = [e for e in entries if category(e) == "flipped"]
    assert len(flipped) == 6
    assert all(e["round1b_kind"] == "repeat" and e["round1"] != e["round1b"] for e in flipped)


def test_every_control_lies_on_the_computed_caption_band() -> None:
    # Controls are our injected caption by construction, so the band computed from the
    # plan must contain every one of them; a region well above it must not be flagged.
    from capstone.demo.build_lettering import caption_bands, on_caption, sealed_plans

    _rows, plans = sealed_plans()
    plan_of = {p.case_id: p for p in plans}
    controls = [
        e
        for e in items(R1, R1B)
        if e["kind"] == "control" and e.get("set", "ai_art_v1") == "ai_art_v1"
    ]
    assert controls
    for e in controls:
        bands = caption_bands(plan_of[e["case_id"]], e["canvas"][1])
        assert on_caption(e["region"], bands), e["item"]
        x0, y0, x1, y1 = e["region"]
        assert not on_caption([x0, 0, x1, y1 - y0], bands), e["item"]


def test_confidence_is_worded_as_a_measurement_never_a_probability() -> None:
    assert "half a pixel" in confidence_note({"ratio": 0.97, "gap_px": 0.3})
    assert "Close" in confidence_note({"ratio": 0.9, "gap_px": 1.2})
    assert "Clearly under" in confidence_note({"ratio": 0.4, "gap_px": 9.0})
    assert "%" in confidence_note({"ratio": 0.4, "gap_px": 9.0})
    assert confidence_note(None).startswith("No text-size finding")
