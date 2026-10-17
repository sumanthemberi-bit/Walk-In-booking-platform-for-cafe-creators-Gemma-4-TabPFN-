"""Thin wrapper around a local Ollama server running Gemma.

Everything stays on this machine: no API keys, no cloud calls.
"""
import os
import re

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
# RTX 3050 (4 GB VRAM) + 32 GB RAM: e4b runs with partial GPU offload.
# If it feels slow, set WALKIN_MODEL=gemma4:e2b
MODEL = os.getenv("WALKIN_MODEL", "gemma4:e4b")


def is_running() -> tuple[bool, str]:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        names = [m["name"] for m in r.json().get("models", [])]
        if not any(n.startswith(MODEL.split(":")[0]) for n in names):
            return False, f"Ollama is running but `{MODEL}` isn't pulled. Run: ollama pull {MODEL}"
        return True, f"Local model: {MODEL}"
    except requests.RequestException:
        return False, "Ollama isn't running. Start it with: ollama serve"


def generate(system: str, user: str, temperature: float = 0.7) -> str:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        # Gemma 4 "thinks" before answering by default; short messages don't need it and it's much slower.
        "think": False,
        "options": {"temperature": temperature, "num_ctx": 4096},
    }
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=300)
    if r.status_code == 400 and "think" in r.text.lower():  # model/Ollama version without the toggle
        payload.pop("think")
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=300)
    r.raise_for_status()
    text = r.json()["message"]["content"]
    # Drop any reasoning block the model might emit.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()
