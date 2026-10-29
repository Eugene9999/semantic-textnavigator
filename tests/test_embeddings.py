"""Opt-in integration tests: use the actual downloaded E5 model, never a mock."""
import os
import unittest
from pathlib import Path

from textnavigator.embeddings import Embedder, SemanticIndex
from textnavigator.rules import parse_document, FIELD_QUERIES, resolve


@unittest.skipUnless(os.environ.get("TEST_EMBEDDINGS") == "1", "Set TEST_EMBEDDINGS=1 to test actual model")
class EmbeddingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.embedder = Embedder(os.environ.get("TEXTNAV_MODEL_CACHE"))
        cls.embedder.load()
        if cls.embedder.state != "ready":
            raise RuntimeError(cls.embedder.error)
        cls.docs = [parse_document(p.name,p.read_text()) for p in (Path(__file__).resolve().parents[1]/"documents").glob("*.txt")]
        cls.index = SemanticIndex(cls.embedder, cls.docs)

    def test_real_model_shape_and_normalization(self):
        import numpy as np
        v=self.embedder.encode(["Срок поставки товара"])
        self.assertEqual(v.shape,(1,384))
        self.assertAlmostEqual(float(np.linalg.norm(v[0])),1.0,places=5)

    def test_retrieval_recall_at_three_for_required_evidence(self):
        checks = [("02_amendment_signed.txt","amount","1 380 000"),
                  ("02_amendment_signed.txt","delivery","30.04.2026"),
                  ("03_contract.txt","amount","840 000"),
                  ("04_contract.txt","amount","Числовое значение стоимости")]
        for filename, field, phrase in checks:
            doc=next(d for d in self.docs if d["filename"]==filename)
            matches=self.index.search(FIELD_QUERIES[field],limit=3,document_id=doc["id"])
            self.assertTrue(any(phrase in m["quote"] for m in matches), (filename,field,matches))

    def test_real_hybrid_pipeline_preserves_answers(self):
        ranks={(d["id"],f):self.index.search(q,3,document_id=d["id"]) for d in self.docs for f,q in FIELD_QUERIES.items()}
        result=resolve(self.docs,"2026-03-10",ranks)
        c={c["id"]:c for c in result["contracts"]}
        self.assertEqual(c["STN-101"]["fields"]["delivery"]["value"],"2026-04-30")
        self.assertEqual(c["STN-102"]["fields"]["amount"]["value"],840000)
        self.assertIsNone(c["STN-103"]["fields"]["amount"]["value"])

    def test_real_api_accepts_changed_text_and_searches_it(self):
        from textnavigator.app import create_app
        client = create_app(embedder=self.embedder, start_model=False).test_client()
        source = [{"name": d["filename"], "text": d["text"].replace("840 000", "987 650")}
                  for d in self.docs]
        response = client.post("/api/analyze", json={"files":source, "as_of":"2026-03-10", "mode":"semantic"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["mode"], "semantic")
        c = next(c for c in response.json["contracts"] if c["id"] == "STN-102")
        self.assertEqual(c["fields"]["amount"]["value"], 987650)
        search = client.post("/api/search", json={"query":"Какова общая стоимость договора?", "contract_id":"STN-102"})
        self.assertEqual(search.status_code, 200)
        self.assertTrue(any("987 650" in m["quote"] for m in search.json["matches"][:3]))


if __name__ == "__main__":
    unittest.main()
