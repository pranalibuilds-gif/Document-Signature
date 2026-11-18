import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { AuditEvent, DocumentRecord, DocumentStatus } from '../api/types'
import { UploadModal } from '../components/UploadModal'

const filters: { label: string; value: string }[] = [
  { label: 'All documents', value: '' },
  { label: 'Drafts', value: 'DRAFT' },
  { label: 'Waiting for signature', value: 'PENDING' },
  { label: 'Completed', value: 'SIGNED' },
  { label: 'Declined', value: 'REJECTED' },
]

const statusLabels: Record<DocumentStatus, string> = {
  DRAFT: 'Draft',
  PENDING: 'Awaiting signature',
  SIGNED: 'Signed',
  REJECTED: 'Declined',
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(new Date(value))
}

function formatBytes(bytes: number) {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatEvent(value: string) {
  return value
    .toLowerCase()
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

export function Dashboard() {
  const navigate = useNavigate()
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [selected, setSelected] = useState<DocumentRecord | null>(null)
  const [audit, setAudit] = useState<AuditEvent[]>([])
  const [filter, setFilter] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [showUpload, setShowUpload] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [auditError, setAuditError] = useState('')

  useEffect(() => {
    let active = true
    api.listDocuments(filter || undefined)
      .then((result) => {
        if (!active) return
        setDocuments(result)
        setError('')
        setSelected((current) =>
          current ? result.find((document) => document.id === current.id) ?? null : null,
        )
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Could not load documents.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [filter, refreshKey])

  const selectedId = selected?.id
  useEffect(() => {
    if (!selectedId) return
    let active = true
    api.getAudit(selectedId)
      .then((events) => {
        if (active) {
          setAudit(events)
          setAuditError('')
        }
      })
      .catch((reason: unknown) => {
        if (active) setAuditError(reason instanceof Error ? reason.message : 'Could not load the audit trail.')
      })
    return () => {
      active = false
    }
  }, [selectedId])

  function onUploaded(document: DocumentRecord) {
    setShowUpload(false)
    setLoading(true)
    setRefreshKey((current) => current + 1)
    navigate(`/docs/${document.id}/prepare`)
  }

  function chooseFilter(value: string) {
    setLoading(true)
    setSelected(null)
    setAudit([])
    setAuditError('')
    setFilter(value)
  }

  async function downloadSignedDocument(document: DocumentRecord) {
    setError('')
    try {
      const blob = await api.getDocumentPdf(document.id, 'signed')
      const url = URL.createObjectURL(blob)
      const anchor = window.document.createElement('a')
      anchor.href = url
      anchor.download = document.original_filename
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not download the signed PDF.')
    }
  }

  return (
    <>
      <section className="dashboard-heading">
        <div>
          <p className="eyebrow">YOUR WORKSPACE</p>
          <h1>Documents</h1>
          <p className="muted">Prepare, send, and keep track of every agreement.</p>
        </div>
        <button className="button button-primary" onClick={() => setShowUpload(true)}>
          <span aria-hidden="true">＋</span> Upload a PDF
        </button>
      </section>

      <div className="dashboard-grid">
        <section className="panel document-panel">
          <div className="panel-heading">
            <div>
              <h2>All your documents</h2>
              <p className="muted">{documents.length} {documents.length === 1 ? 'document' : 'documents'}</p>
            </div>
            <select
              className="select-input"
              aria-label="Filter documents by status"
              value={filter}
              onChange={(event) => chooseFilter(event.target.value)}
            >
              {filters.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </div>
          {error && <div className="inline-error" role="alert">{error}</div>}
          {loading ? (
            <div className="loading-state"><span className="spinner" /> Loading documents…</div>
          ) : documents.length === 0 ? (
            <div className="empty-state">
              <span className="empty-icon">▤</span>
              <h3>No documents here yet</h3>
              <p>{filter ? 'Try another filter, or upload a new PDF.' : 'Upload a PDF to start a new signing request.'}</p>
              <button className="button button-secondary" onClick={() => setShowUpload(true)}>Upload a PDF</button>
            </div>
          ) : (
            <div className="document-list">
              {documents.map((document) => (
                <button
                  type="button"
                  className={`document-row ${selected?.id === document.id ? 'document-row-selected' : ''}`}
                  key={document.id}
                  onClick={() => {
                    setAudit([])
                    setAuditError('')
                    setSelected(document)
                  }}
                >
                  <span className="file-icon">PDF</span>
                  <span className="document-main">
                    <strong>{document.title}</strong>
                    <span>{document.original_filename} · {formatBytes(document.file_size_bytes)}</span>
                  </span>
                  <span className={`status-badge status-${document.status.toLowerCase()}`}>
                    <i />{statusLabels[document.status]}
                  </span>
                  <span className="document-date">{formatDate(document.updated_at)}</span>
                </button>
              ))}
            </div>
          )}
        </section>

        <aside className="panel detail-panel">
          {selected ? (
            <>
              <div className="detail-topline">
                <span className="eyebrow">DOCUMENT DETAILS</span>
                <span className={`status-badge status-${selected.status.toLowerCase()}`}>
                  <i />{statusLabels[selected.status]}
                </span>
              </div>
              <h2 className="detail-title">{selected.title}</h2>
              <p className="detail-filename">{selected.original_filename}</p>
              <div className="metadata-grid">
                <div><span>Pages</span><strong>{selected.page_count}</strong></div>
                <div><span>File size</span><strong>{formatBytes(selected.file_size_bytes)}</strong></div>
                <div><span>Created</span><strong>{formatDate(selected.created_at)}</strong></div>
                <div><span>Last updated</span><strong>{formatDate(selected.updated_at)}</strong></div>
              </div>
              {selected.rejection_reason && (
                <div className="rejection-note">
                  <strong>Reason for decline</strong>
                  <p>{selected.rejection_reason}</p>
                </div>
              )}
              {selected.status === 'DRAFT' && (
                <Link className="button button-primary button-full" to={`/docs/${selected.id}/prepare`}>
                  Prepare document <span aria-hidden="true">→</span>
                </Link>
              )}
              {selected.status === 'SIGNED' && (
                <button className="button button-secondary button-full" onClick={() => void downloadSignedDocument(selected)}>
                  Download signed PDF <span aria-hidden="true">↓</span>
                </button>
              )}
              <div className="audit-section">
                <div className="section-title-row">
                  <h3>Activity</h3>
                  <span className="activity-count">{audit.length}</span>
                </div>
                {auditError && <p className="form-error">{auditError}</p>}
                {!auditError && audit.length === 0 ? (
                  <p className="muted small-copy">No activity recorded yet.</p>
                ) : (
                  <ol className="audit-list">
                    {audit.map((event) => (
                      <li key={event.id} className="audit-item">
                        <span className="audit-dot" />
                        <div>
                          <strong>{formatEvent(event.event_type)}</strong>
                          <p>{event.actor_email || event.actor_type.toLowerCase()}</p>
                          <time dateTime={event.created_at}>{new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(event.created_at))}</time>
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            </>
          ) : (
            <div className="detail-placeholder">
              <span className="empty-icon">◫</span>
              <h3>Select a document</h3>
              <p>Choose an item to see its details and activity.</p>
            </div>
          )}
        </aside>
      </div>
      {showUpload && <UploadModal onClose={() => setShowUpload(false)} onUploaded={onUploaded} />}
    </>
  )
}
