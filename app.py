"""FastAPI surface for Vingyan Gaani.

Two endpoints:
  POST /api/generate        → NDJSON stream of agent status events + final result
  POST /api/generate-audio  → kicks Lyria 3 Pro, returns the URL of the rendered .wav

Static layout:
  /                         → static/index.html
  /static/*                 → static/ (UI assets only — never the source tree)
  /output/<safe_topic>/*    → generated artifacts (lyrics, notes, mix spec, audio)
"""
import os
import json
import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
import uvicorn

from main import generate_song, generate_audio_from_notes, safe_folder_name, DEFAULT_MODEL, OUTPUT_ROOT
from models import UserInput

ROOT = Path(__file__).parent
STATIC_DIR = ROOT / "static"

app = FastAPI(title="Vingyan Gaani API")


class GenerateRequest(BaseModel):
    topic: str
    region: str = "Maharashtra"
    genre: str = ""
    instruments: str = ""
    additional_info: str = ""
    grade_level: str = "general"
    reference_style: str = ""
    model: str = DEFAULT_MODEL


@app.post("/api/generate")
async def api_generate(req: GenerateRequest):
    """NDJSON stream. Each `await` inside the generator yields control to the event
    loop, which lets each chunk flush to the client before the next agent call starts.
    The previous sync-generator implementation buffered the entire response."""
    async def event_stream():
        try:
            user_input = UserInput(
                topic=req.topic,
                region=req.region,
                genre=req.genre or None,
                instruments=req.instruments or None,
                grade_level=req.grade_level or None,
                reference_style=req.reference_style or None,
                additional_info=req.additional_info or None,
            )
            async for update in generate_song(user_input, req.model):
                yield json.dumps(update, ensure_ascii=False) + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


class AudioRequest(BaseModel):
    # `topic` (not `safe_topic`) — server re-derives the safe form so the client
    # cannot point writes/reads at arbitrary directories.
    topic: str
    genre: str = ""
    producer_notes: str


@app.post("/api/generate-audio")
async def api_generate_audio(req: AudioRequest):
    safe = safe_folder_name(req.topic, req.genre or None)
    try:
        audio_path = await run_in_threadpool(generate_audio_from_notes, safe, req.producer_notes, req.genre or None)
        filename = os.path.basename(audio_path)
        return {"url": f"/output/{safe}/{filename}", "safe_topic": safe}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Ensure output dir exists before mount
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/output", StaticFiles(directory=str(OUTPUT_ROOT)), name="output")

# UI assets only — explicitly NOT the project root.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    print("Starting FastAPI server... Visit http://localhost:8000")
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
