import { Link } from 'react-router-dom'
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useApp } from '../app/context'
import { api, validate, type Models } from '../lib/api/client'
import { ErrorPanel } from '../components/ui/common'
import { InvestigationProgress, useJob } from '../features/investigations/InvestigationProgress'
export function Imports() {
  const { token, config } = useApp()
  const [manifest, setManifest] = useState<File | null>(null), [files, setFiles] = useState<File[]>([])
  const [checked, setChecked] = useState<Models['EpisodeManifest'] | null>(null), [error, setError] = useState<unknown>(null)
  const [checking, setChecking] = useState(false), [id, setId] = useState<string | null>(null)
  const job = useJob(id)
  const upload = useMutation({ mutationFn: async () => {
    if (!checked || !manifest) throw new Error('Validate the manifest and files first.')
    const body = new FormData(); body.set('manifest', await manifest.text()); files.forEach((file) => body.append('files', file, file.name))
    return api('ImportSubmissionView','/imports',{ method:'POST', headers:{ Authorization:`Bearer ${token}`, 'Idempotency-Key':crypto.randomUUID() }, body })
  }, onSuccess: (result) => setId(result.job_id) })
  if (!config) return <p>Loading capabilities…</p>
  if (!config.mutations_enabled) return <><h1>Local imports unavailable</h1><p>This instance is read-only. Importing and computation require a local operator instance.</p></>
  const dryRun = async () => {
    setError(null); setChecked(null); setChecking(true)
    try {
      if (!manifest || manifest.size > 131072) throw new Error('Select a manifest JSON file under 128 KiB.')
      const data = validate('EpisodeManifest', JSON.parse(await manifest.text()))
      if (!data.baseline_interval) throw new Error('A baseline interval is required.')
      if (files.length > 100 || files.length !== data.sources.length || files.reduce((sum,f) => sum + f.size,0) > config.maximum_upload_bundle_bytes) throw new Error('Select every declared file within the 100-file / 100-MiB bundle limit.')
      for (const source of data.sources) {
        const found = files.filter((file) => file.name === source.logical_name)
        if (found.length !== 1 || found[0].size !== source.bytes || found[0].size > config.maximum_upload_file_bytes) throw new Error('A declared file is missing, repeated, or exceeds its declared size / 25-MiB limit.')
        const digest = await crypto.subtle.digest('SHA-256', await found[0].arrayBuffer())
        const hash = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2,'0')).join('')
        if (hash !== source.sha256) throw new Error('A selected file does not match its declared SHA-256.')
      }
      setChecked(data)
    } catch (err) { setError(err) } finally { setChecking(false) }
  }
  return <><p className="eyebrow">Local operator</p><h1>Import an episode</h1><p>Manifest plus individually named JSON, JSONL or authored Markdown files. No archives. Maximum 25 MiB per file and 100 MiB including multipart framing.</p>
    {!token && <p className="notice">Connect an operator token before submitting. Local validation is available without uploading.</p>}
    <section className="panel"><label>Manifest JSON<input type="file" accept=".json" onChange={(e) => { setManifest(e.target.files?.[0] ?? null); setChecked(null) }} /></label><label>Declared source files<input type="file" multiple accept=".json,.jsonl,.md" onChange={(e) => { setFiles(Array.from(e.target.files ?? [])); setChecked(null) }} /></label><div className="toolbar"><button onClick={() => void dryRun()} disabled={checking}>{checking ? 'Checking hashes…' : 'Validate locally'}</button><button className="primary" disabled={!checked || !token || upload.isPending} onClick={() => upload.mutate()}>Submit import</button></div>
      {checked && <p role="status">Local manifest/hash checks passed for {checked.sources.length} files. Server validation and normalization still run in the durable job.</p>}
      {error ? <ErrorPanel error={error} /> : null}{upload.error && <ErrorPanel error={upload.error} />}</section>
    {job.data?.status === 'SUCCEEDED' && <Link className="button" to={'/episodes/' + job.data.episode_id}>Open imported episode</Link>}
    {job.data && <InvestigationProgress job={job.data} refresh={() => void job.refetch()} />}{job.error && <ErrorPanel error={job.error} />}
  </>
}
