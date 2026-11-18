export type DocumentStatus = 'DRAFT' | 'PENDING' | 'SIGNED' | 'REJECTED'

export interface User {
  id: string
  name: string
  email: string
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: User
}

export interface DocumentRecord {
  id: string
  title: string
  original_filename: string
  original_sha256: string
  signed_sha256: string | null
  page_count: number
  file_size_bytes: number
  status: DocumentStatus
  rejection_reason: string | null
  completed_at: string | null
  created_at: string
  updated_at: string
}

export interface SignatureFieldInput {
  page_number: number
  x_norm: number
  y_norm: number
  width_norm: number
  height_norm: number
  label: string
  required: boolean
}

export interface SignatureField extends SignatureFieldInput {
  id: string
  document_id: string
  created_at: string
}

export interface AuditEvent {
  id: string
  document_id: string
  actor_type: 'SENDER' | 'GUEST' | 'SYSTEM'
  actor_id: string | null
  actor_email: string | null
  event_type: string
  ip_address: string | null
  user_agent: string | null
  metadata_json: Record<string, unknown> | null
  created_at: string
}

export interface GuestDocument {
  title: string
  page_count: number
  recipient_name: string
  recipient_email: string
  status: DocumentStatus
  fields: SignatureField[]
  file_url: string
}

export interface SendDocumentResponse {
  document_id: string
  status: DocumentStatus
  recipient_name: string
  recipient_email: string
  delivery_status: 'PENDING' | 'SENT' | 'FAILED'
  last_delivery_error: string | null
  expires_at: string
  signing_url: string
}
