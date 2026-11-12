import { useEffect, useRef, useState } from 'react'
import { uploadTranscript } from '../api.js'
import TranscriptDropzone from './TranscriptDropzone.jsx'
import './UploadModal.css'

const optional = (value) => value.trim() || null
const list = (value) => {
  const items = value.split(',').map((item) => item.trim()).filter(Boolean)
  return items.length ? items : null
}

// Collects a transcript and its metadata; closes once the server accepts the upload.
export default function UploadModal({ onClose, onUploaded }) {
  const dialog = useRef(null)
  const [file, setFile] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!dialog.current.open) dialog.current.showModal()  // StrictMode runs effects twice in dev.
  }, [])

  async function submit(event) {
    event.preventDefault()
    const fields = Object.fromEntries(new FormData(event.currentTarget))
    setSubmitting(true)
    setError('')
    try {
      const job = await uploadTranscript({
        podcast_name: fields.podcast_name.trim(),
        podcast_description: optional(fields.podcast_description),
        title: fields.title.trim(),
        description: optional(fields.description),
        published_at: fields.published_at || null,
        source_url: optional(fields.source_url),
        guests: list(fields.guests),
      }, file)
      onUploaded(job)
      onClose()
    } catch (failure) {
      setError(failure.message)
      setSubmitting(false)
    }
  }

  return (
    // Escape fires the dialog's close event.
    <dialog ref={dialog} className="upload-modal" aria-labelledby="upload-title" onClose={onClose}>
      <form className="dialog" onSubmit={submit}>
        <h2 id="upload-title" className="dialog-title">Upload podcast</h2>
        <TranscriptDropzone file={file} onChange={setFile} />
        <div className="field">
          <label htmlFor="upload-podcast-name">Podcast name</label>
          <input id="upload-podcast-name" className="input" name="podcast_name" required />
        </div>
        <div className="field">
          <label htmlFor="upload-podcast-description">Podcast description</label>
          <textarea id="upload-podcast-description" className="input" name="podcast_description" rows={2} />
        </div>
        <div className="field">
          <label htmlFor="upload-episode-title">Episode title</label>
          <input id="upload-episode-title" className="input" name="title" required />
        </div>
        <div className="field">
          <label htmlFor="upload-episode-description">Episode description</label>
          <textarea id="upload-episode-description" className="input" name="description" rows={3} />
        </div>
        <div className="field">
          <label htmlFor="upload-episode-url">Episode URL</label>
          <input id="upload-episode-url" className="input" name="source_url" type="url" placeholder="https://… (link to the original episode)" />
        </div>
        <div className="field">
          <label htmlFor="upload-guests">Guests</label>
          <input id="upload-guests" className="input" name="guests" placeholder="Comma-separated, e.g. Bill Gates, Madhumita Murgia" />
        </div>
        <div className="field">
          <label htmlFor="upload-date">Date</label>
          <input id="upload-date" className="input" name="published_at" type="date" />
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="dialog-actions">
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={!file || submitting}>{submitting ? 'Uploading…' : 'Submit'}</button>
        </div>
      </form>
    </dialog>
  )
}
