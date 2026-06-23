import type { Metadata } from "next";
import { notFound } from "next/navigation";

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
  content_html?: string | null;
  json_ld?: Record<string, unknown> | null;
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
  if (!a) return { title: "Not found" };
  const title = a.meta_title || a.title;
  const description = a.meta_description ?? undefined;
  return {
    title,
    description,
    openGraph: { title, description, type: "article" },
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
        {/* content_html is sanitized server-side (nh3) before storage. */}
        <div dangerouslySetInnerHTML={{ __html: a.content_html ?? "" }} />
      </article>
    </main>
  );
}
