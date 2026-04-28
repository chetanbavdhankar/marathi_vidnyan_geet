"""Reference table for Marathi musical genres. Lookups feed extra context into the
draft prompt so the LLM doesn't have to recall taal/instrumentation details from
parametric memory alone."""
from typing import Optional

GENRE_GUIDE: dict[str, dict] = {
    "powada": {
        "taal": "Kherwa or Dadra (8-beat cycles)",
        "meter": "Epic narrative meter; declamatory, building lines",
        "instruments": ["Dhol", "Tasha", "Sambal", "Tutari"],
        "vocal_style": "Declamatory, heroic, narrative — a single bard addressing a crowd",
        "notes": "Traditional Marathi ballad celebrating historical bravery. Each verse adds momentum to a building story.",
    },
    "lavani": {
        "taal": "Keherwa or Dhumali (8-beat)",
        "meter": "Flirtatious rhythmic couplets",
        "instruments": ["Dholki", "Tuntuni", "Ghungroo", "Harmonium"],
        "vocal_style": "Feminine, playful, expressive; call-and-response between lead and chorus",
        "notes": "Shringar rasa dominant. Tempo gradually accelerates across the song.",
    },
    "rap": {
        "taal": "Trap or boom-bap groove (4/4, ~85-95 BPM feel)",
        "meter": "Internal rhyme, triplet flows, end-of-bar punchlines",
        "instruments": ["808 sub-bass", "Hi-hats", "Snare", "Marathi percussion accents (dholki)"],
        "vocal_style": "Street Marathi with code-switching to English on technical terms",
        "notes": "Treat as Marathi hip-hop, not imported. Punchline density should rise into the chorus.",
    },
    "bhajan": {
        "taal": "Bhajani (7-beat) or Keherwa",
        "meter": "Devotional call-and-response refrain",
        "instruments": ["Harmonium", "Tabla", "Mridang", "Manjira"],
        "vocal_style": "Devotional, collective chant; lead with congregational response",
        "notes": "Reverent / meditative framing. Suits cosmology, biology of life, foundational physics.",
    },
    "abhang": {
        "taal": "Freeform meditative; loose pulse",
        "meter": "Short declarative spiritual verse",
        "instruments": ["Tal", "Mridanga", "Veena"],
        "vocal_style": "Contemplative, single-voice with restrained ornamentation",
        "notes": "Warkari tradition. Suited to philosophical concepts, conservation laws, symmetry.",
    },
    "koli": {
        "taal": "Keherwa with maritime swing",
        "meter": "Rolling, wave-like phrasing",
        "instruments": ["Dholki", "Conch", "Hand-claps", "Bansuri"],
        "vocal_style": "Communal, work-song energy; group chorus heavy",
        "notes": "Maharashtrian fisherfolk tradition. Natural fit for fluid dynamics, oceanography, ecology.",
    },
}


def lookup_genre(name: Optional[str]) -> Optional[dict]:
    if not name:
        return None
    return GENRE_GUIDE.get(name.strip().lower())


def format_genre_guide(guide: dict) -> str:
    lines = [
        f"- Taal: {guide['taal']}",
        f"- Meter: {guide['meter']}",
        f"- Typical instruments: {', '.join(guide['instruments'])}",
        f"- Vocal style: {guide['vocal_style']}",
        f"- Notes: {guide['notes']}",
    ]
    return "\n".join(lines)
