"""Prompt registry: each system prompt and the user template lives in its own .md file
next to this module. Loaded once at import time."""
from pathlib import Path

_HERE = Path(__file__).parent

def _load(name: str) -> str:
    return (_HERE / name).read_text(encoding="utf-8").strip()

DRAFT_SYSTEM = _load("draft_system.md")
FACTCHECK_SYSTEM = _load("factcheck_system.md")
POLISH_SYSTEM = _load("polish_system.md")
PRODUCER_SCRIPT_SYSTEM = _load("producer_script_system.md")
VERIFIER_SYSTEM = _load("verifier_system.md")
VIRAL_CRITIC_SYSTEM = _load("viral_critic_system.md")
LYRIA_SYSTEM = _load("lyria_system.md")
USER_TEMPLATE = _load("user_template.md")
