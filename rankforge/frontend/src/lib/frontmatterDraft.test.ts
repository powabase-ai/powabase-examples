import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  adoptChanged,
  buildPatch,
  cleanFaq,
  faqEqual,
  fieldChanged,
  fromServer,
  isDirty,
  rebase,
  shouldAdoptSave,
  type FrontmatterDraft,
} from "./frontmatterDraft.ts";

const base: FrontmatterDraft = {
  category: "rag",
  summary: "A summary.",
  faq: [
    { q: "Q1?", a: "A1." },
    { q: "Q2?", a: "A2." },
  ],
  metaTitle: "Meta",
};
const clone = (d: FrontmatterDraft): FrontmatterDraft => ({
  ...d,
  faq: d.faq.map((f) => ({ ...f })),
});

describe("fromServer", () => {
  test("nulls become empty strings and FAQ rows plain {q, a}", () => {
    assert.deepEqual(fromServer({ category: null, summary: null, faq: null, meta_title: null }), {
      category: "",
      summary: "",
      faq: [],
      metaTitle: "",
    });
    assert.deepEqual(fromServer({}).faq, []);
  });

  test("jsonb's sorted {a, q} keys compare equal to {q, a}", () => {
    const server = fromServer({ faq: [{ a: "A1.", q: "Q1?" }] });
    assert.ok(faqEqual(server.faq, [{ q: "Q1?", a: "A1." }]));
  });
});

describe("FAQ comparison", () => {
  test("key order does not matter", () => {
    // Build {a, q} objects explicitly, as jsonb returns them.
    const sorted = [{ a: "A1.", q: "Q1?" }, { a: "A2.", q: "Q2?" }];
    assert.ok(faqEqual(sorted, base.faq));
  });

  test("whitespace-only edits are not changes", () => {
    const d = clone(base);
    d.faq[0].q = "  Q1?  ";
    d.faq.push({ q: "   ", a: "" });
    assert.ok(faqEqual(d.faq, base.faq));
    assert.equal(fieldChanged("faq", d, base), false);
  });

  test("a changed answer, a reorder or an extra row is a change", () => {
    const edited = clone(base);
    edited.faq[1].a = "Other.";
    assert.equal(faqEqual(edited.faq, base.faq), false);
    assert.equal(faqEqual([...base.faq].reverse(), base.faq), false);
    assert.equal(faqEqual([...base.faq, { q: "Q3?", a: "A3." }], base.faq), false);
  });

  test("cleanFaq trims, drops empty rows, keeps half-filled ones", () => {
    assert.deepEqual(
      cleanFaq([
        { q: " Q ", a: " A " },
        { q: " ", a: "" },
        { q: "half", a: "  " },
      ]),
      [
        { q: "Q", a: "A" },
        { q: "half", a: "" },
      ]
    );
  });
});

describe("dirty and PATCH", () => {
  test("an untouched draft is clean and patches nothing", () => {
    assert.equal(isDirty(clone(base), base), false);
    assert.deepEqual(buildPatch(clone(base), base), {});
  });

  test("text is compared trimmed", () => {
    const d = { ...clone(base), summary: " A summary. ", metaTitle: "Meta  " };
    assert.equal(isDirty(d, base), false);
  });

  test("only changed fields are sent; emptied fields are explicit nulls", () => {
    const d = { ...clone(base), summary: "  New.  ", metaTitle: "   ", category: "" };
    assert.ok(isDirty(d, base));
    assert.deepEqual(buildPatch(d, base), {
      category: null,
      summary: "New.",
      meta_title: null,
    });
  });

  test("FAQ patch is cleaned; an emptied FAQ is null", () => {
    const d = clone(base);
    d.faq = [{ q: " Q1? ", a: "A1." }, { q: "", a: "" }];
    assert.deepEqual(buildPatch(d, base), { faq: [{ q: "Q1?", a: "A1." }] });
    d.faq = [{ q: " ", a: " " }];
    assert.deepEqual(buildPatch(d, base), { faq: null });
  });
});

describe("rebase (a new server record)", () => {
  const server: FrontmatterDraft = {
    category: "seo",
    summary: "Server summary.",
    faq: [{ q: "SQ?", a: "SA." }],
    metaTitle: "Server meta",
  };

  test("an untouched draft resyncs every field", () => {
    const r = rebase(clone(base), base, server, false);
    assert.deepEqual(r.draft, server);
    assert.deepEqual(r.baseline, server);
  });

  for (const field of ["category", "summary", "faq", "metaTitle"] as const) {
    test(`an edited ${field} keeps the draft; the other fields resync`, () => {
      const d = clone(base);
      const edits: FrontmatterDraft = {
        category: "edited",
        summary: "Edited summary.",
        faq: [{ q: "EQ?", a: "EA." }],
        metaTitle: "Edited meta",
      };
      if (field === "faq") d.faq = edits.faq;
      else d[field] = edits[field];
      const r = rebase(d, base, server, false);
      assert.deepEqual(r.baseline, server);
      for (const f of ["category", "summary", "faq", "metaTitle"] as const) {
        assert.deepEqual(r.draft[f], f === field ? edits[f] : server[f], f);
      }
      // The edit is still pending against the new baseline; nothing else is.
      assert.deepEqual(Object.keys(buildPatch(r.draft, r.baseline)), [
        field === "metaTitle" ? "meta_title" : field,
      ]);
    });
  }

  test("a different article replaces the draft outright", () => {
    const d = { ...clone(base), summary: "Edited." };
    const r = rebase(d, base, server, true);
    assert.deepEqual(r.draft, server);
    assert.deepEqual(r.baseline, server);
  });
});

describe("save adoption", () => {
  test("only for the article still shown", () => {
    assert.equal(shouldAdoptSave("a1", "a1"), true);
    assert.equal(shouldAdoptSave("a1", "a2"), false);
  });
});

describe("adoptChanged (Generate summary & FAQ)", () => {
  const server = {
    category: "seo",
    summary: "Generated.",
    faq: [{ a: "GA.", q: "GQ?" }],
    meta_title: "Generated meta",
  };

  test("changed fields replace draft and baseline, even over an edit", () => {
    const d = { ...clone(base), summary: "My edit.", category: "mine" };
    const r = adoptChanged(d, base, server, ["summary", "faq"]);
    assert.equal(r.draft.summary, "Generated.");
    assert.equal(r.baseline.summary, "Generated.");
    assert.deepEqual(r.draft.faq, [{ q: "GQ?", a: "GA." }]);
    assert.deepEqual(r.baseline.faq, [{ q: "GQ?", a: "GA." }]);
    // Not in `changed`: the edit and the old baseline stay.
    assert.equal(r.draft.category, "mine");
    assert.equal(r.baseline.category, "rag");
    assert.deepEqual(buildPatch(r.draft, r.baseline), { category: "mine" });
  });

  test("meta_title maps to metaTitle; unknown names are ignored", () => {
    const r = adoptChanged(clone(base), base, server, [
      "meta_title",
      "meta_description",
      "content_md",
    ]);
    assert.equal(r.draft.metaTitle, "Generated meta");
    assert.equal(r.baseline.metaTitle, "Generated meta");
    assert.equal(r.draft.summary, base.summary);
    assert.equal(r.draft.category, base.category);
  });

  test("nothing changed leaves draft and baseline alone", () => {
    const d = { ...clone(base), summary: "My edit." };
    const r = adoptChanged(d, base, server, []);
    assert.deepEqual(r.draft, d);
    assert.deepEqual(r.baseline, base);
  });
});
