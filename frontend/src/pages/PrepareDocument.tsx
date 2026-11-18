import { useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { DocumentRecord, SendDocumentResponse, SignatureFieldInput } from '../api/types'

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

interface DragState {
  pageNumber: number
  startX: number
  startY: number
  currentX: number
  currentY: number
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}

function PreparationPage({
  pageNumber,
  width,
  fields,
  onCreate,
  onRemove,
}: {
  pageNumber: number
  width: number
  fields: SignatureFieldInput[]
  onCreate: (field: SignatureFieldInput) => void
  onRemove: (index: number) => void
}) {
  const layerRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<DragState | null>(null)
  const [preview, setPreview] = useState<DragState | null>(null)
  const pageFields = fields
    .map((field, index) => ({ field, index }))
    .filter(({ field }) => field.page_number === pageNumber)

  function getPoint(event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    return {
      x: clamp((event.clientX - bounds.left) / bounds.width, 0, 1),
      y: clamp((event.clientY - bounds.top) / bounds.height, 0, 1),
    }
  }

  function start(event: PointerEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).closest('[data-field]')) return
    event.currentTarget.setPointerCapture(event.pointerId)
    const point = getPoint(event)
    const next = {
      pageNumber,
      startX: point.x,
      startY: point.y,
      currentX: point.x,
      currentY: point.y,
    }
    dragRef.current = next
    setPreview(next)
  }

  function move(event: PointerEvent<HTMLDivElement>) {
    if (!dragRef.current) return
    const point = getPoint(event)
    const next = { ...dragRef.current, currentX: point.x, currentY: point.y }
    dragRef.current = next
    setPreview(next)
  }

  function finish() {
    const drag = dragRef.current
    dragRef.current = null
    setPreview(null)
    if (!drag) return
    const bounds = layerRef.current?.getBoundingClientRect()
    if (!bounds) return
    const draggedEnough =
      Math.abs(drag.currentX - drag.startX) * bounds.width > 8 ||
      Math.abs(drag.currentY - drag.startY) * bounds.height > 8
    const widthNorm = draggedEnough
      ? clamp(Math.abs(drag.currentX - drag.startX), 0.14, 0.55)
      : 0.28
    const heightNorm = draggedEnough
      ? clamp(Math.abs(drag.currentY - drag.startY), 0.045, 0.2)
      : 0.075
    const x = draggedEnough ? Math.min(drag.startX, drag.currentX) : drag.startX - widthNorm / 2
    const y = draggedEnough ? Math.min(drag.startY, drag.currentY) : drag.startY - heightNorm / 2
    const field: SignatureFieldInput = {
      page_number: pageNumber,
      x_norm: clamp(x, 0, 1 - widthNorm),
      y_norm: clamp(y, 0, 1 - heightNorm),
      width_norm: widthNorm,
      height_norm: heightNorm,
      label: 'Signature',
      required: true,
    }
    onCreate(field)
  }

  return (
    <div className="pdf-page-wrap">
      <div
        className="pdf-page-layer"
        ref={layerRef}
        onPointerDown={start}
        onPointerMove={move}
        onPointerUp={finish}
        onPointerCancel={finish}
        style={{ width }}
      >
        <Page pageNumber={pageNumber} width={width} renderTextLayer={false} renderAnnotationLayer={false} />
        <div className="field-overlay" aria-label={`Signature fields on page ${pageNumber}`}>
          {pageFields.map(({ field, index }) => (
            <div
              className="signature-field"
              key={`${field.page_number}-${index}`}
              data-field
              style={{
                left: `${field.x_norm * 100}%`,
                top: `${field.y_norm * 100}%`,
                width: `${field.width_norm * 100}%`,
                height: `${field.height_norm * 100}%`,
              }}
            >
              <span>{field.label}</span>
              <button type="button" aria-label="Remove signature field" onPointerDown={(event) => event.stopPropagation()} onClick={() => onRemove(index)}>×</button>
            </div>
          ))}
          {preview && (
            <div
              className="signature-field signature-field-preview"
              style={{
                left: `${Math.min(preview.startX, preview.currentX) * 100}%`,
                top: `${Math.min(preview.startY, preview.currentY) * 100}%`,
                width: `${Math.abs(preview.currentX - preview.startX) * 100}%`,
                height: `${Math.abs(preview.currentY - preview.startY) * 100}%`,
              }}
            >
              <span>Signature</span>
            </div>
          )}
        </div>
      </div>
      <span className="pdf-page-number">Page {pageNumber}</span>
    </div>
  )
}

export function PrepareDocument() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const viewerRef = useRef<HTMLDivElement>(null)
  const [viewerWidth, setViewerWidth] = useState(720)
  const [document, setDocument] = useState<DocumentRecord | null>(null)
  const [pdfUrl, setPdfUrl] = useState('')
  const [fields, setFields] = useState<SignatureFieldInput[]>([])
  const [pageCount, setPageCount] = useState(0)
  const [recipientName, setRecipientName] = useState('')
  const [recipientEmail, setRecipientEmail] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [sent, setSent] = useState<SendDocumentResponse | null>(null)

  useEffect(() => {
    const element = viewerRef.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => {
      setViewerWidth(Math.max(180, Math.min(720, entry.contentRect.width - 32)))
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    let active = true
    let objectUrl = ''
    Promise.all([api.getDocument(id), api.getFields(id), api.getDocumentPdf(id)])
      .then(([doc, currentFields, pdf]) => {
        if (!active) return
        setDocument(doc)
        setFields(currentFields.map(({ page_number, x_norm, y_norm, width_norm, height_norm, label, required }) => ({
          page_number, x_norm, y_norm, width_norm, height_norm, label, required,
        })))
        objectUrl = URL.createObjectURL(pdf)
        setPdfUrl(objectUrl)
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : 'Could not load document.'))
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [id])

  const requiredCount = useMemo(() => fields.filter((field) => field.required).length, [fields])

  function removeField(index: number) {
    setFields((current) => current.filter((_, fieldIndex) => fieldIndex !== index))
  }

  async function saveFields() {
    if (!fields.length) throw new Error('Add at least one signature field before saving.')
    const savedFields = await api.saveFields(id, fields)
    setFields(savedFields.map(({ page_number, x_norm, y_norm, width_norm, height_norm, label, required }) => ({
      page_number, x_norm, y_norm, width_norm, height_norm, label, required,
    })))
  }

  async function saveOnly() {
    setBusy(true)
    setError('')
    try {
      await saveFields()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not save signature fields.')
    } finally {
      setBusy(false)
    }
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await saveFields()
      setSent(await api.sendDocument(id, recipientName.trim(), recipientEmail.trim()))
      setDocument((current) => current ? { ...current, status: 'PENDING' } : current)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not send this document.')
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <div className="loading-state centered"><span className="spinner" /> Loading document…</div>
  if (error && !document) return <div className="page-error"><p role="alert">{error}</p><Link to="/dashboard">Back to documents</Link></div>
  if (!document) return null

  return (
    <div className="studio-page">
      <header className="studio-topbar">
        <Link className="back-link" to="/dashboard">← Documents</Link>
        <div className="studio-document-title">
          <strong>{document.title}</strong>
          <span className="status-badge status-draft"><i />Draft</span>
        </div>
        <div className="studio-actions">
          <button className="button button-secondary button-small" onClick={() => void saveOnly()} disabled={busy}>
            {busy ? 'Saving…' : 'Save fields'}
          </button>
          <button className="button button-quiet button-small" onClick={() => navigate('/dashboard')}>Exit</button>
        </div>
      </header>
      <div className="studio-layout">
        <section className="studio-viewer" ref={viewerRef}>
          <div className="viewer-intro">
            <div>
              <p className="eyebrow">FIELD PREPARATION</p>
              <h1>Place signature fields</h1>
              <p className="muted">Click to add a field, or drag to draw its size. Coordinates scale with the page.</p>
            </div>
            <span className="field-count">{fields.length} {fields.length === 1 ? 'field' : 'fields'}</span>
          </div>
          {error && <div className="inline-error" role="alert">{error}</div>}
          <div className="pdf-pages">
            {pdfUrl && (
              <Document
                file={pdfUrl}
                onLoadSuccess={({ numPages }) => setPageCount(numPages)}
                onLoadError={(reason) => setError(reason.message)}
                loading={<div className="loading-state"><span className="spinner" /> Loading PDF…</div>}
                error={<div className="inline-error">This PDF could not be displayed.</div>}
              >
                {Array.from({ length: pageCount }, (_, index) => (
                  <PreparationPage
                    key={index + 1}
                    pageNumber={index + 1}
                    width={viewerWidth}
                    fields={fields}
                    onCreate={(field) => setFields((current) => [...current, field])}
                    onRemove={removeField}
                  />
                ))}
              </Document>
            )}
          </div>
        </section>
        <aside className="studio-sidebar">
          <div className="sidebar-card">
            <p className="eyebrow">SIGNATURE FIELDS</p>
            <h2>Ready to sign</h2>
            <p className="muted">Fields mark where your recipient should sign. You can place them on any page.</p>
            <div className="field-summary">
              <span className="summary-icon">✎</span>
              <span><strong>{fields.length} fields placed</strong><small>{requiredCount} required</small></span>
            </div>
            {fields.length > 0 && (
              <ul className="field-list">
                {fields.map((field, index) => (
                  <li key={`${field.page_number}-${index}`}>
                    <span>{field.label}<small>Page {field.page_number} · {field.required ? 'Required' : 'Optional'}</small></span>
                    <button className="text-button" onClick={() => removeField(index)} aria-label={`Remove field ${index + 1}`}>Remove</button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <form className="sidebar-card form-stack" onSubmit={send}>
            <p className="eyebrow">SEND FOR SIGNATURE</p>
            <h2>Who should sign?</h2>
            <label className="field-label" htmlFor="recipient-name">Recipient name</label>
            <input id="recipient-name" className="text-input" required maxLength={120} value={recipientName} onChange={(event) => setRecipientName(event.target.value)} placeholder="Jordan Lee" />
            <label className="field-label" htmlFor="recipient-email">Recipient email</label>
            <input id="recipient-email" className="text-input" type="email" required value={recipientEmail} onChange={(event) => setRecipientEmail(event.target.value)} placeholder="jordan@example.com" />
            <p className="privacy-note">We’ll email them a secure link to review and sign this document.</p>
            <button className="button button-primary button-full" disabled={busy || !fields.length}>
              {busy ? 'Sending…' : 'Save & send invitation'}
            </button>
          </form>
        </aside>
      </div>
      {sent && (
        <div className="modal-backdrop">
          <section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="sent-title">
            <div className="success-mark">✓</div>
            <p className="eyebrow">INVITATION {sent.delivery_status === 'SENT' ? 'SENT' : sent.delivery_status}</p>
            <h2 id="sent-title">Your document is on its way</h2>
            <p className="muted">A signing link was prepared for {sent.recipient_name} ({sent.recipient_email}).</p>
            {sent.delivery_status === 'FAILED' && <p className="inline-error">{sent.last_delivery_error || 'Email delivery failed. You can copy the link and share it directly.'}</p>}
            <label className="field-label" htmlFor="signing-link">Signing link</label>
            <div className="copy-link-row">
              <input id="signing-link" className="text-input" readOnly value={sent.signing_url} />
              <button className="button button-secondary" onClick={() => void navigator.clipboard.writeText(sent.signing_url)}>Copy</button>
            </div>
            <div className="modal-actions">
              <button className="button button-primary" onClick={() => navigate('/dashboard')}>Back to documents</button>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
