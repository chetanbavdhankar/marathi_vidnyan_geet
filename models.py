"""Pydantic schemas only. All prompt text lives in the `prompts/` package."""
from typing import Optional, Literal
from pydantic import BaseModel, Field, field_validator


# --- User input ---

class UserInput(BaseModel):
    topic: str = Field(description="The scientific topic to explain")
    region: str = Field(default="Maharashtra", description="The cultural region to draw inspiration from")
    genre: Optional[str] = Field(default=None, description="The musical genre")
    instruments: Optional[str] = Field(default=None, description="Preferred instruments to feature")
    additional_info: Optional[str] = Field(default=None, description="Any other specific constraints")
    grade_level: Optional[str] = Field(
        default=None,
        description="Target audience: elementary, middle school, high school, college, general, or 18+ (raw street slang / explicit mode)",
    )
    reference_style: Optional[str] = Field(
        default=None,
        description="Optional name of an artist or song to use as a style reference.",
    )


# --- Agent 1: Drafter ---

class DraftSong(BaseModel):
    title: str = Field(description="A catchy Marathi title for the song")
    lyrics: str = Field(description="The Marathi draft lyrics, with [Chorus]/[Verse] markers")
    claims: list[str] = Field(
        default_factory=list,
        description="Atomic scientific claims the lyrics make, one short sentence each",
    )


# --- Agent 2: Fact-Checker ---

class ClaimVerdict(BaseModel):
    claim: str
    verdict: Literal["correct", "incorrect", "unverified"]
    correction: Optional[str] = Field(
        default=None,
        description="When verdict is incorrect or unverified, what the lyricist should change",
    )


class FactCheckReport(BaseModel):
    verdicts: list[ClaimVerdict]
    misconceptions: list[str] = Field(
        default_factory=list,
        description="Common student misconceptions about this topic, each as a short sentence",
    )


# --- Agent 3: Polish ---

class PolishedLyrics(BaseModel):
    title: str = Field(description="Final polished Marathi title")
    lyrics: str = Field(description="Final polished Marathi lyrics with section markers")


# --- Agent 4: Producer Script ---

class SongSection(BaseModel):
    name: str = Field(description="e.g. 'intro', 'verse 1', 'chorus', 'verse 2', 'bridge', 'outro'")
    bpm: Optional[str] = Field(default=None, description="The Beats Per Minute for this section")
    mood: str
    energy: Literal["low", "medium", "high"]
    instrumentation: list[str]
    sfx: list[str] = Field(default_factory=list, description="Sound effects to be used in this section")
    vocal_delivery: str

    @field_validator("energy", mode="before")
    @classmethod
    def normalize_energy(cls, v: str) -> str:
        """Small models output 'High'/'Medium'/'Low' — Literal needs lowercase."""
        return v.lower() if isinstance(v, str) else v

    @field_validator("bpm", mode="before")
    @classmethod
    def coerce_bpm(cls, v):
        """Small models output bpm as int (90) instead of str ('90')."""
        return str(v) if v is not None and not isinstance(v, str) else v


class MixSpec(BaseModel):
    genre: str
    sections: list[SongSection]
    global_instrumentation: list[str]
    target_duration_seconds: int = Field(default=180)


class ProducerScript(BaseModel):
    producer_notes: str = Field(
        description="Full Suno/Lyria-ready meta-tagged script with all lyrics rewritten"
    )
    mix_spec: MixSpec
    audio_generator_style_prompt: str = Field(
        description="A highly detailed style prompt text (e.g. genre, vocal style, instruments, mood). You MUST also include explicit instructions: 'Pronounce all Marathi words correctly with authentic Marathi phonetics.'",
        default="",
    )


# --- Agent 5: Listener Comprehension Verifier ---

class ComprehensionCheck(BaseModel):
    question: str
    answer_from_lyrics: str
    answerable: bool
    gap: Optional[str] = None


class VerifierReport(BaseModel):
    questions: list[ComprehensionCheck]
    verdict: Literal["pass", "partial", "fail"]
    summary: str


# --- Agent 6: Viral Potential Critic ---

class ViralDimension(BaseModel):
    score: int = Field(ge=1, le=10, description="1-10 rating")
    justification: str = Field(description="2-3 sentence rationale")


class ViralPotentialReport(BaseModel):
    hook_score: ViralDimension
    replay_score: ViralDimension
    magnetism_score: ViralDimension
    emotion_score: ViralDimension
    cultural_fit_score: ViralDimension
    verdict: Literal["pass", "fail"]
    improvement_notes: Optional[str] = Field(
        default=None,
        description="Detailed analysis of weaknesses and actionable alternatives. Required when verdict is 'fail'.",
    )

    @property
    def average_score(self) -> float:
        scores = [self.hook_score.score, self.replay_score.score,
                  self.magnetism_score.score, self.emotion_score.score,
                  self.cultural_fit_score.score]
        return sum(scores) / len(scores)

    @property
    def min_score(self) -> int:
        return min(self.hook_score.score, self.replay_score.score,
                   self.magnetism_score.score, self.emotion_score.score,
                   self.cultural_fit_score.score)


# --- Final response surfaced to CLI / web ---

class SongResponse(BaseModel):
    title: str
    lyrics: str
    producer_notes: str
    mix_spec: Optional[MixSpec] = None
    verifier_report: Optional[VerifierReport] = None
    viral_report: Optional[ViralPotentialReport] = None
