"""Small shared helpers."""

import json
import re
from typing import Any


def extract_json(content: str) -> dict[str, Any]:
    """Pull a JSON object from an LLM's message (```json fenced or bare).

    Tries the fenced block first (greedy, so nested braces aren't truncated), then
    the outermost {...} span; returns the first that parses.
    """
    candidates: list[str] = []
    fenced = re.search(r"```json\s*(\{.*\})\s*```", content, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        candidates.append(content[start : end + 1])
    for raw in candidates:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    raise ValueError("no parseable JSON object found in model output")


_EM_DASH_RE = re.compile(r"\s*—\s*")  # — with any surrounding spaces


def strip_em_dashes(md: str) -> str:
    """Deterministically replace em-dashes (U+2014 —) with commas in prose.

    Em-dashes are the single most-flagged AI tell and the LLM reviser keeps leaving
    them in, so we remove them mechanically. An em-dash is grammatically a comma /
    parenthetical break, so a comma is safe in the overwhelming majority of cases —
    and always better than the tell. Leaves en-dashes (– ranges) and hyphens alone,
    and skips fenced code blocks + inline `code` spans so we never mangle code.
    """
    if "—" not in md:
        return md
    out: list[str] = []
    in_fence = False
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence or "—" not in line:
            out.append(line)
            continue
        # Split on backticks; even segments are outside inline code, odd are inside.
        parts = line.split("`")
        for k in range(0, len(parts), 2):
            if "—" in parts[k]:
                seg = _EM_DASH_RE.sub(", ", parts[k])
                # collapse a doubled comma (em-dash next to existing comma) incl. its
                # trailing space, so we don't leave ",  " behind
                parts[k] = re.sub(r",\s*,\s*", ", ", seg)
        out.append("`".join(parts))
    return "\n".join(out)
