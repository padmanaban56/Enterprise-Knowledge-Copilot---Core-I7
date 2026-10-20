export function documentFilename(doc) {
  for (const value of [doc.source_file, doc.filename]) {
    const filename = typeof value === 'string' ? value.trim().split(/[\\/]/).filter(Boolean).pop() : ''
    if (filename) return filename
  }
  return doc.title?.trim() || doc.name?.trim() || 'Untitled document'
}
