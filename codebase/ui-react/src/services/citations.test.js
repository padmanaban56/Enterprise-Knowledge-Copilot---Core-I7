import { test } from 'node:test'
import assert from 'node:assert/strict'
import { citationHref, citationParts } from './citations.js'

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
