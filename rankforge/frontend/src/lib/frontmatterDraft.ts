/** Pure draft/baseline logic for the frontmatter editor (PostFrontmatterEditor).
 *
 *  The editor keeps two snapshots:
 *  - `baseline`: the last server values it adopted (initial load, a server change,
 *    or a save response);
 *  - `draft`: what the inputs currently show.
 *
 *  "Dirty" and the PATCH are both computed draft-vs-baseline, never draft-vs-the-
 *  current-server-record — otherwise a server-side change (generate, refine, revert,
 *  a poll) would make an untouched draft look edited and Save would write the stale
 *  values back. FAQ items are compared item by item on trimmed q/a: `articles.faq` is
 *  jsonb, which returns object keys sorted ({a, q}), so JSON.stringify comparisons
 *  never match. Type-only imports keep this module free of runtime dependencies. */
import type { ArticleUpdate, FaqItem } from "@/lib/api";

export interface FrontmatterDraft {
  category: string;
  summary: string;
  faq: FaqItem[];
  metaTitle: string;
}

export interface FrontmatterSource {
  category?: string | null;
  summary?: string | null;
  faq?: FaqItem[] | null;
  meta_title?: string | null;
}

/** Server record → editor snapshot (null → ""; FAQ items copied as plain {q, a}). */
export function fromServer(src: FrontmatterSource): FrontmatterDraft {
  return {
    category: src.category ?? "",
    summary: src.summary ?? "",
    faq: (src.faq ?? []).map((f) => ({ q: f.q ?? "", a: f.a ?? "" })),
    metaTitle: src.meta_title ?? "",
  };
}

/** Trim every row and drop fully empty ones. A half-filled row is kept (trimmed)
 *  so the server rejects it rather than the typed half being silently lost. */
export function cleanFaq(faq: FaqItem[]): FaqItem[] {
  return faq
    .map((f) => ({ q: f.q.trim(), a: f.a.trim() }))
    .filter((f) => f.q || f.a);
}

export function faqEqual(a: FaqItem[], b: FaqItem[]): boolean {
  const x = cleanFaq(a);
  const y = cleanFaq(b);
  return x.length === y.length && x.every((f, i) => f.q === y[i].q && f.a === y[i].a);
}

export type FrontmatterField = "category" | "summary" | "faq" | "metaTitle";

/** Is one field of `draft` different from the same field of `baseline`? Text is
 *  compared trimmed, as the server trims summary/meta title on save. */
export function fieldChanged(
  field: FrontmatterField,
  draft: FrontmatterDraft,
  baseline: FrontmatterDraft
): boolean {
  switch (field) {
    case "category":
      return draft.category !== baseline.category;
    case "summary":
      return draft.summary.trim() !== baseline.summary.trim();
    case "metaTitle":
      return draft.metaTitle.trim() !== baseline.metaTitle.trim();
    case "faq":
      return !faqEqual(draft.faq, baseline.faq);
  }
}

const FIELDS: FrontmatterField[] = ["category", "summary", "faq", "metaTitle"];

export function isDirty(draft: FrontmatterDraft, baseline: FrontmatterDraft): boolean {
  return FIELDS.some((f) => fieldChanged(f, draft, baseline));
}

/** PATCH body: only the fields that differ from the baseline. An omitted key is left
 *  alone server-side; an explicit null clears the field. */
export function buildPatch(
  draft: FrontmatterDraft,
  baseline: FrontmatterDraft
): ArticleUpdate {
  const payload: ArticleUpdate = {};
  if (fieldChanged("category", draft, baseline)) payload.category = draft.category || null;
  if (fieldChanged("summary", draft, baseline)) {
    payload.summary = draft.summary.trim() || null;
  }
  if (fieldChanged("faq", draft, baseline)) {
    const faq = cleanFaq(draft.faq);
    payload.faq = faq.length ? faq : null;
  }
  if (fieldChanged("metaTitle", draft, baseline)) {
    payload.meta_title = draft.metaTitle.trim() || null;
  }
  return payload;
}

/** A new server record arrived. Returns the draft to show and the new baseline.
 *
 *  - Different article (`idChanged`): adopt the server values outright.
 *  - Same article: rebase field by field — a field the user hasn't edited (draft
 *    equals the previous baseline) takes the server's new value; an edited field
 *    keeps the user's text. An untouched draft therefore resyncs completely, and an
 *    edit to one field never makes a server change to another field look like an
 *    edit (which Save would then revert).
 *
 *  The baseline always becomes the new server values. */
export function rebase(
  draft: FrontmatterDraft,
  prevBaseline: FrontmatterDraft,
  next: FrontmatterDraft,
  idChanged: boolean
): { draft: FrontmatterDraft; baseline: FrontmatterDraft } {
  if (idChanged) return { draft: next, baseline: next };
  const keep = (f: FrontmatterField) => fieldChanged(f, draft, prevBaseline);
  return {
    draft: {
      category: keep("category") ? draft.category : next.category,
      summary: keep("summary") ? draft.summary : next.summary,
      faq: keep("faq") ? draft.faq : next.faq,
      metaTitle: keep("metaTitle") ? draft.metaTitle : next.metaTitle,
    },
    baseline: next,
  };
}

/** Should a save response be adopted? Only while the editor still shows the article
 *  the save was for — after navigation the component may show another article. */
export function shouldAdoptSave(updatedId: string, currentId: string): boolean {
  return updatedId === currentId;
}

/** Server field names (`FrontmatterResult.changed`) → editor fields. Names the
 *  editor doesn't hold (meta_description, content_md) are ignored. */
const SERVER_FIELD: Record<string, FrontmatterField> = {
  category: "category",
  summary: "summary",
  faq: "faq",
  meta_title: "metaTitle",
};

/** "Generate summary & FAQ" succeeded: the fields the server actually wrote
 *  (`changed`) are an explicit request, so they replace the draft *and* the
 *  baseline even where the user had unsaved edits — otherwise the editor would
 *  keep showing the edit and Save would write it back over the generated value.
 *  Fields the server didn't write keep their draft and baseline. */
export function adoptChanged(
  draft: FrontmatterDraft,
  baseline: FrontmatterDraft,
  server: FrontmatterSource,
  changed: string[]
): { draft: FrontmatterDraft; baseline: FrontmatterDraft } {
  const next = fromServer(server);
  const d: FrontmatterDraft = { ...draft };
  const b: FrontmatterDraft = { ...baseline };
  for (const name of changed) {
    const f = SERVER_FIELD[name];
    if (!f) continue;
    switch (f) {
      case "faq":
        d.faq = next.faq;
        b.faq = next.faq;
        break;
      default:
        d[f] = next[f];
        b[f] = next[f];
    }
  }
  return { draft: d, baseline: b };
}
