import unittest
from unittest.mock import AsyncMock, MagicMock

from retrieval.hybrid_engine import HybridRetrievalEngine, RetrievalResult, settings
from retrieval.query_understanding import QueryContext


class DocumentScopeFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def run_fallback(self, top_k=None):
        ctx = QueryContext(active_document_ids=['finance-doc', 'engineering-doc'])
        engine = HybridRetrievalEngine.__new__(HybridRetrievalEngine)
        engine.vector_store = MagicMock()
        def chunks(doc_id, **kwargs):
            repo = 'Finance' if doc_id == 'finance-doc' else 'Engineering'
            return [{'doc_id':doc_id, 'chunk_id':f'{doc_id}-{i}',
                     'content':f'Policy section {i}', 'section_title':f'Section {i}',
                     'source_file':f'{repo}.pdf', 'repository':repo}
                    for i in range(12)]
        engine.vector_store.get_chunks_by_doc.side_effect = chunks
        engine.retrieve = AsyncMock(return_value=RetrievalResult(
            chunks=[], query_context=ctx, confidence=0, low_confidence=True,
            latency_ms=0, total_candidates=0))
        return await engine.retrieve_cascading(ctx, top_k=top_k)

    async def test_unset_limit_samples_both_selected_documents(self):
        result = await self.run_fallback()
        self.assertEqual(result.scope_label, 'DOCUMENT_FALLBACK')
        for doc_id in ('finance-doc', 'engineering-doc'):
            samples = [c for c in result.chunks if c.doc_id == doc_id
                       and c.retrieval_source == 'document_scope']
            self.assertEqual(len(samples), settings.reranker_top_k)
        self.assertFalse(result.low_confidence)

    async def test_explicit_limit_is_preserved(self):
        result = await self.run_fallback(top_k=2)
        samples = [c for c in result.chunks if c.retrieval_source == 'document_scope']
        self.assertEqual(len(samples), 4)


if __name__ == '__main__':
    unittest.main()
