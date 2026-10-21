import * as Dialog from '@radix-ui/react-dialog'
import { useState } from 'react'
import { api } from '../../lib/api/client'
import { useApp } from '../../app/context'
import { ErrorPanel } from '../../components/ui/common'
export function OperatorTokenDialog() {
  const { token, setToken, config } = useApp()
  const [value, setValue] = useState(''), [open, setOpen] = useState(false), [error, setError] = useState<unknown>(null)
  if (!config?.mutations_enabled) return <span className="badge">Replay only</span>
  return <Dialog.Root open={open} onOpenChange={(next) => { setOpen(next); setValue(''); setError(null) }}>
    <Dialog.Trigger asChild><button>{token ? 'Operator connected' : 'Connect operator'}</button></Dialog.Trigger>
    <Dialog.Portal><Dialog.Overlay className="overlay" /><Dialog.Content className="dialog">
      <Dialog.Title>Local operator access</Dialog.Title><Dialog.Description>The token stays in memory until you leave or disconnect.</Dialog.Description>
      <form onSubmit={async (event) => { event.preventDefault(); try { await api('AuthStatus', '/auth/check', { headers: { Authorization: `Bearer ${value}` } }); setToken(value); setValue(''); setOpen(false) } catch (err) { setError(err) } }}>
        <label>Operator token<input type="password" autoComplete="off" value={value} onChange={(event) => setValue(event.target.value)} required /></label>
        <button type="submit">Connect</button>{token && <button type="button" onClick={() => { setToken(''); setOpen(false) }}>Disconnect</button>}
      </form>{error ? <ErrorPanel error={error} /> : null}<Dialog.Close asChild><button>Close</button></Dialog.Close>
    </Dialog.Content></Dialog.Portal>
  </Dialog.Root>
}
