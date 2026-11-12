import { useState } from 'react'
import { UploadSimpleIcon } from '@phosphor-icons/react'
import UploadModal from './UploadModal.jsx'

// Opens a fresh upload form each time; the modal unmounts (and resets) when closed.
export default function UploadPodcastButton({ className, onUploaded }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>
        <UploadSimpleIcon size={15} aria-hidden="true" />Upload podcast
      </button>
      {open && <UploadModal onClose={() => setOpen(false)} onUploaded={onUploaded} />}
    </>
  )
}
