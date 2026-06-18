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
