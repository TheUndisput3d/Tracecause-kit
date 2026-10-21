import { Component, type ReactNode } from 'react'
export class ErrorBoundary extends Component<{ children:ReactNode },{ failed:boolean }> {
  state = { failed:false }
  static getDerivedStateFromError() { return { failed:true } }
  render() {
    if (this.state.failed) return <main><h1>The view could not be displayed</h1><p>Reload the application to recover. Any submitted job continues on the server.</p><button onClick={() => location.reload()}>Reload application</button></main>
    return this.props.children
  }
}
