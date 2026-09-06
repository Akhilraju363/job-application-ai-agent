"""Shared chat-completions client for the automated pipeline (score/tailor/research).

All three automated steps hit one OpenAI-compatible /chat/completions endpoint with
identical retry + hard-timeout behavior. Centralizing it here means switching model or
endpoint (e.g. OpenRouter -> a local Ollama server) is a one-line .env change instead of
the same edit in three files.

Config -- all via .env, all optional. Defaults reproduce the original OpenRouter setup:

  llm_base_url          default https://openrouter.ai/api/v1
  llm_model             default nvidia/nemotron-3.5-lightning:free
  llm_fallback_model    default "" (disabled). Tried only if llm_model fails every retry.
  llm_api_key           default: falls back to open_router_apikey. Set "ollama" (or leave
                        blank) for a local Ollama server.
  llm_reasoning_effort  default "low". Set "" to omit the field entirely -- non-reasoning
                        models and Ollama don't understand it.
  llm_request_deadline  default 240 -- hard wall-clock seconds per attempt.
  llm_max_retries       default 2.

Local Ollama example (no key, no quota, no rate limits -- good for testing the scripts
without burning OpenRouter's free-tier daily budget):

  llm_base_url=http://localhost:11434/v1
  llm_model=qwen2.5:7b
  llm_api_key=ollama
  llm_reasoning_effort=

Ollama can't be reached from the Modal cron container, so the deployed path keeps its
OpenRouter defaults; this switch is for local runs.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import requests

_BASE_URL = os.environ.get("llm_base_url", "https://openrouter.ai/api/v1").rstrip("/")
CHAT_URL = f"{_BASE_URL}/chat/completions"

MODEL = os.environ.get("llm_model", "nvidia/nemotron-3.5-lightning:free")
FALLBACK_MODEL = os.environ.get("llm_fallback_model", "").strip()
_API_KEY = os.environ.get("llm_api_key") or os.environ.get("open_router_apikey", "")
_REASONING_EFFORT = os.environ.get("llm_reasoning_effort", "low").strip()

MAX_RETRIES = int(os.environ.get("llm_max_retries", "2"))
REQUEST_DEADLINE = int(os.environ.get("llm_request_deadline", "240"))
# 240s is a hard wall-clock cap per attempt. requests' own `timeout` can be bypassed by a
# server that trickles bytes slowly enough to keep resetting the per-read window without
# ever finishing, so every attempt is also raced against a thread with this deadline.
# Single-flight calls to the free-tier reasoning model measured ~10-40s typically, but its
# hidden "thinking" tokens scale with prompt complexity -- one Modal run saw 7/10 jobs blow
# past a 90s deadline identically on every retry, so the deadline (not flakiness) was the
# bottleneck. Retrying against too short a deadline just repeats the failure. Firing calls
# concurrently also makes the free tier throttle/serialize them (2 concurrent measured at
# 171s/181s each) -- callers score sequentially, do not parallelize.


def _post(payload):
    headers = {}
    if _API_KEY:
        headers["Authorization"] = f"Bearer {_API_KEY}"
    return requests.post(CHAT_URL, headers=headers, json=payload, timeout=REQUEST_DEADLINE)


def _call_model(model, prompt, label, json_mode):
    """Try one model up to MAX_RETRIES times. Raises the last exception on total failure."""
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if _REASONING_EFFORT:
        # Caps this reasoning model's hidden "thinking" tokens -- measured cutting a trivial
        # prompt's reasoning trace from 22k+ chars to 377. Reduces how often a job blows
        # REQUEST_DEADLINE from slow thinking; doesn't fix mid-stream connection drops.
        payload["reasoning"] = {"effort": _REASONING_EFFORT}

    for attempt in range(1, MAX_RETRIES + 1):
        # Not using ThreadPoolExecutor as a context manager: its __exit__ calls
        # shutdown(wait=True), which would block on the very hung thread we're trying to
        # time out on. shutdown(wait=False) abandons it -- the orphaned request is
        # discarded when it eventually returns.
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            resp = ex.submit(_post, payload).result(timeout=REQUEST_DEADLINE)
        except FutureTimeoutError:
            ex.shutdown(wait=False)
            if attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt
            print(f"  retry {attempt}/{MAX_RETRIES} for {label!r} ({model}) after hard timeout ({REQUEST_DEADLINE}s, waiting {wait}s)")
            time.sleep(wait)
            continue
        ex.shutdown(wait=False)

        try:
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            if content is None:
                raise ValueError("model returned null content (refusal or empty completion)")
            return content.strip()
        except Exception as e:
            # Deliberately broad: this has crashed the batch on RequestException (dropped
            # connection), FutureTimeoutError (hung request), and null-content responses.
            # Intent is "retry transient failures, surface permanent ones after retries".
            if attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt
            print(f"  retry {attempt}/{MAX_RETRIES} for {label!r} ({model}) after {type(e).__name__}: {e} (waiting {wait}s)")
            time.sleep(wait)

    raise RuntimeError("unreachable: retry loop exited without return or raise")


def call_llm(prompt, label, json_mode=False):
    """Call llm_model, falling back to llm_fallback_model (if set) only on total failure.

    Returns the response content as a stripped string. With json_mode=True the caller is
    still responsible for json.loads() -- the model is asked for JSON but may not comply.
    """
    try:
        return _call_model(MODEL, prompt, label, json_mode)
    except Exception as e:
        if not FALLBACK_MODEL:
            raise
        print(f"  {MODEL} exhausted for {label!r} ({type(e).__name__}: {e}) -- falling back to {FALLBACK_MODEL}")
        return _call_model(FALLBACK_MODEL, prompt, label, json_mode)
