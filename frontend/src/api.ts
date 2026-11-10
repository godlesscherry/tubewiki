// Typed API client (v2 lean API). Same-origin paths are proxied by Vite or served by FastAPI.
import type {
  ConfigOut,
  SettingsOut,
  SettingsUpdate,
  TestLLMOut,
  DedupeOut,
  IngestResponse,
  JobOut,
  SourcesOut,
} from './types'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = data.detail ?? detail
    } catch {
      /* ignore */
    }
    throw new ApiError(typeof detail === 'string' ? detail : JSON.stringify(detail), res.status)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
  return parts.length ? `?${parts.join('&')}` : ''
}

export const api = {
  health: () => request<{ ok: boolean; version: string }>('/api/health'),
  config: () => request<ConfigOut>('/api/config'),

  ingest: (urls: string[], force = false) =>
    request<IngestResponse>('/api/ingest', {
      method: 'POST',
      body: JSON.stringify({ urls, force }),
    }),
  jobs: () => request<JobOut[]>('/api/jobs'),
  job: (id: string) => request<JobOut>(`/api/jobs/${encodeURIComponent(id)}`),

  sources: (params: { q?: string; status?: string } = {}) =>
    request<SourcesOut>(`/api/sources${qs(params)}`),
  check: (url: string) => request<DedupeOut>(`/api/sources/check${qs({ url })}`),
  deleteSource: (videoId: string) =>
    request<{ deleted: boolean }>(`/api/sources/${encodeURIComponent(videoId)}`, {
      method: 'DELETE',
    }),

  settings: () => request<SettingsOut>('/api/settings'),
  updateSettings: (payload: SettingsUpdate) =>
    request<SettingsOut>('/api/settings', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  testLLM: () => request<TestLLMOut>('/api/settings/test-llm', { method: 'POST' }),
}
