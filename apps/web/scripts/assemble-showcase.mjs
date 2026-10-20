/** Publish only verified, actual capture media and a portable provenance manifest. */
import process from 'node:process'
import { createHash } from 'node:crypto'
import { copyFileSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { basename, resolve } from 'node:path'

const [newsArg, mediaArg, aiArg, destinationArg = 'docs/assets/operational'] = process.argv.slice(2)
if (!newsArg || !mediaArg || !aiArg) throw new Error('Usage: node apps/web/scripts/assemble-showcase.mjs NEWSROOM_CAPTURE MEDIA_CAPTURE AI_CAPTURE [DESTINATION]')
const roots = { newsroom: resolve(newsArg), media: resolve(mediaArg), ai: resolve(aiArg) }
const destination = resolve(destinationArg)
const read = path => JSON.parse(readFileSync(path, 'utf8'))
const reports = Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, read(resolve(root, 'showcase.json'))]))
for (const [name, report] of Object.entries(reports)) {
  if (report.status !== 'PASS') throw new Error(name + ' capture did not pass')
  if (!(report.source_responses?.length) || report.source_responses.some(item => item.status !== 200)) throw new Error(name + ' source inspections are missing or unsuccessful')
  if ((report.console_errors ?? report.errors ?? []).length) throw new Error(name + ' capture contains browser errors')
}
for (const name of ['newsroom', 'media']) {
  const report = reports[name]
  for (const width of [360, 768, 1440]) if (!report.accessibility.some(item => item.width === width && !item.overflow && item.violations.length === 0)) throw new Error(name + ' lacks a clean responsive check at ' + width)
  if (!report.accessibility.some(item => item.view === 'dark_operational_graph' && item.violations.length === 0)) throw new Error(name + ' lacks a clean dark graph accessibility check')
  if (report.review_first < 1 || report.signals < 1) throw new Error(name + ' capture lacks observed change/signal reasoning')
}
if (!reports.media.accessibility.some(item => item.view === 'message_handoff_evidence' && item.violations.length === 0)) throw new Error('Media capture lacks the actual handoff inspection')
if (reports.ai.generation_origin !== 'ollama' || reports.ai.fallback_reason || !reports.ai.export_verified) throw new Error('AI capture must contain actual Ollama generation and verified export')
const aiExport = read(resolve(roots.ai, 'investigation-export.json'))
const selections = [
  ...['episode-library', 'investigation-workspace', 'change-reasoning', 'operational-knowledge-graph', 'service-deployment-footprint', 'historical-comparison', 'operational-source', 'measured-signal-source', 'operational-relationships', 'history-360', 'history-768', 'operational-graph-dark'].map(name => ['newsroom', name + '.png', name + '.png']),
  ['media', 'message-handoff-evidence.png', 'message-handoff-evidence.png'],
  ['media', 'operational-knowledge-graph.png', 'asynchronous-knowledge-graph.png'],
  ...['ai-investigation-request', 'ai-investigation-findings', 'ai-cited-evidence', 'ai-saved-result-export'].map(name => ['ai', name + '.png', name + '.png']),
  ['newsroom', 'tracecause-operational-demo.webm', 'tracecause-operational-demo.webm'],
  ['media', 'tracecause-operational-demo.webm', 'tracecause-message-flow-demo.webm'],
  ['ai', 'tracecause-live-ai-demo.webm', 'tracecause-live-ai-demo.webm'],
]
// Check every source before changing the publication directory.
const assets = selections.map(([capture, source, file]) => {
  const path = resolve(roots[capture], source), bytes = readFileSync(path)
  if (!bytes.length) throw new Error('Empty media: ' + source)
  const image = file.endsWith('.png')
  const width = file === 'history-360.png' ? 360 : file === 'history-768.png' ? 768 : 1440
  return { capture, source, file, bytes: statSync(path).size, sha256: createHash('sha256').update(bytes).digest('hex'), kind: image ? 'screenshot' : 'video', ...(image ? { viewport: { width, height: 1000 }, theme: file.includes('-dark') ? 'dark' : 'light' } : { frame_size: { width: 1440, height: 1000 } }) }
})
mkdirSync(destination, { recursive: true })
for (const asset of assets) copyFileSync(resolve(roots[asset.capture], asset.source), resolve(destination, asset.file))
const captures = Object.fromEntries(Object.entries(reports).map(([name, report]) => [name, {
  status: report.status, base_url: report.base, captured_at: report.observed_at,
  episode_id: report.episode_id,
  scope: report.scope ?? { episode_id: aiExport.result.episode_id, snapshot_id: aiExport.result.snapshot_id, as_of: aiExport.result.as_of, temporal_mode: aiExport.result.temporal_mode },
  recording_script: name === 'ai' ? 'apps/web/scripts/ai-showcase.mjs' : 'apps/web/scripts/operational-showcase.mjs',
  source_inspections: report.source_responses,
  accessibility: report.accessibility ?? [],
  console_errors: report.console_errors ?? report.errors,
  ...(name === 'ai' ? { generation_origin: report.generation_origin, fallback_reason: report.fallback_reason, investigation_id: report.investigation_id, timings_ms: report.timings_ms, evidence_count: report.evidence_count, export_verified: report.export_verified, job_states: report.job_states } : { operational_facts: report.operational_facts, review_first: report.review_first, signals: report.signals, relationship_rows: report.relationship_rows }),
}]))
const manifest = { schema_version: 1, assembled_at: new Date().toISOString(), description: 'Actual deployed browser recordings. No network interception, generated screen images or mocked product data. Videos show prior live runs; replaying media performs no new inference.', source_validation_note: 'Browser inspection responses succeeded with HTTP200 and rendered source records. Artifact validation is performed by the application; this manifest does not claim an independent cryptographic source-content audit.', captures, assets: assets.map(({ source, ...asset }) => ({ ...asset, original_filename: basename(source) })) }
writeFileSync(resolve(destination, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n')
process.stdout.write(JSON.stringify({ status: 'PASS', destination, assets: assets.length, captures: Object.keys(captures) }) + '\n')
