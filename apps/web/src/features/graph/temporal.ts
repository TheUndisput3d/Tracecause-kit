import type { Models } from '../../lib/api/client'

export function timelineData(points: Models['SeriesPoint'][]) {
  const byTime = new Map(points.map(point => [Number(BigInt(point.end_ns) / 1000000n), point]))
  const data: { time: number; p95: number | null; errors: number | null }[] = []
  let previous: number | undefined
  for (const [time, point] of [...byTime].sort(([a], [b]) => a - b)) {
    if (previous !== undefined && time - previous > 10000) data.push({ time: previous + 10000, p95: null, errors: null })
    data.push({ time, p95: point.p95_ms, errors: point.sample_count ? point.error_count / point.sample_count : null })
    previous = time
  }
  return data
}

export function compareGraphs(before: Models['GraphResponse'], after: Models['GraphResponse']) {
  if (before.episode_id !== after.episode_id || before.snapshot_id !== after.snapshot_id || before.temporal_mode !== after.temporal_mode || Date.parse(before.as_of) > Date.parse(after.as_of)) return null
  const key = (edge: Models['GraphEdge']) => `${edge.source_service_id}:${edge.target_service_id}`
  const old = new Set(before.edges.map(key)), current = new Set(after.edges.map(key))
  return {
    added: after.edges.filter(edge => !old.has(key(edge))),
    absent: before.edges.filter(edge => !current.has(key(edge))),
    retained: after.edges.filter(edge => old.has(key(edge))),
    incomplete: before.truncated || after.truncated,
  }
}
