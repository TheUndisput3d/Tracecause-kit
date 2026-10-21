export const queryKeys = {
  config: ['config'] as const,
  episode: (id: string) => ['episode', id] as const,
  scoped: (kind: string, episode: string, snapshot: string, cutoff: string, temporal: string, method: string, configuration: string | undefined) =>
    [kind, episode, snapshot, cutoff, temporal, method, configuration] as const,
}
