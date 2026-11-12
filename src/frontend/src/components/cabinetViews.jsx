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
    load: getTheses,
    empty: 'No theses yet. Ask the chat to save one.',
    render: (thesis) => (
      <>
        {thesis.description}
        {!thesis.is_active && <span className="cabinet-muted"> (inactive)</span>}
      </>
    ),
  },
  {
    id: 'podcasts',
    label: 'Podcasts',
    title: 'Watched podcasts',
    load: getWatchedPodcasts,
    empty: 'Not watching any podcast feeds yet.',
    render: (podcast) => podcast.name,
  },
  {
    id: 'subscriptions',
    label: 'Subscriptions',
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
    title: 'Recent ingestions',
    load: getRecentIngestions,
    empty: 'No episodes ingested yet.',
    render: (job) => (
      <>
        <div className="cabinet-row">
          <strong>{job.podcast}</strong>
          <span className={`cabinet-status ${job.status}`}>{job.status}</span>
        </div>
        <div className="cabinet-muted">{job.title}</div>
        <div className="cabinet-muted">{formatTime(job.updated_at)}</div>
        {job.error && <div className="cabinet-error" title={job.error}>{job.error}</div>}
      </>
    ),
  },
  {
    id: 'graph',
    label: 'Knowledge graph',
    placeholder: 'A knowledge graph view is coming soon.',
  },
]
