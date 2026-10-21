import { lazy } from 'react'
import { ErrorBoundary } from './ErrorBoundary'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { ApiFailure } from '../lib/api/client'
import { AppContext } from './context'
import { AppShell } from './AppShell'
const Overview = lazy(() => import('../routes/Overview').then((module) => ({default:module.Overview})))
const Workbench = lazy(() => import('../routes/Workbench').then((module) => ({default:module.Workbench})))
const SavedResult = lazy(() => import('../routes/SavedResult').then((module) => ({default:module.SavedResult})))
const Evaluations = lazy(() => import('../routes/Evaluations').then((module) => ({default:module.Evaluations})))
const Compare = lazy(() => import('../routes/Compare').then((module) => ({default:module.Compare})))
const Imports = lazy(() => import('../routes/Imports').then((module) => ({default:module.Imports})))
const Documentation = lazy(() => import('../routes/Documentation').then((module) => ({default:module.Documentation})))
const client = new QueryClient({ defaultOptions: { queries: { staleTime: 15000, refetchOnWindowFocus: false,
  retry: (count, error) => count < 2 && !(error instanceof ApiFailure && [401,403,404,409,422].includes(error.status)),
  retryDelay: (attempt) => Math.min(500 * 2 ** attempt, 2000),
}, mutations: { retry: false } } })
export function App() {
  return <ErrorBoundary><QueryClientProvider client={client}><AppContext><BrowserRouter><Routes><Route element={<AppShell />}>
    <Route index element={<Overview />} /><Route path="episodes/:episodeId" element={<Workbench />} />
    <Route path="investigations/:investigationId" element={<SavedResult />} />
    <Route path="evaluations" element={<Evaluations />} /><Route path="evaluations/:reportId" element={<Evaluations />} />
    <Route path="compare" element={<Compare />} /><Route path="admin/imports" element={<Imports />} />
    <Route path="about/:card" element={<Documentation />} />
    <Route path="*" element={<h1>Page not found</h1>} />
  </Route></Routes></BrowserRouter></AppContext></QueryClientProvider></ErrorBoundary>
}
