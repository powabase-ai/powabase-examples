/**
 * Backend API client. Server-side calls hit the FastAPI backend; the browser
 * never holds Powabase secrets (only the Anon key, for GoTrue/PostgREST).
 */

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function getBackendHealth(): Promise<{ status: string }> {
  const res = await fetch(`${API_BASE_URL}/health`, { cache: "no-store" });
  if (!res.ok) throw new Error(`backend ${res.status}`);
  return res.json();
}
