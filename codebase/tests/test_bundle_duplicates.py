import unittest
from unittest.mock import MagicMock

from fastapi import HTTPException

from api.bundles_help_admin import BundleService


class BundleDuplicateTests(unittest.TestCase):
    def check_documents(self, selected, existing, exclude=None):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            MagicMock(_mapping=bundle) for bundle in existing
        ]
        BundleService._reject_matching_documents(conn, "owner", selected, exclude)
        query, params = conn.execute.call_args.args
        self.assertIn("user_id = :uid AND deleted_at IS NULL", str(query))
        self.assertEqual(params, {"uid": "owner"})

    def test_reordered_repeated_documents_rejected_with_existing_name(self):
        with self.assertRaises(HTTPException) as caught:
            self.check_documents(["b", "a", "a"], [
                {"bundle_id": "1", "name": "Original", "document_ids": ["a", "b"]}
            ])
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn('"Original"', caught.exception.detail)

    def test_subset_and_superset_are_different_bundles(self):
        existing = [{"bundle_id": "1", "name": "Original", "document_ids": ["a", "b"]}]
        self.check_documents(["a"], existing)
        self.check_documents(["a", "b", "c"], existing)

    def test_edit_excludes_itself(self):
        self.check_documents(["a"], [
            {"bundle_id": "1", "name": "Original", "document_ids": ["a"]}
        ], exclude="1")

    def test_edit_still_rejects_another_matching_bundle(self):
        with self.assertRaises(HTTPException):
            self.check_documents(["a"], [
                {"bundle_id": "1", "name": "Own", "document_ids": ["b"]},
                {"bundle_id": "2", "name": "Other", "document_ids": ["a"]},
            ], exclude="1")

    def test_empty_sets_are_also_duplicates(self):
        with self.assertRaises(HTTPException):
            self.check_documents([], [
                {"bundle_id": "1", "name": "Empty", "document_ids": []}
            ])


if __name__ == "__main__":
    unittest.main()
