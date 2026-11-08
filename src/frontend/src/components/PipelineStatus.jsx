import { useEffect } from 'react'
import usePipelineStatus from '../hooks/usePipelineStatus.js'
import './PipelineStatus.css'

const STEP_LABELS = {
  screen: 'Screening transcript',
  chunk: 'Splitting into passages',
  embed: 'Embedding passages',
  extract_claims: 'Extracting claims (takes a few minutes)',
  project_graph: 'Updating the knowledge graph',
}

function describe(job) {
  if (!job || job.status === 'pending') return 'Queued for ingestion'
  if (job.status === 'completed') return 'Ingestion complete'
  if (job.status === 'skipped') return `Not ingested: ${job.error}`
  if (job.status === 'failed') return `Ingestion failed: ${job.error}`
  return STEP_LABELS[job.current_step] ?? job.current_step
}

// A green bar that fills as the job moves through the pipeline. It disappears shortly
// after success; a rejected or failed job stays visible until dismissed.
export default function PipelineStatus({ jobId, onDismiss }) {
  const { job, progress, finished } = usePipelineStatus(jobId)
  const succeeded = job?.status === 'completed'

  useEffect(() => {
    if (!succeeded) return
    const timer = setTimeout(onDismiss, 1500)  // Let the full bar register before hiding it.
    return () => clearTimeout(timer)
  }, [succeeded, onDismiss])

  const problem = finished && !succeeded
  return (
    <div className={`pipeline-status${problem ? ' problem' : ''}`}>
      <div className="pipeline-track" role="progressbar" aria-label="Ingestion progress"
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)}>
        <div className="pipeline-fill" style={{ width: `${progress * 100}%` }} />
      </div>
      <p role="status">
        {describe(job)}
        {problem && <button type="button" className="secondary" onClick={onDismiss}>Dismiss</button>}
      </p>
    </div>
  )
}
