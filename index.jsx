/* Evidence Desk launcher: create, list and open this app's Projects, and offer
 * prompt text to copy into a project's chat.
 *
 * Paper files, evidence and briefs live in ordinary Möbius Projects; this
 * frame only reaches them through the shell's app-scoped Projects runtime,
 * which exposes this app's own templates and projects and nothing else.
 * The template is resolved by its local id, never by installation slug.
 *
 * The launcher never runs Evidence Desk tools and never reads project files:
 * tools run only for the agent in a project chat. The prompts below are text
 * the owner copies, reviews and sends there. Copying uses the documented
 * window.mobius.clipboard.writeText (it resolves to a boolean); when it is
 * unavailable the text is selected for a manual copy. A frame has no API to
 * write project files, so there is deliberately no upload control here: the
 * instructions point to the Upload tool of the project file list, which
 * writes into the folder that is open (inbox/). Each prompt must stay
 * identical to the template action with the same id in mobius.json (tested),
 * and must not contain a single quote.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

const TEMPLATE_ID = 'paper-comparison'
const TOPIC_PLACEHOLDER = '[describe the topic here]'

const PROMPTS = [
  {
    id: 'find-papers',
    title: 'Find papers',
    use: 'Searches arXiv for a topic and saves only the papers you choose to the project library.',
    text: 'Find papers on arXiv about this topic: [describe the topic here]. Use search_literature and show me the titles, authors, years and arXiv ids. Use save_reference only for the papers I choose. Library records are metadata, not evidence: do not download PDFs and do not record findings from abstracts.',
  },
  {
    id: 'register-and-check',
    title: 'Register PDFs and check evidence',
    use: 'Registers the PDFs you uploaded to inbox/, then checks every recorded quotation.',
    text: 'Register the PDFs I uploaded to inbox/ with add_source and list the sources. Then run check_evidence and report every failed quotation and every dimension that is still Not assessed. Fix a quotation only by re-reading its source page. Never invent findings and never loosen a quotation to make it pass.',
  },
  {
    id: 'export-review',
    title: 'Export and review the comparison',
    use: 'Exports the comparison and explains what it shows, including measurements that were not plotted.',
    text: 'Run check_evidence. If no evidence file is invalid, run export_comparison. Then summarize what the comparison shows and which measurements were not plotted, with the reason for each. Plot or compare measurements only when every comparison requirement is met. Tell me to open the Evidence comparison view or exports/comparison.html.',
  },
]

// Möbius theme variables only (--text, --muted, --border, --surface, --bg, --accent, ...), so the
// page follows the owner's light or dark theme. Classes are prefixed ed- to stay local to this page.
const CSS = `
.ed { max-width: 720px; margin: 0 auto; padding: 20px 16px 32px; color: var(--text); font-family: var(--font); font-size: 14px; line-height: 1.45; }
.ed *, .ed *::before, .ed *::after { box-sizing: border-box; }
.ed h1 { margin: 0; font-size: 22px; line-height: 1.2; font-weight: 650; }
.ed h2 { margin: 0; font-size: 15px; font-weight: 600; }
.ed h3 { margin: 0; font-size: 14px; font-weight: 600; }
.ed p { margin: 0; }
.ed :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.ed-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.ed-muted { color: var(--muted); }
.ed-lead { margin-top: 4px; color: var(--muted); }
.ed-steps { list-style: none; margin: 14px 0 0; padding: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px 12px; }
.ed-steps li { display: flex; gap: 8px; align-items: flex-start; font-size: 13px; color: var(--muted); }
.ed-steps span { min-width: 0; overflow-wrap: anywhere; }
.ed-steps b { flex: none; display: grid; place-items: center; width: 20px; height: 20px; border-radius: 50%; background: var(--accent-dim); color: var(--text); font-size: 12px; font-weight: 600; }
.ed-note { margin-top: 12px; padding-left: 10px; border-left: 2px solid var(--border); font-size: 13px; color: var(--muted); }
.ed-panel { margin-top: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); overflow: hidden; }
.ed-panel-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; padding: 14px 16px 0; }
.ed-form { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 16px 14px; }
.ed-input { flex: 1 1 200px; min-height: 44px; padding: 0 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg); color: inherit; font: inherit; }
.ed-btn { min-height: 44px; padding: 0 14px; border-radius: 8px; border: 1px solid var(--border); background: transparent; color: var(--text); font: inherit; white-space: nowrap; cursor: pointer; }
.ed-btn:hover:not(:disabled) { background: var(--surface-2); }
.ed-btn:disabled { opacity: 0.55; cursor: default; }
.ed-btn--primary { border-color: var(--accent); background: var(--accent); color: var(--accent-fg); }
.ed-btn--primary:hover:not(:disabled) { background: var(--accent-hover); }
.ed-btn--accent { border-color: var(--accent); }
.ed-list { list-style: none; margin: 0; padding: 0; }
.ed-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 16px; border-top: 1px solid var(--border); }
.ed-row-main { flex: 1 1 160px; min-width: 0; }
.ed-row-name { display: block; font-weight: 600; overflow-wrap: anywhere; }
.ed-row-date { font-size: 12px; color: var(--muted); }
.ed-state { padding: 12px 16px; border-top: 1px solid var(--border); color: var(--muted); }
.ed-alert { padding: 0 16px 12px; color: var(--danger); }
.ed-quick { margin-top: 20px; padding: 14px 16px 16px; border: 1px solid var(--border); border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--border)); border-left: 3px solid var(--accent); border-radius: 12px; background: var(--accent-dim); }
.ed-quick-note { margin-top: 2px; font-size: 13px; color: var(--muted); }
.ed-quick-list { list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 8px; }
.ed-prompt { padding: 10px 12px; border: 1px solid var(--border); border: 1px solid color-mix(in srgb, var(--accent) 22%, var(--border)); border-radius: 10px; background: var(--surface); }
.ed-files { margin-top: 12px; font-size: 13px; color: var(--muted); }
.ed-files summary { padding: 6px 0; cursor: pointer; }
.ed-files ul { margin: 4px 0 0; padding-left: 18px; }
.ed-files li { margin: 4px 0; }
.ed-files strong { color: var(--text); font-weight: 600; }
.ed-prompt-top { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px; }
.ed-prompt-text { flex: 1 1 200px; min-width: 0; }
.ed-prompt-use { font-size: 13px; color: var(--muted); }
.ed-prompt details { margin-top: 4px; }
.ed-prompt summary { padding: 6px 0; font-size: 13px; color: var(--muted); cursor: pointer; }
.ed-prompt textarea { display: block; width: 100%; min-height: 150px; margin-top: 4px; padding: 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); color: inherit; font: inherit; resize: vertical; }
.ed-prompt-actions { display: flex; gap: 8px; margin-top: 8px; }
.ed-msg { margin-top: 6px; font-size: 13px; color: var(--muted); }
.ed-msg--manual { color: var(--text); }
@media (max-width: 560px) { .ed-steps { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 480px) { .ed-form .ed-btn { flex: 1 1 100%; } .ed-prompt-top .ed-btn { flex: 1 1 100%; } }
@media (max-width: 380px) { .ed-steps { grid-template-columns: 1fr; } }
`

function formatDate(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString()
}

function byRecent(a, b) {
  return String(b.updated_at || '').localeCompare(String(a.updated_at || ''))
}

function PromptCard({ prompt }) {
  const [text, setText] = useState(prompt.text)
  const [note, setNote] = useState(null)
  const [open, setOpen] = useState(false)
  const [selectRequest, setSelectRequest] = useState(0)
  const area = useRef(null)
  const fieldId = `prompt-${prompt.id}`

  // The text is selected only once its (collapsed) editor is open and rendered.
  useEffect(() => {
    if (!selectRequest) return
    area.current?.focus()
    area.current?.select()
  }, [selectRequest])

  async function copy() {
    setNote(null)
    let copied = false
    try {
      // Called first, inside the tap itself: the runtime's own copy needs the user gesture.
      copied = (await window.mobius?.clipboard?.writeText?.(text)) === true
    } catch {
      copied = false
    }
    const reminder = text.includes(TOPIC_PLACEHOLDER) ? ` Replace ${TOPIC_PLACEHOLDER} with your topic before you send it.` : ''
    if (copied) {
      setNote({ ok: true, message: `Copied. Paste it into the project chat, review it and send it.${reminder}` })
      return
    }
    setOpen(true)
    setSelectRequest(count => count + 1)
    setNote({
      ok: false,
      message: `Copying is not available here. The text is selected: copy it manually (Ctrl+C, Cmd+C, or long-press on a phone), paste it into the project chat, review it and send it.${reminder}`,
    })
  }

  return <li className="ed-prompt">
    <div className="ed-prompt-top">
      <div className="ed-prompt-text">
        <h3>{prompt.title}</h3>
        <p className="ed-prompt-use">{prompt.use}</p>
      </div>
      <button
        type="button" className="ed-btn ed-btn--accent" onClick={copy} disabled={!text.trim()}
        aria-label={`Copy prompt: ${prompt.title}`}
      >Copy prompt</button>
    </div>
    <details open={open} onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>Edit prompt text</summary>
      <label htmlFor={fieldId} className="ed-sr">{prompt.title} prompt text</label>
      <textarea
        id={fieldId} ref={area} value={text} maxLength={4000}
        onChange={event => { setText(event.target.value); setNote(null) }}
      />
      {text !== prompt.text && <div className="ed-prompt-actions">
        <button type="button" className="ed-btn" onClick={() => { setText(prompt.text); setNote(null) }}>Reset text</button>
      </div>}
    </details>
    <p role="status" className={note && !note.ok ? 'ed-msg ed-msg--manual' : 'ed-msg'} style={note ? undefined : { margin: 0 }}>
      {note ? note.message : ''}
    </p>
  </li>
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
    return <main className="ed">
      <style>{CSS}</style>
      <h1>Evidence Desk</h1>
      <p role="alert" className="ed-alert" style={{ padding: '12px 0 0' }}>
        Projects are unavailable here. Open Evidence Desk from the Möbius app list.
      </p>
    </main>
  }

  return <main className="ed">
    <style>{CSS}</style>

    <header>
      <h1>Evidence Desk</h1>
      <p className="ed-lead">
        Compare research papers with every finding traced to a page and an exact quotation.
      </p>
      <ol className="ed-steps" aria-label="Workflow">
        <li><b aria-hidden="true">1</b><span>Create or open a comparison below.</span></li>
        <li><b aria-hidden="true">2</b><span>In the project, open the inbox/ folder in the file list and choose Upload to add your PDFs.</span></li>
        <li><b aria-hidden="true">3</b><span>Copy a prompt, paste it into the project chat, review it and send it.</span></li>
        <li><b aria-hidden="true">4</b><span>Read synthesis.md (the research summary) or open the comparison view.</span></li>
      </ol>
      <p className="ed-note">
        This page creates and opens comparisons and gives you prompt text to copy. It does not run
        Evidence Desk tools, search for papers, read your PDFs or show project files. All of that happens in
        the project chat, after you review and send a prompt.
      </p>
    </header>

    <section className="ed-panel" aria-labelledby="comparisons-title">
      <div className="ed-panel-head">
        <h2 id="comparisons-title">Your comparisons</h2>
        {projects !== null && projects.length > 0 && <span className="ed-muted">{projects.length}</span>}
      </div>
      <form className="ed-form" onSubmit={create}>
        <label htmlFor="comparison-name" className="ed-sr">Comparison name</label>
        <input
          id="comparison-name" className="ed-input" value={name} maxLength={200}
          placeholder="New comparison name" onChange={event => setName(event.target.value)}
        />
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
      {projects !== null && projects.length > 0 && <ul className="ed-list">
        {projects.map(project => <li key={project.id} className="ed-row">
          <span className="ed-row-main">
            <strong className="ed-row-name">{project.name || 'Untitled comparison'}</strong>
            {formatDate(project.updated_at) && <span className="ed-row-date">Updated {formatDate(project.updated_at)}</span>}
          </span>
          <button type="button" className="ed-btn" disabled={busy !== ''} onClick={() => open(project.id)}>
            {busy === project.id ? 'Opening…' : 'Open project'}
          </button>
        </li>)}
      </ul>}
      <p className="ed-note">
        To delete a comparison, open the Projects directory in Möbius and use the project's own menu there.
        Apps cannot delete projects, so this page has no Delete button; Möbius asks you to confirm before
        anything is removed.{' '}
        <button type="button" className="ed-btn" onClick={browse}>Open Möbius Projects</button>
      </p>
    </section>

    <details className="ed-files">
      <summary>What are the files in a project?</summary>
      <ul>
        <li><strong>inbox/</strong>: your papers. Open it in the project file list and choose Upload to add PDFs; this page cannot upload files for you.</li>
        <li><strong>synthesis.md</strong>: the readable research summary and comparison. Read this first.</li>
        <li><strong>README.md</strong> and <strong>desk.json</strong>: support files, a guide and technical settings the agent keeps. You can ignore them.</li>
        <li>The other folders (sources, evidence, exports, library) are filled in by Evidence Desk and the agent. Do not edit them.</li>
      </ul>
    </details>

    <section className="ed-quick" aria-labelledby="prompts-title">
      <h2 id="prompts-title">Quick prompts for the project chat</h2>
      <p className="ed-quick-note">
        Copy a prompt, paste it into the chat of the project you are working in, review it and send it.
        Nothing is sent from this page. The agent never records a finding without a page and an exact quotation.
        Projects created from this version also show these prompts as buttons on the project page.
      </p>
      <ul className="ed-quick-list">
        {PROMPTS.map(prompt => <PromptCard key={prompt.id} prompt={prompt} />)}
      </ul>
    </section>
  </main>
}
