from __future__ import annotations

import argparse
import hashlib
import os
import threading
import time
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .embeddings import Embedder, SemanticIndex, MODEL_ID, MODEL_REVISION
from .rules import FIELD_QUERIES, parse_document, resolve

ROOT = Path(__file__).resolve().parents[1]


def create_app(embedder=None, start_model=True):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024
    model = embedder or Embedder(os.environ.get("TEXTNAV_MODEL_CACHE"))
    current = {"index": None, "result": None}
    lock = threading.Lock()
    if start_model:
        model.start()

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        return response

    @app.before_request
    def local_origin():
        # A browser page on another origin cannot mutate the local workspace.
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return jsonify(error="Запрос с другого сайта отклонен."), 403

    @app.get("/")
    def home():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/status")
    def status():
        return jsonify(state=model.state, model=MODEL_ID, revision=MODEL_REVISION,
                       error="Не удалось загрузить модель. Проверьте подключение, установку зависимостей и путь к кешу. Подробности — в терминале." if model.error else None)

    @app.get("/api/demo")
    def demo():
        return jsonify(files=[{"name": p.name, "text": p.read_text(encoding="utf-8")}
                              for p in sorted((ROOT / "documents").glob("*.txt"))])

    @app.post("/api/analyze")
    def analyze():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="Ожидаются TXT-файлы и дата проверки."), 400
        try:
            as_of = date.fromisoformat(data.get("as_of", "")).isoformat()
        except (ValueError, TypeError):
            return jsonify(error="Укажите корректную дату проверки."), 400
        files = data.get("files")
        if not isinstance(files, list) or not 1 <= len(files) <= 30:
            return jsonify(error="Загрузите от 1 до 30 TXT-файлов."), 400
        if data.get("mode", "semantic") not in ("semantic", "rules"):
            return jsonify(error="Неизвестный режим обработки."), 400
        semantic = data.get("mode", "semantic") == "semantic"
        if semantic and model.state != "ready":
            return jsonify(error="Модель пока не готова. Дождитесь загрузки или явно выберите режим только с правилами."), 503
        docs, seen, duplicates = [], set(), 0
        for file in files:
            if not isinstance(file, dict) or not isinstance(file.get("name"), str) or not isinstance(file.get("text"), str):
                return jsonify(error="Некорректный формат файла."), 400
            name = file["name"].replace("\\", "/").split("/")[-1]
            content = file["text"]
            if not name.lower().endswith(".txt") or len(name) > 200:
                return jsonify(error="Поддерживаются только файлы .txt."), 400
            if not content.strip() or len(content.encode()) > 1024 * 1024 or "\x00" in content:
                return jsonify(error=f"{name}: пустой, слишком большой или не текстовый файл. Максимум 1 МБ."), 400
            key = hashlib.sha256(content.lstrip("\ufeff").encode()).hexdigest()
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            docs.append(parse_document(name, content))
        started = time.monotonic()
        with lock:
            try:
                index = SemanticIndex(model, docs) if semantic else None
                rankings = {(d["id"], field): index.search(query, limit=3, document_id=d["id"])
                            for d in docs for field, query in FIELD_QUERIES.items()} if index else {}
                result = resolve(docs, as_of, rankings)
                result["mode"] = "semantic" if semantic else "rules"
                result["elapsed_seconds"] = round(time.monotonic() - started, 2)
                result["duplicate_count"] = duplicates
                result["model"] = MODEL_ID if semantic else None
                current.update(index=index, result=result)
            except Exception:
                app.logger.exception("Analysis failed")
                return jsonify(error="Не удалось завершить обработку. Результаты не обновлены; подробности в терминале."), 500
        return jsonify(result)

    @app.post("/api/search")
    def search():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error="Некорректный запрос."), 400
        query = data.get("query", "")
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
            return jsonify(error="Введите запрос длиной до 500 символов."), 400
        with lock:
            if current["index"] is None:
                return jsonify(error="Сначала обработайте документы с embedding-моделью."), 409
            matches = current["index"].search(query.strip(), limit=6, contract_id=data.get("contract_id") or None)
            docs = {d["id"]: d for d in current["result"]["documents"]}
            for match in matches:
                doc = docs[match["document_id"]]
                match.update(state=doc["state"], state_reason=doc["state_reason"])
            return jsonify(matches=matches)

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error="Суммарный размер запроса превышает 4 МБ. Загрузите меньше файлов."), 413

    return app


def main():
    parser = argparse.ArgumentParser(description="Semantic TextNavigator — локальная проверка договоров")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--rules-only", action="store_true", help="Запустить без загрузки модели; ограниченный режим обозначен в интерфейсе")
    args = parser.parse_args()
    app = create_app(start_model=not args.rules_only)
    print(f"TextNavigator: http://127.0.0.1:{args.port} — остановка Ctrl+C", flush=True)
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
