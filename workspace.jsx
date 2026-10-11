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

export const ANALYSIS_PROMPT = [
  'Compare the papers in this workspace.',
  'Read the evidence-desk skill first, then call project_status.',
  'Register every PDF in inbox/ with add_source, record page-level evidence with exact quotations for the six dimensions,',
  'mark anything a paper does not say as not_reported and anything you did not look at as not_assessed,',
  'record contradictions between papers, run check_evidence, fix every refusal, and finish with export_comparison.',
  'Never invent findings and never draw a chart unless the plotting rules allow it.',
].join(' ')

const CSS = `
.edw { width: 100%; max-width: 1000px; margin: 22px auto 0; border: 1px solid var(--border); border-radius: 14px; background: var(--surface); overflow: hidden; }
.edw-head { padding: 16px 18px 0; }
.edw-block, .edw-head { min-width: 0; }
.edw-head { padding: 18px 18px 0; }
@media (min-width: 900px) { .edw-block, .edw-head { padding-left: 28px; padding-right: 28px; } .edw-md { font-size: 14px; } }
@media (max-width: 560px) { .edw { border-radius: 10px; } .edw-block, .edw-head { padding-left: 14px; padding-right: 14px; } .edw-actions .edw-btn, .edw-drop .edw-btn { flex: 1 1 100%; justify-content: center; } }
.edw-back { min-height: 36px; margin-bottom: 10px; color: var(--muted); }
.edw-head h2 { margin: 0; font-size: 17px; font-weight: 600; }
.edw-sub { margin: 4px 0 0; color: var(--muted); font-size: 13px; }
.edw-block { margin: 0; padding: 18px; }
.edw-block + .edw-block, .edw-head + .edw-block { border-top: 1px solid var(--border); margin-top: 16px; }
.edw-block:last-child { padding-bottom: 20px; }
.edw-step { display: flex; align-items: center; gap: 8px; margin: 0 0 10px; font-size: 14px; font-weight: 600; }
.edw-step b { display: grid; place-items: center; width: 20px; height: 20px; border-radius: 50%; background: var(--accent-dim); font-size: 12px; }
.edw-drop { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; max-width: 100%; }
.edw-btn { min-height: 44px; padding: 0 14px; border-radius: 8px; border: 1px solid var(--border); background: transparent; color: var(--text); font: inherit; cursor: pointer; }
.edw-btn:hover:not(:disabled) { background: var(--surface-2); }
.edw-btn:disabled { opacity: 0.55; cursor: default; }
.edw-btn--primary { border-color: var(--accent); background: var(--accent); color: var(--accent-fg); font-weight: 600; }
.edw-btn--primary:hover:not(:disabled) { background: var(--accent-hover); }
.edw-papers { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
.edw-papers li { max-width: 100%; padding: 4px 10px; border: 1px solid var(--border); border-radius: 999px; background: var(--bg); font-size: 13px; overflow-wrap: anywhere; }
.edw-msg { margin: 8px 0 0; font-size: 13px; color: var(--muted); }
.edw-err { margin: 8px 0 0; font-size: 13px; color: var(--danger); }
.edw-explain { margin: 0 0 10px; font-size: 13px; color: var(--muted); }
.edw-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-bottom: 12px; }
.edw-chat { height: clamp(240px, 34vh, 340px); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; background: var(--bg); }
.edw-view { display: block; width: 100%; height: clamp(420px, 72vh, 760px); margin-top: 12px; border: 1px solid var(--border); border-radius: 12px; background: #fff; }
.edw-text { margin: 0; padding: 14px 16px; max-height: min(70vh, 640px); overflow: auto; border: 1px solid var(--border); border-radius: 12px; background: var(--bg); font-size: 13px; white-space: pre-wrap; overflow-wrap: anywhere; }
.edw-md { white-space: normal; line-height: 1.5; }
.edw-md h2, .edw-md h3, .edw-md h4 { margin: 14px 0 6px; font-weight: 650; }
.edw-md h2 { font-size: 18px; } .edw-md h3 { font-size: 15px; } .edw-md h4 { font-size: 14px; }
.edw-md > :first-child { margin-top: 0; }
.edw-md p { margin: 0 0 8px; } .edw-md ul, .edw-md ol { margin: 0 0 8px; padding-left: 20px; }
.edw-md code { padding: 1px 4px; border-radius: 4px; background: var(--surface-2); }
.edw-links { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
`


// A small, safe Markdown renderer for the research summary: headings, lists,
// paragraphs, **bold**, `code`. Builds React elements only (no raw HTML), and
// hides HTML comments such as the agent's writing guide.
function inline(text, key) {
  const parts = []
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0, match, n = 0
  while ((match = pattern.exec(text))) {
    if (match.index > last) parts.push(text.slice(last, match.index))
    const token = match[0]
    parts.push(token.startsWith('**')
      ? <strong key={`${key}-${n++}`}>{token.slice(2, -2)}</strong>
      : <code key={`${key}-${n++}`}>{token.slice(1, -1)}</code>)
    last = match.index + token.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

export function renderMarkdown(source) {
  const lines = String(source).replace(/<!--[\s\S]*?-->/g, '').split(/\r?\n/)
  const out = []
  let paragraph = [], list = null
  const flushParagraph = () => {
    if (paragraph.length) out.push(<p key={`p${out.length}`}>{inline(paragraph.join(' '), `p${out.length}`)}</p>)
    paragraph = []
  }
  const flushList = () => {
    if (list) {
      const Tag = list.ordered ? 'ol' : 'ul'
      out.push(<Tag key={`l${out.length}`}>{list.items.map((item, i) => <li key={i}>{inline(item, `l${out.length}-${i}`)}</li>)}</Tag>)
    }
    list = null
  }
  for (const line of lines) {
    const heading = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line)
    const item = /^\s*(?:([-*+])|(\d+)[.)])\s+(.*)$/.exec(line)
    if (heading) {
      flushParagraph(); flushList()
      const Tag = `h${Math.min(heading[1].length + 1, 6)}`
      out.push(<Tag key={`h${out.length}`}>{inline(heading[2], `h${out.length}`)}</Tag>)
    } else if (item) {
      flushParagraph()
      const ordered = Boolean(item[2])
      if (!list || list.ordered !== ordered) { flushList(); list = { ordered, items: [] } }
      list.items.push(item[3])
    } else if (!line.trim()) {
      flushParagraph(); flushList()
    } else {
      flushList(); paragraph.push(line.trim())
    }
  }
  flushParagraph(); flushList()
  return out
}

function baseName(entry) {
  const raw = typeof entry === 'string' ? entry : (entry?.path || entry?.name || '')
  return String(raw).split('/').filter(Boolean).pop() || ''
}

// Keep a readable name, drop anything a path could misuse.
function safePdfName(name) {
  const stem = name.replace(/\.pdf$/i, '').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^[.-]+/, '').slice(0, 80)
  return `${stem || 'paper'}.pdf`
}

export default function Comparison({ id: wid, name, onBack }) {
  const storage = window.mobius?.storage
  const [papers, setPapers] = useState([])
  const [synthesis, setSynthesis] = useState(null)
  const [viewHtml, setViewHtml] = useState(null)
  const [csvReady, setCsvReady] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [copiedExport, setCopiedExport] = useState('')
  const mountRef = useRef(null)

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

  // Mini-app frames have no download API, so exports are copied as text to paste into a file.
  async function copyExport(key, text) {
    try {
      await window.mobius.clipboard.writeText(text)
      setCopiedExport(key)
      setError('')
    } catch { setError('Could not copy this export.') }
  }
  async function copyCsv() {
    try {
      const text = await storage.getText(`workspaces/${wid}/exports/comparison.csv`)
      if (text == null) throw new Error('missing')
      await copyExport('csv', text)
    } catch { setError('Could not read the CSV export. Refresh the results and try again.') }
  }

  if (!storage) return null

  return <section className="edw" aria-labelledby="edw-title">
    <style>{CSS}</style>
    <div className="edw-head">
      <button type="button" className="edw-btn edw-back" onClick={onBack}>← All comparisons</button>
      <h2 id="edw-title">{name}</h2>
      <p className="edw-sub">Add papers, ask for the analysis and read the comparison here. Each comparison keeps its own papers, chat and results.</p>
    </div>

    <div className="edw-block">
      <h3 className="edw-step"><b aria-hidden="true">1</b>Add papers</h3>
      <div className="edw-drop">
        <label className="edw-btn edw-btn--primary" style={{ display: 'inline-flex', alignItems: 'center' }}>
          {busy ? 'Adding…' : 'Add PDFs'}
          <input type="file" accept="application/pdf,.pdf" multiple disabled={busy} onChange={addFiles} style={{ position: 'absolute', width: 1, height: 1, opacity: 0 }} />
        </label>
        <span className="edw-msg" style={{ margin: 0 }}>PDFs up to 25 MiB each.</span>
      </div>
      {message && <p className="edw-msg" role="status">{message}</p>}
      {error && <p className="edw-err" role="alert">{error}</p>}
      {papers.length > 0 && <ul className="edw-papers" aria-label="Papers">{papers.map(name => <li key={name}>{name}</li>)}</ul>}
    </div>

    <div className="edw-block">
      <h3 className="edw-step"><b aria-hidden="true">2</b>Run the analysis</h3>
      <p className="edw-explain">Ask the assistant to read your papers and compare them. Every finding is tied to a page and an exact quotation, and anything a paper does not say is marked as such.</p>
      <div className="edw-actions">
        <button type="button" className="edw-btn" onClick={copyPrompt}>{copied ? 'Copied' : 'Copy request'}</button>
        <span className="edw-msg" style={{ margin: 0 }}>{copied ? 'Now paste it into the chat below and send.' : 'Copy, then paste it into the chat below and send.'}</span>
      </div>
      <div className="edw-chat" ref={mountRef} />
    </div>

    <div className="edw-block">
      <h3 className="edw-step"><b aria-hidden="true">3</b>Read the comparison</h3>
      <button type="button" className="edw-btn" onClick={refresh}>Refresh results</button>
      {viewHtml == null && synthesis == null && <p className="edw-msg">Nothing yet. Results appear after the analysis finishes.</p>}
      {viewHtml != null && <iframe className="edw-view" title="Comparison view" sandbox="allow-scripts" srcDoc={viewHtml} />}
      {synthesis != null && <><h3 className="edw-step" style={{ marginTop: 12 }}>Research summary</h3><div className="edw-text edw-md">{renderMarkdown(synthesis)}</div></>}
      {(synthesis != null || viewHtml != null || csvReady) && <div className="edw-exports">
        <h3 className="edw-step" style={{ marginTop: 12 }}>Copy exports</h3>
        <p className="edw-msg">Mini-apps cannot download files, so copy an export and paste it into a file you save yourself (for example summary.md, comparison.html, comparison.csv).</p>
        <div className="edw-actions">
          {synthesis != null && <button type="button" className="edw-btn" onClick={() => copyExport('summary', synthesis)}>{copiedExport === 'summary' ? 'Copied summary' : 'Copy summary (Markdown)'}</button>}
          {viewHtml != null && <button type="button" className="edw-btn" onClick={() => copyExport('html', viewHtml)}>{copiedExport === 'html' ? 'Copied HTML' : 'Copy comparison (HTML)'}</button>}
          {csvReady && <button type="button" className="edw-btn" onClick={copyCsv}>{copiedExport === 'csv' ? 'Copied CSV' : 'Copy comparison (CSV)'}</button>}
        </div>
      </div>}
    </div>
  </section>
}
