"""Local BGE-M3 embedding HTTP service (port 11435 by default).

Serves the BAAI/bge-m3 ONNX weights via onnxruntime so the project's
embedding adapter (``LocalOpenAIEmbeddingAdapter``) can call into a real
semantic embedding model without going through Ollama (whose model
registry uses a Cloudflare R2 endpoint that is unreachable from mainland
China).

HTTP API
--------
``POST /v1/embeddings``
    Body: ``{"model": "bge-m3", "input": "text" | ["t1", ...]}``
    Resp: ``{"data": [{"embedding": [...]}, ...], "model": "bge-m3", "dim": 1024}``

``POST /embeddings``     (legacy Ollama-compatible)
    Body: ``{"model": "bge-m3", "prompt": "text"}``
    Resp: ``{"embedding": [...], "model": "bge-m3", "dim": 1024}``

``GET  /health``         quick liveness probe

Run::

    pip install optimum[onnxruntime] onnxruntime sentencepiece flask
    python dl_bge_m3.py                  # one-time download
    python embedding_server.py --port 11435
"""
import argparse
import os
import sys
import time

from flask import Flask, jsonify, request

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# Default cache mirrors dl_bge_m3.py's --cache-dir default.
DEFAULT_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".cache", "huggingface", "models--BAAI--bge-m3", "snapshots", "main",
)

app = Flask(__name__)
SESSION = None
TOKENIZER = None
DIM = 1024


def _resolve_cache_dir() -> str:
    return os.environ.get("BGE_M3_CACHE_DIR", DEFAULT_CACHE_DIR)


def load_model():
    global SESSION, TOKENIZER
    import onnxruntime as ort
    from transformers import AutoTokenizer

    cache_dir = _resolve_cache_dir()
    onnx_path = os.path.join(cache_dir, "onnx", "model.onnx")
    if not os.path.exists(onnx_path):
        raise SystemExit(
            f"ONNX weights not found at {onnx_path}.\n"
            f"Run `python dl_bge_m3.py` first (or set BGE_M3_CACHE_DIR)."
        )

    print(f"[load] onnxruntime session: {onnx_path}", flush=True)
    t0 = time.time()
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.intra_op_num_threads = 0  # 0 = use all cores
    SESSION = ort.InferenceSession(
        onnx_path, sess_options=so, providers=["CPUExecutionProvider"]
    )
    TOKENIZER = AutoTokenizer.from_pretrained(
        cache_dir, subfolder="onnx", use_fast=False,
    )
    print(f"[load] 完成  耗时 {time.time() - t0:.1f}s  维度 {DIM}", flush=True)


def embed(texts):
    """Embed one or more strings; empty strings map to zero vectors."""
    if isinstance(texts, str):
        texts = [texts]
    # Track blank inputs so we can substitute zero vectors and keep alignment.
    placeholders = []
    safe = []
    for i, t in enumerate(texts):
        if t and t.strip():
            safe.append(t)
            placeholders.append(None)
        else:
            safe.append("。")
            placeholders.append(i)

    enc = TOKENIZER(
        safe,
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="np",
    )
    feeds = {
        "input_ids": enc["input_ids"].astype("int64"),
        "attention_mask": enc["attention_mask"].astype("int64"),
    }
    if "token_type_ids" in enc:
        feeds["token_type_ids"] = enc["token_type_ids"].astype("int64")

    last = SESSION.run(None, feeds)[0]  # (n, seq, dim)
    cls = last[:, 0, :]                # [CLS]
    norm = (cls ** 2).sum(axis=-1, keepdims=True) ** 0.5
    cls = cls / norm.clip(min=1e-12)
    out = cls.astype("float32").tolist()

    final = []
    pi = 0
    for slot in placeholders:
        if slot is not None:
            final.append([0.0] * DIM)
        else:
            final.append(out[pi])
            pi += 1
    return final


@app.post("/v1/embeddings")
def v1_embeddings():
    body = request.get_json(force=True, silent=True) or {}
    inp = body.get("input")
    if inp is None:
        return jsonify({"error": "missing 'input'"}), 400
    if isinstance(inp, str):
        inp = [inp]
    if not inp:
        return jsonify({"error": "empty input"}), 400
    try:
        t0 = time.time()
        vecs = embed(inp)
    except Exception as e:
        return jsonify({"error": f"encode failed: {e}"}), 500
    return jsonify({
        "object": "list",
        "model": body.get("model", "bge-m3"),
        "data": [
            {"object": "embedding", "embedding": v, "index": i}
            for i, v in enumerate(vecs)
        ],
        "dim": DIM,
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
        "ms": int((time.time() - t0) * 1000),
    })


@app.post("/embeddings")
def legacy_embeddings():
    """Ollama-style single-prompt endpoint."""
    body = request.get_json(force=True, silent=True) or {}
    text = body.get("prompt") or body.get("input")
    if not text:
        return jsonify({"error": "missing 'prompt'"}), 400
    vec = embed(text if isinstance(text, list) else [text])[0]
    return jsonify({"embedding": vec, "model": body.get("model", "bge-m3"),
                    "dim": DIM})


@app.get("/health")
def health():
    return jsonify({"status": "ok", "dim": DIM, "model": "bge-m3"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11435)
    args = parser.parse_args()
    load_model()
    print(f"[serve] http://{args.host}:{args.port}", flush=True)
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()