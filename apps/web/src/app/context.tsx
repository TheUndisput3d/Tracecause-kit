import { createContext, useContext, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type Models } from '../lib/api/client'

const Context = createContext<{ token: string; setToken: (token: string) => void; config?: Models['ApiCapabilities'] }>({ token: '', setToken: () => undefined })
export function AppContext({ children }: { children: ReactNode }) {
  const [token, setToken] = useState('')
  const { data: config } = useQuery({ queryKey: ['config'], queryFn: ({ signal }) => api('ApiCapabilities', '/config', { signal }) })
  return <Context.Provider value={{ token, setToken, config }}>{children}</Context.Provider>
}
export const useApp = () => useContext(Context)
