/**
 * Typed API client.
 *
 * Handles token refresh transparently: a 401 triggers one refresh attempt and
 * a replay of the original request. Concurrent 401s share a single refresh
 * promise so a page with six queries does not fire six refreshes.
 *
 * The Anthropic key lives only on the server. Nothing in this file, or anywhere
 * in the browser bundle, ever holds a model credential.
 */

import type { ApiError } from './types'

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

let accessToken: string | null = null
let refreshPromise: Promise<boolean> | null = null

export function setAccessToken(token: string | null) {
  accessToken = token
  if (token) sessionStorage.setItem('resumeiq.token', token)
  else sessionStorage.removeItem('resumeiq.token')
}

export function getAccessToken(): string | null {
  if (!accessToken) accessToken = sessionStorage.getItem('resumeiq.token')
  return accessToken
}

export class ApiRequestError extends Error {
  code: string
  status: number
  requestId?: string
  fields?: { field: string; message: string }[]

  constructor(status: number, error: ApiError) {
    super(error.message)
    this.name = 'ApiRequestError'
    this.status = status
    this.code = error.code
    this.requestId = error.request_id
    this.fields = error.fields
  }
}

async function refreshSession(): Promise<boolean> {
  if (refreshPromise) return refreshPromise
  refreshPromise = (async () => {
    try {
      const res = await fetch(`${BASE}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      if (!res.ok) return false
      const data = await res.json()
      setAccessToken(data.access_token)
      return true
    } catch {
      return false
    } finally {
      refreshPromise = null
    }
  })()
  return refreshPromise
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  retryOn401?: boolean
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, retryOn401 = true, ...init } = options
  const token = getAccessToken()

  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string>),
  }
  if (token) headers.Authorization = `Bearer ${token}`

  let payload: BodyInit | undefined
  if (body instanceof FormData) {
    payload = body
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
    body: payload,
    credentials: 'include',
  })

  if (res.status === 401 && retryOn401) {
    if (await refreshSession()) {
      return request<T>(path, { ...options, retryOn401: false })
    }
    setAccessToken(null)
    window.dispatchEvent(new CustomEvent('resumeiq:session-expired'))
  }

  if (res.status === 204) return undefined as T

  const text = await res.text()
  const data = text ? JSON.parse(text) : null

  if (!res.ok) {
    throw new ApiRequestError(
      res.status,
      data?.error ?? { code: 'unknown', message: 'Something went wrong.' },
    )
  }
  return data as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

/**
 * Subscribe to screening progress over SSE.
 * Returns an unsubscribe function.
 */
export function subscribeToScreening(
  batchId: string,
  handlers: {
    onProgress?: (data: ProgressEvent_) => void
    onComplete?: (data: ProgressEvent_) => void
    onFailed?: (data: ProgressEvent_) => void
  },
): () => void {
  const source = new EventSource(`${BASE}/screenings/${batchId}/events`, {
    withCredentials: true,
  })

  const parse = (e: MessageEvent) => JSON.parse(e.data) as ProgressEvent_

  source.addEventListener('progress', (e) => handlers.onProgress?.(parse(e as MessageEvent)))
  source.addEventListener('completed', (e) => {
    handlers.onComplete?.(parse(e as MessageEvent))
    source.close()
  })
  source.addEventListener('failed', (e) => {
    handlers.onFailed?.(parse(e as MessageEvent))
    source.close()
  })
  source.onerror = () => source.close()

  return () => source.close()
}

export interface ProgressEvent_ {
  status: string
  processed: number
  total: number
  quarantined: number
  failed: number
  progress: number
}
