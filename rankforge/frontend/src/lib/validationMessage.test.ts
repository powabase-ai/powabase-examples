import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { validationMessage } from "./validationMessage.ts";

describe("validationMessage", () => {
  test("prefixes each message with its location, minus the request part", () => {
    assert.equal(
      validationMessage([
        {
          loc: ["body", "blog_profile", "links", "hub_pages", 2, "path"],
          msg: "Value error, hub path must start with '/'",
          type: "value_error",
        },
      ]),
      "blog_profile.links.hub_pages.2.path: hub path must start with '/'"
    );
  });

  test("joins several errors with '; '", () => {
    assert.equal(
      validationMessage([
        { loc: ["body", "url_pattern"], msg: "Value error, must contain {slug}" },
        { loc: ["query", "limit"], msg: "Input should be a valid integer" },
      ]),
      "url_pattern: must contain {slug}; limit: Input should be a valid integer"
    );
  });

  test("an error with no usable location is just the message", () => {
    assert.equal(validationMessage([{ loc: ["body"], msg: "Field required" }]), "Field required");
    assert.equal(validationMessage([{ msg: "Value error, bad" }]), "bad");
    assert.equal(validationMessage([{ loc: "body.x", msg: "bad" }]), "bad");
  });

  test("not a pydantic error list → null", () => {
    assert.equal(validationMessage("oops"), null);
    assert.equal(validationMessage([]), null);
    assert.equal(validationMessage([{ loc: ["body"] }]), null);
    assert.equal(validationMessage([null]), null);
  });
});
