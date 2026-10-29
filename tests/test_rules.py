import copy
import random
import unittest
from pathlib import Path

from textnavigator.rules import parse_document, resolve

FIXTURES = Path(__file__).resolve().parents[1] / "documents"


def files():
    return {p.name: p.read_text(encoding="utf-8") for p in FIXTURES.glob("*.txt")}


def analyze(source=None, day="2026-03-10"):
    source = source if source is not None else files()
    result = resolve([parse_document(n, t) for n, t in source.items()], day)
    return {c["id"]: c for c in result["contracts"]}, result


class RulesTests(unittest.TestCase):
    def test_reference_answers_and_proposals(self):
        contracts, _ = analyze()
        self.assertEqual(set(contracts), {"STN-101", "STN-102", "STN-103"})
        expected = {"STN-101": (1380000, "2026-04-30"), "STN-102": (840000, "2026-03-20"), "STN-103": (None, "2026-04-30")}
        for cid, (amount, delivery) in expected.items():
            self.assertEqual(contracts[cid]["fields"]["amount"]["value"], amount)
            self.assertEqual(contracts[cid]["fields"]["delivery"]["value"], delivery)
        self.assertEqual(contracts["STN-101"]["proposals"][0]["fields"]["amount"]["value"], 1450000)
        self.assertEqual(contracts["STN-101"]["proposals"][0]["fields"]["delivery"]["value"], "2026-05-10")
        self.assertEqual(contracts["STN-102"]["proposals"][0]["fields"]["delivery"]["value"], "2026-03-27")

    def test_before_april_uses_current_date_not_proposal(self):
        contracts, _ = analyze()
        matches = [c["id"] for c in contracts.values() if c["fields"]["delivery"]["status"] == "found" and c["fields"]["delivery"]["value"] < "2026-04-01"]
        self.assertEqual(matches, ["STN-102"])

    def test_evidence_is_exact_substring_with_valid_location(self):
        contracts, result = analyze()
        docs = {d["id"]: d for d in result["documents"]}
        fields = [f for c in contracts.values() for f in c["fields"].values()]
        fields += [f for d in docs.values() for f in d["fields"].values()]
        for field in fields:
            for ev in field["evidence"]:
                doc = docs[ev["document_id"]]
                self.assertEqual(doc["text"][ev["start"]:ev["end"]], ev["quote"])
                self.assertIn(ev["paragraph_id"], [p["id"] for p in doc["paragraphs"]])

    def test_changed_field_is_extracted_from_uploaded_text(self):
        source = files()
        source["03_contract.txt"] = source["03_contract.txt"].replace("840 000", "975 500")
        contracts, _ = analyze(source)
        self.assertEqual(contracts["STN-102"]["fields"]["amount"]["value"], 975500)

    def test_new_contract_and_arbitrary_filename(self):
        source = files()
        source["fresh.txt"] = source["03_contract.txt"].replace("STN-102", "NEW-908").replace("840 000", "912 345").replace("20.03.2026", "12.05.2026")
        contracts, _ = analyze(source)
        self.assertEqual(contracts["NEW-908"]["fields"]["amount"]["value"], 912345)
        self.assertEqual(contracts["NEW-908"]["fields"]["delivery"]["value"], "2026-05-12")

    def test_filename_and_upload_order_do_not_determine_priority(self):
        items = list(files().values())
        random.Random(34).shuffle(items)
        contracts, _ = analyze({f"2099-12-{i+1:02d}.txt": t for i, t in enumerate(items)})
        self.assertEqual(contracts["STN-101"]["fields"]["amount"]["value"], 1380000)
        self.assertEqual(contracts["STN-101"]["fields"]["delivery"]["value"], "2026-04-30")

    def test_historical_date_uses_original_terms(self):
        contracts, _ = analyze(day="2026-02-28")
        self.assertEqual(contracts["STN-101"]["fields"]["amount"]["value"], 1200000)
        self.assertEqual(contracts["STN-101"]["fields"]["delivery"]["value"], "2026-04-15")
        self.assertFalse(contracts["STN-101"]["proposals"])

    def test_effective_date_inclusive(self):
        contracts, _ = analyze(day="2026-03-01")
        self.assertEqual(contracts["STN-101"]["fields"]["amount"]["value"], 1380000)

    def test_signed_but_not_yet_effective(self):
        source = files()
        source["02_amendment_signed.txt"] = source["02_amendment_signed.txt"].replace("вступает в силу 01.03.2026", "вступает в силу 01.04.2026")
        contracts, _ = analyze(source)
        self.assertEqual(contracts["STN-101"]["fields"]["amount"]["value"], 1200000)

    def test_missing_effective_date_requires_review(self):
        source = files()
        source["02_amendment_signed.txt"] = source["02_amendment_signed.txt"].replace("вступает в силу 01.03.2026", "вступает в силу в следующем месяце")
        contracts, _ = analyze(source)
        self.assertEqual(contracts["STN-101"]["fields"]["amount"]["status"], "review")
        self.assertIsNone(contracts["STN-101"]["fields"]["amount"]["value"])

    def test_unsigned_is_not_misread_as_signed(self):
        source = files()
        source["02_amendment_signed.txt"] = source["02_amendment_signed.txt"].replace("Статус: подписано", "Статус: не подписано")
        contracts, _ = analyze(source)
        self.assertEqual(contracts["STN-101"]["fields"]["delivery"]["status"], "review")

    def test_future_signature_cannot_apply_retroactively(self):
        source = files()
        source["02_amendment_signed.txt"] = source["02_amendment_signed.txt"].replace("подписано обеими сторонами 01.03.2026", "подписано обеими сторонами 15.03.2026")
        contracts, _ = analyze(source)
        self.assertEqual(contracts["STN-101"]["fields"]["delivery"]["value"], "2026-04-15")

    def test_absent_amount_does_not_become_budget_or_zero(self):
        contracts, _ = analyze()
        field = contracts["STN-103"]["fields"]["amount"]
        self.assertEqual(field["status"], "missing")
        self.assertIsNone(field["value"])
        self.assertTrue(field["evidence"])

    def test_conflicting_amendments_same_effective_date(self):
        source = files()
        source["conflict.txt"] = source["02_amendment_signed.txt"].replace("1 380 000", "1 500 000")
        contracts, _ = analyze(source)
        self.assertEqual(contracts["STN-101"]["fields"]["amount"]["status"], "review")

    def test_changes_are_applied_per_field(self):
        source = files()
        source["later.txt"] = """ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 3 К ДОГОВОРУ STN-101
Дата: 07.03.2026
Статус: подписано обеими сторонами 07.03.2026.
1. Общая стоимость поставки составляет 1 510 000 рублей.
2. Настоящее соглашение вступает в силу 08.03.2026.
"""
        contracts, _ = analyze(source)
        self.assertEqual(contracts["STN-101"]["fields"]["amount"]["value"], 1510000)
        self.assertEqual(contracts["STN-101"]["fields"]["delivery"]["value"], "2026-04-30")

    def test_orphan_amendment_not_presented_as_contract(self):
        source = files(); del source["01_contract.txt"]
        contracts, result = analyze(source)
        self.assertNotIn("STN-101", contracts)
        self.assertTrue(any("отсутствует исходный" in w["message"] for w in result["warnings"]))

    def test_duplicate_contract_number_requires_review(self):
        source = files(); source["other.txt"] = source["01_contract.txt"].replace("1 200 000", "999 000")
        contracts, _ = analyze(source)
        self.assertEqual(contracts["STN-101"]["fields"]["amount"]["status"], "review")

    def test_bad_date_requires_review(self):
        source = files(); source["03_contract.txt"] = source["03_contract.txt"].replace("20.03.2026", "32.03.2026")
        contracts, _ = analyze(source)
        self.assertEqual(contracts["STN-102"]["fields"]["delivery"]["status"], "review")

    def test_multiple_contract_references_are_not_guessed(self):
        source = files(); source["02_amendment_signed.txt"] += "\nТакже относится к договору STN-102."
        contracts, result = analyze(source)
        self.assertTrue(any("не удалось однозначно связать" in w["message"] for w in result["warnings"]))
        self.assertEqual(contracts["STN-101"]["fields"]["amount"]["status"], "review")
        self.assertEqual(contracts["STN-102"]["fields"]["delivery"]["status"], "review")

    def test_supplier_mismatch_requires_review(self):
        source = files(); source["02_amendment_signed.txt"] = source["02_amendment_signed.txt"].replace("Вектор Прибор", "Другой Поставщик")
        contracts, _ = analyze(source)
        self.assertEqual(contracts["STN-101"]["fields"]["delivery"]["status"], "review")

    def test_nonbreaking_spaces_and_decimal_rubles(self):
        source = files(); source["03_contract.txt"] = source["03_contract.txt"].replace("840 000", "840\u00a0000,75")
        contracts, _ = analyze(source)
        self.assertEqual(contracts["STN-102"]["fields"]["amount"]["value"], 840000.75)

    def test_semantic_miss_cannot_hide_explicit_clause(self):
        docs = [parse_document(n,t) for n,t in files().items()]
        wrong_rankings = {(d["id"], f): [{"paragraph_id": d["paragraphs"][0]["id"]}] for d in docs for f in ("amount", "delivery")}
        result = resolve(docs, "2026-03-10", wrong_rankings)
        self.assertEqual(result["contracts"][0]["fields"]["delivery"]["value"], "2026-04-30")


if __name__ == "__main__":
    unittest.main()
