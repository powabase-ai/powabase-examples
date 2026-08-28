// Validation for the one field on the Settings form that spends money.
//
// The DATABASE is the enforcement point: `brands_research_daily_cap_range`
// (db/migrations/0014_research_cap_bound.sql) rejects anything outside 0-100
// no matter who sends it, because this form is one `fetch` away from being
// bypassed and was, in fact, bypassed -- a signed-up account PATCHed its own
// brand to 100000 while this file said "0 or greater". What lives here is the
// courtesy half: tell the user before the round trip, and keep the bound the
// form shows identical to the bound the constraint enforces.
export const RESEARCH_CAP_MIN = 0;
export const RESEARCH_CAP_MAX = 100;

export type CapValidation = { value: number; error: null } | { value: null; error: string };

// `Number.parseInt` is deliberately not used: it reads '12abc' as 12 and
// '1e3' as 1, so a value the user can see in the box would be saved as a
// different number. An all-digits test with an optional sign is the only
// reading where what is stored is what was typed.
export function validateResearchCap(raw: string): CapValidation {
  const t = raw.trim();
  if (t === '') return { value: null, error: 'Required.' };
  if (!/^[+-]?\d+$/.test(t)) {
    return { value: null, error: `Must be a whole number between ${RESEARCH_CAP_MIN} and ${RESEARCH_CAP_MAX}.` };
  }
  const n = Number(t);
  if (n < RESEARCH_CAP_MIN) {
    return { value: null, error: `Can't be negative. Use ${RESEARCH_CAP_MIN} to pause research for this brand.` };
  }
  if (n > RESEARCH_CAP_MAX) {
    // Names the ceiling AND why there is one -- "invalid" would leave the user
    // guessing at a number, and the reason is the part that is worth knowing.
    return {
      value: null,
      error: `${RESEARCH_CAP_MAX} is the most this project allows. Every run spends the project owner's platform credits.`,
    };
  }
  return { value: n, error: null };
}
