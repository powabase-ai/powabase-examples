import { ImageResponse } from "next/og";

// Per-article social card, generated on the fly. Referenced explicitly from the
// article's generateMetadata ONLY when no custom image was uploaded, so it's the
// automatic fallback that gives every published article a branded share image.

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  let title = "RankForge";
  let author: string | null = null;
  // A transient backend failure must NOT be cached: social scrapers keep a 200 for
  // days/weeks, so a blip during an article's launch could pin the generic card. On a
  // 404 (article gone/unpublished) the brand card is genuinely correct and cacheable;
  // on a network error or 5xx we render the fallback but mark it no-store + log it.
  let degraded = false;
  try {
    const res = await fetch(`${API}/api/public/articles/${id}`, {
      cache: "no-store",
    });
    if (res.ok) {
      const a = (await res.json()) as {
        title?: string;
        meta_title?: string | null;
        author?: string | null;
      };
      title = a.meta_title || a.title || title;
      author = a.author ?? null;
    } else if (res.status !== 404) {
      degraded = true;
      console.error(`OG card: article fetch ${res.status} for ${id}`);
    }
  } catch (err) {
    degraded = true;
    console.error(`OG card: article fetch failed for ${id}`, err);
  }
  // Keep the headline to a readable size on the card.
  const headline = title.length > 140 ? `${title.slice(0, 137)}…` : title;

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "#16181D",
          color: "#F0F2F5",
          padding: 88,
          fontFamily: "sans-serif",
        }}
      >
        {/* Brand row */}
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 60,
              height: 60,
              borderRadius: 14,
              background: "#191C22",
              border: "1px solid #2E333C",
              fontSize: 30,
              fontWeight: 700,
              letterSpacing: -2,
            }}
          >
            <span style={{ color: "#F0F2F5" }}>R</span>
            <span style={{ color: "#EE4D2D" }}>F</span>
          </div>
          <div style={{ display: "flex", fontSize: 30, fontWeight: 600, letterSpacing: -1 }}>
            <span>Rank</span>
            <span style={{ color: "#EE4D2D" }}>Forge</span>
          </div>
        </div>

        {/* Headline */}
        <div
          style={{
            display: "flex",
            fontSize: headline.length > 80 ? 58 : 72,
            fontWeight: 700,
            lineHeight: 1.1,
            letterSpacing: -2,
            maxWidth: 1024,
          }}
        >
          {headline}
        </div>

        {/* Ember bar + byline */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ display: "flex", width: 200, height: 10, borderRadius: 5, background: "#EE4D2D" }} />
          <div style={{ display: "flex", fontSize: 30, color: "#A5ABB6" }}>
            {author ? `By ${author}` : "Forge SEO/GEO content from live search intelligence."}
          </div>
        </div>
      </div>
    ),
    {
      ...size,
      // Don't let a scraper pin a degraded (headline-less) card; a good card can cache.
      headers: degraded ? { "Cache-Control": "no-store, max-age=0" } : undefined,
    }
  );
}
