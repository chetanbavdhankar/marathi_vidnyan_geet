"""Golden-shape test for the agent pipeline.

We mock `main._call` so no LLM is actually invoked. The mock returns a canned
Pydantic instance keyed off the requested `response_model`. The test asserts:
- the sequence of yielded events matches the expected pipeline shape
- the final result dict carries the expected keys
- artifacts land on disk in the expected layout
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
from models import (  # noqa: E402
    UserInput,
    DraftSong,
    FactCheckReport,
    ClaimVerdict,
    PolishedLyrics,
    ProducerScript,
    MixSpec,
    SongSection,
    VerifierReport,
    ComprehensionCheck,
    ViralPotentialReport,
    ViralDimension,
)


def _canned(response_model):
    """Return a representative instance for whichever model the pipeline asked for."""
    if response_model is DraftSong:
        return DraftSong(title="चाचणी गीत", lyrics="[Verse]\nरेषा एक\n[Chorus]\nहूक", claims=["Test claim 1"])
    if response_model is FactCheckReport:
        return FactCheckReport(
            verdicts=[ClaimVerdict(claim="Test claim 1", verdict="correct")],
            misconceptions=["Stub misconception"],
        )
    if response_model is PolishedLyrics:
        return PolishedLyrics(title="अंतिम चाचणी गीत", lyrics="[Verse]\nपॉलिश\n[Chorus]\nहूक")
    if response_model is ProducerScript:
        return ProducerScript(
            producer_notes="[Style: test]\n[Verse 1: Mood - X | Energy - Low]\nलिरिक्स",
            mix_spec=MixSpec(
                genre="test",
                sections=[SongSection(name="verse 1", mood="X", energy="low",
                                      instrumentation=["dhol"], vocal_delivery="rap")],
                global_instrumentation=["dhol"],
                target_duration_seconds=180,
            ),
        )
    if response_model is VerifierReport:
        return VerifierReport(
            questions=[
                ComprehensionCheck(question=f"Q{i}", answer_from_lyrics="A", answerable=True)
                for i in range(3)
            ],
            verdict="pass",
            summary="Stub verdict",
        )
    if response_model is ViralPotentialReport:
        dim = ViralDimension(score=8, justification="Stub justification")
        return ViralPotentialReport(
            hook_score=dim, replay_score=dim, magnetism_score=dim,
            emotion_score=dim, cultural_fit_score=dim, verdict="pass",
        )
    raise AssertionError(f"unexpected response_model: {response_model}")


@pytest.fixture(autouse=True)
def _mock_io(monkeypatch, tmp_path):
    # Redirect output dir into pytest's tmp_path so we don't pollute the real one.
    monkeypatch.setattr(main, "OUTPUT_ROOT", tmp_path)
    # Skip the network call to Wikipedia.
    monkeypatch.setattr(main, "_fetch_wikipedia_summary_sync", lambda topic: "stub grounding")
    # Replace the LLM call entirely.
    monkeypatch.setattr(main, "_call", lambda model, system, user, response_model, **kw: _canned(response_model))
    yield tmp_path


def test_pipeline_yields_expected_shape(_mock_io):
    user_input = UserInput(topic="Black Holes", grade_level="middle school")

    async def collect():
        out = []
        async for event in main.generate_song(user_input, "fake/model"):
            out.append(event)
        return out

    events = asyncio.run(collect())

    statuses = [e["status"] for e in events if "status" in e]
    results = [e["result"] for e in events if "result" in e]

    # Six agent stages, each emits a status before its call, plus the kickoff banner.
    assert len(statuses) == 7, f"expected 7 status events, got {len(statuses)}: {statuses}"
    # Stages should be announced in pipeline order.
    assert "1/6" in statuses[1] and "Drafting" in statuses[1]
    assert "2/6" in statuses[2] and "Fact-checking" in statuses[2]
    assert "3/6" in statuses[3] and "Polishing" in statuses[3]
    assert "4/6" in statuses[4] and "producer script" in statuses[4].lower()
    assert "5/6" in statuses[5] and "comprehension" in statuses[5].lower()
    assert "6/6" in statuses[6] and "viral" in statuses[6].lower()

    # Exactly one final result.
    assert len(results) == 1
    r = results[0]
    assert set(r) == {"title", "lyrics", "producer_notes", "mix_spec", "verifier_report", "viral_report", "safe_topic"}
    assert r["safe_topic"] == "black_holes"
    assert r["verifier_report"]["verdict"] == "pass"
    assert r["viral_report"]["verdict"] == "pass"
    assert r["mix_spec"]["genre"] == "test"


def test_pipeline_writes_artifacts(_mock_io):
    tmp_path = _mock_io
    user_input = UserInput(topic="Photosynthesis")

    async def run():
        async for _ in main.generate_song(user_input, "fake/model"):
            pass

    asyncio.run(run())

    out = tmp_path / "photosynthesis"
    assert (out / "photosynthesis_lyrics.txt").is_file()
    assert (out / "photosynthesis_producer_notes.txt").is_file()
    assert (out / "photosynthesis_mix_spec.json").is_file()
    assert (out / "photosynthesis_verifier_report.json").is_file()
    assert (out / "photosynthesis_viral_report.json").is_file()

    # mix_spec on disk must round-trip as valid JSON matching MixSpec.
    spec = json.loads((out / "photosynthesis_mix_spec.json").read_text(encoding="utf-8"))
    MixSpec(**spec)

    # viral_report on disk must round-trip.
    vr = json.loads((out / "photosynthesis_viral_report.json").read_text(encoding="utf-8"))
    ViralPotentialReport(**vr)


def test_verifier_failure_triggers_one_retry(monkeypatch, tmp_path):
    """If the verifier's first verdict is not 'pass', the pipeline must re-polish
    once. Polish should therefore be called twice total. The second verdict is
    accepted regardless."""
    monkeypatch.setattr(main, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(main, "_fetch_wikipedia_summary_sync", lambda topic: None)

    call_log = []

    def first_verifier_fails(model, system, user, response_model, **kw):
        call_log.append(response_model.__name__)
        if response_model is VerifierReport:
            verifier_calls = sum(1 for x in call_log if x == "VerifierReport")
            verdict = "fail" if verifier_calls == 1 else "pass"
            return VerifierReport(
                questions=[ComprehensionCheck(question="q", answer_from_lyrics="a", answerable=verdict == "pass")] * 3,
                verdict=verdict,
                summary="stub",
            )
        return _canned(response_model)

    monkeypatch.setattr(main, "_call", first_verifier_fails)

    async def run():
        async for _ in main.generate_song(UserInput(topic="DNA"), "fake/model"):
            pass

    asyncio.run(run())

    polish_calls = sum(1 for x in call_log if x == "PolishedLyrics")
    producer_calls = sum(1 for x in call_log if x == "ProducerScript")
    verifier_calls = sum(1 for x in call_log if x == "VerifierReport")
    assert polish_calls == 2, f"expected 2 polish calls (initial + 1 retry), got {polish_calls}"
    assert producer_calls == 2
    assert verifier_calls == 2
