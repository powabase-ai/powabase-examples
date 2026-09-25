import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { describeFrontmatterResult } from "./frontmatterResult.ts";

const r = (changed: string[], flags: string[] = [], export_issues: string[] = []) => ({
  changed,
  flags,
  export_issues,
});

describe("describeFrontmatterResult", () => {
  test("nothing changed, no flags, no issues", () => {
    assert.deepEqual(describeFrontmatterResult(r([]), "generate"), {
      kind: "info",
      message: "Nothing changed",
    });
    assert.deepEqual(describeFrontmatterResult(r([]), "fix"), {
      kind: "info",
      message: "Nothing changed",
    });
  });

  test("names exactly the changed fields", () => {
    assert.deepEqual(describeFrontmatterResult(r(["summary", "faq"]), "generate"), {
      kind: "success",
      message: "Updated summary and FAQ",
    });
    assert.equal(
      describeFrontmatterResult(r(["category", "faq", "meta_title"]), "generate").message,
      "Updated category, FAQ and meta title"
    );
    assert.equal(
      describeFrontmatterResult(r(["meta_description"]), "generate").message,
      "Updated meta description"
    );
  });

  test("never claims a field that is not in changed", () => {
    const { message } = describeFrontmatterResult(
      r(["category", "faq"], ["model's summary was 3 words (needs 40-60) — kept the stored one"]),
      "generate"
    );
    assert.equal(
      message,
      "Updated category and FAQ; model's summary was 3 words (needs 40-60) — kept the stored one"
    );
    assert.doesNotMatch(message.split(";")[0], /summary/i);
  });

  test("content_md is the body's FAQ section being removed", () => {
    assert.deepEqual(describeFrontmatterResult(r(["content_md"]), "generate"), {
      kind: "success",
      message: "Removed the FAQ section from the body",
    });
    assert.equal(
      describeFrontmatterResult(r(["faq", "content_md"]), "generate").message,
      "Updated FAQ; removed the FAQ section from the body"
    );
  });

  test("flags make it a warning and are shown, even when nothing changed", () => {
    assert.deepEqual(
      describeFrontmatterResult(r([], ["category kept as rag (the model's was not a listed key)"]), "generate"),
      {
        kind: "warning",
        message: "Nothing changed; category kept as rag (the model's was not a listed key)",
      }
    );
    assert.deepEqual(describeFrontmatterResult(r(["category"], ["category defaulted to rag"]), "fix"), {
      kind: "warning",
      message: "Updated category; category defaulted to rag",
    });
  });

  test("remaining export issues are counted", () => {
    assert.deepEqual(describeFrontmatterResult(r(["faq"], [], ["a", "b"]), "fix"), {
      kind: "warning",
      message: "Updated FAQ — 2 issues remain",
    });
    assert.deepEqual(describeFrontmatterResult(r([], [], ["a"]), "generate"), {
      kind: "warning",
      message: "Nothing changed — 1 issue remains",
    });
  });

  test("a clean fix says to try exporting again", () => {
    assert.deepEqual(describeFrontmatterResult(r(["summary"]), "fix"), {
      kind: "success",
      message: "Updated summary — try exporting again",
    });
  });

  test("a pre-K12 response without flags (or changed) doesn't throw", () => {
    const old = { changed: ["faq"], export_issues: [] } as unknown as Parameters<
      typeof describeFrontmatterResult
    >[0];
    assert.deepEqual(describeFrontmatterResult(old, "generate"), {
      kind: "success",
      message: "Updated FAQ",
    });
    const bare = {} as unknown as Parameters<typeof describeFrontmatterResult>[0];
    assert.deepEqual(describeFrontmatterResult(bare, "fix"), {
      kind: "info",
      message: "Nothing changed",
    });
  });
});
