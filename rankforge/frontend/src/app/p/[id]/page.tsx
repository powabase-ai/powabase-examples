import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { SITE_URL } from "@/lib/constants";

// Public, server-rendered view of a published article. Because this is a Server
// Component, the JSON-LD + content land in the INITIAL HTML — crawlable by search
// and answer engines (the whole point of GEO). No auth: published == public.

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

interface PublicArticle {
  id: string;
  title: string;
  slug?: string | null;
  meta_title?: string | null;
  meta_description?: string | null;
  description?: string | null;
  content_html?: string | null;
  json_ld?: Record<string, unknown> | null;
  canonical_url?: string | null;
  og_image_url?: string | null;
  author?: string | null;
  published_at?: string | null;
  updated_at: string;
}

async function getArticle(id: string): Promise<PublicArticle | null> {
  try {
    const res = await fetch(`${API}/api/public/articles/${id}`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as PublicArticle;
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const a = await getArticle(id);
  if (!a) return { title: "Not found", robots: { index: false, follow: false } };
  const title = a.meta_title || a.title;
  // Guaranteed non-empty: backend derives `description` from the body when the author
  // left meta_description blank, so the share card / <meta description> is never empty.
  const description = a.description || a.meta_description || undefined;
  // Where the article actually lives (brand url_pattern / override) → else our SSR page.
  const canonical = a.canonical_url || `${SITE_URL}/p/${id}`;
  // A custom uploaded card wins; otherwise the per-article generated card.
  const image = a.og_image_url || `${SITE_URL}/p/${id}/og`;
  return {
    title,
    description,
    alternates: { canonical },
    openGraph: {
      type: "article",
      title,
      description,
      url: canonical,
      siteName: "RankForge",
      images: [{ url: image, width: 1200, height: 630, alt: title }],
      publishedTime: a.published_at ?? undefined,
      modifiedTime: a.updated_at ?? undefined,
      authors: a.author ? [a.author] : undefined,
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [image],
    },
  };
}

export default async function PublicArticlePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const a = await getArticle(id);
  if (!a) notFound();

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      {a.json_ld && (
        <script
          type="application/ld+json"
          // Escape "<" so content can't break out of the <script> tag.
          dangerouslySetInnerHTML={{
            __html: JSON.stringify(a.json_ld).replace(/</g, "\\u003c"),
          }}
        />
      )}
      <article className="prose prose-neutral max-w-none">
        <h1>{a.title}</h1>
        {/* content_html is rendered fresh per request from content_md and sanitized
            server-side with nh3 — it is NOT stored. Don't "optimize" this into a
            cached/stored value that would bypass the per-request sanitize. */}
        <div dangerouslySetInnerHTML={{ __html: a.content_html ?? "" }} />
      </article>
    </main>
  );
}
