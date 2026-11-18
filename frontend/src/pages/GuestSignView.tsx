import { useEffect, useMemo, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { GuestDocument } from '../api/types'
import { SignaturePad } from '../components/SignaturePad'

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

export function GuestSignView() {
  const tokenRef = useRef(new URLSearchParams(window.location.hash.slice(1)).get('token') || '')
  const guestRequestRef = useRef<Promise<[GuestDocument, Blob]> | null>(null)
  const [guestDocument, setGuestDocument] = useState<GuestDocument | null>(null)
  const [pdfUrl, setPdfUrl] = useState('')
  const [pageCount, setPageCount] = useState(0)
  const [viewerWidth, setViewerWidth] = useState(720)
  const viewerRef = useRef<HTMLDivElement>(null)
  const [signingFieldId, setSigningFieldId] = useState<string | null>(null)
  const [signatures, setSignatures] = useState<Record<string, string>>({})
  const [reason, setReason] = useState('')
  const [loading, setLoading] = useState(() =>
    Boolean(new URLSearchParams(window.location.hash.slice(1)).get('token')),
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(() =>
    new URLSearchParams(window.location.hash.slice(1)).get('token')
      ? ''
      : 'This signing link is missing its secure token. Please open the complete link from your invitation.',
  )
  const [complete, setComplete] = useState(false)
  const [rejected, setRejected] = useState(false)

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
    const params = new URLSearchParams(window.location.hash.slice(1))
    const rawToken = tokenRef.current || params.get('token') || ''
    tokenRef.current = rawToken
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`)
    if (!rawToken) return
    let active = true
    let objectUrl = ''
    guestRequestRef.current ??= Promise.all([
      api.getGuestDocument(rawToken),
      api.getGuestPdf(rawToken),
    ])
    guestRequestRef.current
      .then(([document, pdf]) => {
        if (!active) return
        setGuestDocument(document)
        objectUrl = URL.createObjectURL(pdf)
        setPdfUrl(objectUrl)
      })
      .catch((failure: unknown) => {
        if (active) setError(failure instanceof Error ? failure.message : 'Could not open this signing link.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [])

  const requiredFields = useMemo(
    () => guestDocument?.fields.filter((field) => field.required) ?? [],
    [guestDocument],
  )
  const unsignedRequiredCount = requiredFields.filter((field) => !signatures[field.id]).length

  async function submitSignature() {
    if (!tokenRef.current || !guestDocument || unsignedRequiredCount > 0) return
    setBusy(true)
    setError('')
    try {
      await api.completeSigning(tokenRef.current, Object.entries(signatures).map(([field_id, signature_image_base64]) => ({
        field_id,
        signature_image_base64,
      })))
      setComplete(true)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not submit your signature.')
    } finally {
      setBusy(false)
    }
  }

  async function submitRejection() {
    if (!tokenRef.current || !reason.trim()) return
    setBusy(true)
    setError('')
    try {
      await api.rejectSigning(tokenRef.current, reason.trim())
      setRejected(true)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not decline this document.')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return <main className="guest-page"><div className="loading-state centered"><span className="spinner" /> Opening secure signing link…</div></main>
  }

  if (complete || rejected) {
    return (
      <main className="guest-page guest-terminal">
        <div className={`terminal-icon ${rejected ? 'terminal-declined' : ''}`}>{rejected ? '↗' : '✓'}</div>
        <p className="eyebrow">{rejected ? 'RESPONSE RECORDED' : 'SIGNING COMPLETE'}</p>
        <h1>{rejected ? 'You declined this document' : 'You’re all done'}</h1>
        <p className="muted">{rejected ? 'The sender has been notified of your decision.' : 'Your signature has been securely added. The sender will be notified.'}</p>
      </main>
    )
  }

  if (error && !guestDocument) {
    return (
      <main className="guest-page guest-terminal">
        <Link className="brand auth-brand" to="/">papertrail</Link>
        <div className="terminal-icon terminal-declined">!</div>
        <p className="eyebrow">LINK UNAVAILABLE</p>
        <h1>We can’t open this document</h1>
        <p className="muted" role="alert">{error}</p>
      </main>
    )
  }
  if (!guestDocument) return null

  return (
    <div className="guest-page">
      <header className="guest-topbar">
        <a className="brand" href="/" aria-label="Papertrail"><span className="brand-mark">P</span><span>papertrail</span></a>
        <span className="secure-label"><span>◆</span> Secure signing</span>
      </header>
      <main className="guest-content">
        <section className="guest-heading">
          <div>
            <p className="eyebrow">DOCUMENT FOR YOUR SIGNATURE</p>
            <h1>{guestDocument.title}</h1>
            <p className="muted">Hello {guestDocument.recipient_name}, review the document and sign the marked fields.</p>
          </div>
          <span className="secure-pill">🔒 Private link</span>
        </section>
        {error && <div className="inline-error" role="alert">{error}</div>}
        <div className="guest-layout">
          <section className="guest-pdf-viewer" ref={viewerRef}>
            <div className="guest-viewer-toolbar">
              <span>{guestDocument.page_count} {guestDocument.page_count === 1 ? 'page' : 'pages'}</span>
              <span>{guestDocument.fields.length} signature {guestDocument.fields.length === 1 ? 'field' : 'fields'}</span>
            </div>
            <div className="pdf-pages">
              {pdfUrl && (
                <Document
                  file={pdfUrl}
                  onLoadSuccess={({ numPages }) => setPageCount(numPages)}
                  onLoadError={(failure) => setError(failure.message)}
                  loading={<div className="loading-state"><span className="spinner" /> Loading PDF…</div>}
                  error={<div className="inline-error">This PDF could not be displayed.</div>}
                >
                  {Array.from({ length: pageCount }, (_, index) => (
                    <div className="pdf-page-wrap" key={index + 1}>
                      <div className="pdf-page-layer" style={{ width: viewerWidth }}>
                        <Page pageNumber={index + 1} width={viewerWidth} renderTextLayer={false} renderAnnotationLayer={false} />
                        <div className="field-overlay">
                          {guestDocument.fields.filter((field) => field.page_number === index + 1).map((field) => (
                            <button
                              className={`signature-field guest-signature-field ${signatures[field.id] ? 'signature-field-done' : ''}`}
                              key={field.id}
                              style={{
                                left: `${field.x_norm * 100}%`,
                                top: `${field.y_norm * 100}%`,
                                width: `${field.width_norm * 100}%`,
                                height: `${field.height_norm * 100}%`,
                              }}
                              onClick={() => setSigningFieldId(field.id)}
                            >
                              <span>{signatures[field.id] ? 'Signed ✓' : `✎ ${field.label}`}</span>
                              {signatures[field.id] && <img src={signatures[field.id]} alt="Your signature" />}
                            </button>
                          ))}
                        </div>
                      </div>
                      <span className="pdf-page-number">Page {index + 1}</span>
                    </div>
                  ))}
                </Document>
              )}
            </div>
          </section>
          <aside className="guest-sidebar">
            <section className="sidebar-card">
              <p className="eyebrow">YOUR SIGNATURES</p>
              <h2>Fields to complete</h2>
              <p className="muted">Select a highlighted field on the document to draw your signature.</p>
              <ul className="guest-field-list">
                {guestDocument.fields.map((field) => (
                  <li key={field.id}>
                    <span className={`field-check ${signatures[field.id] ? 'field-check-done' : ''}`}>{signatures[field.id] ? '✓' : ''}</span>
                    <span>{field.label}<small>Page {field.page_number} · {field.required ? 'Required' : 'Optional'}</small></span>
                    <button className="text-button" onClick={() => setSigningFieldId(field.id)}>
                      {signatures[field.id] ? 'Redo' : 'Sign'}
                    </button>
                  </li>
                ))}
              </ul>
              <button className="button button-primary button-full" disabled={busy || unsignedRequiredCount > 0 || Object.keys(signatures).length === 0} onClick={() => void submitSignature()}>
                {busy ? 'Submitting…' : 'Finish signing'}
              </button>
              {unsignedRequiredCount > 0 && <p className="form-hint">{unsignedRequiredCount} required {unsignedRequiredCount === 1 ? 'field' : 'fields'} remaining</p>}
            </section>
            <details className="sidebar-card reject-card">
              <summary>Decline to sign</summary>
              <p className="muted">Let the sender know why you can’t sign this document.</p>
              <label className="field-label" htmlFor="decline-reason">Reason</label>
              <textarea id="decline-reason" className="text-input textarea-input" maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Share a brief reason…" />
              <button className="button button-danger button-full" disabled={busy || !reason.trim()} onClick={() => void submitRejection()}>
                {busy ? 'Sending…' : 'Decline document'}
              </button>
            </details>
          </aside>
        </div>
      </main>
      <footer className="site-footer"><span>Papertrail</span><span>Your signing link is private to you.</span></footer>
      {signingFieldId && (
        <SignaturePad
          onCancel={() => setSigningFieldId(null)}
          onSave={(image) => {
            setSignatures((current) => ({ ...current, [signingFieldId]: image }))
            setSigningFieldId(null)
          }}
        />
      )}
    </div>
  )
}
