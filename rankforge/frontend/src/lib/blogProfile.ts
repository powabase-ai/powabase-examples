/** Runtime handling of a brand's stored `blog_profile`.
 *
 *  The backend returns an invalid *stored* profile as-is (untyped), so
 *  `BusinessProfile.blog_profile` is `unknown` and every read goes through
 *  `asBlogProfile`. It mirrors the backend model's shape rules: a missing section
 *  takes its default, while an unknown key or a wrongly typed value makes the whole
 *  profile invalid (null). Numeric ranges and the category-key pattern are left to
 *  the server's validation on save. Type-only imports keep this module free of
 *  runtime dependencies. */
import type { BlogCategory, BlogProfile, HubPage } from "@/lib/api";

export const DEFAULT_BLOG_PROFILE: BlogProfile = {
  categories: [{ key: "general", label: "General", description: "", technical: true }],
  summary: { enabled: true, min_words: 40, max_words: 60 },
  faq: { enabled: true, min: 3, max: 6 },
  meta: { title_max: 60, description_max: 160 },
  links: { min: 3, max: 5, trailing_slash: true, hub_pages: [] },
  stance: "neutral",
};

type Obj = Record<string, unknown>;

function isObj(x: unknown): x is Obj {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}

const isStr = (x: unknown): x is string => typeof x === "string";
const isBool = (x: unknown): x is boolean => typeof x === "boolean";
const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const isStrList = (x: unknown): x is string[] => Array.isArray(x) && x.every(isStr);

type Check = (x: unknown) => boolean;

/** Strict: `x` must be an object with only `spec`'s keys, each (when present)
 *  passing its check; missing keys take `defaults`. Returns null otherwise. */
function strict<T extends object>(
  x: unknown,
  spec: Record<keyof T, Check>,
  defaults: T
): T | null {
  if (x === undefined) return { ...defaults };
  if (!isObj(x)) return null;
  const out: Obj = { ...(defaults as Obj) };
  for (const [k, v] of Object.entries(x)) {
    if (!(k in spec)) return null;
    if (!spec[k as keyof T](v)) return null;
    out[k] = v;
  }
  return out as T;
}

/** Lenient: keep only `spec`'s keys whose values pass their check, on `defaults`. */
function lenient<T extends object>(
  x: unknown,
  spec: Record<keyof T, Check>,
  defaults: T
): T {
  const out: Obj = { ...(defaults as Obj) };
  if (isObj(x)) {
    for (const k of Object.keys(spec) as (keyof T & string)[]) {
      if (k in x && spec[k](x[k])) out[k] = x[k];
    }
  }
  return out as T;
}

const BLANK_CATEGORY: BlogCategory = { key: "", label: "", description: "", technical: true };
const BLANK_HUB: HubPage = { path: "/", title: "", topics: [] };
const CATEGORY_SPEC: Record<keyof BlogCategory, Check> = {
  key: isStr, label: isStr, description: isStr, technical: isBool,
};
const HUB_SPEC: Record<keyof HubPage, Check> = {
  path: isStr, title: isStr, topics: isStrList,
};
const SUMMARY_SPEC: Record<keyof BlogProfile["summary"], Check> = {
  enabled: isBool, min_words: isNum, max_words: isNum,
};
const FAQ_SPEC: Record<keyof BlogProfile["faq"], Check> = {
  enabled: isBool, min: isNum, max: isNum,
};
const META_SPEC: Record<keyof BlogProfile["meta"], Check> = {
  title_max: isNum, description_max: isNum,
};
type LinkScalars = Omit<BlogProfile["links"], "hub_pages">;
const LINK_SCALARS: Record<keyof LinkScalars, Check> = {
  min: isNum, max: isNum, trailing_slash: isBool,
};
const isStance = (x: unknown): x is BlogProfile["stance"] =>
  x === "neutral" || x === "favor_brand";
const TOP_KEYS = ["categories", "summary", "faq", "meta", "links", "stance"];

/** The stored value as a conforming BlogProfile (missing sections defaulted), or
 *  null when it is absent or invalid. */
export function asBlogProfile(x: unknown): BlogProfile | null {
  if (!isObj(x)) return null;
  if (Object.keys(x).some((k) => !TOP_KEYS.includes(k))) return null;
  const d = DEFAULT_BLOG_PROFILE;
  const linkDefaults: LinkScalars = {
    min: d.links.min, max: d.links.max, trailing_slash: d.links.trailing_slash,
  };

  if (!Array.isArray(x.categories) || x.categories.length === 0) return null;
  const categories: BlogCategory[] = [];
  for (const c of x.categories) {
    const cat = strict(c, CATEGORY_SPEC, BLANK_CATEGORY);
    if (!cat || c === undefined) return null;
    categories.push(cat);
  }
  const summary = strict(x.summary, SUMMARY_SPEC, d.summary);
  const faq = strict(x.faq, FAQ_SPEC, d.faq);
  const meta = strict(x.meta, META_SPEC, d.meta);
  if (!summary || !faq || !meta) return null;

  let links: BlogProfile["links"] = { ...d.links, hub_pages: [] };
  if (x.links !== undefined) {
    if (!isObj(x.links)) return null;
    const { hub_pages: hubs, ...scalars } = x.links;
    const base = strict(scalars, LINK_SCALARS, linkDefaults);
    if (!base) return null;
    const hub_pages: HubPage[] = [];
    if (hubs !== undefined) {
      if (!Array.isArray(hubs)) return null;
      for (const h of hubs) {
        const hub = strict(h, HUB_SPEC, BLANK_HUB);
        if (!hub || h === undefined) return null;
        hub_pages.push(hub);
      }
    }
    links = { min: base.min, max: base.max, trailing_slash: base.trailing_slash, hub_pages };
  }

  if (x.stance !== undefined && !isStance(x.stance)) return null;
  const stance = isStance(x.stance) ? x.stance : d.stance;
  return { categories, summary, faq, meta, links, stance };
}

export type BlogProfileState =
  | { kind: "absent" }
  | { kind: "invalid"; raw: unknown }
  | { kind: "valid"; profile: BlogProfile };

/** Absent (null/undefined), present but invalid, or a conforming profile. The
 *  "invalid" case matters: generation then runs without the profile's rules and
 *  the UI must say so rather than behave as if there were no profile. */
export function blogProfileState(raw: unknown): BlogProfileState {
  if (raw === null || raw === undefined) return { kind: "absent" };
  const profile = asBlogProfile(raw);
  return profile ? { kind: "valid", profile } : { kind: "invalid", raw };
}

/** Best-effort repair of an invalid stored profile ("Start from stored values"):
 *  deep-merge the raw object onto DEFAULT_BLOG_PROFILE, keeping only known keys
 *  with correctly typed values. Always returns a conforming profile. */
export function mergeOntoDefault(x: unknown): BlogProfile {
  const d = DEFAULT_BLOG_PROFILE;
  const raw = isObj(x) ? x : {};
  const linkDefaults: LinkScalars = {
    min: d.links.min, max: d.links.max, trailing_slash: d.links.trailing_slash,
  };
  const categories = Array.isArray(raw.categories)
    ? raw.categories.filter(isObj).map((c) => lenient(c, CATEGORY_SPEC, BLANK_CATEGORY))
    : [];
  const rawLinks = isObj(raw.links) ? raw.links : {};
  const hub_pages = Array.isArray(rawLinks.hub_pages)
    ? rawLinks.hub_pages.filter(isObj).map((h) => lenient(h, HUB_SPEC, BLANK_HUB))
    : [];
  const linkScalars = lenient(rawLinks, LINK_SCALARS, linkDefaults);
  return {
    categories: categories.length ? categories : d.categories.map((c) => ({ ...c })),
    summary: lenient(raw.summary, SUMMARY_SPEC, d.summary),
    faq: lenient(raw.faq, FAQ_SPEC, d.faq),
    meta: lenient(raw.meta, META_SPEC, d.meta),
    links: {
      min: linkScalars.min,
      max: linkScalars.max,
      trailing_slash: linkScalars.trailing_slash,
      hub_pages,
    },
    stance: isStance(raw.stance) ? raw.stance : d.stance,
  };
}
