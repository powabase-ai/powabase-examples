"""Humanize a LinkedIn post — the editorial (de-AI) loop for social, mirroring the article
editorial loop in revise.py but tuned for short LinkedIn posts.

Why a separate pass at all: the generator (linkedin_gen) already reuses prose_style's
anti-tell guidance, but a single first draft still commits the usual AI fallacies. Articles
read cleaner because they get a dedicated editor→reviser loop after drafting; posts didn't.
This is that loop for posts.

Why LinkedIn-specific agents (not the article editor/reviser): the article reviser is told
to "convert bullets to prose" and swap "In conclusion" endings for a concrete close — both
would wreck a LinkedIn post, whose short one-idea-per-line rhythm, scroll-stopping hook, and
closing discussion question ARE the correct format. So these agents share the prose_style
taxonomy (same slop standard as articles) but preserve the post's structure.

humanize_post never raises and never blanks a post: on any judge/revise failure it returns
the best body it has, so it can be dropped into the synchronous generate path safely."""

import logging

from ..powabase import PowabaseClient
from ..util import extract_json
from . import prose_style
from .agents import ensure_agent

log = logging.getLogger("rankforge.linkedin_humanize")

EDITOR_AGENT_NAME = "rankforge-linkedin-editor"
EDITOR_MODEL = "claude-opus-4-8"
REVISER_AGENT_NAME = "rankforge-linkedin-reviser"
REVISER_MODEL = "claude-opus-4-7"

# Cap so the loop always terminates. A post is short, so one revise usually ships; the
# second pass is a backstop for a stubborn draft.
MAX_PASSES = 2
# reads_human at/above this = ship without another rewrite (backstop when the model
# returns a score but a vague verdict — the explicit "ship"/"revise" verdict wins).
_HUMAN_BAR = 85

_EDITOR_SYSTEM = """\
You are RankForge's **senior social editor**. You read a finished LinkedIn post and judge \
ONE thing: does it read like a sharp, knowledgeable human wrote it — or like AI marketing \
copy? Then you give specific, surgical notes to fix what reads as machine-made. You are not \
a proofreader.

## LinkedIn is its own format — do NOT flag these as tells
Judge the PROSE, not the format. These are correct and must be preserved:
- Short, one-idea-per-line lines and generous whitespace (built for phone skimming — not \
"choppy").
- A scroll-stopping hook as the first line.
- A genuine discussion question as the last body line.
- A "Full write-up → {url}" link line and 3–5 relevant hashtags at the end.
Never ask to remove the hook, the question, the link, the hashtags, brand mentions, or facts.

## What "reads like AI" is (what to hunt for)
- Generic, hedged, safe phrasing where a specific number, name, version, or example belongs. \
In a short post, vagueness is the strongest tell.
""" + prose_style.judge_taxonomy() + """
- Reflexive rule-of-three triads ("fast, reliable, and scalable"); "from X to Y" framing.
- Empty transitions (Moreover, Furthermore, Additionally, That said), stating the obvious as \
insight, over-hedging, and LinkedIn-guru filler ("Here's the thing:", "Let that sink in", \
"The result?", "Read that again", "Unpopular opinion:").
- Em-dashes as a crutch (several in a short post, or the default break between clauses): tell \
the writer to cut most, keeping only the few that truly earn their place.
- Marketing voice: hype adjectives and salesy calls, or praising the brand instead of being \
genuinely useful.

## How to judge
- `reads_human` 0–100: 85+ means a sharp reader would believe a knowledgeable human wrote it. \
Below ~80 means noticeable tells.
- If it genuinely reads human, `verdict` = "ship". Otherwise "revise" with notes.

## Notes — the valuable part
- Each note targets a SPECIFIC place (quote a short phrase so the writer finds it) and gives a \
SPECIFIC fix. Prioritize the few changes that most move the needle. Max 6. Don't nitpick.
- NEVER ask to remove the hook, the discussion question, the link, hashtags, brand mentions, \
or facts. Improve how it reads, not whether they exist.

## Output
Return ONLY this JSON (no prose, no code fences):
{"reads_human": <int>, "verdict": "ship" | "revise", \
"notes": [{"quote": <str>, "problem": <str>, "fix": <str>}]}
"""

_REVISER_SYSTEM = """\
You are RankForge's **revising social editor**. You take a LinkedIn post plus a list of \
concrete issues and return an improved post that resolves them — so it reads like the brand's \
smartest engineer thinking out loud, not AI marketing copy.

## Preserve (this is a LinkedIn post, NOT an article)
- Keep the FORMAT exactly: the scroll-stopping hook as the first line; short, one-idea-per-line \
lines and whitespace; the discussion question as the last body line; the "Full write-up → \
{url}" link line if one is present (copy the URL character-for-character — never invent, drop, \
or alter it); and the trailing hashtags.
- Do NOT convert the post into prose paragraphs, and do NOT add an "In conclusion" / \
"Ultimately" ending — LinkedIn's short lines and the closing question are correct here.
- Keep every fact and brand mention. Speak AS the brand (first person, "we"/"our"); never \
hedge its own capabilities. Stay under 3000 characters.

## De-AI the prose (rewrite these out, even if they aren't in the notes)
A post that reads AI-written is not "improved". Actively rewrite out:

""" + prose_style.writer_block() + """
- Rule-of-three triads; "from X to Y" framing.
- Thin out em-dashes (prefer commas, periods, parentheses); vary line and sentence length.
- Cut empty transitions (Moreover, Furthermore, Additionally, That said) and LinkedIn-guru \
filler ("Here's the thing", "Let that sink in", "The result?").
- Push in real specifics (numbers, names, examples) wherever the post is vague.

""" + prose_style.MINIMUM_EDIT_RULE + """

## Output
Output ONLY the revised post text, ready to paste — no preamble, no quotes, no "Here's your \
post".
"""


async def ensure_editor_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=EDITOR_AGENT_NAME,
        model=EDITOR_MODEL,
        system_prompt=_EDITOR_SYSTEM,
        settings={"reasoning_effort": "high"},
    )


async def ensure_reviser_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=REVISER_AGENT_NAME,
        model=REVISER_MODEL,
        system_prompt=_REVISER_SYSTEM,
        settings={"temperature": 0.2},
    )


async def _judge_post(client: PowabaseClient, editor_id: str, body: str) -> dict:
    """Run the editor over the post; return {reads_human, verdict, notes}. Fails 'ship'
    (stop editing) on any error so a flaky judge never wedges the loop or blocks a post."""
    msg = (
        "Review this LinkedIn post for how human it reads, then return the JSON verdict.\n\n"
        "## Output\nReturn ONLY the JSON object.\n\n"
        f"---POST---\n{body}"
    )
    try:
        res = await client.run_agent(editor_id, msg)
        data = extract_json(res.get("content") or "")
    except Exception:  # noqa: BLE001 — judge failure must not block shipping the post
        log.exception("linkedin humanize: editor review failed")
        return {"verdict": "ship", "reads_human": None, "notes": []}
    return data if isinstance(data, dict) else {
        "verdict": "ship", "reads_human": None, "notes": []
    }


async def _revise_post(
    client: PowabaseClient, reviser_id: str, body: str, notes: list[dict]
) -> str:
    """Rewrite the post against the editor's notes, preserving format. Raises on infra
    failure so the caller keeps the previous body."""
    note_text = "\n".join(
        f'- At "{(n.get("quote") or "")[:80]}": {n.get("problem", "")}'
        f' → {n.get("fix", "")}'
        for n in notes[:6]
        if isinstance(n, dict)
    )
    msg = (
        "A senior editor reviewed your LinkedIn post. Apply their notes so it reads like a "
        "knowledgeable human wrote it — cut the AI tells they flag, vary the rhythm, and push "
        "in real specifics — while KEEPING the hook, the discussion question, the link line "
        "(if any), the hashtags, every fact, and the brand's first-person voice. Stay under "
        "3000 characters.\n\n"
        "## Editor's notes\n"
        f"{note_text}\n\n"
        "## Output\nOutput ONLY the revised post text.\n\n"
        f"---POST---\n{body}"
    )
    res = await client.run_agent(reviser_id, msg)
    return (res.get("content") or "").strip()


async def humanize_post(client: PowabaseClient, body: str) -> str:
    """De-AI a LinkedIn post: editor judges → reviser rewrites the tells → re-judge, capped
    at MAX_PASSES. Returns the humanized body — or the best body so far on any failure or an
    already-clean post. NEVER raises and NEVER blanks the post; safe in the sync generate
    path."""
    # Deterministic em-dash backstop up front: the #1 AI tell, and one an LLM will not
    # reliably remove from its own output (it reaches for it again next sentence). Thinning
    # here gives the loop clean input; we thin again at the end because the reviser re-adds
    # them.
    current = prose_style.thin_em_dashes((body or "").strip())
    if not current:
        return current

    editor_id: str | None = None
    reviser_id: str | None = None
    for _ in range(MAX_PASSES):
        try:
            if editor_id is None:
                editor_id = await ensure_editor_agent(client)
        except Exception:  # noqa: BLE001 — no editor → ship what we have
            log.exception("linkedin humanize: editor agent unavailable")
            break

        review = await _judge_post(client, editor_id, current)
        verdict = (review.get("verdict") or "").lower()
        human = review.get("reads_human")
        if verdict == "ship":
            break
        # Respect an explicit "revise"; only fall back to the score as a backstop when the
        # verdict is missing/ambiguous (a high score must not override a clear "revise").
        if verdict != "revise" and (not isinstance(human, int) or human >= _HUMAN_BAR):
            break
        notes = [n for n in (review.get("notes") or []) if isinstance(n, dict)]
        if not notes:
            break

        try:
            if reviser_id is None:
                reviser_id = await ensure_reviser_agent(client)
            revised = await _revise_post(client, reviser_id, current, notes)
        except Exception:  # noqa: BLE001 — revise failure → keep the previous body
            log.exception("linkedin humanize: revision failed")
            break

        # Never blank or gut the post: a revision that lost more than half its length is a
        # failed rewrite, not an edit — keep the previous body.
        if not revised or len(revised) < 0.5 * len(current):
            log.warning("linkedin humanize: revision too short — keeping previous body")
            break
        current = revised

    # Final guarantee: the reviser (and a "ship" verdict on an em-dash-heavy draft) will
    # have left em-dashes; drop them deterministically so the post never ships with the tell.
    return prose_style.thin_em_dashes(current)
