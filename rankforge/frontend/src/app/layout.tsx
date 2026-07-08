import type { Metadata, Viewport } from "next";
import { Space_Grotesk, Hanken_Grotesk, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { QueryProvider } from "@/lib/providers/QueryProvider";
import { AuthProvider } from "@/lib/auth/AuthProvider";
import { Toaster } from "@/components/ui/sonner";
import { SITE_URL } from "@/lib/constants";

const display = Space_Grotesk({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["500", "600", "700"],
});
const body = Hanken_Grotesk({
  variable: "--font-body",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});
const mono = JetBrains_Mono({
  variable: "--font-mono-jb",
  subsets: ["latin"],
  weight: ["400", "500"],
});

const TAGLINE = "Forge SEO/GEO content from live search intelligence.";
const SOCIAL_TITLE = `RankForge — ${TAGLINE}`;

export const metadata: Metadata = {
  // Absolute base so OG/Twitter image URLs, canonicals, and file-convention icons
  // resolve to absolute URLs (social scrapers & crawlers reject relative ones).
  metadataBase: new URL(SITE_URL),
  // Clean tab title on app pages; child routes render "%s — RankForge".
  title: { default: "RankForge", template: "%s — RankForge" },
  description: TAGLINE,
  applicationName: "RankForge",
  openGraph: {
    type: "website",
    siteName: "RankForge",
    title: SOCIAL_TITLE,
    description: TAGLINE,
    url: "/",
    // og:image comes from the app/opengraph-image route (auto-wired by Next).
  },
  twitter: {
    card: "summary_large_image",
    title: SOCIAL_TITLE,
    description: TAGLINE,
  },
  // Default to indexable so public article pages (/p/*) are crawlable; robots.ts
  // disallows the private, auth-gated app routes at the crawl level.
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  // Matches the paper/graphite canvas in globals.css so the mobile browser chrome
  // blends with the app in both light and dark.
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F4F5F8" },
    { media: "(prefers-color-scheme: dark)", color: "#16181D" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${body.variable} ${mono.variable} h-full`}
    >
      <body className="min-h-full bg-background font-sans antialiased">
        <QueryProvider>
          <AuthProvider>{children}</AuthProvider>
        </QueryProvider>
        <Toaster richColors position="top-right" />
      </body>
    </html>
  );
}
