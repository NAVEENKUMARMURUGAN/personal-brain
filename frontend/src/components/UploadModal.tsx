import React, { useState, useRef, useCallback } from 'react'
import { API_URL } from '../config'
import './UploadModal.css'

interface UploadModalProps {
  onClose: () => void
  onSuccess: (filename: string, category: string, saved: number) => void
}

const ACCEPTED = '.pdf,.docx,.doc,.xlsx,.xls,.txt,.md,.csv'
const ACCEPTED_LABEL = 'PDF, DOCX, XLSX, TXT, MD, CSV'

type UploadStatus = 'idle' | 'uploading' | 'success' | 'error'

export default function UploadModal({ onClose, onSuccess }: UploadModalProps) {
  const [file, setFile] = useState<File | null>(null)
  const [category, setCategory] = useState('')
  const [status, setStatus] = useState<UploadStatus>('idle')
  const [progress, setProgress] = useState('')
  const [dragging, setDragging] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFile = useCallback((f: File) => {
    setFile(f)
    setStatus('idle')
    setProgress('')
    // Auto-suggest category from filename
    if (!category) {
      const name = f.name.replace(/\.[^.]+$/, '').replace(/[-_]/g, ' ')
      setCategory(name.charAt(0).toUpperCase() + name.slice(1))
    }
  }, [category])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }, [handleFile])

  const handleUpload = useCallback(async () => {
    if (!file) return
    setStatus('uploading')
    setProgress('Extracting text…')

    const formData = new FormData()
    formData.append('file', file)
    formData.append('category', category || 'General')

    try {
      setProgress('Chunking and embedding…')
      const res = await fetch(`${API_URL}/upload`, {
        method: 'POST',
        body: formData,
      })
      const data = await res.json()

      if (!res.ok) {
        setStatus('error')
        setProgress(data.error || 'Upload failed')
        return
      }

      setStatus('success')
      const msg = data.saved === 0
        ? `All content already saved (${data.skipped} duplicate chunks skipped)`
        : `Saved ${data.saved} chunk${data.saved !== 1 ? 's' : ''} under "${data.category}"${data.skipped ? ` · ${data.skipped} duplicates skipped` : ''}`
      setProgress(msg)

      setTimeout(() => {
        onSuccess(file.name, data.category, data.saved)
        onClose()
      }, 1800)

    } catch (e) {
      setStatus('error')
      setProgress('Network error — is the backend running?')
    }
  }, [file, category, onClose, onSuccess])

  return (
    <div className="upload-backdrop" onClick={onClose}>
      <div className="upload-modal" onClick={(e) => e.stopPropagation()}>
        <div className="upload-modal__header">
          <span className="upload-modal__title">Upload file to Brain</span>
          <button className="upload-modal__close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {/* Drop zone */}
        <div
          className={`upload-dropzone ${dragging ? 'upload-dropzone--dragging' : ''} ${file ? 'upload-dropzone--has-file' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED}
            style={{ display: 'none' }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f) }}
          />
          {file ? (
            <div className="upload-dropzone__file">
              <span className="upload-dropzone__file-icon">📄</span>
              <div className="upload-dropzone__file-info">
                <span className="upload-dropzone__file-name">{file.name}</span>
                <span className="upload-dropzone__file-size">{(file.size / 1024).toFixed(1)} KB</span>
              </div>
              <button
                className="upload-dropzone__remove"
                onClick={(e) => { e.stopPropagation(); setFile(null); setCategory(''); setStatus('idle'); setProgress('') }}
              >✕</button>
            </div>
          ) : (
            <>
              <span className="upload-dropzone__icon">⬆</span>
              <span className="upload-dropzone__label">Drop a file or click to browse</span>
              <span className="upload-dropzone__hint">{ACCEPTED_LABEL}</span>
            </>
          )}
        </div>

        {/* Category */}
        <div className="upload-modal__field">
          <label className="upload-modal__label">Category</label>
          <input
            className="upload-modal__input"
            type="text"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="e.g. Meeting Notes, Research, Personal"
          />
        </div>

        {/* Progress / result */}
        {progress && (
          <div className={`upload-modal__progress upload-modal__progress--${status}`}>
            {status === 'uploading' && <span className="upload-modal__spinner" />}
            {progress}
          </div>
        )}

        {/* Actions */}
        <div className="upload-modal__actions">
          <button className="upload-modal__cancel" onClick={onClose}>Cancel</button>
          <button
            className="upload-modal__submit"
            onClick={handleUpload}
            disabled={!file || status === 'uploading' || status === 'success'}
          >
            {status === 'uploading' ? 'Processing…' : status === 'success' ? 'Done ✓' : 'Upload & Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
