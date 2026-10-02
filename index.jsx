/* Evidence Desk launcher: create, list and open this app's Projects.
 *
 * Paper files, evidence and briefs live in ordinary Möbius Projects; this
 * frame only reaches them through the shell's app-scoped Projects runtime,
 * which exposes this app's own templates and projects and nothing else.
 * The template is resolved by its local id, never by installation slug.
 */
import { useCallback, useEffect, useState } from 'react'

const TEMPLATE_ID = 'paper-comparison'

const styles = {
  main: { maxWidth: 640, margin: '0 auto', padding: '24px 16px', color: 'var(--text)', fontFamily: 'var(--font)' },
  muted: { color: 'var(--muted)' },
  form: { display: 'flex', gap: 8, flexWrap: 'wrap', margin: '16px 0 28px' },
  input: {
    flex: '1 1 220px', minHeight: 44, padding: '0 12px', borderRadius: 8,
    border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', font: 'inherit',
  },
  primary: {
    minHeight: 44, padding: '0 16px', borderRadius: 8, border: 'none',
    background: 'var(--accent)', color: 'var(--accent-fg)', font: 'inherit', cursor: 'pointer',
  },
  secondary: {
    minHeight: 44, padding: '0 14px', borderRadius: 8, border: '1px solid var(--border)',
    background: 'transparent', color: 'var(--text)', font: 'inherit', cursor: 'pointer',
  },
  list: { listStyle: 'none', padding: 0, margin: 0 },
  row: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
    padding: '10px 0', borderTop: '1px solid var(--border)',
  },
  alert: { color: 'var(--danger)' },
}

function formatDate(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString()
}

function byRecent(a, b) {
  return String(b.updated_at || '').localeCompare(String(a.updated_at || ''))
}

export default function App() {
  const [runtime] = useState(() => window.mobius?.projects ?? null)
  // null while loading; an array once loaded.
  const [projects, setProjects] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState('')
  const [actionError, setActionError] = useState('')

  const load = useCallback(async () => {
    if (!runtime) return
    setProjects(null)
    setLoadError('')
    try {
      const rows = await runtime.list()
      setProjects(Array.isArray(rows) ? [...rows].sort(byRecent) : [])
    } catch (cause) {
      setLoadError(cause?.message || 'Could not load your comparisons.')
      setProjects([])
    }
  }, [runtime])

  useEffect(() => { load() }, [load])

  async function create(event) {
    event.preventDefault()
    setBusy('create')
    setActionError('')
    try {
      const templates = await runtime.templates()
      const template = (templates || []).find(row => row.id === TEMPLATE_ID)
      if (!template) throw new Error('The Paper comparison project type is unavailable. Check the Evidence Desk installation.')
      // Creation opens the new project; do not also call open().
      await runtime.create({ templateId: template.key, name: name.trim() || 'Untitled comparison' })
      setName('')
    } catch (cause) {
      setActionError(cause?.message || 'Could not create the comparison.')
    } finally {
      setBusy('')
    }
  }

  async function open(projectId) {
    setBusy(projectId)
    setActionError('')
    try {
      await runtime.open(projectId)
    } catch (cause) {
      setActionError(cause?.message || 'Could not open the comparison.')
    } finally {
      setBusy('')
    }
  }

  if (!runtime) {
    return <main style={styles.main}>
      <h1>Evidence Desk</h1>
      <p role="alert" style={styles.alert}>Projects are unavailable here. Open Evidence Desk from the Möbius app list.</p>
    </main>
  }

  return <main style={styles.main}>
    <h1>Evidence Desk</h1>
    <p style={styles.muted}>
      Compare research papers with every finding traced to a page and an exact quotation.
      Each comparison is a Möbius project: upload PDFs to its inbox folder and work with the agent in its chat.
    </p>

    <form style={styles.form} onSubmit={create}>
      <label htmlFor="comparison-name" style={{ position: 'absolute', left: -9999 }}>Comparison name</label>
      <input
        id="comparison-name" style={styles.input} value={name} maxLength={200}
        placeholder="New comparison name" onChange={event => setName(event.target.value)}
      />
      <button type="submit" style={styles.primary} disabled={busy !== ''}>
        {busy === 'create' ? 'Creating…' : 'New comparison'}
      </button>
    </form>
    {actionError && <p role="alert" style={styles.alert}>{actionError}</p>}

    <h2>Your comparisons</h2>
    {projects === null && <p style={styles.muted}>Loading…</p>}
    {loadError && <div>
      <p role="alert" style={styles.alert}>{loadError}</p>
      <button type="button" style={styles.secondary} onClick={load}>Try again</button>
    </div>}
    {projects !== null && !loadError && projects.length === 0 &&
      <p style={styles.muted}>No comparisons yet. Create one above.</p>}
    {projects !== null && projects.length > 0 && <ul style={styles.list}>
      {projects.map(project => <li key={project.id} style={styles.row}>
        <span>
          <strong>{project.name || 'Untitled comparison'}</strong>
          {formatDate(project.updated_at) && <span style={styles.muted}> · {formatDate(project.updated_at)}</span>}
        </span>
        <button type="button" style={styles.secondary} disabled={busy !== ''} onClick={() => open(project.id)}>
          {busy === project.id ? 'Opening…' : 'Open'}
        </button>
      </li>)}
    </ul>}
  </main>
}
