/* Evidence Desk workspace: add papers, run the analysis and read the comparison inside this app.
 *
 * Files live in this app's own storage under workspaces/<id>/ (inbox/ for the
 * PDFs, then the same sources/, evidence/, exports/ and synthesis.md a Möbius
 * Project holds). The embedded chat is this app's own chat; the Evidence Desk
 * tools bind it to this workspace from the chat id the app saves in
 * chat_<id>.json. Reading the results never trusts the agent's prose: the
 * comparison view is the service's own export, with citations and quote checks.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

const MAX_PDF_BYTES = 25 * 1024 * 1024 // storage.setBlob limit
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

export const ANALYSIS_PROMPT = [
  'Compare the papers in this workspace.',
  'Read the evidence-desk skill first, then call project_status.',
  'Register every PDF in inbox/ with add_source, record page-level evidence with exact quotations for the six dimensions,',
  'mark anything a paper does not say as not_reported and anything you did not look at as not_assessed,',
  'record contradictions between papers, run check_evidence, fix every refusal, and finish with export_comparison.',
  'Never invent findings and never draw a chart unless the plotting rules allow it.',
].join(' ')

const CSS = `
.edw { margin-top: 22px; border: 1px solid var(--border); border-radius: 14px; background: var(--surface); overflow: hidden; }
.edw-head { padding: 16px 18px 0; }
.edw-head h2 { margin: 0; font-size: 17px; font-weight: 600; }
.edw-sub { margin: 4px 0 0; color: var(--muted); font-size: 13px; }
.edw-block { margin: 14px 18px 0; }
.edw-block:last-child { margin-bottom: 18px; }
.edw-step { display: flex; align-items: center; gap: 8px; margin: 0 0 8px; font-size: 14px; font-weight: 600; }
.edw-step b { display: grid; place-items: center; width: 20px; height: 20px; border-radius: 50%; background: var(--accent-dim); font-size: 12px; }
.edw-drop { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 12px; border: 1px dashed var(--border); border-radius: 12px; background: var(--bg); }
.edw-btn { min-height: 44px; padding: 0 14px; border-radius: 8px; border: 1px solid var(--border); background: transparent; color: var(--text); font: inherit; cursor: pointer; }
.edw-btn:hover:not(:disabled) { background: var(--surface-2); }
.edw-btn:disabled { opacity: 0.55; cursor: default; }
.edw-btn--primary { border-color: var(--accent); background: var(--accent); color: var(--accent-fg); font-weight: 600; }
.edw-btn--primary:hover:not(:disabled) { background: var(--accent-hover); }
.edw-papers { list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 6px; }
.edw-papers li { padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); overflow-wrap: anywhere; }
.edw-msg { margin: 8px 0 0; font-size: 13px; color: var(--muted); }
.edw-err { margin: 8px 0 0; font-size: 13px; color: var(--danger); }
.edw-prompt { margin: 0 0 8px; padding: 10px 12px; border-left: 2px solid var(--border); font-size: 13px; color: var(--muted); white-space: pre-wrap; overflow-wrap: anywhere; }
.edw-chat { height: 440px; border: 1px solid var(--border); border-radius: 12px; overflow: hidden; background: var(--bg); }
.edw-view { width: 100%; height: 560px; border: 1px solid var(--border); border-radius: 12px; background: #fff; }
.edw-text { margin: 0; padding: 12px; max-height: 360px; overflow: auto; border: 1px solid var(--border); border-radius: 12px; background: var(--bg); font-size: 13px; white-space: pre-wrap; overflow-wrap: anywhere; }
.edw-links { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
`

function baseName(entry) {
  const raw = typeof entry === 'string' ? entry : (entry?.path || entry?.name || '')
  return String(raw).split('/').filter(Boolean).pop() || ''
}

// Keep a readable name, drop anything a path could misuse.
function safePdfName(name) {
  const stem = name.replace(/\.pdf$/i, '').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^[.-]+/, '').slice(0, 80)
  return `${stem || 'paper'}.pdf`
}

export default function Workspace() {
  const storage = window.mobius?.storage
  const [wid, setWid] = useState(null)
  const [papers, setPapers] = useState([])
  const [synthesis, setSynthesis] = useState(null)
  const [viewHtml, setViewHtml] = useState(null)
  const [csvReady, setCsvReady] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const mountRef = useRef(null)

  // One workspace per app: its id is created once and kept in storage.
  useEffect(() => {
    if (!storage) return
    let live = true
    ;(async () => {
      try {
        let saved = await storage.get('workspace.json')
        if (!saved || !UUID.test(saved.id || '')) {
          saved = { id: crypto.randomUUID() }
          await storage.set('workspace.json', saved)
        }
        await storage.setText(`workspaces/${saved.id}/README.md`, 'Evidence Desk workspace. Papers go in inbox/; Evidence Desk fills in the rest.\n')
        if (live) setWid(saved.id)
      } catch (cause) {
        if (live) setError(cause?.message || 'Could not open the workspace.')
      }
    })()
    return () => { live = false }
  }, [storage])

  const refresh = useCallback(async () => {
    if (!storage || !wid) return
    const root = `workspaces/${wid}`
    try {
      const { entries } = await storage.listWithStatus(`${root}/inbox/`)
      setPapers((entries || []).map(baseName).filter(name => /\.pdf$/i.test(name)).sort())
    } catch { /* keep what is shown */ }
    try { setSynthesis(await storage.getText(`${root}/synthesis.md`)) } catch { setSynthesis(null) }
    try { setViewHtml(await storage.getText(`${root}/exports/comparison.html`)) } catch { setViewHtml(null) }
    try { setCsvReady((await storage.getText(`${root}/exports/comparison.csv`)) != null) } catch { setCsvReady(false) }
  }, [storage, wid])

  useEffect(() => { refresh() }, [refresh])

  // Embedded chat: same workspace every time, saved as chat_<id>.json.
  useEffect(() => {
    if (!wid || !mountRef.current || !window.mobius?.chat) return
    let handle
    let disposed = false
    window.mobius.chat({
      mount: mountRef.current,
      persist: `chat_${wid}.json`,
      picker: false,
      systemPrompt: 'You are the Evidence Desk analyst for this workspace. Read the evidence-desk skill before working. Use the evidence_desk_* tools; they act on this workspace only. Never invent findings and keep every [S1 p.5] citation tied to an exact quotation.',
      guidance: 'Add PDFs above, then send the analysis request shown above this chat.',
      onTurnDone: () => refresh(),
      onError: cause => setError(cause?.message || 'The chat reported an error.'),
    }).then(h => { if (disposed) h.destroy(); else handle = h })
    return () => { disposed = true; handle?.destroy() }
  }, [wid, refresh])

  async function addFiles(event) {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    if (!files.length || !wid) return
    setBusy(true); setError(''); setMessage('')
    const added = []
    try {
      for (const file of files) {
        if (!/\.pdf$/i.test(file.name) && file.type !== 'application/pdf') throw new Error(`${file.name} is not a PDF.`)
        if (file.size > MAX_PDF_BYTES) throw new Error(`${file.name} is larger than 25 MiB.`)
        const name = safePdfName(file.name)
        await storage.setBlob(`workspaces/${wid}/inbox/${name}`, file)
        added.push(name)
      }
      setMessage(`Added ${added.length} PDF${added.length === 1 ? '' : 's'}.`)
    } catch (cause) {
      setError(cause?.message || 'Could not add the PDF.')
    } finally {
      await refresh()
      setBusy(false)
    }
  }

  async function copyPrompt() {
    try {
      await window.mobius.clipboard.writeText(ANALYSIS_PROMPT)
      setCopied(true)
    } catch { setError('Could not copy; select the request text and copy it by hand.') }
  }

  if (!storage) return null

  return <section className="edw" aria-labelledby="edw-title">
    <style>{CSS}</style>
    <div className="edw-head">
      <h2 id="edw-title">Workspace</h2>
      <p className="edw-sub">Add papers, ask for the analysis and read the comparison here. No Project needed.</p>
    </div>

    <div className="edw-block">
      <h3 className="edw-step"><b aria-hidden="true">1</b>Add papers</h3>
      <div className="edw-drop">
        <label className="edw-btn edw-btn--primary" style={{ display: 'inline-flex', alignItems: 'center' }}>
          {busy ? 'Adding…' : 'Add PDFs'}
          <input type="file" accept="application/pdf,.pdf" multiple disabled={busy || !wid} onChange={addFiles} className="edw-sr" style={{ position: 'absolute', width: 1, height: 1, opacity: 0 }} />
        </label>
        <span className="edw-msg" style={{ margin: 0 }}>PDFs up to 25 MiB each.</span>
      </div>
      {message && <p className="edw-msg" role="status">{message}</p>}
      {error && <p className="edw-err" role="alert">{error}</p>}
      {papers.length > 0 && <ul className="edw-papers" aria-label="Papers">{papers.map(name => <li key={name}>{name}</li>)}</ul>}
    </div>

    <div className="edw-block">
      <h3 className="edw-step"><b aria-hidden="true">2</b>Run the analysis</h3>
      <p className="edw-prompt">{ANALYSIS_PROMPT}</p>
      <button type="button" className="edw-btn" onClick={copyPrompt}>{copied ? 'Copied: paste it into the chat' : 'Copy request'}</button>
      <p className="edw-msg">Paste the request into the chat below and send it. The agent checks every quotation against the page text; nothing is shown as a finding until it passes.</p>
      <div className="edw-chat" ref={mountRef} />
    </div>

    <div className="edw-block">
      <h3 className="edw-step"><b aria-hidden="true">3</b>Read the comparison</h3>
      <button type="button" className="edw-btn" onClick={refresh}>Refresh results</button>
      {viewHtml == null && synthesis == null && <p className="edw-msg">Nothing yet. Results appear after the analysis finishes.</p>}
      {viewHtml != null && <iframe className="edw-view" title="Comparison view" sandbox="allow-scripts" srcDoc={viewHtml} />}
      {synthesis != null && <><h3 className="edw-step" style={{ marginTop: 12 }}>Research summary</h3><pre className="edw-text">{synthesis}</pre></>}
      {csvReady && <p className="edw-msg">A CSV export is saved as exports/comparison.csv in this workspace.</p>}
    </div>
  </section>
}
