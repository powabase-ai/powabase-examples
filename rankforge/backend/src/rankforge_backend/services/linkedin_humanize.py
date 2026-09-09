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
import re

from ..powabase import PowabaseClient, PowabaseError
from ..util import extract_json
from . import prose_style
from .agents import ensure_agent

log = logging.getLogger("rankforge.linkedin_humanize")

_URL_RE = re.compile(r"https?://\S+")
_HASHTAG_RE = re.compile(r"#\w+")
# Strip a chatty preamble the reviser sometimes prepends ("Here's your revised post:") —
# it would otherwise become the post's first line, the single most load-bearing one.
_PREAMBLE_RE = re.compile(
    r"^\s*(?:here(?:'s| is)[^\n:]*:|sure[,!]?|revised post:?)\s*\n+", re.IGNORECASE
)
# Agent stop reasons meaning the output was cut off by the token ceiling (name varies).
_TRUNCATED_STOP_REASONS = {"max_tokens", "length", "max_output_tokens"}


def _score(v: object) -> int | None:
    """Coerce a model-emitted reads_human to an int in 0–100, or None if unusable. Rejects
    bool (an int subclass — `true` must not read as 1) and out-of-range/float-ish values so
    a vague verdict can never ship a genuinely low score unrevised."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        s = v
    elif isinstance(v, float) and v.is_integer():
        s = int(v)
    elif isinstance(v, str) and v.strip().lstrip("-").isdigit():
        s = int(v)
    else:
        return None
    return s if 0 <= s <= 100 else None


def _preserves_structure(before: str, after: str) -> bool:
    """A humanize rewrite must not silently drop the post's load-bearing bits: the article
    URL (the reviser is told to copy it character-for-character) or the hashtags. The 50%
    length guard can't catch a ~60-char loss in a ~1500-char post, so check explicitly."""
    before_urls = set(_URL_RE.findall(before))
    if before_urls and not before_urls <= set(_URL_RE.findall(after)):
        return False  # a dropped or altered URL is a regression
    return not (_HASHTAG_RE.search(before) and not _HASHTAG_RE.search(after))

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
- When the article is published, a "Full write-up → {url}" link line, plus 3–5 relevant \
hashtags at the end. An unpublished article has NO link line — that's correct; never demand one.
Never ask to remove the hook, the question, the link, the hashtags, brand mentions, or facts.

## What "reads like AI" is (what to hunt for)
- Generic, hedged, safe phrasing where a specific number, name, version, or example belongs. \
In a short post, vagueness is the strongest tell.
""" + prose_style.judge_taxonomy() + """
- Reflexive rule-of-three triads ("fast, reliable, and scalable"); "from X to Y" framing.
- Empty transitions (Moreover, Furthermore, Additionally, That said), stating the obvious as \
insight, over-hedging, and LinkedIn-guru filler ("Here's the thing:", "Let that sink in", \
"The result?", "Read that again", "Unpopular opinion:").
- Marketing voice: hype adjectives and salesy calls, or praising the brand instead of being \
genuinely useful.
(Note: every em-dash is stripped deterministically AFTER you review, so don't spend a note \
slot on them — judge the words, not the dashes.)

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
- Vary line and sentence length. (Every em-dash you write is stripped afterward, so do NOT \
build a sentence around one — it will read broken. Use commas, periods, or parentheses.)
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


# Output ceiling for the rewrite — same headroom the generator uses (a <=3000-char post
# is ~750-1000 tokens). Without it a rewrite can be silently truncated, come back short,
# clear the length guard, and replace a good post with a beheaded one.
_REVISER_MAX_TOKENS = 1600


async def ensure_reviser_agent(client: PowabaseClient) -> str:
    return await ensure_agent(
        client,
        name=REVISER_AGENT_NAME,
        model=REVISER_MODEL,
        system_prompt=_REVISER_SYSTEM,
        settings={"temperature": 0.2, "max_tokens": _REVISER_MAX_TOKENS},
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
    except PowabaseError as e:  # routine upstream (429/503/402) — not a stack-trace event
        log.warning("linkedin humanize: editor review upstream error (%s)", e)
        return {"verdict": "ship", "reads_human": None, "notes": []}
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
    stop = res.get("stop_reason") or res.get("finish_reason")
    if isinstance(stop, str) and stop.lower() in _TRUNCATED_STOP_REASONS:
        # A truncated rewrite lost its tail (question / link / hashtags). Fail so the caller
        # keeps the previous body rather than saving a beheaded post.
        raise RuntimeError("reviser output truncated at the token ceiling")
    out = (res.get("content") or "").strip()
    return _PREAMBLE_RE.sub("", out).strip()


async def humanize_post(client: PowabaseClient, body: str, *, label: str = "") -> str:
    """De-AI a LinkedIn post: editor judges → reviser rewrites the tells → re-judge, capped
    at MAX_PASSES (the last revision ships unjudged — the cap trades a final check for a
    bounded call count). Returns the humanized body — or the best body so far on any failure
    or an already-clean post. NEVER raises and NEVER blanks the post; safe in the sync
    generate path. `label` (an article/post id) is only for attributable log lines."""
    # Deterministic em-dash backstop up front: the #1 AI tell, and one an LLM will not
    # reliably remove from its own output (it reaches for it again next sentence). Thinning
    # here gives the loop clean input; we thin again at the end because the reviser re-adds
    # them.
    current = prose_style.thin_em_dashes((body or "").strip())
    if not current:
        return current

    tag = f" [{label}]" if label else ""
    original = current
    editor_id: str | None = None
    reviser_id: str | None = None
    for i in range(MAX_PASSES):
        # One try wraps the WHOLE pass (agent provisioning, judge, and revise): three call
        # sites depend on this never raising, and the values come straight from model JSON.
        try:
            if editor_id is None:
                editor_id = await ensure_editor_agent(client)
            review = await _judge_post(client, editor_id, current)

            verdict = review.get("verdict")
            verdict = verdict.lower() if isinstance(verdict, str) else ""
            human = _score(review.get("reads_human"))
            if verdict == "ship":
                break
            # Respect an explicit "revise"; only fall back to the score as a backstop when
            # the verdict is missing/ambiguous (a high score must not override "revise").
            if verdict != "revise" and (human is None or human >= _HUMAN_BAR):
                break
            notes = [n for n in (review.get("notes") or []) if isinstance(n, dict)]
            if not notes:
                break

            if reviser_id is None:
                reviser_id = await ensure_reviser_agent(client)
            revised = await _revise_post(client, reviser_id, current, notes)

            # A revision that lost more than half its length, or dropped the URL/hashtags,
            # is a failed rewrite, not an edit — keep the previous body.
            if not revised or len(revised) < 0.5 * len(current):
                log.warning(
                    "linkedin humanize%s: revision too short (pass %d, reads_human=%s) — "
                    "keeping previous", tag, i + 1, human,
                )
                break
            if not _preserves_structure(current, revised):
                log.warning(
                    "linkedin humanize%s: revision dropped the URL or hashtags (pass %d) — "
                    "keeping previous", tag, i + 1,
                )
                break
            current = revised
        except PowabaseError as e:  # noqa: BLE001 — routine upstream error, not a bug
            log.warning(
                "linkedin humanize%s: pass %d upstream error (%s) — shipping best body",
                tag, i + 1, e,
            )
            break
        except Exception:  # noqa: BLE001 — humanize must never raise or blank a post
            log.exception(
                "linkedin humanize%s: pass %d failed — shipping best body so far", tag, i + 1
            )
            break

    # Final guarantee: the reviser (and a "ship" verdict on an em-dash-heavy draft) leaves
    # em-dashes; drop them deterministically so the post never ships with the tell.
    final = prose_style.thin_em_dashes(current)
    log.info("linkedin humanize%s: done (changed=%s)", tag, final != original)
    return final
