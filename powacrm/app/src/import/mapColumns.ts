export type TargetField = 'first_name' | 'last_name' | 'email' | 'title' | 'linkedin_url' | 'company_name' | 'company_domain';
export type ImportRow = Partial<Record<TargetField, string>>;

const ALIASES: Record<TargetField, string[]> = {
  first_name: ['first name', 'first_name', 'firstname', 'given name'],
  last_name: ['last name', 'last_name', 'lastname', 'surname', 'family name'],
  email: ['email', 'e-mail', 'email address', 'work email'],
  title: ['title', 'job title', 'job_title', 'position', 'role'],
  linkedin_url: ['linkedin', 'linkedin url', 'linkedin_url', 'linkedin profile'],
  company_name: ['company', 'company name', 'company_name', 'organization', 'account'],
  company_domain: ['website', 'domain', 'company domain', 'company website', 'url'],
};

export function guessMapping(headers: string[]): Record<TargetField, string | null> {
  const out = {} as Record<TargetField, string | null>;
  for (const field of Object.keys(ALIASES) as TargetField[]) {
    out[field] = headers.find(h => ALIASES[field].includes(h.trim().toLowerCase())) ?? null;
  }
  return out;
}

export function extractDomain(raw: string): string | null {
  const s = raw.trim().toLowerCase();
  if (!s) return null;
  try {
    const host = s.includes('://') ? new URL(s).hostname : new URL(`https://${s}`).hostname;
    return host.replace(/^www\./, '') || null;
  } catch { return null; }
}

export function applyMapping(rows: Record<string, string>[], mapping: Record<TargetField, string | null>): ImportRow[] {
  return rows.map(r => {
    const out: ImportRow = {};
    for (const [field, col] of Object.entries(mapping) as [TargetField, string | null][]) {
      if (!col || !r[col]?.trim()) continue;
      out[field] = field === 'company_domain' ? (extractDomain(r[col]) ?? undefined) : r[col].trim();
    }
    return out;
  });
}
