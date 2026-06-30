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
  {
    rules: {
      // React-Compiler-oriented hook rules — advisory for this codebase, kept as
      // warnings (visible, not CI-blocking) rather than refactoring intentional call
      // sites: external-store hydration (AuthProvider), reset-on-close (BrandForm,
      // BrandMaterials), and derive-from-prop sync (settings, InternalLinksPanel,
      // PublishDialog). Revisit with key-based remounts / useSyncExternalStore.
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/preserve-manual-memoization": "warn",
      // Allow deliberately-unused bindings prefixed with _ (e.g. a destructured-rest
      // sibling stripped before a payload is sent).
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
];
