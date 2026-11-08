import { useState } from 'react'
import './TranscriptDropzone.css'

// A drop target (or click-to-choose) that only accepts .txt transcripts for now.
export default function TranscriptDropzone({ file, onChange }) {
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')

  function choose(selected) {
    if (!selected) return
    if (!selected.name.toLowerCase().endsWith('.txt')) {
      setError('Only .txt transcripts are supported for now.')
      onChange(null)
      return
    }
    setError('')
    onChange(selected)
  }

  return (
    <label
      className={`dropzone${dragging ? ' dragging' : ''}`}
      onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        choose(event.dataTransfer.files[0])
      }}
    >
      <input type="file" accept=".txt,text/plain" className="visually-hidden"
        onChange={(event) => choose(event.target.files[0])} />
      {file
        ? <span><strong>{file.name}</strong> · click or drop to replace</span>
        : <span>Drop a <strong>.txt</strong> transcript here, or click to choose one</span>}
      {error && <span className="error" role="alert">{error}</span>}
    </label>
  )
}
