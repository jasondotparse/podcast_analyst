import { useState } from 'react'
import UploadModal from './UploadModal.jsx'

// Opens a fresh upload form each time; the modal unmounts (and resets) when closed.
export default function UploadPodcastButton({ className, onUploaded }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>Upload podcast</button>
      {open && <UploadModal onClose={() => setOpen(false)} onUploaded={onUploaded} />}
    </>
  )
}
