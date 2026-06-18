import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RankForge",
  description: "SEO/GEO blog-article platform on Powabase",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
