async function request(path, options) {
  const response = await fetch(path, options)
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed.')
  return data
}

export function post(path, body) {
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
}

export function getIngestion(jobId) {
  return request(`/api/ingestions/${jobId}`)
}

// Queues a .txt transcript or .mp3 recording for ingestion and resolves with its job once the server accepts it.
export async function uploadTranscript(metadata, file) {
  const form = new FormData()
  form.append('metadata', JSON.stringify(metadata))
  const field = file.name.toLowerCase().endsWith('.mp3') ? 'audio' : 'transcript'
  form.append(field, file)
  const response = await fetch('/api/ingestions', { method: 'POST', body: form })
  const data = await response.json()
  if (response.status === 409) throw new Error(`This episode was already uploaded (status: ${data.status}).`)
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Upload failed. Check the fields and try again.')
  return data
}
