import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  asBlogProfile,
  blogProfileState,
  DEFAULT_BLOG_PROFILE,
  mergeOntoDefault,
} from "./blogProfile.ts";

const cat = { key: "rag", label: "RAG", description: "", technical: true };
const hub = { path: "/rag", title: "RAG hub", topics: ["retrieval"] };
const full = {
  categories: [cat],
  summary: { enabled: true, min_words: 30, max_words: 50 },
  faq: { enabled: false, min: 2, max: 4 },
  meta: { title_max: 55, description_max: 150 },
  links: { min: 1, max: 3, trailing_slash: false, hub_pages: [hub] },
  stance: "favor_brand",
};

describe("asBlogProfile", () => {
  test("a complete valid profile is returned as-is", () => {
    assert.deepEqual(asBlogProfile(full), full);
  });

  test("missing sections take their defaults", () => {
    const p = asBlogProfile({ categories: [cat] });
    assert.ok(p);
    assert.deepEqual(p.summary, DEFAULT_BLOG_PROFILE.summary);
    assert.deepEqual(p.faq, DEFAULT_BLOG_PROFILE.faq);
    assert.deepEqual(p.meta, DEFAULT_BLOG_PROFILE.meta);
    assert.deepEqual(p.links, { min: 3, max: 5, trailing_slash: true, hub_pages: [] });
    assert.equal(p.stance, "neutral");
  });

  test("missing keys inside a section take that section's defaults", () => {
    const p = asBlogProfile({ categories: [cat], summary: { enabled: false } });
    assert.deepEqual(p?.summary, { enabled: false, min_words: 40, max_words: 60 });
    const q = asBlogProfile({ categories: [cat], links: { min: 1 } });
    assert.deepEqual(q?.links, { min: 1, max: 5, trailing_slash: true, hub_pages: [] });
  });

  test("defaults are copies, not the shared default object", () => {
    const p = asBlogProfile({ categories: [cat] });
    assert.notEqual(p?.summary, DEFAULT_BLOG_PROFILE.summary);
  });

  const invalid: [string, unknown][] = [
    ["null", null],
    ["an array", [full]],
    ["a string", "profile"],
    ["an unknown top-level key", { ...full, extra: 1 }],
    ["no categories", { ...full, categories: [] }],
    ["categories not a list", { ...full, categories: cat }],
    ["a category with an unknown key", { ...full, categories: [{ ...cat, x: 1 }] }],
    ["a category with a wrongly typed value", { ...full, categories: [{ ...cat, technical: "yes" }] }],
    ["a non-object category", { ...full, categories: ["rag"] }],
    ["a string min_words", { ...full, summary: { ...full.summary, min_words: "40" } }],
    ["a NaN faq.max", { ...full, faq: { ...full.faq, max: Number.NaN } }],
    ["an unknown meta key", { ...full, meta: { ...full.meta, og: 1 } }],
    ["links not an object", { ...full, links: [] }],
    ["an unknown links key", { ...full, links: { ...full.links, x: 1 } }],
    ["hub_pages not a list", { ...full, links: { ...full.links, hub_pages: hub } }],
    ["a hub with non-string topics", { ...full, links: { ...full.links, hub_pages: [{ ...hub, topics: [1] }] } }],
    ["a hub with an unknown key", { ...full, links: { ...full.links, hub_pages: [{ ...hub, x: 1 }] } }],
    ["an unknown stance", { ...full, stance: "favour" }],
  ];
  for (const [name, value] of invalid) {
    test(`invalid: ${name}`, () => {
      assert.equal(asBlogProfile(value), null);
    });
  }
});

describe("asBlogProfile hub pages follow the backend's HubPage rules", () => {
  const withHub = (h: Record<string, unknown>) => ({
    ...full,
    links: { ...full.links, hub_pages: [{ ...hub, ...h }] },
  });

  const okPaths = ["/", "/rag", "/blog/rag-guide/", "/a%20b", "/café"];
  for (const path of okPaths) {
    test(`accepts path ${JSON.stringify(path)}`, () => {
      assert.ok(asBlogProfile(withHub({ path })));
    });
  }

  const badPaths: [string, string][] = [
    ["empty", ""],
    ["no leading slash", "rag"],
    ["an absolute URL", "https://evil.com/x"],
    ["a leading //", "//evil.com"],
    ["a leading /\\", "/\\evil.com"],
    ["a space", "/a b"],
    ["a tab", "/a\tb"],
    ["/<tab>/host", "/\t/evil.com"],
    ["a newline", "/a\nb"],
    ["NUL", "/a\u0000b"],
    ["DEL", "/a\u007fb"],
    ["NEL (Python isspace)", "/a\u0085b"],
    ["a no-break space", "/a b"],
    ["a backslash", "/a\\b"],
    ["over 300 characters", "/" + "a".repeat(300)],
  ];
  for (const [name, path] of badPaths) {
    test(`rejects a path with ${name}`, () => {
      assert.equal(asBlogProfile(withHub({ path })), null);
    });
  }

  test("topics are 4-80 characters after trimming", () => {
    assert.ok(asBlogProfile(withHub({ topics: ["abcd", "  wxyz  ", "x".repeat(80)] })));
    assert.equal(asBlogProfile(withHub({ topics: ["abc"] })), null);
    assert.equal(asBlogProfile(withHub({ topics: ["  abc   "] })), null);
    assert.equal(asBlogProfile(withHub({ topics: ["x".repeat(81)] })), null);
    assert.equal(asBlogProfile(withHub({ topics: ["retrieval", "ab"] })), null);
  });

  test("a hub needs a title and at least one topic", () => {
    assert.equal(asBlogProfile(withHub({ title: "" })), null);
    assert.equal(asBlogProfile(withHub({ topics: [] })), null);
    const { title: _t, ...noTitle } = hub;
    assert.equal(
      asBlogProfile({ ...full, links: { ...full.links, hub_pages: [noTitle] } }),
      null
    );
  });
});

describe("blogProfileState", () => {
  test("absent, invalid and valid", () => {
    assert.deepEqual(blogProfileState(null), { kind: "absent" });
    assert.deepEqual(blogProfileState(undefined), { kind: "absent" });
    assert.deepEqual(blogProfileState({ bogus: 1 }), { kind: "invalid", raw: { bogus: 1 } });
    assert.deepEqual(blogProfileState(full), { kind: "valid", profile: full });
  });
});

describe("mergeOntoDefault", () => {
  test("a non-object gives the defaults", () => {
    assert.deepEqual(mergeOntoDefault("junk"), DEFAULT_BLOG_PROFILE);
    assert.deepEqual(mergeOntoDefault(null), DEFAULT_BLOG_PROFILE);
  });

  test("keeps known, correctly typed keys and drops the rest", () => {
    const merged = mergeOntoDefault({
      categories: [{ ...cat, x: 1, technical: "no" }, "junk"],
      summary: { enabled: false, min_words: "10", extra: true },
      links: { min: 2, hub_pages: [{ path: "/a", title: 5, topics: ["abcd"] }, 7] },
      stance: "bogus",
      extra: 1,
    });
    assert.deepEqual(merged, {
      categories: [{ key: "rag", label: "RAG", description: "", technical: true }],
      summary: { enabled: false, min_words: 40, max_words: 60 },
      faq: DEFAULT_BLOG_PROFILE.faq,
      meta: DEFAULT_BLOG_PROFILE.meta,
      links: {
        min: 2,
        max: 5,
        trailing_slash: true,
        hub_pages: [{ path: "/a", title: "", topics: ["abcd"] }],
      },
      stance: "neutral",
    });
  });

  test("no usable categories falls back to the default category (a copy)", () => {
    const merged = mergeOntoDefault({ categories: [] });
    assert.deepEqual(merged.categories, DEFAULT_BLOG_PROFILE.categories);
    assert.notEqual(merged.categories[0], DEFAULT_BLOG_PROFILE.categories[0]);
  });

  test("the result is always a conforming profile", () => {
    for (const raw of [{}, { categories: "x" }, full, { links: "x", faq: [] }]) {
      assert.ok(asBlogProfile(mergeOntoDefault(raw)), JSON.stringify(raw));
    }
  });
});
