import test from 'node:test'
import assert from 'node:assert/strict'
import { documentFilename } from './documentNames.js'

test('file requests display the original filename ahead of a generated title', () => {
  assert.equal(documentFilename({ doc_id: 'id', title: '5b7cf467 68a6 41d3 ad33 d0efbb891427', source_file: 'HR_Leave_and_Attendance_Policy.pdf' }), 'HR_Leave_and_Attendance_Policy.pdf')
})

test('file names exclude Windows and Unix directories', () => {
  assert.equal(documentFilename({ source_file: 'E:\\uploads\\Finance\\Invoice Policy.pdf' }), 'Invoice Policy.pdf')
  assert.equal(documentFilename({ source_file: '/uploads/Engineering/Runbook.pdf' }), 'Runbook.pdf')
})

test('legacy metadata has readable fallbacks without displaying the document ID', () => {
  assert.equal(documentFilename({ filename: 'legacy.pdf', title: 'generated id' }), 'legacy.pdf')
  assert.equal(documentFilename({ title: 'Policy' }), 'Policy')
  assert.equal(documentFilename({ name: 'Handbook' }), 'Handbook')
  assert.equal(documentFilename({ doc_id: 'private-id' }), 'Untitled document')
})
