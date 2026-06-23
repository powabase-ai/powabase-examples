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
