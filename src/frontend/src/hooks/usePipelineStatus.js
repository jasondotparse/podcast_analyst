import { useEffect, useState } from 'react'
import { getIngestion } from '../api.js'

// The backend's pipeline steps, in order (IngestionService.steps).
export const PIPELINE_STEPS = ['transcribe', 'screen','chunk', 'embed', 'extract_claims', 'project_graph']
const FINISHED = new Set(['completed', 'failed', 'skipped'])

// Polls an ingestion job until it finishes. progress runs 0 → 1: each step reached
// fills one more segment, and completion fills the last.
export default function usePipelineStatus(jobId, intervalMs = 2000) {
  const [job, setJob] = useState(null)

  useEffect(() => {
    let active = true
    let timer
    async function poll() {
      try {
        const latest = await getIngestion(jobId)
        if (!active) return
        setJob(latest)
        if (FINISHED.has(latest.status)) return
      } catch {
        // A failed poll (e.g. a backend restart) just waits for the next one.
      }
      if (active) timer = setTimeout(poll, intervalMs)
    }
    poll()
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [jobId, intervalMs])

  const reached = job?.status === 'pending' ? 0 : PIPELINE_STEPS.indexOf(job?.current_step) + 1
  const progress = job?.status === 'completed' ? 1 : reached / (PIPELINE_STEPS.length + 1)
  return { job, progress, finished: FINISHED.has(job?.status) }
}
