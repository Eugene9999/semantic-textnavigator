import unittest
from pathlib import Path

from textnavigator.app import create_app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app(start_model=False).test_client()
        self.files = self.client.get("/api/demo").get_json()["files"]

    def payload(self):
        return {"files": self.files, "as_of": "2026-03-10", "mode": "rules"}

    def test_upload_pipeline_in_explicit_rules_mode(self):
        response = self.client.post("/api/analyze", json=self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["mode"], "rules")
        self.assertEqual(len(response.json["contracts"]), 3)

    def test_no_silent_embedding_fallback(self):
        payload = self.payload(); payload["mode"] = "semantic"
        self.assertEqual(self.client.post("/api/analyze", json=payload).status_code, 503)

    def test_missing_and_empty_files_rejected(self):
        for files in [[], [{"name": "empty.txt", "text": " "}], [{"name":"data.pdf", "text":"test"}]]:
            payload = self.payload(); payload["files"] = files
            self.assertEqual(self.client.post("/api/analyze", json=payload).status_code, 400)

    def test_invalid_date_rejected(self):
        payload=self.payload(); payload["as_of"]="2026-02-30"
        self.assertEqual(self.client.post("/api/analyze", json=payload).status_code, 400)

    def test_identical_content_deduplicated(self):
        payload=self.payload(); payload["files"].append({**self.files[0], "name": "copy.txt"})
        response=self.client.post("/api/analyze", json=payload)
        self.assertEqual(response.json["duplicate_count"], 1)
        self.assertEqual(len(response.json["documents"]), 6)

    def test_cross_origin_write_rejected(self):
        self.assertEqual(self.client.post("/api/analyze", json=self.payload(), headers={"Origin":"https://example.org"}).status_code, 403)

    def test_search_requires_real_index(self):
        self.client.post("/api/analyze", json=self.payload())
        self.assertEqual(self.client.post("/api/search", json={"query":"срок поставки"}).status_code, 409)


if __name__ == "__main__":
    unittest.main()
