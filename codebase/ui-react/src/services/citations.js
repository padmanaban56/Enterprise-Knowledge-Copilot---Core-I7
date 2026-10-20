export function citationHref(citation) {
  if (citation.doc_type === 'Ticket') {
    return `/tickets?search=${encodeURIComponent(citation.ticket_id || citation.section || citation.source || '')}`
  }
  const params = new URLSearchParams()
  if (citation.doc_id) params.set('doc_id', citation.doc_id)
  if (citation.source) params.set('source', citation.source)
  if (citation.repository) params.set('repository', citation.repository)
  const page = Number(citation.page)
  if (Number.isInteger(page) && page > 0) params.set('page', String(page))
  return `/sources/document?${params}`
}

// Only link references backed by the response's actual citation metadata.
export function citationParts(content, citations = []) {
  const parts = []
  const pattern = /\[Source\s+(\d+)\]/gi
  let offset = 0
  for (const match of content.matchAll(pattern)) {
    if (match.index > offset) parts.push({ text: content.slice(offset, match.index) })
    const citation = citations[Number(match[1]) - 1]
    parts.push({ text: match[0], ...(citation ? { citation } : {}) })
    offset = match.index + match[0].length
  }
  if (offset < content.length) parts.push({ text: content.slice(offset) })
  return parts
}

// A document can supply several chunks. Keep inline source numbers unchanged,
// but show its file only once in source lists and collect the cited pages.
export function uniqueCitationSources(citations = []) {
  const sources = new Map()
  citations.forEach((citation, index) => {
    const ticket = citation.doc_type === 'Ticket'
    const identity = ticket
      ? citation.ticket_id || citation.doc_id
      : citation.doc_id
    const fallback = ticket
      ? citation.section && `${citation.source || ''}|${citation.section}`
      : citation.source && `${citation.repository || ''}|${citation.source.replaceAll('\\', '/')}`
    const key = `${ticket ? 'ticket' : 'document'}:${identity || fallback || `unknown-${index}`}`
    if (!sources.has(key)) sources.set(key, { citation, sourceNumbers: [], pages: [] })
    const entry = sources.get(key)
    entry.sourceNumbers.push(index + 1)
    const page = Number(citation.page)
    if (Number.isInteger(page) && page > 0 && !entry.pages.includes(page)) entry.pages.push(page)
  })
  return [...sources.values()]
}
