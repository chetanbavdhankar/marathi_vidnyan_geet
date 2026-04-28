# Vingyan Gaani — Marathi Science Song Generator

Generates an entertaining Marathi song that explains a scientific concept, plus a Suno/Lyria-ready producer script and (optionally) a rendered `.wav` via Google's Lyria 3 Pro API.

## Pipeline

Six sequential agents share one LiteLLM-routed model (any provider Gemini/OpenAI/Anthropic/Ollama/etc.):

1. **Drafter** — produces the first cut of lyrics and a list of atomic scientific claims.
2. **Fact-checker** — fetches a Wikipedia summary for the topic, verifies each claim against it, surfaces common student misconceptions. Emits a structured report (`FactCheckReport`).
3. **Polisher** — rewrites the lyrics, acting on every "incorrect" verdict and addressing the misconceptions.
4. **Producer-script writer** — emits the meta-tagged Suno/Lyria script and a parallel structured `MixSpec` JSON.
5. **Comprehension verifier** — at the user-specified grade level, generates three questions, attempts to answer each from the lyrics alone, and reports gaps. If the verdict isn't `pass`, the polish + producer-script + verifier stages run **once more** with the gap report attached.
6. **Viral Potential Critic** — a ruthless hit-maker assessment scoring the song on five dimensions (hook stickiness, replay value, audience magnetism, emotional resonance, cultural fit). If the song doesn't pass the viral gate (avg ≥ 7.0, no dimension below 5), a detailed rejection summary with reasons and actionable alternatives is written to the output folder.

All structured outputs use the [`instructor`](https://python.useinstructor.com/) library so each Pydantic model is enforced model-side; the previous string-heuristic JSON parser is gone.

## Layout

```
main.py                  Pipeline + Lyria audio call. CLI entry point.
app.py                   FastAPI surface. Web entry point.
models.py                Pydantic schemas only.
genres.py                Reference table for Marathi genres (taal, instruments, vocal style).
prompts/                 One .md file per system prompt + the user template.
static/index.html        UI assets.
output/<topic>_<genre>/   Per-topic generated artifacts (lyrics, notes, mix spec, verifier report, viral report, audio).
                         On viral critic fail, also contains *_viral_rejection_summary.txt.
                         Folder name = sanitized topic + genre suffix (e.g. black_hole_rap, photosynthesis_lavani).
                         Falls back to topic-only when no genre is specified.
tests/                   pytest suite.
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env  # if present, otherwise create one yourself
echo "GOOGLE_API_KEY=your_key_here" >> .env
# Optional:
echo "DEFAULT_MODEL=gemini/gemini-3.1-pro-preview" >> .env
```

`.env` lives next to `main.py`. (The previous version looked one directory up — fixed.)

## CLI

```bash
python main.py
```

You'll be prompted for topic, region, genre, instruments, target grade level, and infra (Ollama or API). After the pipeline finishes, you'll be asked whether to also synthesize the audio with Lyria 3 Pro.

## Web

```bash
python app.py
# visit http://127.0.0.1:8000
```

Status events stream over NDJSON in real time (the previous version buffered them — fixed). The Lyria step is a separate button so you can iterate on lyrics without paying the audio render every time.

## Endpoints

- `POST /api/generate` → NDJSON stream of `{status: ...}` events ending in `{result: {...}}`
- `POST /api/generate-audio` → renders the .wav, returns `{url, safe_topic}`. Body takes `topic` and `genre` (server re-derives the safe folder name — clients cannot point writes at arbitrary directories).
- `GET /` → `static/index.html`
- `GET /static/*` → UI assets
- `GET /output/<folder>/*` → generated artifacts

The project root is **not** mounted statically; source files (including `.env`) are not served.

## Tests

```bash
python -m pytest tests/
```

Two suites:
- `test_safe_topic.py` — path-traversal cases against the filename sanitizer.
- `test_pipeline_shape.py` — golden-shape test of yielded events + on-disk artifacts, with the LLM and Wikipedia call mocked. Also verifies the verifier's one-retry loop.

## Adding a genre

Add an entry to `GENRE_GUIDE` in `genres.py`. The drafter automatically receives the taal/instrumentation/vocal notes for any genre present in the table.

## Recent Changes

### Agent 6: Viral Potential Critic — final hit-potential gate (2026-04-28)
- **Why:** Songs were passing scientific accuracy and comprehension checks but lacked the intangible "would I listen again?" quality. A song that's correct but forgettable defeats the purpose of using music as a teaching medium.
- **How:** Added a 6th agent (`ViralPotentialReport` model, `viral_critic_system.md` prompt) that scores five dimensions 1–10: hook stickiness, replay value, audience magnetism, emotional resonance, and cultural fit. Pass requires avg ≥ 7.0 with no dimension below 5. On fail, a human-readable `*_viral_rejection_summary.txt` is written to the output folder containing specific weaknesses, actionable rewrites, and reference examples from hit songs.
- **Impact:** Pipeline is now 6 agents. One additional LLM call per generation (~3-5s). No breaking changes to existing artifacts — the viral report is an additive output.

### Output folder naming: `{topic}_{genre}` convention (2026-04-27)
- **Why:** Previously all output folders were named by topic alone (e.g. `black_hole`). Multiple songs on the same topic with different genres would collide.
- **How:** Introduced `safe_folder_name(topic, genre)` that appends the sanitized genre as a suffix (e.g. `black_hole_rap`, `photosynthesis_lavani`). When no genre is provided, falls back to topic-only. All paths — pipeline, audio generation, API, and frontend — now use the combined name.
- **Impact:** No migration needed; existing folders remain untouched. New generations will use the suffixed naming.

### Fix: Qwen 3.5 / thinking-model empty-content crash (2026-04-23)
- **Why:** Qwen 3.5 (and similar "thinking" models like QwQ, DeepSeek-R1) route all reasoning to an internal `reasoning_tokens` field, leaving `content` empty. The previous `/no_think` prompt-suffix was a soft switch that ≤4B models ignored, causing `instructor` to fail with `Invalid JSON: EOF at column 0`.
- **How:** Added `extra_body={"think": False}` for all Ollama calls, which is the authoritative API-level toggle. Also set `num_ctx=8192` to give small models enough headroom for the JSON schema + full output. The `/no_think` suffix is kept as a belt-and-suspenders fallback.
- **Impact:** Ollama thinking models now produce valid JSON output. Recommended minimum: **7B+** for best results (4B models may still produce low-quality lyrics due to limited capacity).

### Fix: Schema validation failures with small Ollama models (2026-04-23)
- **Why:** qwen2.5:7b outputs `"energy": "High"` (capitalized) and `"bpm": 90` (int), failing strict Pydantic `Literal` and `str` validation. Small models don't perfectly conform to JSON schemas.
- **How:** Added `field_validator` coercions to `SongSection` — case-normalizing `energy` and coercing `bpm` int→str before validation.
- **Impact:** Pipeline now tolerates common small-model deviations. Zero impact on API models that already output correct types.

## Changing the audio backend

`producer_notes` is the text script consumed by Lyria 3 Pro today. The parallel `mix_spec.json` is the structured form — point a different audio backend at it without retouching prompts.
