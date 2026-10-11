/* Evidence Desk: compare research papers entirely inside this app.
 *
 * The home screen lists your comparisons and starts new ones. Each comparison
 * is its own folder in this app's storage (workspaces/<id>/) with its own
 * papers, its own embedded chat (chat_<id>.json) and its own results, so
 * nothing is shared between comparisons. The list of names is comparisons.json. Deleting a comparison (comparisons.js)
 * removes only its own folder and chat link.
 */
import { useCallback, useEffect, useState } from 'react'
import Comparison from './workspace.jsx'
import { REGISTRY, loadComparisons, deleteComparison } from './comparisons.js'

// Möbius theme variables only, so the page follows the owner's light or dark theme.
const CSS = `
.ed-shell { min-height: 100%; background: radial-gradient(900px 360px at 50% -140px, var(--accent-dim), transparent 70%); }
.ed { max-width: 1000px; margin: 0 auto; padding: 28px 16px 40px; color: var(--text); font-family: var(--font); font-size: 14px; line-height: 1.45; }
.ed *, .ed *::before, .ed *::after { box-sizing: border-box; }
.ed h1 { margin: 0; font-size: 24px; line-height: 1.2; font-weight: 650; }
.ed h2 { margin: 0; font-size: 17px; font-weight: 600; }
.ed p { margin: 0; }
.ed :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.ed-hero { display: flex; align-items: center; gap: 14px; }
.ed-mark { flex: none; display: grid; place-items: center; width: 46px; height: 46px; border-radius: 12px; border: 1px solid var(--border); background: var(--accent-dim); color: var(--accent); }
.ed-mark svg { width: 24px; height: 24px; }
.ed-lead { margin-top: 4px; color: var(--muted); }
.ed-panel { width: 100%; margin-top: 22px; border: 1px solid var(--border); border-radius: 14px; background: var(--surface); overflow: hidden; }
.ed-panel-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 16px 18px 0; }
.ed-count { min-width: 24px; padding: 1px 8px; border-radius: 999px; background: var(--surface-2); color: var(--muted); font-size: 12px; text-align: center; }
.ed-form { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 8px 10px; margin: 14px 18px 0; padding: 12px; border: 1px solid var(--border); border-radius: 12px; background: var(--accent-dim); }
.ed-form-field { flex: 1 1 220px; min-width: 0; }
.ed-form-label { display: block; margin-bottom: 4px; font-size: 12px; color: var(--muted); }
.ed-input { width: 100%; min-height: 46px; padding: 0 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg); color: inherit; font: inherit; }
.ed-btn { min-height: 44px; padding: 0 14px; border-radius: 8px; border: 1px solid var(--border); background: transparent; color: var(--text); font: inherit; white-space: nowrap; cursor: pointer; }
.ed-btn:hover:not(:disabled) { background: var(--surface-2); }
.ed-btn:disabled { opacity: 0.55; cursor: default; }
.ed-btn--primary { min-height: 46px; padding: 0 20px; border-color: var(--accent); background: var(--accent); color: var(--accent-fg); font-weight: 600; }
.ed-btn--primary:hover:not(:disabled) { background: var(--accent-hover); }
.ed-hint { padding: 12px 18px 0; font-size: 13px; color: var(--muted); }
.ed-list { list-style: none; margin: 0; padding: 14px 18px 18px; display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }
.ed-card { display: flex; flex-direction: column; justify-content: space-between; gap: 12px; min-width: 0; padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: var(--bg); }
.ed-card:hover { border-color: var(--accent); }
.ed-row-name { display: block; font-weight: 600; overflow-wrap: anywhere; }
.ed-row-date { display: block; margin-top: 2px; font-size: 12px; color: var(--muted); }
.ed-card .ed-btn { width: 100%; }
.ed-btn--quiet { min-height: 36px; font-size: 13px; color: var(--muted); }
.ed-btn--danger { border-color: var(--danger); background: var(--danger); color: #fff; font-weight: 600; white-space: normal; }
.ed-confirm { display: grid; gap: 8px; padding: 10px; border: 1px solid var(--danger); border-radius: 10px; font-size: 13px; }
.ed-confirm-actions { display: grid; gap: 8px; }
.ed-state { margin: 14px 18px 18px; padding: 18px; border: 1px dashed var(--border); border-radius: 12px; text-align: center; color: var(--muted); }
.ed-alert { padding: 10px 18px 0; color: var(--danger); }
@media (max-width: 560px) { .ed-list { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 480px) { .ed-form .ed-btn { flex: 1 1 100%; } }
`

function formatDate(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString()
}

export default function App() {
  const storage = window.mobius?.storage
  // null while loading; an array once loaded.
  const [comparisons, setComparisons] = useState(null)
  const [openId, setOpenId] = useState(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // The comparison awaiting delete confirmation, and the one being deleted.
  const [confirmId, setConfirmId] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const [deleteError, setDeleteError] = useState('')

  const load = useCallback(async () => {
    if (!storage) return
    setError('')
    try {
      setComparisons(await loadComparisons(storage))
    } catch (cause) {
      setError(cause?.message || 'Could not load your comparisons.')
      setComparisons([])
    }
  }, [storage])

  useEffect(() => { load() }, [load])

  async function create(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      // Read again so a list changed elsewhere is never overwritten.
      const current = await loadComparisons(storage)
      const row = { id: crypto.randomUUID(), name: name.trim() || 'Untitled comparison', created: new Date().toISOString() }
      await storage.setText(`workspaces/${row.id}/README.md`, 'Evidence Desk comparison. Papers go in inbox/; Evidence Desk fills in the rest.\n')
      await storage.set(REGISTRY, { comparisons: [row, ...current] })
      setComparisons([row, ...current])
      setName('')
      setOpenId(row.id)
    } catch (cause) {
      setError(cause?.message || 'Could not create the comparison.')
    } finally {
      setBusy(false)
    }
  }

  // Deletes only this comparison's own files and list entry (see comparisons.js).
  async function confirmDelete(row) {
    setDeletingId(row.id)
    setDeleteError('')
    try {
      setComparisons(await deleteComparison(storage, row.id))
      setConfirmId(null)
    } catch (cause) {
      setDeleteError(`Could not delete “${row.name || 'Untitled comparison'}”: ${cause?.message || 'unknown error'}`)
    } finally {
      setDeletingId(null)
    }
  }

  if (!storage) {
    return <div className="ed-shell"><main className="ed">
      <style>{CSS}</style>
      <h1>Evidence Desk</h1>
      <p role="alert" className="ed-alert" style={{ padding: '12px 0 0' }}>
        Storage is unavailable here. Open Evidence Desk from the Möbius app list.
      </p>
    </main></div>
  }

  const open = (comparisons || []).find(row => row.id === openId)
  if (open) {
    return <div className="ed-shell"><main className="ed">
      <Comparison key={open.id} id={open.id} name={open.name} onBack={() => setOpenId(null)} />
    </main></div>
  }

  const rows = comparisons === null ? null : [...comparisons].sort((a, b) => String(b.created || '').localeCompare(String(a.created || '')))

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
        {rows !== null && rows.length > 0 && <span className="ed-count" aria-label={`${rows.length} comparisons`}>{rows.length}</span>}
      </div>
      <form className="ed-form" onSubmit={create}>
        <div className="ed-form-field">
          <label htmlFor="comparison-name" className="ed-form-label">Name your comparison (optional)</label>
          <input
            id="comparison-name" className="ed-input" value={name} maxLength={200}
            placeholder="New comparison name" onChange={event => setName(event.target.value)}
          />
        </div>
        <button type="submit" className="ed-btn ed-btn--primary" disabled={busy}>
          {busy ? 'Creating…' : 'New comparison'}
        </button>
      </form>
      {error && <p role="alert" className="ed-alert">{error}</p>}

      {rows === null && <p className="ed-state">Loading…</p>}
      {rows !== null && rows.length === 0 && <p className="ed-state">No comparisons yet. Create one above, then add your PDFs inside it.</p>}
      {rows !== null && rows.length > 0 && <p className="ed-hint">Open a comparison to add papers, run the analysis and read the results.</p>}
      {rows !== null && rows.length > 0 && <ul className="ed-list">
        {rows.map(row => <li key={row.id} className="ed-card">
          <span>
            <strong className="ed-row-name">{row.name || 'Untitled comparison'}</strong>
            {formatDate(row.created) && <span className="ed-row-date">Started {formatDate(row.created)}</span>}
          </span>
          <button type="button" className="ed-btn" onClick={() => setOpenId(row.id)}>Open comparison</button>
          {confirmId === row.id
            ? <div className="ed-confirm" role="alertdialog" aria-label={`Delete ${row.name || 'Untitled comparison'}`}>
                <p><strong>Delete “{row.name || 'Untitled comparison'}”?</strong></p>
                <p>This permanently removes this comparison’s papers, results and saved chat link. Your other comparisons and Möbius Projects are not touched. Its chat conversation stays in your Möbius chat list; delete it there if you want it gone.</p>
                <div className="ed-confirm-actions">
                  <button type="button" className="ed-btn ed-btn--danger" disabled={deletingId === row.id} onClick={() => confirmDelete(row)}>
                    {deletingId === row.id ? 'Deleting…' : `Delete “${row.name || 'Untitled comparison'}”`}
                  </button>
                  <button type="button" className="ed-btn" disabled={deletingId === row.id} onClick={() => setConfirmId(null)}>Cancel</button>
                </div>
              </div>
            : <button type="button" className="ed-btn ed-btn--quiet" disabled={deletingId !== null}
                aria-label={`Delete comparison ${row.name || 'Untitled comparison'}`}
                onClick={() => { setDeleteError(''); setConfirmId(row.id) }}>Delete comparison</button>}
        </li>)}
      </ul>}
      {deleteError && <p role="alert" className="ed-alert">{deleteError}</p>}
    </section>
  </main></div>
}
