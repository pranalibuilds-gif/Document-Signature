import { useEffect, useRef, useState, type PointerEvent } from 'react'

interface SignaturePadProps {
  onCancel: () => void
  onSave: (dataUri: string) => void
}

export function SignaturePad({ onCancel, onSave }: SignaturePadProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const drawingRef = useRef(false)
  const [hasInk, setHasInk] = useState(false)

  useEffect(() => {
    const canvas = canvasRef.current
    const context = canvas?.getContext('2d')
    if (!canvas || !context) return
    const ratio = window.devicePixelRatio || 1
    const width = canvas.clientWidth
    const height = canvas.clientHeight
    canvas.width = width * ratio
    canvas.height = height * ratio
    context.scale(ratio, ratio)
    context.lineWidth = 2.4
    context.lineCap = 'round'
    context.lineJoin = 'round'
    context.strokeStyle = '#172d4a'
  }, [])

  function point(event: PointerEvent<HTMLCanvasElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    return { x: event.clientX - bounds.left, y: event.clientY - bounds.top }
  }

  function start(event: PointerEvent<HTMLCanvasElement>) {
    event.currentTarget.setPointerCapture(event.pointerId)
    const context = event.currentTarget.getContext('2d')
    const { x, y } = point(event)
    context?.beginPath()
    context?.moveTo(x, y)
    drawingRef.current = true
  }

  function draw(event: PointerEvent<HTMLCanvasElement>) {
    if (!drawingRef.current) return
    const context = event.currentTarget.getContext('2d')
    const { x, y } = point(event)
    context?.lineTo(x, y)
    context?.stroke()
    setHasInk(true)
  }

  function stop() {
    drawingRef.current = false
  }

  function clear() {
    const canvas = canvasRef.current
    const context = canvas?.getContext('2d')
    if (canvas && context) context.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight)
    setHasInk(false)
  }

  function save() {
    const canvas = canvasRef.current
    if (canvas && hasInk) onSave(canvas.toDataURL('image/png'))
  }

  return (
    <div className="modal-backdrop">
      <section className="modal-card signature-modal" role="dialog" aria-modal="true" aria-labelledby="signature-title">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">YOUR SIGNATURE</p>
            <h2 id="signature-title">Draw your signature</h2>
          </div>
          <button className="icon-button" aria-label="Close signature dialog" onClick={onCancel}>×</button>
        </div>
        <p className="muted">Use your mouse, trackpad, or touchscreen in the space below.</p>
        <div className="signature-canvas-wrap">
          <canvas
            ref={canvasRef}
            className="signature-canvas"
            onPointerDown={start}
            onPointerMove={draw}
            onPointerUp={stop}
            onPointerCancel={stop}
            onLostPointerCapture={stop}
            aria-label="Signature drawing area"
          />
          <span className="signature-baseline" />
        </div>
        <div className="modal-actions">
          <button className="button button-quiet" onClick={clear}>Clear</button>
          <span className="action-spacer" />
          <button className="button button-quiet" onClick={onCancel}>Cancel</button>
          <button className="button button-primary" disabled={!hasInk} onClick={save}>Use signature</button>
        </div>
      </section>
    </div>
  )
}
