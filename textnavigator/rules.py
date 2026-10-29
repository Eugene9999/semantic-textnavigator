"""Conservative rules for the documented Russian TXT format.

No filename, contract number or expected answer participates in these rules.
An embedding score can nominate evidence, but never authorize a change.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

DATE = r"\d{2}\.\d{2}\.\d{4}"
REF = re.compile(r"\bдоговор(?:у|а|ом)?(?:\s+поставки)?\s*(?:№\s*)?([A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9_./-]*\d[A-ZА-ЯЁ0-9_./-]*)", re.I)
MONEY = re.compile(r"(?<![\d.,])(?P<number>\d+(?:[ \u00a0\u202f]\d{3})*(?:[,.]\d{1,2})?)\s*(?:\([^\n)]*\)\s*)?(?:руб(?:лей|ля|ль|\.)?|₽)", re.I)
FIELD_QUERIES = {
    "amount": "Какова общая итоговая стоимость договора поставки в рублях?",
    "delivery": "В какой срок поставщик обязуется поставить товар? Дата поставки.",
}


def parse_date(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def dates(text: str) -> list[str]:
    return [parsed for raw in re.findall(DATE, text) if (parsed := parse_date(raw))]


def evidence(doc: dict, p: dict) -> dict:
    return {"document_id": doc["id"], "filename": doc["filename"],
            "paragraph_id": p["id"], "line": p["line"], "quote": p["text"],
            "start": p["start"], "end": p["end"]}


def parse_document(filename: str, text: str) -> dict:
    text = text.lstrip("\ufeff")
    doc_id = hashlib.sha256((filename + "\0" + text).encode()).hexdigest()[:20]
    paragraphs = []
    for line, match in enumerate(re.finditer(r"[^\r\n]+", text), 1):
        if match.group().strip():
            paragraphs.append({"id": f"{doc_id}-p{len(paragraphs) + 1}",
                               "line": text.count("\n", 0, match.start()) + 1,
                               "text": match.group(), "start": match.start(), "end": match.end()})
    title = paragraphs[0]["text"] if paragraphs else ""
    low = title.lower()
    kind = ("draft" if "проект" in low else
            "amendment" if "дополнительное соглашение" in low else
            "email" if "письмо" in low else
            "contract" if low.startswith("договор") else "unknown")
    refs = sorted(set(m.group(1).upper().rstrip(".") for m in REF.finditer(text)))
    status_lines = [p for p in paragraphs if p["text"].lower().startswith("статус:")]
    signed_dates = []
    for p in status_lines:
        m = re.search(r"(?<!не )\bподписан[оа]?\s+обеими сторонами\s+(" + DATE + r")", p["text"], re.I)
        if m and not re.search(r"\bне\s+подписан", p["text"], re.I):
            signed_dates.extend(dates(m.group(1)))
    signed = len(set(signed_dates)) == 1 and len(status_lines) == 1
    dated = [p for p in paragraphs if re.match(r"Дата(?: проекта)?:", p["text"], re.I)]
    document_dates = [d for p in dated for d in dates(p["text"])]
    effective_p = [p for p in paragraphs if re.search(r"вступает в силу\s+(?:с\s+)?" + DATE, p["text"], re.I)]
    effective_dates = [parse_date(m.group(1)) for p in effective_p
                       for m in re.finditer(r"вступает в силу\s+(?:с\s+)?(" + DATE + r")", p["text"], re.I)]
    effective_dates = [d for d in effective_dates if d]
    supplier_matches = list(re.finditer(r"Поставщик:\s*([^\n]+)", text, re.I))
    suppliers = sorted(set(m.group(1).strip().rstrip(".") for m in supplier_matches))
    doc = {"id": doc_id, "filename": filename, "text": text, "paragraphs": paragraphs,
           "kind": kind, "contract_id": refs[0] if len(refs) == 1 else None,
           "references": refs, "signed": signed,
           "signed_date": signed_dates[0] if signed else None,
           "document_date": document_dates[0] if len(set(document_dates)) == 1 else None,
           "effective_date": effective_dates[0] if len(set(effective_dates)) == 1 else None,
           "supplier": suppliers[0] if len(suppliers) == 1 else None,
           "metadata_evidence": []}
    for p in status_lines + effective_p + dated:
        doc["metadata_evidence"].append(evidence(doc, p))
    return doc


def money_values(text: str) -> list[int | float]:
    result = []
    for m in MONEY.finditer(text):
        try:
            value = Decimal(re.sub(r"[\s\u00a0\u202f]", "", m.group("number")).replace(",", "."))
            result.append(int(value) if value == value.to_integral() else float(value))
        except InvalidOperation:
            pass
    return result


def is_field_paragraph(text: str, field: str) -> bool:
    low = text.lower()
    if field == "amount":
        return bool(re.search(r"общ\w*\s+стоимост|итогов\w*\s+стоимост|цен[аыу]\s+договор|сумм[аыу]\s+договор", low))
    return bool(re.search(r"срок\w*\s+поставк|обязуется\s+поставить|поставка\s+.*(?:\bдо\b|не позднее)", low))


def extract_field(doc: dict, field: str, ranked: list[dict] | None = None) -> dict:
    """Union semantic candidates with all explicit field clauses (recall guard).

    Full-source rule scan is intentional: a top-k retrieval miss must not turn
    a present clause into a false 'no data'. Semantic ranking orders inspection.
    """
    by_id = {p["id"]: p for p in doc["paragraphs"]}
    order = [r["paragraph_id"] for r in (ranked or []) if r["paragraph_id"] in by_id]
    order += [p["id"] for p in doc["paragraphs"] if p["id"] not in order]
    candidates, absence, unresolved = [], [], []
    for pid in order:
        p = by_id[pid]
        s, low = p["text"], p["text"].lower()
        if not is_field_paragraph(s, field):
            continue
        ev = evidence(doc, p)
        if field == "amount":
            # A reference from the advance clause to total cost is not a total.
            if re.search(r"аванс|лимит|неустойк|не является.*стоимост", low):
                continue
            if re.search(r"не указан|не установлен|не входит|определяется спецификац", low):
                absence.append(ev)
                continue
            values = money_values(s)
        else:
            if re.search(r"не изменяет|не меняет|а не поставки|не является.*поставк|неустойк|остальные условия", low):
                continue
            if re.search(r"срок.*(?:не указан|не установлен)", low):
                absence.append(ev)
                continue
            # Supplier proposals contain old and proposed dates: take the target.
            target = re.search(r"перенести\s+срок\s+поставки\s+с\s+" + DATE + r"\s+на\s+(" + DATE + r")", s, re.I)
            values = dates(target.group(1)) if target and doc["kind"] in ("email", "draft") else dates(s)
        if not values:
            unresolved.append(ev)
        else:
            candidates.extend({"value": v, "evidence": ev} for v in values)
    unique = list(dict.fromkeys(c["value"] for c in candidates))
    all_evidence = [c["evidence"] for c in candidates] + absence + unresolved
    # Multiple incompatible clauses and unsupported relevant clauses need review.
    if len(unique) > 1 or (unique and (absence or unresolved)):
        return {"status": "review", "value": None, "reason": "Найдены противоречивые или неоднозначные условия.", "evidence": all_evidence, "candidates": candidates}
    if len(unique) == 1:
        return {"status": "found", "value": unique[0], "reason": "Условие найдено в тексте.", "evidence": [c["evidence"] for c in candidates], "candidates": candidates}
    if unresolved:
        return {"status": "review", "value": None, "reason": "Есть связанный пункт, но значение не удалось надежно извлечь.", "evidence": all_evidence, "candidates": []}
    return {"status": "missing", "value": None,
            "reason": "Стоимость вынесена в отсутствующий документ или не указана." if absence and field == "amount" else "В предоставленном тексте нет значения.",
            "evidence": absence, "candidates": []}


def document_state(doc: dict, as_of: str) -> tuple[str, str]:
    if doc["kind"] == "unknown":
        return "review", "Не удалось определить тип документа."
    if doc["kind"] in ("email", "draft"):
        if not doc["document_date"]:
            return "review", "Не удалось определить дату предложения."
        if doc["document_date"] > as_of:
            return "future", "Документ датирован позже даты проверки."
        return "proposal", "Предложение не изменяет действующие условия."
    if not doc["signed"]:
        return "review", "Нет однозначного подтверждения подписи обеих сторон и даты подписания."
    if doc["signed_date"] > as_of:
        return "future", "На дату проверки документ еще не подписан."
    if doc["kind"] == "amendment":
        if not doc["effective_date"]:
            return "review", "Не удалось определить дату вступления допсоглашения в силу."
        if doc["effective_date"] > as_of:
            return "future", "Допсоглашение еще не вступило в силу."
    return "active", "Подписан обеими сторонами; применим на дату проверки."


def review_field(field: dict, reason: str, extra: list[dict]) -> dict:
    return {**field, "status": "review", "value": None, "reason": reason,
            "evidence": field["evidence"] + extra}


def resolve(documents: list[dict], as_of: str, rankings: dict | None = None) -> dict:
    date.fromisoformat(as_of)
    rankings = rankings or {}
    groups, warnings = {}, []
    for doc in documents:
        doc["state"], doc["state_reason"] = document_state(doc, as_of)
        doc["fields"] = {field: extract_field(doc, field, rankings.get((doc["id"], field))) for field in FIELD_QUERIES}
        if doc["state"] == "review":
            warnings.append({"document_id": doc["id"], "message": f'{doc["filename"]}: {doc["state_reason"]}'})
        if not doc["contract_id"]:
            warnings.append({"document_id": doc["id"], "message": f'{doc["filename"]}: не удалось однозначно связать документ с договором.'})
            continue
        groups.setdefault(doc["contract_id"], []).append(doc)
    contracts = []
    for cid, docs in sorted(groups.items()):
        bases = [d for d in docs if d["kind"] == "contract"]
        if not bases:
            warnings.append({"document_id": docs[0]["id"], "message": f"{cid}: отсутствует исходный договор; документы не применены."})
            continue
        base = bases[0]
        fields = {name: {**f, "history": [], "changed_by": None} for name, f in base["fields"].items()}
        problems = []
        if len(bases) != 1 or base["state"] != "active":
            reason = "Несколько исходных договоров с одним номером." if len(bases) != 1 else base["state_reason"]
            problems.append(reason)
            for name in fields:
                fields[name] = review_field(fields[name], reason, [e for b in bases for e in b["metadata_evidence"]])
        else:
            amendments = [d for d in docs if d["kind"] == "amendment" and d["state"] == "active"]
            amendments.sort(key=lambda d: d["effective_date"])
            for name in fields:
                previous_date = None
                for amendment in amendments:
                    update = amendment["fields"][name]
                    if update["status"] == "missing" and not update["evidence"]:
                        continue
                    if amendment["supplier"] and base["supplier"] and amendment["supplier"] != base["supplier"]:
                        problems.append("Поставщик в допсоглашении не совпадает с исходным договором.")
                        fields[name] = review_field(fields[name], problems[-1], update["evidence"])
                        continue
                    # Ambiguity is sticky; a later file must not hide an unresolved conflict.
                    if previous_date == amendment["effective_date"] or fields[name]["status"] == "review":
                        fields[name] = review_field(fields[name], "Не удалось однозначно установить последовательность изменений или разрешить конфликт.", update["evidence"])
                    else:
                        old = fields[name]
                        history = old["history"] + [{"value": old["value"], "status": old["status"], "evidence": old["evidence"]}]
                        fields[name] = {**update, "history": history, "changed_by": amendment["id"],
                                        "reason": "Изменено подписанным допсоглашением, вступившим в силу." if update["status"] == "found" else update["reason"]}
                    previous_date = amendment["effective_date"]
            for doc in docs:
                if doc["kind"] in ("amendment", "unknown") and doc["state"] == "review":
                    problems.append(f'{doc["filename"]}: {doc["state_reason"]}')
                    for name in fields:
                        update = doc["fields"][name]
                        if update["evidence"] or doc["kind"] == "unknown":
                            fields[name] = review_field(fields[name], "Есть связанный документ с неясным статусом; требуется проверка.", update["evidence"] + doc["metadata_evidence"])
        for doc in documents:
            if doc["contract_id"] is None and cid in doc["references"] and doc["kind"] in ("amendment", "unknown") and doc["state"] != "future":
                reason = "Есть документ с неоднозначной ссылкой на этот договор; требуется проверка."
                problems.append(reason)
                for name in fields:
                    update = doc["fields"][name]
                    if update["evidence"] or doc["kind"] == "unknown":
                        fields[name] = review_field(fields[name], reason, update["evidence"] + doc["metadata_evidence"])
        proposals = [{"document_id": d["id"], "filename": d["filename"], "kind": d["kind"],
                      "fields": d["fields"], "reason": d["state_reason"]}
                     for d in docs if d["state"] == "proposal"]
        if not base["supplier"]:
            problems.append("Не удалось однозначно определить поставщика.")
        contracts.append({"id": cid, "supplier": base["supplier"], "fields": fields,
                          "proposals": proposals, "problems": list(dict.fromkeys(problems)),
                          "document_ids": [d["id"] for d in docs]})
    return {"as_of": as_of, "contracts": contracts, "documents": documents, "warnings": warnings}
