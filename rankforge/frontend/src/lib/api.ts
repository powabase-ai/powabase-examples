/**
 * Backend API client. Calls the FastAPI backend; the browser never holds Powabase
 * secrets (only the Anon key, used elsewhere for GoTrue/PostgREST).
 */

import { getAccessToken, getSession, refresh } from "./auth/session";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** Error carrying the HTTP status so callers can branch on it (not regex the text). */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Turn a backend error into a user-facing message. The expensive AI routes can
 * now return 429 (rate limited) and 409 (a generation/refine already running);
 * surface those gracefully instead of a raw "API 429: ..." string. */
function friendlyMessage(status: number, detail: string): string {
  if (status === 429)
    return "You're going a bit fast — please wait a moment and try again.";
  if (status === 409)
    return detail || "That action is already in progress.";
  if (status === 503)
    return "The service is busy right now — please retry shortly.";
  return detail ? `${detail}` : `Request failed (${status})`;
}

export interface Competitor {
  name?: string | null;
  domain: string;
}

export interface BusinessProfile {
  id: string;
  name: string;
  domain?: string | null;
  description?: string | null;
  niche?: string | null;
  audience?: string | null;
  seed_topics: string[];
  target_keywords: string[];
  competitors: Competitor[];
  brand_kb_id?: string | null;
  sitemap_url?: string | null;
  url_pattern?: string | null;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

export interface BusinessProfileInput {
  name: string;
  domain?: string | null;
  description?: string | null;
  niche?: string | null;
  audience?: string | null;
  seed_topics?: string[];
  target_keywords?: string[];
  competitors?: Competitor[];
  brand_kb_id?: string | null;
  sitemap_url?: string | null;
  url_pattern?: string | null;
}

async function request<T>(
  path: string,
  init?: RequestInit,
  retry = false
): Promise<T> {
  const { headers: initHeaders, ...rest } = init ?? {};
  const token = getAccessToken();
  const res = await fetch(`${API_BASE_URL}${path}`, {
    cache: "no-store",
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...(initHeaders as Record<string, string> | undefined),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  // Token expired? Refresh once and retry transparently.
  if (res.status === 401 && !retry && getSession()) {
    const ns = await refresh();
    if (ns) return request<T>(path, init, true);
  }
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, friendlyMessage(res.status, detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export async function getBackendHealth(): Promise<{ status: string }> {
  return request("/health");
}

export const brandsApi = {
  list: () => request<BusinessProfile[]>("/api/business-profiles"),
  get: (id: string) => request<BusinessProfile>(`/api/business-profiles/${id}`),
  create: (data: BusinessProfileInput) =>
    request<BusinessProfile>("/api/business-profiles", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  update: (id: string, data: Partial<BusinessProfileInput>) =>
    request<BusinessProfile>(`/api/business-profiles/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  remove: (id: string) =>
    request<void>(`/api/business-profiles/${id}`, { method: "DELETE" }),
};

// --- Research (Stage A) ---
export interface SerpResult {
  rank?: number | null;
  title?: string | null;
  url?: string | null;
  snippet?: string | null;
}

export interface CompetitorTeardown {
  url?: string | null;
  title?: string | null;
  word_count?: number | null;
  headings: string[];
  source_id?: string | null;
}

export type ResearchStatus =
  | "queued"
  | "searching"
  | "scraping"
  | "analyzing"
  | "done"
  | "failed";

export interface ResearchRun {
  id: string;
  business_id?: string | null;
  topic: string;
  locale: string;
  status: ResearchStatus;
  error?: string | null;
  progress: { phase?: string; total?: number; done?: number };
  serp: {
    results?: SerpResult[];
    paa?: string[];
    related_queries?: string[];
  };
  competitors: CompetitorTeardown[];
  clusters: Array<Record<string, unknown>>;
  intent?: string | null;
  agent_run_id?: string | null;
  created_at: string;
}

export const researchApi = {
  listByBrand: (businessId: string) =>
    request<ResearchRun[]>(`/api/research?business_id=${businessId}`),
  get: (id: string) => request<ResearchRun>(`/api/research/${id}`),
  run: (data: { business_id: string; topic: string; locale?: string; depth?: string }) =>
    request<ResearchRun>("/api/research", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  remove: (id: string) =>
    request<void>(`/api/research/${id}`, { method: "DELETE" }),
};

export const TERMINAL_RESEARCH: ResearchStatus[] = ["done", "failed"];

// --- Sources library ---
export interface BrandSource {
  id: string;
  source_id: string;
  url?: string | null;
  title?: string | null;
  word_count?: number | null;
  status?: string | null;
  created_at: string;
  research_run_id: string;
  run_topic?: string | null;
}

export const sourcesApi = {
  listByBrand: (businessId: string) =>
    request<BrandSource[]>(`/api/sources?business_id=${businessId}`),
  markdown: (sourceId: string) =>
    request<{ source_id: string; markdown: string }>(
      `/api/sources/${sourceId}/markdown`
    ),
};

// --- Brand materials (own-site KB) ---
/** A page the brand fed into its materials KB (its own site/docs), via sitemap
 *  crawl or manually added URL. Distinct from research `BrandSource` (scraped
 *  competitor pages). */
export interface BrandMaterialSource {
  id: string;
  url: string;
  title?: string | null;
  status?: string | null;
  origin: "sitemap" | "manual" | "crawl";
  source_id?: string | null;
  created_at?: string | null;
}

/** How to discover the brand's pages for an ingest. */
export interface MaterialsIngestRequest {
  mode: "sitemap" | "crawl" | "urls";
  url?: string;
  urls?: string[];
  max_pages?: number;
  origin?: "sitemap" | "manual" | "crawl";
}

/** Crawl preview — pages discovered (grouped by subdomain) without importing. */
export interface DiscoveredHost {
  host: string;
  urls: string[];
}
export interface MaterialsDiscovery {
  hosts: DiscoveredHost[];
  total: number;
}

export interface MaterialsProgress {
  phase?: string;
  message?: string;
  total?: number;
  done?: number;
}

export interface MaterialsView {
  sources: BrandMaterialSource[];
  progress: MaterialsProgress;
  kb_ready: boolean;
}

/** Ingest is finished (idle) when progress is empty or its phase is terminal. */
export function materialsRunning(progress?: MaterialsProgress | null): boolean {
  const phase = progress?.phase;
  if (!phase) return false;
  return phase !== "done" && phase !== "failed";
}

export const materialsApi = {
  get: (businessId: string) =>
    request<MaterialsView>(
      `/api/business-profiles/${businessId}/materials`
    ),
  ingest: (businessId: string, body: MaterialsIngestRequest) =>
    request<{ status: string }>(
      `/api/business-profiles/${businessId}/materials/ingest`,
      { method: "POST", body: JSON.stringify(body) }
    ),
  discover: (businessId: string, url: string, maxPages?: number) =>
    request<MaterialsDiscovery>(
      `/api/business-profiles/${businessId}/materials/discover`,
      { method: "POST", body: JSON.stringify({ url, max_pages: maxPages }) }
    ),
  remove: (businessId: string, rowId: string) =>
    request<void>(
      `/api/business-profiles/${businessId}/materials/${rowId}`,
      { method: "DELETE" }
    ),
  refresh: (businessId: string, rowIds: string[]) =>
    request<{ status: string }>(
      `/api/business-profiles/${businessId}/materials/refresh`,
      { method: "POST", body: JSON.stringify({ row_ids: rowIds }) }
    ),
  bulkDelete: (businessId: string, rowIds: string[]) =>
    request<{ status: string }>(
      `/api/business-profiles/${businessId}/materials/bulk-delete`,
      { method: "POST", body: JSON.stringify({ row_ids: rowIds }) }
    ),
  content: (businessId: string, rowId: string) =>
    request<{ content: string }>(
      `/api/business-profiles/${businessId}/materials/${rowId}/content`
    ),
  uploadFile: (businessId: string, file: File) =>
    uploadMaterial(businessId, file),
};

/** Multipart upload of a brand-materials file. Mirrors request()'s auth +
 *  401→refresh→retry, but lets the browser set the multipart Content-Type
 *  boundary (request() hard-codes application/json, which breaks file uploads). */
async function uploadMaterial(
  businessId: string,
  file: File,
  retry = false
): Promise<{ status: string }> {
  const form = new FormData();
  form.append("file", file);
  const token = getAccessToken();
  const res = await fetch(
    `${API_BASE_URL}/api/business-profiles/${businessId}/materials/upload`,
    {
      method: "POST",
      cache: "no-store",
      body: form,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }
  );
  if (res.status === 401 && !retry && getSession()) {
    const ns = await refresh();
    if (ns) return uploadMaterial(businessId, file, true);
  }
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, friendlyMessage(res.status, detail));
  }
  return res.json();
}

// --- Articles (Stage C) ---
export type GenerationStatus =
  | "queued"
  | "grounding"
  | "outlining"
  | "drafting"
  | "optimizing"
  | "refining"
  | "done"
  | "failed";

export interface ArticleSummary {
  id: string;
  title: string;
  status: string;
  generation_status: GenerationStatus;
  progress: {
    phase?: string;
    total?: number;
    done?: number;
    word_count?: number;
    iteration?: number;
    step?: string;
  };
  updated_at: string;
}

export interface ScoreSignal {
  key: string;
  label: string;
  score: number;
  weight: number;
  explanation: string;
  fixes: string[];
  method: "deterministic" | "llm";
}

export interface Score {
  total: number;
  target: number;
  met: boolean;
  signals: ScoreSignal[];
}

export interface GroundingFlag {
  claim: string;
  quote?: string; // the article's verbatim wording, so the user can find the sentence
  issue: string;
  suggestion: string;
}

export interface GroundingReport {
  grounding_score?: number | null;
  claims_checked?: number;
  supported?: number;
  flagged?: GroundingFlag[];
  error?: string;
}

export interface Article extends ArticleSummary {
  business_id?: string | null;
  brief_id?: string | null;
  research_run_id?: string | null;
  slug?: string | null;
  generation_error?: string | null;
  content_md: string;
  meta_title?: string | null;
  meta_description?: string | null;
  seo_score?: Score | null;
  geo_score?: Score | null;
  readability_score?: Score | null;
  json_ld?: Record<string, unknown> | null;
  grounding_report?: GroundingReport | null;
  canonical_url?: string | null;
  cluster_id?: string | null;
  cluster_role?: "pillar" | "member" | null;
  created_at: string;
}

export const TERMINAL_GENERATION: GenerationStatus[] = ["done", "failed"];

export type ArticleStatus =
  | "draft"
  | "in_review"
  | "approved"
  | "published"
  | "archived";

export const ARTICLE_STATUSES: ArticleStatus[] = [
  "draft",
  "in_review",
  "approved",
  "published",
  "archived",
];

export interface ArticleVersion {
  id: string;
  article_id: string;
  created_at: string;
  word_count?: number | null;
}

export const articlesApi = {
  listByBrand: (businessId: string) =>
    request<ArticleSummary[]>(`/api/articles?business_id=${businessId}`),
  get: (id: string) => request<Article>(`/api/articles/${id}`),
  generate: (briefId: string) =>
    request<Article>("/api/articles", {
      method: "POST",
      body: JSON.stringify({ brief_id: briefId }),
    }),
  score: (id: string) =>
    request<Article>(`/api/articles/${id}/score`, { method: "POST" }),
  optimize: (id: string) =>
    request<Article>(`/api/articles/${id}/optimize`, { method: "POST" }),
  refine: (id: string, targets?: string[]) =>
    request<Article>(`/api/articles/${id}/refine`, {
      method: "POST",
      body: JSON.stringify({ targets: targets ?? null }),
    }),
  retry: (id: string) =>
    request<Article>(`/api/articles/${id}/retry`, { method: "POST" }),
  update: (id: string, data: ArticleUpdate) =>
    request<Article>(`/api/articles/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  remove: (id: string) =>
    request<void>(`/api/articles/${id}`, { method: "DELETE" }),
  versions: (id: string) =>
    request<ArticleVersion[]>(`/api/articles/${id}/versions`),
  restoreVersion: (id: string, versionId: string) =>
    request<Article>(`/api/articles/${id}/versions/${versionId}/restore`, {
      method: "POST",
    }),
  comments: (id: string) =>
    request<Comment[]>(`/api/articles/${id}/comments`),
  addComment: (id: string, body: string, anchor?: string | null) =>
    request<Comment>(`/api/articles/${id}/comments`, {
      method: "POST",
      body: JSON.stringify({ body, anchor }),
    }),
  updateComment: (
    id: string,
    commentId: string,
    data: { body?: string; resolved?: boolean }
  ) =>
    request<Comment>(`/api/articles/${id}/comments/${commentId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  removeComment: (id: string, commentId: string) =>
    request<void>(`/api/articles/${id}/comments/${commentId}`, {
      method: "DELETE",
    }),
  links: (id: string) =>
    request<LinkSuggestion[]>(`/api/articles/${id}/links`),
  suggestLinks: (id: string) =>
    request<LinkSuggestion[]>(`/api/articles/${id}/links/suggest`, {
      method: "POST",
    }),
  applyLink: (id: string, suggestionId: string) =>
    request<LinkSuggestion>(`/api/articles/${id}/links/${suggestionId}/apply`, {
      method: "POST",
    }),
  generateLink: (id: string, suggestionId: string) =>
    request<LinkSuggestion>(
      `/api/articles/${id}/links/${suggestionId}/generate`,
      { method: "POST" }
    ),
  dismissLink: (id: string, suggestionId: string) =>
    request<LinkSuggestion>(
      `/api/articles/${id}/links/${suggestionId}/dismiss`,
      { method: "POST" }
    ),
  linkHealth: (id: string) =>
    request<BrokenLink[]>(`/api/articles/${id}/links/health`),
  checkLinks: (id: string) =>
    request<BrokenLink[]>(`/api/articles/${id}/links/check`, { method: "POST" }),
  ignoreBrokenLink: (id: string, findingId: string) =>
    request<BrokenLink>(
      `/api/articles/${id}/links/health/${findingId}/ignore`,
      { method: "POST" }
    ),
};

/** A broken outbound link found in a published article (internal target gone /
 *  external 4xx-5xx / unreachable), surfaced for an editor to fix or ignore. */
export interface BrokenLink {
  id: string;
  business_id: string;
  article_id: string;
  url: string;
  anchor_text?: string | null;
  kind: "internal" | "external";
  http_status?: number | null;
  reason?: string | null;
  status: "open" | "ignored" | "resolved";
  checked_at?: string | null;
  created_at?: string | null;
}

/** A staged internal-link suggestion: an unlinked mention in this article that
 *  could link to another of the brand's published articles. */
export interface LinkSuggestion {
  id: string;
  business_id: string;
  article_id: string;
  target_article_id: string;
  anchor_text?: string | null; // null = a structural gap (no natural anchor yet)
  target_url: string;
  target_title?: string | null;
  reason?: string | null;
  kind: "mention" | "pillar" | "member";
  status: "pending" | "accepted" | "dismissed";
  created_at?: string | null;
}

/** Per-brand schedule for the monthly re-linking scout (M6 / Phase 12.3). */
export interface RelinkConfig {
  business_id: string;
  enabled: boolean;
  cadence: "weekly" | "monthly";
  last_run_at?: string | null;
  next_run_at?: string | null;
  last_found: number;
  updated_at?: string | null;
}

export const relinkApi = {
  get: (businessId: string) =>
    request<RelinkConfig>(`/api/business-profiles/${businessId}/relink`),
  update: (
    businessId: string,
    body: { enabled?: boolean; cadence?: "weekly" | "monthly" }
  ) =>
    request<RelinkConfig>(`/api/business-profiles/${businessId}/relink`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  run: (businessId: string) =>
    request<{ status: string }>(
      `/api/business-profiles/${businessId}/relink/run`,
      { method: "POST" }
    ),
};

// --- Auth / membership ---
export type Role = "writer" | "editor" | "admin";

export interface Profile {
  id: string;
  email?: string | null;
  display_name?: string | null;
  role: Role;
  created_at?: string;
  updated_at?: string;
}

export const accountApi = {
  me: () => request<Profile>("/api/me"),
  members: () => request<Profile[]>("/api/members"),
  setRole: (id: string, role: Role) =>
    request<Profile>(`/api/members/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),
};

// --- Review comments ---
export interface Comment {
  id: string;
  article_id: string;
  author_id?: string | null;
  author_email?: string | null;
  author_name?: string | null;
  body: string;
  anchor?: string | null;
  resolved: boolean;
  created_at: string;
  updated_at: string;
}

/** Whether a role may approve/publish (the editorial gate). */
export function canApprove(role?: Role | null): boolean {
  return role === "editor" || role === "admin";
}

// --- Content scouts (M5) ---
export type Autonomy = "suggest" | "auto_draft";
export type OpportunityStatus =
  | "new"
  | "queued"
  | "drafting"
  | "drafted"
  | "dismissed";

export interface ScoutConfig {
  business_id: string;
  enabled: boolean;
  cadence: "twice_daily" | "daily" | "weekly";
  autonomy: Autonomy;
  min_score: number;
  max_drafts_per_run: number;
  focus: string[];
  last_run_at?: string | null;
  next_run_at?: string | null;
  updated_at?: string | null;
}

export interface ScoutRunProgress {
  phase?: string;
  message?: string;
  considered?: string[];
  drafted?: number;
  total?: number;
}

export type PlanSource = "news" | "youtube" | "social" | "web";
export interface PlanQuery {
  query: string;
  source: PlanSource;
  rationale?: string | null;
}
export interface ScoutPlan {
  themes: string[];
  queries: PlanQuery[];
  edited: boolean;
}

export interface ScoutRun {
  id: string;
  business_id: string;
  status: "planned" | "running" | "done" | "failed";
  trigger: "schedule" | "manual";
  found: number;
  drafted: number;
  error?: string | null;
  progress?: ScoutRunProgress;
  plan?: ScoutPlan | null;
  created_at: string;
}

export interface Opportunity {
  id: string;
  business_id: string;
  scout_run_id?: string | null;
  title: string;
  angle?: string | null;
  why_now?: string | null;
  keyword?: string | null;
  source_type?: string | null;
  source_url?: string | null;
  evidence: Record<string, unknown>;
  score: number;
  scores: Record<string, unknown>;
  status: OpportunityStatus;
  article_id?: string | null;
  cluster_id?: string | null;
  cluster_role?: "pillar" | "member" | null;
  progress?: { phase?: string; message?: string };
  created_at: string;
  updated_at: string;
}

export const scoutsApi = {
  config: (businessId: string) =>
    request<ScoutConfig>(`/api/scouts/config?business_id=${businessId}`),
  updateConfig: (businessId: string, data: Partial<ScoutConfig>) =>
    request<ScoutConfig>(`/api/scouts/config?business_id=${businessId}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  run: (businessId: string) =>
    request<{ status: string }>(`/api/scouts/run?business_id=${businessId}`, {
      method: "POST",
    }),
  runs: (businessId: string) =>
    request<ScoutRun[]>(`/api/scouts/runs?business_id=${businessId}`),
  // Two-phase manual run: plan → review/edit → execute.
  plan: (businessId: string) =>
    request<ScoutRun>(`/api/scouts/plan?business_id=${businessId}`, {
      method: "POST",
    }),
  getRun: (runId: string) => request<ScoutRun>(`/api/scouts/runs/${runId}`),
  updatePlan: (runId: string, plan: ScoutPlan) =>
    request<ScoutRun>(`/api/scouts/runs/${runId}/plan`, {
      method: "PATCH",
      body: JSON.stringify(plan),
    }),
  execute: (runId: string) =>
    request<{ status: string }>(`/api/scouts/runs/${runId}/execute`, {
      method: "POST",
    }),
};

export const opportunitiesApi = {
  list: (businessId: string) =>
    request<Opportunity[]>(`/api/opportunities?business_id=${businessId}`),
  draft: (id: string) =>
    request<Opportunity>(`/api/opportunities/${id}/draft`, { method: "POST" }),
  dismiss: (id: string) =>
    request<Opportunity>(`/api/opportunities/${id}/dismiss`, { method: "POST" }),
  restore: (id: string) =>
    request<Opportunity>(`/api/opportunities/${id}/restore`, { method: "POST" }),
  remove: (id: string) =>
    request<void>(`/api/opportunities/${id}`, { method: "DELETE" }),
};

// --- Content clusters (topical authority) ---
export interface ClusterMember {
  id: string;
  title: string;
  slug?: string | null;
  status: string;
  cluster_role?: "pillar" | "member" | null;
  canonical_url?: string | null;
}
export interface ContentCluster {
  id: string;
  business_id: string;
  label: string;
  theme?: string | null;
  pillar_article_id?: string | null;
  pillar_locked: boolean;
  pillar_title?: string | null;
  member_count: number;
  created_at?: string | null;
  updated_at?: string | null;
}
export interface ClusterDetail extends ContentCluster {
  members: ClusterMember[];
}

export const clustersApi = {
  list: (businessId: string) =>
    request<ContentCluster[]>(
      `/api/business-profiles/${businessId}/clusters`
    ),
  get: (clusterId: string) =>
    request<ClusterDetail>(`/api/clusters/${clusterId}`),
  setPillar: (clusterId: string, articleId: string) =>
    request<ClusterDetail>(`/api/clusters/${clusterId}/pillar`, {
      method: "POST",
      body: JSON.stringify({ article_id: articleId }),
    }),
  analyzeGaps: (clusterId: string) =>
    request<{ created: number }>(
      `/api/clusters/${clusterId}/analyze-gaps`,
      { method: "POST" }
    ),
  backfill: (businessId: string) =>
    request<{ assigned: number }>(
      `/api/business-profiles/${businessId}/clusters/backfill`,
      { method: "POST" }
    ),
  remove: (clusterId: string) =>
    request<void>(`/api/clusters/${clusterId}`, { method: "DELETE" }),
};

// --- Publishing / export (M8) ---
export type PublishTarget = "export" | "webhook";

export interface Publication {
  id: string;
  article_id: string;
  target_type: string;
  status: "pending" | "success" | "failed";
  url?: string | null;
  external_id?: string | null;
  published_at?: string | null;
  created_at: string;
}

export const publishApi = {
  publish: (id: string, body: { target_type: PublishTarget; config?: Record<string, unknown> }) =>
    request<Publication>(`/api/articles/${id}/publish`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  publications: (id: string) =>
    request<Publication[]>(`/api/articles/${id}/publications`),
  unpublish: (id: string) =>
    request<Article>(`/api/articles/${id}/unpublish`, { method: "POST" }),
};

/** Export an article as raw text (markdown/html) with the auth header attached.
 *  Mirrors request()'s 401 → refresh → retry so export doesn't break on token expiry. */
export async function exportArticle(
  id: string,
  format: "markdown" | "html",
  retry = false
): Promise<string> {
  const token = getAccessToken();
  const res = await fetch(
    `${API_BASE_URL}/api/articles/${id}/export?format=${format}`,
    {
      cache: "no-store",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }
  );
  if (res.status === 401 && !retry && getSession()) {
    const ns = await refresh();
    if (ns) return exportArticle(id, format, true);
  }
  if (!res.ok) throw new ApiError(res.status, `Export failed (${res.status})`);
  return res.text();
}

export interface ArticleUpdate {
  title?: string;
  content_md?: string;
  meta_title?: string;
  meta_description?: string;
  status?: string;
  canonical_url?: string;
}

// --- Brief (Stage B) ---
export interface ContentTemplate {
  id: string;
  type: string;
  label: string;
  outline_guidance: string;
  schema_org_type: string;
  default_word_count?: number | null;
  geo_target: number;
  enabled: boolean;
}

export const templatesApi = {
  list: () => request<ContentTemplate[]>("/api/templates"),
};

export interface Brief {
  id: string;
  business_id?: string | null;
  research_run_id?: string | null;
  article_type?: string | null;
  topic: string;
  primary_keyword?: string | null;
  secondary_keywords: string[];
  target_word_count?: number | null;
  headings: string[];
  entities: string[];
  questions: string[];
  link_suggestions: { internal?: string[]; external?: string[] };
  suggested_title?: string | null;
  suggested_meta?: string | null;
  created_at: string;
  updated_at: string;
}

export const briefsApi = {
  listByBrand: (businessId: string) =>
    request<Brief[]>(`/api/briefs?business_id=${businessId}`),
  get: (id: string) => request<Brief>(`/api/briefs/${id}`),
  generate: (researchRunId: string, articleType?: string) =>
    request<Brief>("/api/briefs", {
      method: "POST",
      body: JSON.stringify({
        research_run_id: researchRunId,
        article_type: articleType,
      }),
    }),
  update: (id: string, data: Partial<Omit<Brief, "id" | "created_at" | "updated_at">>) =>
    request<Brief>(`/api/briefs/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
};
