// ESLint 9 flat config. eslint-config-next@16 already ships NATIVE flat-config arrays
// at these subpaths, so import and spread them directly. (Routing them back through
// @eslint/eslintrc's FlatCompat double-wraps the self-referential plugin objects and
// crashes the config validator with "Converting circular structure to JSON".)
import cwv from "eslint-config-next/core-web-vitals";
import ts from "eslint-config-next/typescript";

export default [
  ...cwv,
  ...ts,
  { ignores: [".next/**", "node_modules/**"] },
];
