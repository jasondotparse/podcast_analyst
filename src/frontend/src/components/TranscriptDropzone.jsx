import { useState } from 'react'
import { FileTextIcon, UploadSimpleIcon } from '@phosphor-icons/react'
import './TranscriptDropzone.css'

// A drop target (or click-to-choose) for a .txt transcript or an .mp3 recording.
export default function TranscriptDropzone({ file, onChange }) {
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')

  function choose(selected) {
    if (!selected) return
    const name = selected.name.toLowerCase()
    if (!name.endsWith('.txt') && !name.endsWith('.mp3')) {
      setError('Only .txt transcripts and .mp3 audio files are supported.')
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
      <input type="file" accept=".txt,.mp3,text/plain,audio/mpeg" className="visually-hidden"
        onChange={(event) => choose(event.target.files[0])} />
      {file ? <FileTextIcon size={22} aria-hidden="true" /> : <UploadSimpleIcon size={22} aria-hidden="true" />}
      {file
        ? <span><strong>{file.name}</strong> · click or drop to replace</span>
        : <span>Drop a <strong>.txt</strong> transcript or <strong>.mp3</strong> audio file here, or click to choose one</span>}
      {error && <span className="error" role="alert">{error}</span>}
    </label>
  )
}
