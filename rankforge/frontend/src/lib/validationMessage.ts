/** Readable text for a FastAPI/pydantic 422 `detail`: a list of `{loc, msg, type}`
 *  errors (e.g. a brand's `url_pattern` or a hub page failing its validator).
 *  Kept free of runtime imports so it can be unit-tested on its own. */

/** Where the request part came from — not useful to the user. */
const SOURCES = new Set(["body", "query", "path", "header", "cookie"]);

/** `["body", "blog_profile", "links", "hub_pages", 2, "path"]` →
 *  "blog_profile.links.hub_pages.2.path"; "" when there is nothing to show. */
function location(loc: unknown): string {
  if (!Array.isArray(loc)) return "";
  const parts = loc.filter((p) => typeof p === "string" || typeof p === "number");
  if (parts.length > 0 && SOURCES.has(String(parts[0]))) parts.shift();
  return parts.join(".");
}

/** Each error as "location: message" (pydantic's "Value error, " prefix dropped),
 *  joined with "; ". Null if `detail` isn't that shape, so the caller falls back
 *  to the raw body. */
export function validationMessage(detail: unknown): string | null {
  if (!Array.isArray(detail) || detail.length === 0) return null;
  const msgs: string[] = [];
  for (const e of detail) {
    const err = e as { msg?: unknown; loc?: unknown } | null;
    if (typeof err?.msg !== "string") return null;
    const msg = err.msg.replace(/^Value error, /, "");
    const where = location(err.loc);
    msgs.push(where ? `${where}: ${msg}` : msg);
  }
  return msgs.join("; ");
}
