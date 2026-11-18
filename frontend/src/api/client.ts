import type {
  AuditEvent,
  DocumentRecord,
  GuestDocument,
  SendDocumentResponse,
  SignatureField,
  SignatureFieldInput,
  TokenResponse,
  User,
} from './types'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/$/, '')
const TOKEN_KEY = 'document-signature.access-token'

export class ApiError extends Error {
  readonly status: number

  constructor(
    message: string,
    status: number,
  ) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function storeToken(token: string | null): void {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token)
  } else {
    localStorage.removeItem(TOKEN_KEY)
  }
}

function getErrorMessage(body: unknown, fallback: string): string {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = body.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          typeof item === 'object' && item !== null && 'msg' in item
            ? String(item.msg)
            : String(item),
        )
        .join('; ')
    }
  }
  return fallback
}

async function requestAt<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const token = getStoredToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(url, { ...init, headers })
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw new ApiError(
      getErrorMessage(body, `Request failed (${response.status})`),
      response.status,
    )
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

async function guestRequestAt<T>(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('X-Signing-Token', token)
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw new ApiError(
      getErrorMessage(body, `Request failed (${response.status})`),
      response.status,
    )
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

async function guestBlob(path: string, token: string): Promise<Blob> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'X-Signing-Token': token },
  })
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw new ApiError(
      getErrorMessage(body, `Could not load PDF (${response.status})`),
      response.status,
    )
  }
  return response.blob()
}

function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  return requestAt<T>(`${API_BASE}${path}`, init)
}

async function apiBlob(path: string): Promise<Blob> {
  const headers = new Headers()
  const token = getStoredToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE}${path}`, { headers })
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw new ApiError(
      getErrorMessage(body, `Could not load PDF (${response.status})`),
      response.status,
    )
  }
  return response.blob()
}

export const api = {
  register: (payload: { name: string; email: string; password: string }) =>
    apiRequest<User>('/auth/register', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  login: (payload: { email: string; password: string }) =>
    apiRequest<TokenResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  me: () => apiRequest<User>('/auth/me'),

  listDocuments: (status?: string) =>
    apiRequest<DocumentRecord[]>(
      `/docs${status ? `?status=${encodeURIComponent(status)}` : ''}`,
    ),

  getDocument: (id: string) =>
    apiRequest<DocumentRecord>(`/docs/${encodeURIComponent(id)}`),

  uploadDocument: (file: File, title: string) => {
    const formData = new FormData()
    formData.append('file', file)
    if (title.trim()) formData.append('title', title.trim())
    return apiRequest<DocumentRecord>('/docs/upload', {
      method: 'POST',
      body: formData,
    })
  },

  getDocumentPdf: (id: string, version: 'original' | 'signed' = 'original') =>
    apiBlob(`/docs/${encodeURIComponent(id)}/file?version=${version}`),

  getAudit: (id: string) =>
    apiRequest<AuditEvent[]>(`/audit/${encodeURIComponent(id)}`),

  getFields: (id: string) =>
    apiRequest<SignatureField[]>(`/docs/${encodeURIComponent(id)}/fields`),

  saveFields: (id: string, fields: SignatureFieldInput[]) =>
    apiRequest<SignatureField[]>(`/docs/${encodeURIComponent(id)}/fields`, {
      method: 'PUT',
      body: JSON.stringify({ fields }),
    }),

  sendDocument: (id: string, recipient_name: string, recipient_email: string) =>
    apiRequest<SendDocumentResponse>(`/docs/${encodeURIComponent(id)}/send`, {
      method: 'POST',
      body: JSON.stringify({ recipient_name, recipient_email }),
    }),

  getGuestDocument: (token: string) =>
    guestRequestAt<GuestDocument>('/public/sign', token),

  getGuestPdf: (token: string) => guestBlob('/public/sign/file', token),

  completeSigning: (
    token: string,
    signatures: { field_id: string; signature_image_base64: string }[],
  ) =>
    guestRequestAt<{ document_id: string; status: string; signed_sha256: string }>(
      '/public/sign/complete',
      token,
      { method: 'POST', body: JSON.stringify({ signatures }) },
    ),

  rejectSigning: (token: string, reason: string) =>
    guestRequestAt<{ document_id: string; status: string; rejection_reason: string }>(
      '/public/sign/reject',
      token,
      { method: 'POST', body: JSON.stringify({ reason }) },
    ),
}
