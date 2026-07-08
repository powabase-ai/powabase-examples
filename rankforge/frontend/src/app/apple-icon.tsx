import { ImageResponse } from "next/og";

// Apple touch icon (home-screen). iOS applies its own rounded mask, so we fill the
// tile edge-to-edge with the graphite ground and center the ember-"F" RF monogram.
export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#191C22",
          fontFamily: "sans-serif",
          fontWeight: 700,
          fontSize: 100,
          letterSpacing: -8,
        }}
      >
        <span style={{ color: "#F0F2F5" }}>R</span>
        <span style={{ color: "#EE4D2D" }}>F</span>
      </div>
    ),
    { ...size }
  );
}
