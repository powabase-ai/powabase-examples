import { ImageResponse } from "next/og";

// Default social-share card for every non-article route (login, app shell, etc.).
// Per-article pages override this with their own generated or uploaded card.
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "RankForge — Forge SEO/GEO content from live search intelligence";

export default function OpengraphImage() {
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
        {/* RF tile mark */}
        <div style={{ display: "flex", alignItems: "center" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 76,
              height: 76,
              borderRadius: 18,
              background: "#191C22",
              border: "1px solid #2E333C",
              fontSize: 40,
              fontWeight: 700,
              letterSpacing: -3,
            }}
          >
            <span style={{ color: "#F0F2F5" }}>R</span>
            <span style={{ color: "#EE4D2D" }}>F</span>
          </div>
        </div>

        {/* Wordmark + tagline */}
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", fontSize: 96, fontWeight: 700, letterSpacing: -3 }}>
            <span>Rank</span>
            <span style={{ color: "#EE4D2D" }}>Forge</span>
          </div>
          <div style={{ display: "flex", fontSize: 38, color: "#A5ABB6", marginTop: 12 }}>
            Forge SEO/GEO content from live search intelligence.
          </div>
        </div>

        {/* Ember accent bar */}
        <div style={{ display: "flex", width: 240, height: 12, borderRadius: 6, background: "#EE4D2D" }} />
      </div>
    ),
    { ...size }
  );
}
