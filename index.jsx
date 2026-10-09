/* Evidence Desk launcher: create, list and open this app's Projects.
 *
 * Paper files, evidence and briefs live in ordinary Möbius Projects; this
 * frame only reaches them through the shell's app-scoped Projects runtime,
 * which exposes this app's own templates and projects and nothing else.
 * The template is resolved by its local id, never by installation slug.
 *
 * The launcher never runs Evidence Desk tools and never reads project files:
 * tools run only for the agent in a project chat, where the Paper comparison
 * template offers its own prompt actions. A frame has no API to write project
 * files, so there is deliberately no upload control here: the instructions
 * point to the Upload tool of the project file list, which writes into the
 * folder that is open (inbox/). Möbius gives apps no way to delete a project,
 * so there is no Delete control either.
 */
import { useCallback, useEffect, useState } from 'react'

const TEMPLATE_ID = 'paper-comparison'

// Möbius theme variables only (--text, --muted, --border, --surface, --bg, --accent, ...), so the
// page follows the owner's light or dark theme. Classes are prefixed ed- to stay local to this page.
const CSS = `
.ed-shell { min-height: 100%; background: radial-gradient(900px 360px at 50% -140px, var(--accent-dim), transparent 70%); }
.ed { max-width: 760px; margin: 0 auto; padding: 28px 16px 40px; color: var(--text); font-family: var(--font); font-size: 14px; line-height: 1.45; }
.ed *, .ed *::before, .ed *::after { box-sizing: border-box; }
.ed h1 { margin: 0; font-size: 24px; line-height: 1.2; font-weight: 650; }
.ed h2 { margin: 0; font-size: 15px; font-weight: 600; }
.ed h3 { margin: 0; font-size: 14px; font-weight: 600; }
.ed p { margin: 0; }
.ed :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.ed-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.ed-muted { color: var(--muted); }
.ed-hero { display: flex; align-items: center; gap: 14px; }
.ed-mark { flex: none; display: grid; place-items: center; width: 46px; height: 46px; border-radius: 12px; border: 1px solid var(--border); border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--border)); background: var(--accent-dim); color: var(--accent); }
.ed-mark svg { width: 24px; height: 24px; }
.ed-lead { margin-top: 4px; color: var(--muted); }
.ed-panel { margin-top: 22px; border: 1px solid var(--border); border-radius: 14px; background: var(--surface); overflow: hidden; }
.ed-panel-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 16px 18px 0; }
.ed-panel-head h2 { font-size: 17px; }
.ed-count { min-width: 24px; padding: 1px 8px; border-radius: 999px; background: var(--surface-2); color: var(--muted); font-size: 12px; text-align: center; }
.ed-form { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 8px 10px; margin: 14px 18px 0; padding: 12px; border: 1px solid var(--border); border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--border)); border-radius: 12px; background: var(--accent-dim); }
.ed-form-field { flex: 1 1 220px; min-width: 0; }
.ed-form-label { display: block; margin-bottom: 4px; font-size: 12px; color: var(--muted); }
.ed-input { width: 100%; min-height: 46px; padding: 0 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg); color: inherit; font: inherit; }
.ed-btn { min-height: 44px; padding: 0 14px; border-radius: 8px; border: 1px solid var(--border); background: transparent; color: var(--text); font: inherit; white-space: nowrap; cursor: pointer; }
.ed-btn:hover:not(:disabled) { background: var(--surface-2); }
.ed-btn:disabled { opacity: 0.55; cursor: default; }
.ed-btn--primary { min-height: 46px; padding: 0 20px; border-color: var(--accent); background: var(--accent); color: var(--accent-fg); font-weight: 600; }
.ed-btn--primary:hover:not(:disabled) { background: var(--accent-hover); }
.ed-btn--accent { border-color: var(--accent); }
.ed-list { list-style: none; margin: 0; padding: 14px 18px 18px; display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }
.ed-card { display: flex; flex-direction: column; justify-content: space-between; gap: 12px; min-width: 0; padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: var(--bg); }
.ed-card:hover { border-color: var(--accent); }
.ed-card-main { min-width: 0; }
.ed-row-name { display: block; font-weight: 600; overflow-wrap: anywhere; }
.ed-row-date { display: block; margin-top: 2px; font-size: 12px; color: var(--muted); }
.ed-card .ed-btn { width: 100%; }
.ed .ed-state { margin: 14px 18px 18px; padding: 18px; border: 1px dashed var(--border); border-radius: 12px; text-align: center; color: var(--muted); }
.ed-alert { padding: 10px 18px 0; color: var(--danger); }
.ed .ed-hint { padding: 12px 18px 0; font-size: 13px; color: var(--muted); }
.ed-note { margin-top: 12px; padding-left: 10px; border-left: 2px solid var(--border); font-size: 13px; color: var(--muted); }
.ed-how { margin-top: 18px; border: 1px solid var(--border); border-radius: 12px; font-size: 13px; color: var(--muted); }
.ed-how summary { padding: 11px 14px; cursor: pointer; font-weight: 600; }
.ed-how-body { padding: 0 14px 14px; }
.ed-steps { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px 12px; }
.ed-steps li { display: flex; gap: 8px; align-items: flex-start; font-size: 13px; color: var(--muted); }
.ed-steps span { min-width: 0; overflow-wrap: anywhere; }
.ed-steps b { flex: none; display: grid; place-items: center; width: 20px; height: 20px; border-radius: 50%; background: var(--accent-dim); color: var(--text); font-size: 12px; font-weight: 600; }
.ed-manage { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px 16px; margin-top: 28px; padding-top: 14px; border-top: 1px solid var(--border); }
.ed-manage-text { flex: 1 1 260px; min-width: 0; }
.ed-manage h2 { font-size: 13px; color: var(--muted); }
.ed-manage p { margin-top: 2px; font-size: 13px; color: var(--muted); }
.ed-files { margin-top: 12px; font-size: 13px; color: var(--muted); }
.ed-files summary { padding: 6px 0; cursor: pointer; }
.ed-files ul { margin: 4px 0 0; padding-left: 18px; }
.ed-files li { margin: 4px 0; }
.ed-files strong { color: var(--text); font-weight: 600; }
@media (max-width: 560px) { .ed-steps { grid-template-columns: repeat(2, minmax(0, 1fr)); } .ed-list { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 480px) { .ed-form .ed-btn { flex: 1 1 100%; } .ed-manage .ed-btn { flex: 1 1 100%; } }
@media (max-width: 380px) { .ed-steps { grid-template-columns: 1fr; } .ed-panel-head, .ed-list { padding-left: 12px; padding-right: 12px; } .ed-form, .ed .ed-state { margin-left: 12px; margin-right: 12px; } }
`

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
  const [howOpen, setHowOpen] = useState(false)

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

  // With no comparison yet, the short guide is the most useful thing on the page, so it starts open.
  useEffect(() => {
    if (projects !== null && projects.length === 0 && !loadError) setHowOpen(true)
  }, [projects, loadError])

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

  // Möbius apps can list, create and open their projects, and open the Projects directory. They cannot
  // delete a project, so deleting is left to Möbius itself, which asks for confirmation there.
  async function browse() {
    setActionError('')
    try {
      await runtime.browse()
    } catch (cause) {
      setActionError(cause?.message || 'Could not open the Projects directory.')
    }
  }

  if (!runtime) {
    return <div className="ed-shell"><main className="ed">
      <style>{CSS}</style>
      <h1>Evidence Desk</h1>
      <p role="alert" className="ed-alert" style={{ padding: '12px 0 0' }}>
        Projects are unavailable here. Open Evidence Desk from the Möbius app list.
      </p>
    </main></div>
  }

  return <div className="ed-shell"><main className="ed">
    <style>{CSS}</style>

    <header className="ed-hero">
      <span className="ed-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" />
          <path d="M14 3v5h5" />
          <path d="m9 15 2 2 4-4" />
        </svg>
      </span>
      <div>
        <h1>Evidence Desk</h1>
        <p className="ed-lead">
          Compare research papers with every finding traced to a page and an exact quotation.
        </p>
      </div>
    </header>

    <section className="ed-panel" aria-labelledby="comparisons-title">
      <div className="ed-panel-head">
        <h2 id="comparisons-title">Your comparisons</h2>
        {projects !== null && projects.length > 0 && <span className="ed-count" aria-label={`${projects.length} comparisons`}>{projects.length}</span>}
      </div>
      <form className="ed-form" onSubmit={create}>
        <div className="ed-form-field">
          <label htmlFor="comparison-name" className="ed-form-label">Name your comparison (optional)</label>
          <input
            id="comparison-name" className="ed-input" value={name} maxLength={200}
            placeholder="New comparison name" onChange={event => setName(event.target.value)}
          />
        </div>
        <button type="submit" className="ed-btn ed-btn--primary" disabled={busy !== ''}>
          {busy === 'create' ? 'Creating…' : 'New comparison'}
        </button>
      </form>
      {actionError && <p role="alert" className="ed-alert">{actionError}</p>}

      {projects === null && <p className="ed-state">Loading…</p>}
      {loadError && <div className="ed-state">
        <p role="alert" style={{ color: 'var(--danger)', marginBottom: 8 }}>{loadError}</p>
        <button type="button" className="ed-btn" onClick={load}>Try again</button>
      </div>}
      {projects !== null && !loadError && projects.length === 0 &&
        <p className="ed-state">No comparisons yet. Create one above.</p>}
      {projects !== null && projects.length > 0 && <p className="ed-hint">Open a project to continue your paper review.</p>}
      {projects !== null && projects.length > 0 && <ul className="ed-list">
        {projects.map(project => <li key={project.id} className="ed-card">
          <span className="ed-card-main">
            <strong className="ed-row-name">{project.name || 'Untitled comparison'}</strong>
            {formatDate(project.updated_at) && <span className="ed-row-date">Updated {formatDate(project.updated_at)}</span>}
          </span>
          <button type="button" className="ed-btn" disabled={busy !== ''} onClick={() => open(project.id)}>
            {busy === project.id ? 'Opening…' : 'Open project'}
          </button>
        </li>)}
      </ul>}
    </section>

    <section className="ed-manage" aria-labelledby="manage-title">
      <div className="ed-manage-text">
        <h2 id="manage-title">Manage projects</h2>
        <p>
          Projects are deleted from Möbius Projects, where Möbius asks you to confirm before anything is removed.
        </p>
      </div>
      <button type="button" className="ed-btn" onClick={browse}>Open Möbius Projects</button>
    </section>

    <details className="ed-how" open={howOpen} onToggle={event => setHowOpen(event.currentTarget.open)}>
      <summary>How it works</summary>
      <div className="ed-how-body">
        <ol className="ed-steps" aria-label="Workflow">
          <li><b aria-hidden="true">1</b><span>Create or open a comparison above.</span></li>
          <li><b aria-hidden="true">2</b><span>In the project, open the inbox/ folder in the file list and choose Upload to add your PDFs.</span></li>
          <li><b aria-hidden="true">3</b><span>In the project chat, use the prompt buttons on the project page (projects created from this version) or your own words, and review each request before you send it.</span></li>
          <li><b aria-hidden="true">4</b><span>Read synthesis.md (the research summary) or open the comparison view.</span></li>
        </ol>
        <p className="ed-note">
          This page creates and opens comparisons. It does not run
          Evidence Desk tools, search for papers, read your PDFs or show project files. All of that happens in
          the project chat, after you review and send a prompt.
        </p>
      </div>
    </details>

    <details className="ed-files">
      <summary>What are the files in a project?</summary>
      <ul>
        <li><strong>inbox/</strong>: your papers. Open it in the project file list and choose Upload to add PDFs; this page cannot upload files for you.</li>
        <li><strong>synthesis.md</strong>: the readable research summary and comparison. Read this first.</li>
        <li><strong>README.md</strong> and <strong>desk.json</strong>: support files, a guide and technical settings the agent keeps. You can ignore them.</li>
        <li>The other folders (sources, evidence, exports, library) are filled in by Evidence Desk and the agent. Do not edit them.</li>
      </ul>
    </details>
  </main></div>
}
