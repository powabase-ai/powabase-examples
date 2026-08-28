// STRING() IS NOT TOTAL, which is the hole this closes. It was the defence the
// research panel claimed for every rendered field, and it throws on the one
// input class that panel exists to survive: `String(x)` runs ToPrimitive, so an
// object carrying a non-function `toString` -- `{"toString": "x"}` -- raises
// `TypeError: Cannot convert object to primitive value` instead of returning
// text. (Same for a `valueOf` that is not callable, and for an object created
// with `Object.create(null)`, which has no `toString` at all.)
//
// It was reachable: complete_research_job validated `summary` with
// `_payload->>'summary'`, and `->>` on a JSON object returns that object's text,
// which is not empty -- so `{"summary":{"toString":"x"},"fit":[]}` was accepted,
// stored, and threw at render. 0012 now requires a string summary, but the rows
// written before that check exists are still in the table, and no server-side
// check covers a hook's `hook` or `evidence`. Layer two, again.
//
// IT LIVES IN `lib/` RATHER THAN IN THE PANEL, and that moved here in review
// (round 3) for two reasons. The panel is not the only renderer of
// agent-authored jsonb: lead/Timeline.tsx interpolates `events.properties`,
// which complete_research_job writes from the same payload, and it was still
// doing that through a bare `String()`. And a file under `lead/` importing from
// `research/` is the wrong direction for a leaf helper -- both features consume
// it, neither owns it.
export function asText(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  try {
    return String(v);
  } catch {
    // Deliberately not '': an unreadable value is a malformed report, and a
    // blank line reads as "the agent had nothing to say" instead.
    return '(unreadable value)';
  }
}
