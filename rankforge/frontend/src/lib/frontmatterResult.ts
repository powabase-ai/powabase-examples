/** Toast wording for `POST /articles/{id}/frontmatter` ("Generate summary & FAQ"
 *  in the editor, "Fix automatically" in the publish dialog).
 *
 *  Worded only from what the server reports: `changed` (the fields actually
 *  written) and `flags` (what it declined or defaulted, e.g. "model's summary was
 *  3 words (needs 40-60) — kept the stored one"). A field is never named as
 *  updated unless it is in `changed`. Type-only imports keep this module free of
 *  runtime dependencies. */
import type { FrontmatterResult } from "@/lib/api";

export type FrontmatterOutcome = Pick<FrontmatterResult, "changed" | "flags" | "export_issues">;

export interface FrontmatterToast {
  kind: "success" | "warning" | "info";
  message: string;
}

const LABEL: Record<string, string> = {
  category: "category",
  summary: "summary",
  faq: "FAQ",
  meta_title: "meta title",
  meta_description: "meta description",
};

/** `content_md` only changes when the body's own FAQ section is stripped. */
const BODY_FAQ_REMOVED = "removed the FAQ section from the body";

function joinList(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function describeFrontmatterResult(
  result: FrontmatterOutcome,
  action: "generate" | "fix"
): FrontmatterToast {
  const { changed, flags, export_issues: issues } = result;
  const fields = changed
    .filter((f) => f !== "content_md")
    .map((f) => LABEL[f] ?? f.replace(/_/g, " "));
  const parts: string[] = [];
  if (fields.length) parts.push(`updated ${joinList(fields)}`);
  if (changed.includes("content_md")) parts.push(BODY_FAQ_REMOVED);
  if (!parts.length) parts.push("nothing changed");
  let message = capitalize([...parts, ...flags].join("; "));

  if (issues.length) {
    message += ` — ${issues.length} issue${issues.length === 1 ? " remains" : "s remain"}`;
  } else if (action === "fix" && changed.length && !flags.length) {
    message += " — try exporting again";
  }

  const kind: FrontmatterToast["kind"] =
    flags.length || issues.length ? "warning" : changed.length ? "success" : "info";
  return { kind, message };
}
