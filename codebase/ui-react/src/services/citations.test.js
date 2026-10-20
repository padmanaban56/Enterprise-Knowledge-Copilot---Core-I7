import { test } from 'node:test'
import assert from 'node:assert/strict'
import { citationHref, citationParts, uniqueCitationSources } from './citations.js'

test('document links preserve exact identity and cited page without accepting arbitrary URLs', () => {
  const link = citationHref({ doc_id: 'doc-123', source: 'Policy & leave.pdf', repository: 'HR', page: 4 })
  const url = new URL(link, 'http://localhost')
  assert.equal(url.pathname, '/sources/document')
  assert.equal(url.searchParams.get('doc_id'), 'doc-123')
  assert.equal(url.searchParams.get('source'), 'Policy & leave.pdf')
  assert.equal(url.searchParams.get('page'), '4')
  assert.equal(new URL(citationHref({ source: 'javascript:alert(1)', page: -1 }), 'http://localhost').pathname, '/sources/document')
  assert.equal(citationHref({ page: -1 }).includes('page='), false)
})

test('inline reference numbers map to their own document, with repeated references preserved', () => {
  const docs = [{ doc_id: 'finance' }, { doc_id: 'engineering' }]
  const parts = citationParts('First [Source 2], then [Source 1] and [Source 2].', docs)
  assert.deepEqual(parts.filter(part => part.citation).map(part => part.citation.doc_id), ['engineering', 'finance', 'engineering'])
  assert.equal(parts.map(part => part.text).join(''), 'First [Source 2], then [Source 1] and [Source 2].')
})

test('unsupported or missing references stay plain text', () => {
  const text = 'Unknown [Source 7]. No source metadata: [Source 1].'
  const parts = citationParts(text)
  assert.equal(parts.some(part => part.citation), false)
  assert.equal(parts.map(part => part.text).join(''), text)
})

test('ticket links retain ticket navigation rather than trying to open an uploaded file', () => {
  assert.equal(citationHref({ doc_type: 'Ticket', ticket_id: 'INC-42', source: 'tickets.csv' }), '/tickets?search=INC-42')
})

test('repeated document chunks show one source while inline references keep the correct page', () => {
  const citations = [{ doc_id: 'a', page: 1 }, { doc_id: 'b', page: 4 }, { doc_id: 'a', page: 3 }, { doc_id: 'a', page: 1 }]
  const sources = uniqueCitationSources(citations)
  assert.equal(sources.length, 2)
  assert.deepEqual(sources[0].sourceNumbers, [1, 3, 4])
  assert.deepEqual(sources[0].pages, [1, 3])
  const inline = citationParts('See [Source 3]', citations).find(part => part.citation)
  assert.equal(new URL(citationHref(inline.citation), 'http://localhost').searchParams.get('page'), '3')
})

test('distinct documents with identical filenames and distinct tickets remain separate', () => {
  assert.equal(uniqueCitationSources([{ doc_id: 'a', source: 'policy.pdf' }, { doc_id: 'b', source: 'policy.pdf' }]).length, 2)
  assert.equal(uniqueCitationSources([{ source: 'policy.pdf', repository: 'HR' }, { source: 'policy.pdf', repository: 'Finance' }]).length, 2)
  assert.equal(uniqueCitationSources([{ doc_type: 'Ticket', ticket_id: 'INC1', source: 'tickets.csv' }, { doc_type: 'Ticket', ticket_id: 'INC2', source: 'tickets.csv' }]).length, 2)
  assert.equal(uniqueCitationSources([{ source: 'policy.pdf', repository: 'HR' }, { source: 'policy.pdf', repository: 'HR' }]).length, 1)
})
