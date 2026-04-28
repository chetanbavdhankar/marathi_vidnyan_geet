"""Vingyan Gaani — Marathi science song generator.

Pipeline (all agents share one LiteLLM-routed model unless overridden):
  Draft → Fact-check (Wikipedia-grounded) → Polish → Producer Script → Verifier → Viral Critic
  (Verifier may trigger one retry through Polish + Producer Script.)
  (Viral Critic is the final gate — on fail it writes a rejection summary with alternatives.)
"""
import os
import re
import json
import base64
import asyncio
import urllib.parse
import urllib.request
import urllib.error
import traceback
from pathlib import Path
from typing import Optional

import instructor
import litellm
from dotenv import load_dotenv
from litellm import completion

# Silently drop params unsupported by specific providers (e.g. parallel_tool_calls for Ollama).
litellm.drop_params = True

from models import (
    UserInput,
    DraftSong,
    FactCheckReport,
    PolishedLyrics,
    ProducerScript,
    VerifierReport,
    ViralPotentialReport,
    SongResponse,
)
from prompts import (
    DRAFT_SYSTEM,
    FACTCHECK_SYSTEM,
    POLISH_SYSTEM,
    PRODUCER_SCRIPT_SYSTEM,
    VERIFIER_SYSTEM,
    VIRAL_CRITIC_SYSTEM,
    LYRIA_SYSTEM,
    USER_TEMPLATE,
)
from genres import lookup_genre, format_genre_guide

# Load .env from the project root (next to this file). Previous code looked one level up — bug.
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# LiteLLM Gemini SDK reads GEMINI_API_KEY; users typically set GOOGLE_API_KEY. Bridge them.
if "GOOGLE_API_KEY" in os.environ and "GEMINI_API_KEY" not in os.environ:
    os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]

DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "gemini/gemini-3.1-pro-preview")
PROJECT_ROOT = Path(__file__).parent
OUTPUT_ROOT = PROJECT_ROOT / "output"


# --- Safe filename helper (also covers traversal) ---

_SAFE_TOPIC_RE = re.compile(r"[^a-z0-9]+")

def safe_topic(topic: str) -> str:
    """Whitelist-only sanitizer. Strips everything that isn't ASCII alphanumeric,
    collapses runs to a single underscore, trims leading/trailing underscores.
    Devanagari and other Unicode are dropped — we use this only for filesystem
    paths, not for display."""
    if not topic:
        return "unknown_topic"
    cleaned = _SAFE_TOPIC_RE.sub("_", topic.lower()).strip("_")
    return cleaned or "unknown_topic"


def safe_folder_name(topic: str, genre: Optional[str] = None) -> str:
    """Combines sanitized topic with genre suffix for output folder naming.
    Example: safe_folder_name('Black Holes', 'Rap') → 'black_holes_rap'
    Falls back to topic-only when genre is absent."""
    base = safe_topic(topic)
    if genre:
        suffix = safe_topic(genre)
        if suffix != "unknown_topic":
            return f"{base}_{suffix}"
    return base


# --- One instructor client, shared across agents ---

def _client(model: str = ""):
    """instructor wraps LiteLLM completions to enforce a Pydantic response_model.
    Ollama models use JSON mode (schema-in-prompt) to avoid tool-call issues;
    API models use the default TOOLS mode."""
    mode = instructor.Mode.JSON if model.startswith("ollama/") else instructor.Mode.TOOLS
    return instructor.from_litellm(completion, mode=mode)


def _call(model: str, system: str, user: str, response_model, *, temperature: float = 0.7, max_retries: int = 2):
    """Single synchronous call. Used inside `asyncio.to_thread` from the async pipeline."""
    is_ollama = model.startswith("ollama/")
    extra_kwargs: dict = {}

    if is_ollama:
        # Thinking models (Qwen 3.5, QwQ, DeepSeek-R1) route reasoning to a
        # separate `thinking` field, leaving `content` empty.
        # Two-layer defence:
        #   1. API-level: `think: false` via extra_body (authoritative toggle).
        #   2. Prompt-level: `/no_think` suffix (soft switch, unreliable on ≤4B).
        system = system.rstrip() + "\n\n/no_think"
        extra_kwargs["extra_body"] = {"think": False}
        # Give small models enough context window for prompt + JSON output.
        extra_kwargs["num_ctx"] = 8192

    return _client(model).chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_model=response_model,
        max_retries=max_retries if not is_ollama else max(max_retries, 3),
        temperature=temperature,
        timeout=180 if is_ollama else 120,
        **extra_kwargs,
    )


# --- Wikipedia grounding for the fact-checker ---

def _fetch_wikipedia_summary_sync(topic: str) -> Optional[str]:
    """One HTTP call, no API key. Returns the lead extract or None on miss."""
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(topic)}"
    req = urllib.request.Request(url, headers={"User-Agent": "VingyanGaani/1.0 (educational)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("extract")
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        return None


# --- The pipeline ---

async def generate_song(user_input: UserInput, model_name: str):
    yield {"status": f"🚀 Initiating multi-agent workflow on {model_name}..."}

    # Build the user prompt once; later agents extend it with their context.
    base_user_prompt = USER_TEMPLATE.format(
        topic=user_input.topic,
        region=user_input.region,
        genre=user_input.genre or "Not specified (use culturally appropriate)",
        instruments=user_input.instruments or "Not specified",
        grade_level=user_input.grade_level or "general",
        reference_style=user_input.reference_style or "None",
        additional_info=user_input.additional_info or "None",
    )
    if (guide := lookup_genre(user_input.genre)) is not None:
        base_user_prompt += f"\n\nGenre reference (Marathi musical tradition):\n{format_genre_guide(guide)}"

    # --- Agent 1: Draft (lyrics + extracted claims) ---
    yield {"status": "[Agent 1/6] Drafting lyrics and extracting claims... ⏳"}
    draft: DraftSong = await asyncio.to_thread(
        _call, model_name, DRAFT_SYSTEM, base_user_prompt, DraftSong, temperature=0.85
    )

    # --- Agent 2: Wikipedia-grounded fact-check ---
    yield {"status": "[Agent 2/6] Fact-checking against external grounding... 🔎"}
    wiki_extract = await asyncio.to_thread(_fetch_wikipedia_summary_sync, user_input.topic)
    factcheck_user = (
        f"Topic: {user_input.topic}\n\n"
        f"Wikipedia grounding (may be empty if no article found):\n"
        f"{wiki_extract or '[no grounding available — use your own knowledge with caution]'}\n\n"
        f"Claims to verify:\n"
        + "\n".join(f"- {c}" for c in draft.claims)
    )
    factcheck: FactCheckReport = await asyncio.to_thread(
        _call, model_name, FACTCHECK_SYSTEM, factcheck_user, FactCheckReport, temperature=0.2
    )

    # --- Agent 3: Polish (incorporates fact-check) ---
    yield {"status": "[Agent 3/6] Polishing lyrics with corrections... 🎤"}
    polish_user = _polish_user_prompt(user_input, draft.lyrics, factcheck, gap_report=None)
    polished: PolishedLyrics = await asyncio.to_thread(
        _call, model_name, POLISH_SYSTEM, polish_user, PolishedLyrics, temperature=0.7
    )

    # --- Agent 4: Producer Script (split out of the polish prompt) ---
    yield {"status": "[Agent 4/6] Generating Suno/Lyria producer script and mix spec... 🎛️"}
    producer = await _generate_producer_script(model_name, user_input, polished)

    # --- Agent 5: Comprehension verifier ---
    yield {"status": "[Agent 5/6] Verifying listener comprehension at target grade level... 🎓"}
    verifier = await _run_verifier(model_name, user_input, polished)

    # If verdict isn't pass, give it ONE retry through Polish + Producer Script.
    if verifier.verdict != "pass":
        yield {"status": f"⚠ Verifier verdict '{verifier.verdict}' — re-polishing once with gap report..."}
        gap_summary = _summarize_verifier_gaps(verifier)
        polish_user = _polish_user_prompt(user_input, polished.lyrics, factcheck, gap_report=gap_summary)
        polished = await asyncio.to_thread(
            _call, model_name, POLISH_SYSTEM, polish_user, PolishedLyrics, temperature=0.7
        )
        producer = await _generate_producer_script(model_name, user_input, polished)
        verifier = await _run_verifier(model_name, user_input, polished)

    # --- Agent 6: Viral Potential Critic (final gate) ---
    yield {"status": "[Agent 6/6] Viral potential critic reviewing... 🔥"}
    viral_report = await _run_viral_critic(model_name, user_input, polished)

    # Persist artifacts — folder name: {topic}_{genre} (e.g. black_hole_rap)
    safe = safe_folder_name(user_input.topic, user_input.genre)
    topic_dir = OUTPUT_ROOT / safe
    topic_dir.mkdir(parents=True, exist_ok=True)

    (topic_dir / f"{safe}_lyrics.txt").write_text(
        f"=== {polished.title} ===\n\n--- MARATHI LYRICS ---\n\n{polished.lyrics}\n",
        encoding="utf-8",
    )
    (topic_dir / f"{safe}_producer_notes.txt").write_text(
        f"=== {polished.title} (Producer Script) ===\n\n--- GRAMMY-LEVEL PRODUCER NOTES ---\n\n{producer.producer_notes}\n",
        encoding="utf-8",
    )
    (topic_dir / f"{safe}_mix_spec.json").write_text(
        producer.mix_spec.model_dump_json(indent=2), encoding="utf-8"
    )
    (topic_dir / f"{safe}_audio_style_prompt.txt").write_text(
        producer.audio_generator_style_prompt, encoding="utf-8"
    )
    (topic_dir / f"{safe}_verifier_report.json").write_text(
        verifier.model_dump_json(indent=2), encoding="utf-8"
    )
    (topic_dir / f"{safe}_viral_report.json").write_text(
        viral_report.model_dump_json(indent=2), encoding="utf-8"
    )

    # On viral critic fail → write a detailed rejection summary with alternatives.
    if viral_report.verdict == "fail":
        yield {"status": "⚠ Viral critic: song did NOT pass the hit-potential gate. Writing rejection summary..."}
        _write_viral_rejection_summary(topic_dir, safe, polished, viral_report)

    response = SongResponse(
        title=polished.title,
        lyrics=polished.lyrics,
        producer_notes=producer.producer_notes,
        mix_spec=producer.mix_spec,
        verifier_report=verifier,
        viral_report=viral_report,
    )
    yield {
        "result": {
            "title": response.title,
            "lyrics": response.lyrics,
            "producer_notes": response.producer_notes,
            "mix_spec": response.mix_spec.model_dump() if response.mix_spec else None,
            "verifier_report": response.verifier_report.model_dump() if response.verifier_report else None,
            "viral_report": response.viral_report.model_dump() if response.viral_report else None,
            "safe_topic": safe,
        }
    }


def _polish_user_prompt(
    user_input: UserInput,
    draft_lyrics: str,
    factcheck: FactCheckReport,
    gap_report: Optional[str],
) -> str:
    parts = [
        f"Original Topic: {user_input.topic}",
        f"Genre: {user_input.genre or 'unspecified'}",
        f"Target Grade Level: {user_input.grade_level or 'general'}",
        "",
        "Draft Lyrics:",
        draft_lyrics,
        "",
        "Fact Checker Report (structured):",
        factcheck.model_dump_json(indent=2),
    ]
    if gap_report:
        parts += ["", "Comprehension Gap Report from previous verifier pass:", gap_report]
    parts += [
        "",
        "Rewrite and finalize the song. Act on every 'incorrect' verdict; address the listed misconceptions where they fit.",
    ]
    return "\n".join(parts)


async def _generate_producer_script(model_name: str, user_input: UserInput, polished: PolishedLyrics) -> ProducerScript:
    user = (
        f"Title: {polished.title}\n"
        f"Genre: {user_input.genre or 'unspecified'}\n"
        f"Reference Style: {user_input.reference_style or 'none'}\n"
        f"Preferred Instruments: {user_input.instruments or 'producer choice'}\n\n"
        f"Final Lyrics:\n{polished.lyrics}"
    )
    return await asyncio.to_thread(
        _call, model_name, PRODUCER_SCRIPT_SYSTEM, user, ProducerScript, temperature=0.5
    )


async def _run_verifier(model_name: str, user_input: UserInput, polished: PolishedLyrics) -> VerifierReport:
    user = (
        f"Topic: {user_input.topic}\n"
        f"Target Grade Level: {user_input.grade_level or 'general'}\n\n"
        f"Final Lyrics:\n{polished.lyrics}"
    )
    return await asyncio.to_thread(
        _call, model_name, VERIFIER_SYSTEM, user, VerifierReport, temperature=0.3
    )


def _summarize_verifier_gaps(report: VerifierReport) -> str:
    lines = [f"Overall verdict: {report.verdict}", f"Summary: {report.summary}", "", "Specific gaps:"]
    for q in report.questions:
        if not q.answerable:
            lines.append(f"- Question: {q.question}")
            lines.append(f"  Gap: {q.gap or 'lyrics did not address this'}")
    return "\n".join(lines)


async def _run_viral_critic(model_name: str, user_input: UserInput, polished: PolishedLyrics) -> ViralPotentialReport:
    user = (
        f"Topic: {user_input.topic}\n"
        f"Genre: {user_input.genre or 'unspecified'}\n"
        f"Target Audience: {user_input.grade_level or 'general'}\n"
        f"Reference Style: {user_input.reference_style or 'none'}\n\n"
        f"Final Lyrics:\n{polished.lyrics}"
    )
    return await asyncio.to_thread(
        _call, model_name, VIRAL_CRITIC_SYSTEM, user, ViralPotentialReport, temperature=0.4
    )


def _write_viral_rejection_summary(
    topic_dir: Path, safe: str, polished: PolishedLyrics, report: ViralPotentialReport
) -> None:
    """Writes a human-readable rejection summary when the viral critic fails the song."""
    lines = [
        f"{'=' * 60}",
        f"VIRAL POTENTIAL REJECTION — {polished.title}",
        f"{'=' * 60}",
        f"",
        f"Verdict: FAIL (avg={report.average_score:.1f}/10, min={report.min_score}/10)",
        f"",
        f"--- DIMENSION SCORES ---",
        f"",
        f"  Hook Stickiness:    {report.hook_score.score}/10 — {report.hook_score.justification}",
        f"  Replay Value:       {report.replay_score.score}/10 — {report.replay_score.justification}",
        f"  Audience Magnetism: {report.magnetism_score.score}/10 — {report.magnetism_score.justification}",
        f"  Emotional Resonance:{report.emotion_score.score}/10 — {report.emotion_score.justification}",
        f"  Cultural Fit:       {report.cultural_fit_score.score}/10 — {report.cultural_fit_score.justification}",
        f"",
        f"--- WHY IT WON'T SUCCEED & ALTERNATIVES ---",
        f"",
        report.improvement_notes or "(No improvement notes provided)",
        f"",
        f"{'=' * 60}",
    ]
    (topic_dir / f"{safe}_viral_rejection_summary.txt").write_text(
        "\n".join(lines), encoding="utf-8"
    )


# --- Lyria audio generation ---

def generate_audio_from_notes(safe_topic_str: str, producer_notes: str, genre: Optional[str] = None) -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("Google API key not found in environment for audio generation.")

    # Re-derive the canonical folder name from raw inputs.
    folder = safe_folder_name(safe_topic_str, genre)

    # Endpoint without query-string key; auth moved to header.
    url = "https://generativelanguage.googleapis.com/v1beta/models/lyria-3-pro-preview:generateContent"

    lyria_prompt = LYRIA_SYSTEM.replace("{producer_notes}", producer_notes)

    payload = {
        "contents": [{"role": "user", "parts": [{"text": lyria_prompt}]}],
        "generationConfig": {"responseModalities": ["AUDIO"]},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
    )

    print("\n[Lyria] Sending track instructions to Lyria 3 Pro... ⏳")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Read the body but don't echo headers (the API key would be in the request, not response — still defensive)
        raise ValueError(f"Lyria HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise ValueError(f"Failed to connect to Lyria 3 Pro API: {e}")

    audio_data = None
    try:
        for part in data["candidates"][0]["content"]["parts"]:
            if "inlineData" in part:
                audio_data = part["inlineData"].get("data")
                break
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"Lyria response missing expected fields: {e}")

    if not audio_data:
        raise ValueError("No inline audio data found in the Lyria 3 Pro response.")

    output_dir = OUTPUT_ROOT / folder
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / f"{folder}_song.wav"
    audio_path.write_bytes(base64.b64decode(audio_data))
    print(f"\n✅ Audio saved to: {audio_path}")
    return str(audio_path)


# --- CLI ---

def _get_user_input() -> tuple[UserInput, str]:
    print("\n=== 🎵 Vingyan Gaani: Marathi Science Song Generator 🎵 ===")
    print("Welcome! Let's create an entertaining science song.\n")

    topic = input("Scientific Topic (Mandatory): ").strip()
    while not topic:
        print("Error: Topic is mandatory!")
        topic = input("Scientific Topic (Mandatory): ").strip()

    region = input("Region Focus [Default: Maharashtra]: ").strip() or "Maharashtra"
    genre = input("Song Genre (e.g., Powada, Lavani, Rap, Bhajan, Abhang, Koli) [Optional]: ").strip() or None
    instruments = input("Preferred Instruments [Optional]: ").strip() or None
    grade_level = input("Target Grade Level (elementary/middle school/high school/college/general/18+) [Default: general]: ").strip() or "general"
    reference_style = input("Reference Style (Artist or Song Name) [Optional]: ").strip() or None
    additional_info = input("Any other specifics? [Optional]: ").strip() or None

    infra_choice = input("Use local Ollama or API models? (ollama/api) [Default: api]: ").strip().lower()
    if infra_choice == "ollama":
        default_ollama = "qwen2.5:7b"
        print("\nChecking for running Ollama instance and available models...")
        try:
            req = urllib.request.Request("http://localhost:11434/api/tags")
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode())
                available = [m["name"] for m in data.get("models", [])]
                if available:
                    print("Available local models:")
                    for m in available:
                        print(f" - {m}")
                else:
                    print(f"No models found. You may need to run `ollama pull {default_ollama}`")
        except Exception:
            print("Could not connect to Ollama. Ensure the Ollama app is running.")
        model_name = input(f"\nEnter Ollama model [Default: {default_ollama}]: ").strip() or default_ollama
        model_choice = f"ollama/{model_name}"
    else:
        model_choice = input(f"API Model [Default: {DEFAULT_MODEL}]: ").strip() or DEFAULT_MODEL

    return (
        UserInput(
            topic=topic,
            region=region,
            genre=genre,
            instruments=instruments,
            grade_level=grade_level,
            reference_style=reference_style,
            additional_info=additional_info,
        ),
        model_choice,
    )


async def _amain():
    user_input, model_name = _get_user_input()
    result = None
    async for update in generate_song(user_input, model_name):
        if "status" in update:
            print(update["status"])
        elif "result" in update:
            result = update["result"]

    print("\n" + "=" * 60)
    print(f"🎶 TITLE: {result['title']}")
    print("=" * 60)
    print("\n📄 --- MARATHI LYRICS ---:\n")
    print(result["lyrics"])
    print("\n" + "=" * 60)
    print("\n🎛️ --- PRODUCER NOTES ---:\n")
    print(result["producer_notes"])
    print("=" * 60)

    if (vr := result.get("verifier_report")) is not None:
        print(f"\n🎓 Comprehension verdict: {vr['verdict']} — {vr['summary']}")
        for q in vr["questions"]:
            mark = "✓" if q["answerable"] else "✗"
            print(f"  {mark} {q['question']}")
            if not q["answerable"] and q.get("gap"):
                print(f"     gap: {q['gap']}")

    if (viral := result.get("viral_report")) is not None:
        avg = sum(viral[k]["score"] for k in ("hook_score", "replay_score", "magnetism_score", "emotion_score", "cultural_fit_score")) / 5
        icon = "✅" if viral["verdict"] == "pass" else "❌"
        print(f"\n🔥 Viral Potential: {icon} {viral['verdict'].upper()} (avg {avg:.1f}/10)")
        for dim in ("hook_score", "replay_score", "magnetism_score", "emotion_score", "cultural_fit_score"):
            label = dim.replace("_score", "").replace("_", " ").title()
            print(f"  {viral[dim]['score']:>2}/10  {label}: {viral[dim]['justification']}")
        if viral["verdict"] == "fail" and viral.get("improvement_notes"):
            print(f"\n📋 Rejection summary saved. See *_viral_rejection_summary.txt in output folder.")

    print("\n✅ Files saved to the 'output' directory.")
    try:
        choice = input("\nGenerate music using Lyria 3 Pro now? (y/n): ").strip().lower()
        if choice == "y":
            generate_audio_from_notes(result["safe_topic"], result["producer_notes"])
    except Exception as e:
        print(f"\n❌ Error during audio generation: {e}")


def main():
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        print("\n\nExiting application...")
    except EOFError:
        print("\n\nError: EOF received. Please run this in an interactive terminal.")
    except Exception as e:
        print(f"\nFatal Error: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
