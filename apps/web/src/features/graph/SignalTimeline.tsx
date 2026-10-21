import { useState } from 'react'
import { CartesianGrid, Line, LineChart, Legend, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Models } from '../../lib/api/client'
import { timelineData } from './temporal'
export function SignalTimeline({ series, names, baseline }: { baseline: Models['TimeInterval'] | null; series: Models['SeriesResponse']; names: Map<string,string> }) {
  const services = [...new Set(series.points.map((point) => point.service_id))]
  const [choice, setChoice] = useState('')
  const service = services.includes(choice) ? choice : services[0]
  const points = series.points.filter((point) => point.service_id === service)
  const data = timelineData(points)
  const times = data.map(point => point.time)
  return <section className="panel"><div className="panel-heading"><h2>Recorded signal timeline</h2><label>Service<select value={service ?? ''} onChange={(e) => setChoice(e.target.value)}>{services.map((id) => <option key={id} value={id}>{names.get(id) ?? id.slice(0,12)}</option>)}</select></label></div>
    {!data.length ? <p>No eligible ten-second SERVER-span buckets at this cutoff.</p> : <><p className="muted">{points.length} recorded buckets · null gaps mean missing measurements. p95 is milliseconds; error fraction ranges from 0 to 1.</p><div className="chart" role="img" aria-label="Server p95 latency and error fraction timeline"><ResponsiveContainer width="100%" height={220}><LineChart data={data}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="time" type="number" domain={['dataMin','dataMax']} tickFormatter={(value) => new Date(value).toISOString().slice(11,19)} /><YAxis yAxisId="latency" width={55} label={{ value:'p95 ms', angle:-90, position:'insideLeft' }} /><YAxis yAxisId="errors" orientation="right" domain={[0,1]} width={45} /><Legend /><Tooltip labelFormatter={(value) => new Date(Number(value)).toISOString()} />{baseline && <ReferenceArea yAxisId="latency" x1={Math.max(Date.parse(baseline.start), Math.min(...times))} x2={Math.min(Date.parse(baseline.end), Math.max(...times))} fill="#087e8b" fillOpacity={0.08} label="Baseline" />}<ReferenceLine x={Date.parse(series.as_of)} yAxisId="latency" stroke="#d97706" /><Line isAnimationActive={false} yAxisId="latency" dataKey="p95" name="SERVER p95 (ms)" stroke="#087e8b" dot={false} connectNulls={false} /><Line isAnimationActive={false} yAxisId="errors" dataKey="errors" name="Error fraction" stroke="#c2410c" dot={false} connectNulls={false} /></LineChart></ResponsiveContainer></div>
      <details><summary>Timeline values as a table</summary><div className="table-scroll"><table><thead><tr><th>UTC bucket end</th><th>Requests</th><th>Errors</th><th>p95 ms</th></tr></thead><tbody>{points.map((point) => <tr key={point.observation_id}><td>{new Date(Number(BigInt(point.end_ns)/1000000n)).toISOString()}</td><td>{point.sample_count}</td><td>{point.error_count}</td><td>{point.p95_ms ?? 'Missing'}</td></tr>)}</tbody></table></div></details></>}
    {series.truncated && <p className="notice">Timeline truncated at 2,000 points.</p>}
  </section>
}
