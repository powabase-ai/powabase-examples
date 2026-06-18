"""Small shared helpers."""

import json
import re
from typing import Any


def extract_json(content: str) -> dict[str, Any]:
    """Pull a JSON object from an LLM's message (```json fenced or bare)."""
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in model output")
        raw = content[start : end + 1]
    return json.loads(raw)
