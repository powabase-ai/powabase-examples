export const LAST_BRAND_KEY = "rankforge:lastBrand";
// A teammate invite token stashed from an /accept-invite link so it survives the
// sign-in / sign-up round-trip and can be resumed after auth.
export const PENDING_INVITE_KEY = "rankforge:pendingInvite";

// Absolute origin of the deployed app, used as the metadataBase so Open Graph /
// Twitter image URLs and canonicals resolve to absolute URLs (crawlers and social
// scrapers reject relative ones). Set NEXT_PUBLIC_SITE_URL in production; the
// localhost fallback keeps dev working. Trailing slash trimmed for clean joins.
export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3007"
).replace(/\/+$/, "");

if (process.env.NODE_ENV === "production" && !process.env.NEXT_PUBLIC_SITE_URL) {
  // A prod build without this ships canonicals + OG image URLs pointing at localhost.
  // Warn loudly (rather than fail the build, which would hard-block an example-app
  // deploy) — set NEXT_PUBLIC_SITE_URL to the real origin to silence it.
  console.warn(
    "[rankforge] NEXT_PUBLIC_SITE_URL is not set — canonical + Open Graph URLs fall " +
      "back to http://localhost:3007. Set it to the deployed origin."
  );
}
