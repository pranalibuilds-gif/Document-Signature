import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { DocumentRecord } from '../api/types'

const MAX_FILE_SIZE = 10 * 1024 * 1024

interface UploadModalProps {
  onClose: () => void
  onUploaded: (document: DocumentRecord) => void
}

export function UploadModal({ onClose, onUploaded }: UploadModalProps) {
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  function selectFile(selected: File | undefined) {
    setError('')
    if (!selected) {
      setFile(null)
      return
    }
    if (
      selected.type !== 'application/pdf' &&
      !selected.name.toLowerCase().endsWith('.pdf')
    ) {
      setError('Choose a PDF file to continue.')
      setFile(null)
      return
    }
    if (selected.size > MAX_FILE_SIZE) {
      setError('PDFs must be 10 MiB or smaller.')
      setFile(null)
      return
    }
    setFile(selected)
    if (!title) setTitle(selected.name.replace(/\.pdf$/i, ''))
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file) {
      setError('Choose a PDF file to continue.')
      return
    }
    setBusy(true)
    setError('')
    try {
      onUploaded(await api.uploadDocument(file, title))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Upload failed.')
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="upload-title"
      >
        <div className="modal-heading">
          <div>
            <p className="eyebrow">NEW DOCUMENT</p>
            <h2 id="upload-title">Upload a PDF</h2>
          </div>
          <button className="icon-button" aria-label="Close upload dialog" onClick={onClose}>
            ×
          </button>
        </div>
        <p className="muted">Start with a PDF, then place signature fields before sending.</p>
        <form className="form-stack" onSubmit={submit}>
          <label className="field-label" htmlFor="document-file">PDF file</label>
          <label className="dropzone" htmlFor="document-file">
            <span className="dropzone-icon">↑</span>
            <strong>{file ? file.name : 'Choose a PDF from your device'}</strong>
            <span>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MiB` : 'Maximum file size: 10 MiB'}</span>
          </label>
          <input
            id="document-file"
            className="sr-only"
            type="file"
            accept="application/pdf,.pdf"
            onChange={(event) => selectFile(event.currentTarget.files?.[0])}
          />
          <label className="field-label" htmlFor="document-title">Document title</label>
          <input
            id="document-title"
            className="text-input"
            value={title}
            maxLength={255}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="e.g. Consulting agreement"
          />
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="modal-actions">
            <button type="button" className="button button-quiet" onClick={onClose}>Cancel</button>
            <button className="button button-primary" disabled={busy || !file}>
              {busy ? 'Uploading…' : 'Upload PDF'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}
