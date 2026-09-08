"""Provider-aware chat client for the automated pipeline (score / tailor / research).

Free-only by design. There is no paid model, no OpenRouter credit, no billing-enabled
fallback anywhere in here -- see COST NOTES at the bottom.

Two modes, selected automatically:

  LOCAL (recovery)   -- `llm_base_url` is set in .env (e.g. http://localhost:11434/v1).
                        One provider, no failover chain, no request spacing, no cloud
                        calls. This is the Ollama recovery path.

  CLOUD (Modal cron) -- `llm_base_url` is NOT set. Builds a failover chain from whichever
                        of these API keys are present, in `llm_provider_order`:

    groq        api.groq.com/openai/v1            GROQ_API_KEY
                default model: llama-3.3-70b-versatile
                free tier: no card, ~30 RPM / ~1K RPD, no training on inputs/outputs,
                commercial use permitted.
    openrouter  openrouter.ai/api/v1             OPENROUTER_API_KEY (or open_router_apikey)
                default model: google/gemma-4-26b-a4b-it:free   (:free only, never paid)
                free tier: no card, ~50 req/day, aggressively burst-throttled.
    gemini      generativelanguage.googleapis.com/v1beta/openai   GEMINI_API_KEY
                default model: gemini-2.5-flash
                free tier: no card, ~10-15 RPM / ~1K RPD. NOTE: Google may use free-tier
                inputs/outputs for training -- provider C on purpose; omit the key to skip.

  A provider is tried in order; on 429 (rate limit) it backs off honouring Retry-After,
  on 404 (model gone -- e.g. the delisted minimax model) it is skipped immediately with
  no retries, on 5xx/timeout/bad-JSON it retries then moves on. When every configured
  provider fails, call_llm raises -- the caller turns that into the Telegram alert.

Config (.env / Modal secret), all optional:

  llm_provider_order        default "groq,openrouter,gemini"
  llm_request_delay_seconds default 5 (cloud) / 0 (local) -- spacing before each request
  llm_max_retries           default 2  -- attempts per provider before moving on
  llm_request_deadline      default 120 -- hard wall-clock cap per attempt (seconds); the
                            free chat models here answer in ~5-40s, so a longer wait means
                            a hung connection -- cut it and fail over rather than sit on it
  llm_groq_model / llm_openrouter_model / llm_gemini_model -- override the model per provider
  llm_base_url / llm_model / llm_api_key -- LOCAL mode only (Ollama)
"""
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import requests

REQUEST_DEADLINE = int(os.environ.get("llm_request_deadline", "120"))
MAX_RETRIES = max(1, int(os.environ.get("llm_max_retries", "2")))

_LOCAL_BASE_URL = os.environ.get("llm_base_url", "").strip()
IS_LOCAL = bool(_LOCAL_BASE_URL)

# name -> (base_url, (api-key env names, first hit wins), default model, model-override env)
_PROVIDER_SPECS = {
    "groq": ("https://api.groq.com/openai/v1", ("GROQ_API_KEY",),
             "llama-3.3-70b-versatile", "llm_groq_model"),
    "openrouter": ("https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY", "open_router_apikey"),
                   "google/gemma-4-26b-a4b-it:free", "llm_openrouter_model"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
               "gemini-2.5-flash", "llm_gemini_model"),
}
_DEFAULT_ORDER = ("groq", "openrouter", "gemini")


def _build_providers():
    if IS_LOCAL:
        return [{
            "name": "local",
            "base_url": _LOCAL_BASE_URL.rstrip("/"),
            "api_key": os.environ.get("llm_api_key") or "ollama",
            "model": os.environ.get("llm_model", "qwen2.5:7b"),
            "delay": float(os.environ.get("llm_request_delay_seconds", "0")),
        }]
    order = [p.strip() for p in os.environ.get("llm_provider_order", ",".join(_DEFAULT_ORDER)).split(",") if p.strip()]
    delay = float(os.environ.get("llm_request_delay_seconds", "5"))
    out = []
    for name in order:
        spec = _PROVIDER_SPECS.get(name)
        if not spec:
            continue
        base_url, key_envs, default_model, model_env = spec
        api_key = next((os.environ[e] for e in key_envs if os.environ.get(e, "").strip()), None)
        if not api_key:
            continue
        out.append({
            "name": name,
            "base_url": base_url,
            "api_key": api_key,
            "model": os.environ.get(model_env, "").strip() or default_model,
            "delay": delay,
        })
    return out


PROVIDERS = _build_providers()

# Back-compat exports (score_jobs.py logs these).
MODEL = PROVIDERS[0]["model"] if PROVIDERS else "(no provider configured)"
FALLBACK_MODEL = PROVIDERS[1]["model"] if len(PROVIDERS) > 1 else ""
PROVIDER_SUMMARY = " -> ".join(f"{p['name']}/{p['model']}" for p in PROVIDERS) or "(no provider configured)"


class AllProvidersFailed(RuntimeError):
    """Every configured provider failed for one call. Caller escalates to Telegram."""


class _SkipProvider(Exception):
    """Internal: stop trying this provider, move to the next one."""


_dead_models = set()  # (provider_name, model) seen returning 404 this process -- don't re-try


def _post(provider, payload):
    return requests.post(
        f"{provider['base_url']}/chat/completions",
        headers={"Authorization": f"Bearer {provider['api_key']}"},
        json=payload,
        timeout=REQUEST_DEADLINE,
    )


def _status_of(exc, resp):
    s = getattr(getattr(exc, "response", None), "status_code", None)
    if s is None and resp is not None:
        s = getattr(resp, "status_code", None)
    return s


def _parse_json(text):
    """Return a dict from an LLM response, tolerating stray prose / ``` fences.

    Raises ValueError (JSONDecodeError is a subclass) when nothing parseable is found.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in response")
    return json.loads(m.group(0))


def _extract(resp, json_mode):
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    if content is None:
        raise ValueError("model returned null content (refusal or empty completion)")
    content = content.strip()
    if not json_mode:
        return content
    return json.dumps(_parse_json(content))  # normalized, guaranteed-valid JSON string


def _backoff_429(resp, attempt):
    retry_after = None
    if resp is not None:
        raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        if raw:
            try:
                retry_after = float(raw)
            except ValueError:
                retry_after = None
    if retry_after is None:
        retry_after = 5.0 * (2 ** (attempt - 1))  # 5, 10, 20, ...
    wait = min(retry_after + random.uniform(0, 3), 90.0)
    print(f"    429 -- backing off {wait:.1f}s (attempt {attempt})")
    time.sleep(wait)


def _call_provider(provider, prompt, label, json_mode):
    if (provider["name"], provider["model"]) in _dead_models:
        raise _SkipProvider(f"{provider['model']} previously 404'd this run")

    payload = {"model": provider["model"], "messages": [{"role": "user", "content": prompt}]}
    send_json_param = json_mode
    if send_json_param:
        payload["response_format"] = {"type": "json_object"}

    last = "unknown"
    for attempt in range(1, MAX_RETRIES + 1):
        if provider["delay"]:
            time.sleep(provider["delay"])

        resp = None
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            resp = ex.submit(_post, provider, payload).result(timeout=REQUEST_DEADLINE)
            return _extract(resp, json_mode)
        except Exception as e:  # noqa: BLE001 -- classified below
            status = _status_of(e, resp)
            bad_json = isinstance(e, (json.JSONDecodeError, ValueError)) and not isinstance(e, requests.RequestException)
            last = "invalid JSON" if bad_json else (f"HTTP {status}" if status else type(e).__name__)

            if status in (401, 403):
                raise _SkipProvider(f"auth failed ({last})") from e
            if status == 404:
                _dead_models.add((provider["name"], provider["model"]))
                raise _SkipProvider(f"model not found ({provider['model']})") from e
            if status == 400 and send_json_param:
                # provider likely rejects response_format -- retry once without it, then
                # fall back to tolerant parsing of a plain completion.
                print(f"  {provider['name']}: 400 with response_format -- retrying without it")
                payload.pop("response_format", None)
                send_json_param = False
                continue
            if status == 400:
                raise _SkipProvider(f"bad request ({last})") from e

            if attempt == MAX_RETRIES:
                raise _SkipProvider(f"exhausted {MAX_RETRIES} attempts ({last})") from e

            if status == 429:
                _backoff_429(resp, attempt)
            else:
                wait = min(30.0, 2.0 * (2 ** attempt)) + random.uniform(0, 2)
                print(f"  retry {attempt}/{MAX_RETRIES} {provider['name']}/{provider['model']} "
                      f"for {label!r} after {last} ({wait:.1f}s)")
                time.sleep(wait)
        finally:
            ex.shutdown(wait=False)

    raise _SkipProvider(f"fell through retry loop ({last})")


def call_llm(prompt, label, json_mode=False):
    """Run one prompt through the provider chain. Returns the response text.

    With json_mode=True the returned string is guaranteed to parse as JSON (the client
    validates and, if needed, fails over to the next provider on malformed output), so
    callers can json.loads() it directly.

    Raises RuntimeError if no provider is configured, or AllProvidersFailed if every
    configured provider failed for this call.
    """
    if not PROVIDERS:
        raise RuntimeError(
            "no LLM provider configured -- set llm_base_url for local Ollama, or one of "
            "GROQ_API_KEY / OPENROUTER_API_KEY / GEMINI_API_KEY for the cloud chain"
        )

    failures = []
    for provider in PROVIDERS:
        try:
            return _call_provider(provider, prompt, label, json_mode)
        except _SkipProvider as e:
            print(f"  provider {provider['name']} unavailable for {label!r}: {e}")
            failures.append(f"{provider['name']} ({e})")
        if len(PROVIDERS) > 1:
            time.sleep(random.uniform(0.5, 1.5))  # small gap before the next provider

    raise AllProvidersFailed(
        f"all {len(PROVIDERS)} provider(s) failed for {label!r}: " + "; ".join(failures)
    )


# ---------------------------------------------------------------------------
# COST NOTES -- every provider above is a genuinely free tier:
#   groq        no credit card, rate-limited free developer tier, commercial use allowed,
#               inputs/outputs not used for training. No auto-upgrade to paid.
#   openrouter  ":free" model suffix only; a $0-credit key cannot call paid models.
#   gemini      Google AI Studio free tier, no card; free models only (Flash / Flash-Lite).
# Nothing here reads a billing flag or falls back to a paid model. If a provider ever
# requires a card or auto-bills, remove it from llm_provider_order -- do not add credit.
# ---------------------------------------------------------------------------
