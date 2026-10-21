import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, query } from '../lib/api/client'
import { EvidenceDrawer, type Citation } from '../features/evidence/EvidenceDrawer'
import { ResultPanel } from '../features/investigations/ResultPanel'
import { ErrorPanel, Loading } from '../components/ui/common'
export function SavedResult() {
  const { investigationId = '' } = useParams()
  const [citation, setCitation] = useState<Citation | null>(null)
  const response = useQuery({ queryKey: ['saved-result', investigationId], queryFn: ({ signal }) => api('InvestigationView', '/investigations/' + investigationId, { signal }) })
  const scope = response.data?.result
  const graph = useQuery({ queryKey:['saved-graph',scope?.snapshot_id,scope?.as_of,scope?.temporal_mode],enabled:!!scope,
    queryFn:({signal}) => api('GraphResponse','/graph' + query({episode_id:scope!.episode_id,snapshot_id:scope!.snapshot_id,as_of:scope!.as_of,temporal_mode:scope!.temporal_mode}),{signal}) })
  const names = new Map(graph.data?.nodes.map((node) => [node.service_id,node.display_name]))
  return <><p className="eyebrow">Immutable replay</p><h1>Saved investigation</h1><p>Opening this page never runs the model. The original snapshot, cutoff and provenance are retained.</p>
    {response.isPending && <Loading />}{response.error && <ErrorPanel error={response.error} retry={() => void response.refetch()} />}
    {response.data && (response.data.result ? <><div className="toolbar"><a className="button" href={'/api/v1/investigations/' + investigationId + '/export'}>Export JSON</a><button onClick={() => window.print()}>Print result</button></div><ResultPanel result={response.data.result} names={names} onOpen={setCitation} /></> : <p className="notice">Status: {response.data.status}. No saved result is available yet.</p>)}
    <EvidenceDrawer citation={citation} close={() => setCitation(null)} /></>
}
