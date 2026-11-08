import { useState } from 'react'
import UploadModal from './UploadModal.jsx'

// Opens a fresh upload form each time; the modal unmounts (and resets) when closed.
export default function UploadPodcastButton() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Upload podcast</button>
      {open && <UploadModal onClose={() => setOpen(false)} />}
    </>
  )
}
