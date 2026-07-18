#!/usr/bin/env python3
"""Download ONNX model for Docker build."""
import sys

try:
    import httpx
except ImportError:
    print("httpx not available, skipping ONNX download")
    sys.exit(0)

urls = [
    "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx/model.onnx",
    "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/model.onnx",
]

for url in urls:
    try:
        print(f"Trying {url}...")
        resp = httpx.get(url, timeout=180.0, follow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 1000:
            with open("/build/model.onnx", "wb") as f:
                f.write(resp.content)
            print(f"Downloaded ONNX model: {len(resp.content)} bytes")
            sys.exit(0)
    except Exception as e:
        print(f"Failed from {url}: {e}")

print("ONNX model download failed, will download at runtime")
sys.exit(0)  # Don't fail the build
