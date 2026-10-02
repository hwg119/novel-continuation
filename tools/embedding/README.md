# Local BGE-M3 Embedding Service

Self-hosted BGE-M3 embedding that bypasses Ollama. Why it exists:

- Ollama's model registry serves blobs from Cloudflare R2 with presigned
  URLs. From mainland China this times out reliably, so ``ollama pull
  bge-m3`` will not succeed.
- A user with no internet access still wants the import → retrieval →
  continuation pipeline to work, so ``embedding_adapters.py`` exposes an
  ``OfflineHashEmbeddingAdapter`` fallback. With this service you can get
  the real semantic embeddings without Ollama at all.

## Files

| File | Purpose |
| --- | --- |
| `dl_bge_m3.py` | Download BAAI/bge-m3 ONNX weights from `hf-mirror.com` |
| `embedding_server.py` | Flask HTTP service speaking the OpenAI /v1/embeddings protocol |
| `probe_embed.py` | Smoke test: prints a 6×6 cosine matrix so you can eyeball sanity |
| `.cache/` | Hugging Face snapshot cache (gitignored, ~2.3 GB) |

## Setup

```powershell
# 1. one-time download (≈ 3 minutes on a 16 MB/s link)
python dl_bge_m3.py

# 2. install server deps (into the project's venv)
uv pip install --python .venv\Scripts\python.exe optimum[onnxruntime] onnxruntime sentencepiece flask

# 3. start the server in the background
python embedding_server.py --port 11435

# 4. smoke test
python probe_embed.py
```

## Wiring it into the project

Pass `--embed 本地bge bge-m3 http://127.0.0.1:11435` (or set the equivalent
fields in the GUI) to the continuation scripts under
`scripts/continuation/`. The factory in `embedding_adapters.py` resolves
`本地bge` / `localopenai` / `本地embedding` to
`LocalOpenAIEmbeddingAdapter`, which speaks the same `/v1/embeddings`
contract this server implements.

## Why a separate port (11435)?

Ollama owns 11434. Running this service on 11435 keeps the two stacks
independent — you can keep using Ollama for the LLM while the embedding
service runs entirely outside of it.