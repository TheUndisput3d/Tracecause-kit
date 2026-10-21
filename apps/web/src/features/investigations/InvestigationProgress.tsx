import { useMutation, useQuery } from '@tanstack/react-query'
import { api, post, type Models } from '../../lib/api/client'
import { useApp } from '../../app/context'
import { ErrorPanel } from '../../components/ui/common'
export const terminal = (status?: string) => ['SUCCEEDED','FAILED','CANCELLED'].includes(status ?? '')
export function useJob(id: string | null) {
  return useQuery({ queryKey: ['job', id], enabled: !!id,
    queryFn: ({ signal }) => api('JobView', '/jobs/' + id, { signal }),
    refetchInterval: (query) => terminal(query.state.data?.status) ? false : query.state.data && Date.now() - Date.parse(query.state.data.created_at) > 10000 ? 2000 : 1000,
  })
}
export function InvestigationProgress({ job, refresh }: { job: Models['JobView']; refresh: () => void }) {
  const { token } = useApp()
  const cancel = useMutation({ mutationFn: () => post('JobView', '/jobs/' + job.job_id + '/cancel', token), onSuccess: refresh })
  return <section className="notice" aria-label="Investigation progress" aria-live="polite"><strong>{job.status.replaceAll('_',' ')}</strong><p>Phase: {job.phase ?? 'queued'} · Attempt {job.attempt_count} · {job.completed_units}/{job.total_units ?? '?'} complete</p>{job.error && <ErrorPanel error={new Error(`${job.error.code}: ${job.error.message}`)} />}
    {!terminal(job.status) && token && <button disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel job</button>}
    {cancel.error && <ErrorPanel error={cancel.error} />}
    <small>Job {job.job_id}. Leaving this page does not cancel server work.</small></section>
}
