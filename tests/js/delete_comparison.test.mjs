// Runs against a fake in-memory storage only: no real app data is read or changed.
import assert from 'node:assert/strict'
import test from 'node:test'
import { deleteComparison, loadComparisons } from '../../comparisons.js'

const A = '11111111-1111-4111-8111-111111111111'
const B = '22222222-2222-4222-8222-222222222222'

function fakeStorage(files, { incomplete = false, failOn = null } = {}) {
  const store = new Map(Object.entries(files))
  const removed = []
  return {
    store, removed,
    async get(path) { return store.has(path) ? structuredClone(store.get(path)) : null },
    async set(path, value) { store.set(path, structuredClone(value)) },
    async remove(path) {
      if (path === failOn) throw new Error('boom')
      removed.push(path); store.delete(path)
    },
    async listWithStatus(prefix) {
      const base = prefix.replace(/\/+$/, '') + '/'
      const kids = new Map()
      for (const key of store.keys()) {
        if (!key.startsWith(base)) continue
        const rest = key.slice(base.length)
        const slash = rest.indexOf('/')
        const name = slash === -1 ? rest : rest.slice(0, slash)
        kids.set(name, { name, path: base + name, type: slash === -1 ? 'file' : 'directory' })
      }
      return { entries: [...kids.values()], complete: !incomplete }
    },
  }
}

function seed() {
  return {
    'comparisons.json': { comparisons: [
      { id: A, name: 'Alpha', created: '2026-01-01' },
      { id: B, name: 'Beta', created: '2026-01-02' },
    ] },
    [`workspaces/${A}/README.md`]: 'a',
    [`workspaces/${A}/inbox/p1.pdf`]: 'pdf',
    [`workspaces/${A}/exports/comparison.html`]: 'html',
    [`workspaces/${A}/evidence/S1.json`]: {},
    [`chat_${A}.json`]: { id: 'chat-a' },
    [`workspaces/${B}/README.md`]: 'b',
    [`workspaces/${B}/inbox/p2.pdf`]: 'pdf',
    [`chat_${B}.json`]: { id: 'chat-b' },
    'workspace.json': { id: 'not-a-comparison' },
    'projects/other/file.txt': 'older Möbius Project data',
  }
}

test('deleting one comparison preserves every other file and comparison', async () => {
  const s = fakeStorage(seed())
  const before = new Set(s.store.keys())
  const remaining = await deleteComparison(s, A)
  assert.deepEqual(remaining.map(r => r.id), [B])
  assert.deepEqual((await loadComparisons(s)).map(r => r.id), [B])
  for (const key of before) {
    const mine = key.startsWith(`workspaces/${A}/`) || key === `chat_${A}.json`
    assert.equal(s.store.has(key), !mine || false, key)
  }
  assert.ok(s.removed.every(p => p.startsWith(`workspaces/${A}/`) || p === `chat_${A}.json`), s.removed.join())
  assert.ok(s.store.has('projects/other/file.txt'))
})

test('the registry is rewritten last', async () => {
  const s = fakeStorage(seed())
  let registryWrittenAfterRemovals = false
  const set = s.set
  s.set = async (...a) => { registryWrittenAfterRemovals = s.removed.length === 5; return set(...a) }
  await deleteComparison(s, A)
  assert.ok(registryWrittenAfterRemovals)
})

test('an incomplete listing deletes nothing and keeps the comparison', async () => {
  const s = fakeStorage(seed(), { incomplete: true })
  await assert.rejects(deleteComparison(s, A), /whole comparison/)
  assert.equal(s.removed.length, 0)
  assert.equal((await loadComparisons(s)).length, 2)
})

test('a failure part-way keeps the comparison listed so it can be retried', async () => {
  const s = fakeStorage(seed(), { failOn: `workspaces/${A}/inbox/p1.pdf` })
  await assert.rejects(deleteComparison(s, A), /boom/)
  assert.equal((await loadComparisons(s)).length, 2)
  assert.ok(s.store.has(`workspaces/${B}/inbox/p2.pdf`))
})

test('ids that are not comparison ids are refused', async () => {
  const s = fakeStorage(seed())
  for (const bad of ['', '..', 'x/../y', `${A}/..`, null]) {
    await assert.rejects(deleteComparison(s, bad), /valid comparison/)
  }
  assert.equal(s.removed.length, 0)
})
