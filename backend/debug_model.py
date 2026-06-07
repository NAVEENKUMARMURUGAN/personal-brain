import sys
import os

# Intercept torch.load to trace exactly what file is being loaded
import torch
original_load = torch.load

def patched_load(f, *args, **kwargs):
    path = f if isinstance(f, str) else "(file-like object)"
    exists = os.path.exists(f) if isinstance(f, str) else "N/A"
    size = os.path.getsize(f) if isinstance(f, str) and os.path.exists(f) else "N/A"
    print(f"[TRACE] torch.load: {path}", flush=True)
    print(f"[TRACE]   exists={exists}  size={size}", flush=True)
    return original_load(f, *args, **kwargs)

torch.load = patched_load

# Also trace huggingface_hub downloads
try:
    import huggingface_hub
    orig_download = huggingface_hub.hf_hub_download
    def patched_download(*args, **kwargs):
        print(f"[TRACE] hf_hub_download: args={args} kwargs={kwargs}", flush=True)
        result = orig_download(*args, **kwargs)
        print(f"[TRACE] hf_hub_download result: {result}", flush=True)
        return result
    huggingface_hub.hf_hub_download = patched_download
except Exception as e:
    print(f"[WARN] Could not patch hf_hub_download: {e}", flush=True)

print("[INFO] HF cache dir:", os.environ.get("HF_HOME", "/root/.cache/huggingface"), flush=True)
print("[INFO] Cache exists:", os.path.exists("/root/.cache/huggingface"), flush=True)

from sentence_transformers import SentenceTransformer
try:
    m = SentenceTransformer("BAAI/bge-m3")
    print("[OK] Model loaded successfully!", flush=True)
except Exception as e:
    print(f"[ERROR] {type(e).__name__}: {e}", flush=True)
