"""Download BAAI/bge-m3 ONNX weights from a Hugging Face mirror.

Why this exists
---------------
Pulling ``bge-m3`` through Ollama goes through a Cloudflare R2 presigned URL
that is unreachable from mainland China. Downloading the ONNX weights from
``hf-mirror.com`` (a Hugging Face mirror) bypasses that constraint and keeps
the whole pipeline offline once the download finishes.

Usage
-----
::

    python dl_bge_m3.py                  # default: HF mirror, default cache
    python dl_bge_m3.py --repo BAAI/bge-m3
    python dl_bge_m3.py --cache-dir ~/.cache/ai-novel/bge-m3

The cache layout matches ``huggingface_hub`` snapshots so the same directory
can be opened by either this tool or ``transformers``.
"""
import argparse
import os
import sys
import time
from urllib.request import Request, urlopen

DEFAULT_MIRROR = "https://hf-mirror.com"
DEFAULT_REPO = "BAAI/bge-m3"


# Files required for the ONNX runtime path. Sizes (bytes) are used for an
# integrity check and to skip already-downloaded files.
FILES = [
    ("onnx/model.onnx",              724923),
    ("onnx/model.onnx_data",         2266820608),
    ("onnx/config.json",             698),
    ("onnx/sentencepiece.bpe.model", 5069051),
    ("onnx/special_tokens_map.json", 964),
    ("onnx/tokenizer.json",           17082821),
    ("onnx/tokenizer_config.json",   1173),
    ("config.json",                  687),
    ("config_sentence_transformers.json", 123),
    ("modules.json",                 349),
    ("sentence_bert_config.json",    54),
    ("tokenizer_config.json",        444),
    ("special_tokens_map.json",      964),
]


def _download(mirror: str, repo: str, rel_path: str, expected_size: int,
              out_path: str, timeout: int = 1800) -> bool:
    if os.path.exists(out_path) and os.path.getsize(out_path) == expected_size:
        print(f"[skip] {rel_path} ({expected_size} bytes) ok", flush=True)
        return True
    url = f"{mirror}/{repo}/resolve/main/{rel_path}"
    print(f"[get]  {rel_path} 期望 {expected_size / 1e6:.1f} MB", flush=True)
    t0 = time.time()
    # Spawn curl: avoids Python stdout buffering and gives us progress + resume.
    rc = os.system(
        f'curl.exe -L --retry 5 --connect-timeout 30 '
        f'-C - -o "{out_path}" --max-time {timeout} "{url}"'
    )
    if rc != 0:
        print(f"[FAIL] curl rc={rc}", flush=True)
        return False
    print(flush=True)
    got = os.path.getsize(out_path)
    if got != expected_size:
        print(f"[FAIL] {rel_path} 期望 {expected_size} 实得 {got}", flush=True)
        os.remove(out_path)
        return False
    print(f"[ok]   {rel_path} {got / 1e6:.1f} MB  耗时 {time.time() - t0:.1f}s", flush=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mirror", default=DEFAULT_MIRROR)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--cache-dir", default=None,
                        help="Override the snapshot cache directory.")
    args = parser.parse_args()

    if args.cache_dir is None:
        # Default to <repo>/.cache/hf_cache so the snapshot layout mirrors
        # huggingface_hub and the same dir can be opened by transformers.
        script_dir = os.path.dirname(os.path.abspath(__file__))
        args.cache_dir = os.path.join(script_dir, ".cache", "huggingface",
                                       f"models--{args.repo.replace('/', '--')}",
                                       "snapshots", "main")

    cache_dir = args.cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    print(f"下载到: {cache_dir}")

    fails = 0
    for rel, size in FILES:
        out = os.path.join(cache_dir, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if not _download(args.mirror, args.repo, rel, size, out):
            fails += 1
    print(f"\n完成，失败 {fails} 个")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())