// Renders `companies.research_data`, the structured payload written by
// `complete_research_job` (db/migrations/0012_research_rpcs.sql).
//
// EVERY FIELD IS AGENT-AUTHORED AND THEREFORE UNTRUSTED, including its TYPE.
// The payload came from a model that had just read an attacker-controlled web
// page, so `hooks` can arrive as the string "none found" as easily as an array
// -- plausible model output on its own, and steerable by the prompt injection
// this feature explicitly anticipates.
//
// That used to blank the entire app. `data.hooks ?? []` catches null and
// undefined and nothing else, so a string sailed through to `hooks.map(...)`,
// which throws during render. There is no error boundary above a route by
// default, so React 19 unmounted the whole root: blank page, nav gone, no way
// back but a reload onto the same lead. (There is one now -- see
// app/src/shell/ErrorBoundary.tsx -- but a component that needs it to survive
// its own data is still broken.)
//
// The types below therefore describe what the payload is SUPPOSED to look like,
// and `asArray` below decides what is actually rendered. 0012 rejects a
// non-array `hooks`/`sources` at write time as well; this is the second of the
// two layers, because research_data rows written before that check exists are
// still in the table.
export type ResearchHook = { hook: string; evidence: string; source_url: string | null };
export type ResearchData = {
  summary: string;
  tech_stack?: unknown;
  why_now?: string | null;
  hooks?: unknown;
  sources?: unknown;
  injection_observed?: unknown;
};

// Any non-array becomes empty, and the caller is told it happened so the panel
// can say the payload was malformed rather than quietly rendering a section
// short. An array with the wrong element shape is still an array: the elements
// are read defensively below (String(...) on anything rendered) rather than
// dropped, since a hook missing `evidence` is still worth showing.
function asArray(v: unknown): { items: unknown[]; malformed: boolean } {
  if (v === null || v === undefined) return { items: [], malformed: false };
  if (Array.isArray(v)) return { items: v, malformed: false };
  return { items: [], malformed: true };
}

// A string is what a model reaches for when it has something to say about a
// boolean field ("maybe", "detected on the pricing page"). Anything that is not
// an explicit false/null/undefined counts as observed: this banner is a warning,
// and the failure that matters is not showing it. Matches how
// complete_research_job resolves the same value server-side.
function injectionObserved(v: unknown): boolean {
  if (v === null || v === undefined || v === false) return false;
  if (typeof v === 'string') return v.trim().toLowerCase() !== 'false' && v.trim() !== '';
  return Boolean(v);
}

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
  // A research_data that is not an object at all (a bare string, an array) has
  // no fields to read; treat it as no research rather than dereferencing it.
  const raw = company?.research_data;
  const data = raw !== null && typeof raw === 'object' && !Array.isArray(raw) ? (raw as ResearchData) : null;

  if (!company || !data) {
    return <p style={{ color: 'var(--fg-light)' }}>No research yet.</p>;
  }

  const techStack = asArray(data.tech_stack);
  const hooksField = asArray(data.hooks);
  const sourcesField = asArray(data.sources);
  const tags = techStack.items.map(t => String(t));
  const hooks = hooksField.items as Partial<ResearchHook>[];
  const sources = sourcesField.items.map(s => String(s));
  const malformed = [
    techStack.malformed ? 'tech_stack' : null,
    hooksField.malformed ? 'hooks' : null,
    sourcesField.malformed ? 'sources' : null,
  ].filter((f): f is string => f !== null);

  return (
    <div style={{ display: 'grid', gap: 'var(--space-4)', fontSize: 'var(--font-sm)' }}>
      {malformed.length > 0 && (
        <div style={{ background: 'var(--tag-red-bg)', color: 'var(--tag-red-fg)', padding: 'var(--space-3)',
          borderRadius: 'var(--radius-md)', fontSize: 'var(--font-sm)' }}>
          {/* Said out loud rather than rendered as an empty section: "no hooks"
              and "the agent returned something that is not a list of hooks" are
              different facts, and the second one is a reason to re-run. */}
          ⚠ Part of this report came back in an unexpected shape and was left out
          ({malformed.join(', ')}). Re-running the research may fix it.
        </div>
      )}

      {injectionObserved(data.injection_observed) && (
        <div style={{ background: 'var(--tag-orange-bg)', color: 'var(--tag-orange-fg)', padding: 'var(--space-3)',
          borderRadius: 'var(--radius-md)', fontSize: 'var(--font-sm)' }}>
          {/* A fact about the prospect worth seeing, not something to hide: the
              page this agent read contained text trying to instruct it. */}
          ⚠ A page this agent read attempted to instruct it. Treat this company's research with extra scrutiny.
        </div>
      )}

      <div style={{ color: 'var(--fg-secondary)', whiteSpace: 'pre-wrap' }}>
        {data.summary == null ? '' : String(data.summary)}
      </div>

      {data.why_now != null && data.why_now !== '' && (
        <div>
          <div style={sectionLabel}>Why now</div>
          <div style={{ color: 'var(--fg-secondary)' }}>{String(data.why_now)}</div>
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
                {/* String(...) rather than a bare render: a hook whose `hook` is
                    a number or an object is a bad report, not a crash. React
                    throws on an object child, which is the same blank-page
                    failure a string `hooks` used to cause. */}
                <div style={{ color: 'var(--fg-primary)' }}>{h?.hook == null ? '' : String(h.hook)}</div>
                <div style={{ color: 'var(--fg-tertiary)', fontSize: 'var(--font-xs)' }}>
                  {h?.evidence == null ? '' : String(h.evidence)}
                  {/* A hook's source_url may be a search-result URL rather than a
                      page the agent actually fetched -- linked, but never labeled
                      "fetched" or "verified". */}
                  {typeof h?.source_url === 'string' && h.source_url
                    && <> · <a href={h.source_url} target="_blank" rel="noreferrer">source</a></>}
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
            {sources.map((s, i) => (
              <li key={`${i}-${s}`} style={{ fontSize: 'var(--font-xs)' }}>
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
