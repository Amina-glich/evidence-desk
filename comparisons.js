/* Evidence Desk: the comparison list and per-comparison deletion.
 *
 * Plain functions over the app's storage object, so
 * they run unchanged against a fake in-memory storage in tests.
 *
 * Deleting one comparison removes only its own files: everything under
 * workspaces/<id>/ and its chat link chat_<id>.json. Apps cannot delete a
 * Möbius chat or a folder, so the chat transcript stays in the Möbius chat list
 * and empty folders may remain. Other comparisons and Möbius Projects are
 * never read or changed.
 */
export const REGISTRY = 'comparisons.json'
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

// The saved list, or [] for a new app. An older single workspace (workspace.json)
// becomes the first comparison so nothing already added is lost.
export async function loadComparisons(storage) {
  const saved = await storage.get(REGISTRY)
  if (saved && Array.isArray(saved.comparisons)) {
    return saved.comparisons.filter(row => UUID.test(row?.id || ''))
  }
  const legacy = await storage.get('workspace.json')
  if (legacy && UUID.test(legacy.id || '')) {
    const rows = [{ id: legacy.id, name: 'First comparison', created: new Date().toISOString() }]
    await storage.set(REGISTRY, { comparisons: rows })
    return rows
  }
  return []
}

// Every file path under one folder, following sub-folders. A listing that is not
// complete is an error: deleting from a partial view could leave files behind
// while the comparison disappears from the list.
async function filesUnder(storage, folder, depth = 0) {
  if (depth > 8) throw new Error('This comparison has folders nested too deeply to delete safely.')
  const listing = await storage.listWithStatus(folder)
  if (!listing || listing.complete !== true) {
    throw new Error('Could not read the whole comparison, so nothing more was deleted. Try again.')
  }
  const files = []
  for (const entry of listing.entries || []) {
    const path = String(entry.path || '')
    if (!path.startsWith(`${folder}/`) || path.includes('..')) {
      throw new Error('Unexpected file path while deleting; stopped.')
    }
    if (entry.type === 'dir' || entry.type === 'directory') files.push(...await filesUnder(storage, path, depth + 1))
    else files.push(path)
  }
  return files
}

// Deletes one comparison. Order matters: files first, the list entry last, so a
// failure part-way leaves the comparison in the list and the delete can be retried.
export async function deleteComparison(storage, id) {
  if (!UUID.test(id || '')) throw new Error('Not a valid comparison.')
  const folder = `workspaces/${id}`
  for (const path of await filesUnder(storage, folder)) await storage.remove(path)
  await storage.remove(`chat_${id}.json`)
  // Read the list again so a change made elsewhere is not overwritten.
  const current = await loadComparisons(storage)
  const remaining = current.filter(row => row.id !== id)
  await storage.set(REGISTRY, { comparisons: remaining })
  return remaining
}
