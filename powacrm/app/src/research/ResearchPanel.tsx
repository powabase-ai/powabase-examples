// Renders `companies.research_data`, the validated structured payload written
// by `complete_research_job` (db/migrations/0012_research_rpcs.sql). The
// payload came from an agent that just read an attacker-controlled web page,
// so nothing here is assumed present beyond what the RPC's own validation
// guarantees (an object with a non-empty `summary`) -- `why_now` can
// legitimately be null, `hooks`/`sources`/`tech_stack` can legitimately be
// empty, and none of that is a rendering failure.
export type ResearchHook = { hook: string; evidence: string; source_url: string | null };
export type ResearchData = {
  summary: string;
  tech_stack?: string[];
  why_now: string | null;
  hooks?: ResearchHook[];
  sources?: string[];
  injection_observed?: boolean;
};

// The shape ResearchPanel needs off a company row. LeadPage's embed satisfies
// this structurally (see the widened select in Step 2).
export type ResearchCompany = {
  id: string;
  name: string | null;
  domain: string | null;
  research: string | null;
  research_data: ResearchData | null;
  tech_stack: unknown;
  researched_at: string | null;
};

// Same relative-time shape as lead/Timeline.tsx's local `rel()` -- not
// extracted to a shared util, since Timeline's isn't exported and this is the
// only other caller so far.
function rel(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

const sectionLabel = {
  fontSize: 'var(--font-xs)', fontWeight: 600, color: 'var(--fg-light)', marginBottom: 'var(--space-1)',
} as const;

export function ResearchPanel({ company }: { company: ResearchCompany | null | undefined }) {
  const data = company?.research_data ?? null;

  if (!company || !data) {
    return <p style={{ color: 'var(--fg-light)' }}>No research yet.</p>;
  }

  const tags = Array.isArray(data.tech_stack) && data.tech_stack.length > 0 ? data.tech_stack : [];
  const hooks = data.hooks ?? [];
  const sources = data.sources ?? [];

  return (
    <div style={{ display: 'grid', gap: 'var(--space-4)', fontSize: 'var(--font-sm)' }}>
      {data.injection_observed && (
        <div style={{ background: 'var(--tag-orange-bg)', color: 'var(--tag-orange-fg)', padding: 'var(--space-3)',
          borderRadius: 'var(--radius-md)', fontSize: 'var(--font-sm)' }}>
          {/* A fact about the prospect worth seeing, not something to hide: the
              page this agent read contained text trying to instruct it. */}
          ⚠ A page this agent read attempted to instruct it. Treat this company's research with extra scrutiny.
        </div>
      )}

      <div style={{ color: 'var(--fg-secondary)', whiteSpace: 'pre-wrap' }}>{data.summary}</div>

      {data.why_now && (
        <div>
          <div style={sectionLabel}>Why now</div>
          <div style={{ color: 'var(--fg-secondary)' }}>{data.why_now}</div>
        </div>
      )}

      {tags.length > 0 && (
        <div>
          <div style={sectionLabel}>Tech stack</div>
          <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
            {tags.map(t => (
              <span key={t} style={{ background: 'var(--tag-gray-bg)', color: 'var(--tag-gray-fg)',
                padding: '1px var(--space-2)', borderRadius: 'var(--radius-sm)', fontSize: 'var(--font-xs)', fontWeight: 500 }}>
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      {hooks.length > 0 && (
        <div>
          <div style={sectionLabel}>Hooks</div>
          <ul style={{ margin: 0, paddingLeft: 'var(--space-4)', display: 'grid', gap: 'var(--space-2)' }}>
            {hooks.map((h, i) => (
              <li key={i}>
                <div style={{ color: 'var(--fg-primary)' }}>{h.hook}</div>
                <div style={{ color: 'var(--fg-tertiary)', fontSize: 'var(--font-xs)' }}>
                  {h.evidence}
                  {/* A hook's source_url may be a search-result URL rather than a
                      page the agent actually fetched -- linked, but never labeled
                      "fetched" or "verified". */}
                  {h.source_url && <> · <a href={h.source_url} target="_blank" rel="noreferrer">source</a></>}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {sources.length > 0 && (
        <div>
          <div style={sectionLabel}>Sources</div>
          <ul style={{ margin: 0, paddingLeft: 'var(--space-4)', display: 'grid', gap: 'var(--space-1)' }}>
            {sources.map(s => (
              <li key={s} style={{ fontSize: 'var(--font-xs)' }}>
                <a href={s} target="_blank" rel="noreferrer">{s}</a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {company.researched_at && (
        <div style={{ color: 'var(--fg-tertiary)', fontSize: 'var(--font-xs)' }}>
          Researched <span title={new Date(company.researched_at).toLocaleString()}>{rel(company.researched_at)}</span>
        </div>
      )}
    </div>
  );
}
