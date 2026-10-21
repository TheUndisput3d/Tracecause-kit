import { Suspense, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { ArrowUpRight, Blocks, BookOpen, CircleDot, FlaskConical, GitCompareArrows, Layers3, Menu, Moon, Network, Sun, Upload } from 'lucide-react'
import { OperatorTokenDialog } from '../features/admin/OperatorTokenDialog'
import { useApp } from './context'
export function AppShell() {
  const { config } = useApp()
  const [dark, setDark] = useState(false)
  const [nav, setNav] = useState(false)
  return <div className="shell" data-theme={dark ? 'dark' : 'light'}>
    <a className="skip" href="#content">Skip to content</a>
    <header className="topbar"><NavLink className="brand" to="/"><span className="brand-mark"><Network size={23} aria-hidden="true" /></span><span>TraceCause<span className="brand-caption">Evidence intelligence</span></span></NavLink>
      <div className="workspace-label"><span className="workspace-divider" /><Layers3 size={15} aria-hidden="true" /><span>Local workspace</span></div>
      <div className="toolbar"><span className="environment-label"><CircleDot size={12} aria-hidden="true" /> {config?.public_read_only ? 'Recorded replay' : 'Local deployment'}</span><OperatorTokenDialog /><button className="icon-button" aria-label="Toggle theme" onClick={() => setDark(!dark)}>{dark ? <Sun size={17} /> : <Moon size={17} />}</button><button className="mobile-nav icon-button" aria-label="Toggle navigation" aria-expanded={nav} onClick={() => setNav(!nav)}><Menu size={19} /></button></div></header>
    {nav && <button className="nav-backdrop" aria-label="Close navigation" onClick={() => setNav(false)} />}
    <aside aria-label="Workbench navigation" className={`sidebar ${nav ? 'shown' : ''}`}>
      <div className="sidebar-workspace"><span className="workspace-emblem"><Blocks size={18} aria-hidden="true" /></span><div><strong>Investigation workspace</strong><small>TraceCause / local</small></div></div>
      <p className="nav-label">Workspace</p><nav aria-label="Main navigation" onClick={() => setNav(false)}>
        <NavLink to="/" end><Layers3 size={17} aria-hidden="true" />Episodes</NavLink>
        <NavLink to="/compare"><GitCompareArrows size={17} aria-hidden="true" />Compare methods</NavLink>
        <NavLink to="/evaluations"><FlaskConical size={17} aria-hidden="true" />Evaluation reports</NavLink>
        <NavLink to="/admin/imports"><Upload size={17} aria-hidden="true" />Local imports</NavLink>
      </nav>
      <p className="nav-label">Project reference</p><nav aria-label="Reference navigation" onClick={() => setNav(false)}>
        <NavLink to="/about/data"><BookOpen size={17} aria-hidden="true" />Data & provenance<ArrowUpRight size={13} className="nav-trailing" aria-hidden="true" /></NavLink>
        <NavLink to="/about/model"><Network size={17} aria-hidden="true" />Models & methods<ArrowUpRight size={13} className="nav-trailing" aria-hidden="true" /></NavLink>
      </nav>
      <div className="sidebar-note"><span className="note-rule" /><strong>Follow the evidence.</strong><p>Observed dependencies connect the investigation. Sources support each claim.</p><span className="sidebar-footnote">Controlled demo data · AI reviewed</span></div>
    </aside>
    <main id="content" tabIndex={-1}>{config?.public_read_only && <div className="replay-banner">Public replay · Saved results only. Compute and imports are disabled.</div>}<Suspense fallback={<p role="status">Loading view…</p>}><Outlet /></Suspense></main>
  </div>
}
