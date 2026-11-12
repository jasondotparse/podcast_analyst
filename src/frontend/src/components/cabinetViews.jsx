import { BroadcastIcon, EnvelopeSimpleIcon, GraphIcon, MicrophoneIcon, StackIcon, TargetIcon } from '@phosphor-icons/react'
import { getRecentIngestions, getSubscriptions, getTheses, getWatchedPodcasts } from '../api.js'

function formatTime(iso) {
  return new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
}

// The cabinet's buttons, in order: what each one loads and how it renders one row.
// A view without `load` is a placeholder.
export const CABINET_VIEWS = [
  {
    id: 'theses',
    label: 'Theses',
    icon: TargetIcon,
    load: getTheses,
    empty: 'No theses yet. Ask the chat to save one.',
    render: (thesis) => (
      <>
        {thesis.description}
        {!thesis.is_active && <span className="cabinet-inactive"> (inactive)</span>}
      </>
    ),
  },
  {
    id: 'podcasts',
    label: 'Podcasts',
    icon: MicrophoneIcon,
    title: 'Watched podcasts',
    load: getWatchedPodcasts,
    empty: 'Not watching any podcast feeds yet.',
    render: (podcast) => <span className="cabinet-podcast"><BroadcastIcon size={15} aria-hidden="true" />{podcast.name}</span>,
  },
  {
    id: 'subscriptions',
    label: 'Subscriptions',
    icon: EnvelopeSimpleIcon,
    title: 'Email subscriptions',
    load: getSubscriptions,
    empty: 'Nobody is subscribed to thesis alerts yet.',
    render: (subscription) => (
      <>
        <strong>{subscription.email}</strong>
        <span className="cabinet-muted"> → {subscription.thesis}</span>
      </>
    ),
  },
  {
    id: 'ingestions',
    label: 'Ingestions',
    icon: StackIcon,
    title: 'Recent ingestions',
    load: getRecentIngestions,
    empty: 'No episodes ingested yet.',
    render: (job) => (
      <div className="cabinet-job">
        <div className="cabinet-row">
          <strong>{job.podcast}</strong>
          <span className={`tag cabinet-status ${job.status}`}>{job.status}</span>
        </div>
        <div className="cabinet-job-title">{job.title}</div>
        <div className="cabinet-time">{formatTime(job.updated_at)}</div>
        {job.error && <div className="cabinet-error" title={job.error}>{job.error}</div>}
      </div>
    ),
  },
  {
    id: 'graph',
    label: 'Show knowledge graph',
    icon: GraphIcon,
    title: 'Knowledge graph',
  },
]
