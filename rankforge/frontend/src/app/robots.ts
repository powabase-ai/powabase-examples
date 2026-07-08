import type { MetadataRoute } from "next";

// Let crawlers index the public article pages (/p/*) — the whole point of the GEO
// work — while keeping the private, auth-gated app out of the index. The app routes
// only render a login shell to a signed-out crawler anyway, so there's nothing worth
// indexing there.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: ["/brands/", "/login", "/accept-invite"],
    },
  };
}
