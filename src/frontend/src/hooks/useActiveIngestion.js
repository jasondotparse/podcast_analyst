import { useEffect } from 'react'
import { getActiveIngestion } from '../api.js'

// While enabled (no job is on screen), polls for the pipeline's current job, which may come from
// an RSS watcher, another tab, or an upload before a page reload, and passes its ID to onFound.
export default function useActiveIngestion(enabled, onFound, intervalMs = 10000) {
  useEffect(() => {
    if (!enabled) return
    let active = true
    let timer
    async function poll() {
      try {
        const job = await getActiveIngestion()
        if (!active) return
        if (job) {
          onFound(job.job_id)
          return
        }
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
  }, [enabled, onFound, intervalMs])
}
