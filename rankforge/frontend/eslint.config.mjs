import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

// ESLint 9 uses flat config. eslint-config-next ships the classic ("extends") shape,
// so bridge it through FlatCompat — this is what `create-next-app` (Next 16 + ESLint 9)
// emits. Without this file `npm run lint` (bare `eslint`) finds no config and lint is
// effectively disabled even though eslint-config-next is installed.
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    ignores: [".next/**", "node_modules/**"],
  },
];

export default eslintConfig;
