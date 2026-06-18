import { getBackendHealth } from "@/lib/api";

export default async function Home() {
  let backend = "unknown";
  try {
    const health = await getBackendHealth();
    backend = health.status;
  } catch {
    backend = "unreachable";
  }

  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <h1 className="text-4xl font-bold tracking-tight">RankForge</h1>
      <p className="mt-3 text-lg text-neutral-400">
        SEO/GEO blog-article platform on Powabase.
      </p>
      <p className="mt-6 text-sm text-neutral-500">
        Backend status: <span className="font-mono">{backend}</span>
      </p>
      <p className="mt-8 text-sm text-neutral-500">
        Scaffold in place. See <span className="font-mono">docs/PRD.md</span> for
        the roadmap.
      </p>
    </main>
  );
}
