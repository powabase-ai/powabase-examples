/**
 * Backend API client. Calls the FastAPI backend; the browser never holds Powabase
 * secrets (only the Anon key, used elsewhere for GoTrue/PostgREST).
 */

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    ...init,
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(`API ${res.status}: ${detail}`);
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

// --- Articles (Stage C) ---
export type GenerationStatus =
  | "queued"
  | "grounding"
  | "outlining"
  | "drafting"
  | "optimizing"
  | "done"
  | "failed";

export interface ArticleSummary {
  id: string;
  title: string;
  status: string;
  generation_status: GenerationStatus;
  progress: { phase?: string; total?: number; done?: number; word_count?: number };
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
  created_at: string;
}

export const TERMINAL_GENERATION: GenerationStatus[] = ["done", "failed"];

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
  update: (id: string, data: ArticleUpdate) =>
    request<Article>(`/api/articles/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
};

export interface ArticleUpdate {
  title?: string;
  content_md?: string;
  meta_title?: string;
  meta_description?: string;
  status?: string;
}

// --- Brief (Stage B) ---
export interface Brief {
  id: string;
  business_id?: string | null;
  research_run_id?: string | null;
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
  generate: (researchRunId: string) =>
    request<Brief>("/api/briefs", {
      method: "POST",
      body: JSON.stringify({ research_run_id: researchRunId }),
    }),
  update: (id: string, data: Partial<Omit<Brief, "id" | "created_at" | "updated_at">>) =>
    request<Brief>(`/api/briefs/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
};
