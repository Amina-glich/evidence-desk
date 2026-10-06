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
 * unavailable the text is selected for a manual copy. Each prompt must stay
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
  note: {
    margin: '16px 0', padding: '10px 12px', borderRadius: 8,
    border: '1px solid var(--border)', background: 'var(--surface)',
  },
  steps: { margin: '8px 0 24px', paddingLeft: 22, lineHeight: 1.5 },
  card: { margin: '16px 0', padding: 12, borderRadius: 8, border: '1px solid var(--border)' },
  textarea: {
    display: 'block', width: '100%', boxSizing: 'border-box', minHeight: 208, margin: '8px 0',
    padding: 8, borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)',
    color: 'var(--text)', font: 'inherit', resize: 'vertical',
  },
  actions: { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' },
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

function PromptCard({ prompt }) {
  const [text, setText] = useState(prompt.text)
  const [note, setNote] = useState(null)
  const area = useRef(null)
  const fieldId = `prompt-${prompt.id}`

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
    area.current?.focus()
    area.current?.select()
    setNote({
      ok: false,
      message: `Copying is not available here. The text is selected: copy it manually (Ctrl+C, Cmd+C, or long-press on a phone), paste it into the project chat, review it and send it.${reminder}`,
    })
  }

  return <section style={styles.card} aria-labelledby={`${fieldId}-title`}>
    <h3 id={`${fieldId}-title`} style={{ margin: 0 }}>{prompt.title}</h3>
    <p style={{ ...styles.muted, margin: '4px 0 0' }}>{prompt.use}</p>
    <label htmlFor={fieldId} style={{ position: 'absolute', left: -9999 }}>{prompt.title} prompt text</label>
    <textarea
      id={fieldId} ref={area} style={styles.textarea} value={text} maxLength={4000}
      onChange={event => { setText(event.target.value); setNote(null) }}
    />
    <div style={styles.actions}>
      <button type="button" style={styles.secondary} onClick={copy} disabled={!text.trim()}>Copy prompt</button>
      {text !== prompt.text && <button type="button" style={styles.secondary} onClick={() => { setText(prompt.text); setNote(null) }}>Reset text</button>}
    </div>
    <p role="status" style={note && !note.ok ? styles.alert : styles.muted}>{note ? note.message : ''}</p>
  </section>
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
      Each comparison is a Möbius project.
    </p>
    <p style={styles.note}>
      This page creates and opens comparisons and gives you prompt text to copy. It does not run
      Evidence Desk tools, search for papers, read your PDFs or show project files. All of that happens in
      the project chat, after you review and send a prompt.
    </p>

    <h2>How it works</h2>
    <ol style={styles.steps}>
      <li>Create a comparison below, or open one you already have.</li>
      <li>In the project, upload your PDFs to the inbox/ folder.</li>
      <li>
        Copy a prompt from this page, paste it into the project chat, review it and send it. In a project
        created from this version, the same prompts are also buttons on the project page that open an
        editable draft.
      </li>
      <li>Open the results in the project: the Evidence comparison view, or exports/comparison.html.</li>
    </ol>

    <h2>Your comparisons</h2>
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
          {busy === project.id ? 'Opening…' : 'Open project'}
        </button>
      </li>)}
    </ul>}

    <h2>Prompts for the project chat</h2>
    <p style={styles.muted}>
      Edit the text if you like, copy it, then paste it into the chat of the project you are working in.
      Nothing is sent from this page. The agent never records a finding without a page and an exact quotation.
    </p>
    {PROMPTS.map(prompt => <PromptCard key={prompt.id} prompt={prompt} />)}
  </main>
}
