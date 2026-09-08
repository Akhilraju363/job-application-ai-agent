"""Mock-based tests for the provider-aware LLM client and the reconciliation guard.

Stdlib only (no pytest):  python -m unittest discover -s tests -v

Every network call is mocked -- these tests never hit a real provider, never spend
rate limit, never risk cost.
"""
import importlib
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


class FakeResp:
    def __init__(self, status=200, body=None, text=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {
            "choices": [{"message": {"content": text if text is not None else '{"score": 7}'}}]
        }
        self.headers = headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            err = requests.exceptions.HTTPError(f"{self.status_code} Error")
            err.response = self
            raise err


def load_llm(**env):
    """Import scripts/llm.py fresh with a controlled environment."""
    keep = {k: v for k, v in os.environ.items()
            if not k.startswith("llm_") and k not in
            ("GROQ_API_KEY", "OPENROUTER_API_KEY", "open_router_apikey", "GEMINI_API_KEY", "GOOGLE_API_KEY")}
    with mock.patch.dict(os.environ, {**keep, **env}, clear=True):
        sys.modules.pop("llm", None)
        llm = importlib.import_module("llm")
        return llm


class ProviderResolution(unittest.TestCase):
    def test_local_mode_single_provider_no_chain(self):
        llm = load_llm(llm_base_url="http://localhost:11434/v1", llm_model="qwen2.5:7b")
        self.assertTrue(llm.IS_LOCAL)
        self.assertEqual([p["name"] for p in llm.PROVIDERS], ["local"])
        self.assertEqual(llm.PROVIDERS[0]["delay"], 0.0)

    def test_cloud_chain_order_and_keys(self):
        llm = load_llm(GROQ_API_KEY="g", OPENROUTER_API_KEY="o", GEMINI_API_KEY="x")
        self.assertFalse(llm.IS_LOCAL)
        self.assertEqual([p["name"] for p in llm.PROVIDERS], ["groq", "openrouter", "gemini"])

    def test_missing_key_drops_provider(self):
        llm = load_llm(GEMINI_API_KEY="x")
        self.assertEqual([p["name"] for p in llm.PROVIDERS], ["gemini"])

    def test_no_provider_raises(self):
        llm = load_llm()
        self.assertEqual(llm.PROVIDERS, [])
        with self.assertRaises(RuntimeError):
            llm.call_llm("hi", "t")

    def test_backcompat_openrouter_key_name(self):
        llm = load_llm(open_router_apikey="legacy")
        self.assertEqual([p["name"] for p in llm.PROVIDERS], ["openrouter"])


class Failover(unittest.TestCase):
    def setUp(self):
        self.llm = load_llm(GROQ_API_KEY="g", OPENROUTER_API_KEY="o", GEMINI_API_KEY="x",
                            llm_request_delay_seconds="0", llm_max_retries="2")
        # make backoff instant
        self._sleep = mock.patch.object(time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    def _run(self, responses):
        """responses: list of FakeResp or Exception, consumed one per _post call.

        Starts the _post patch and registers its teardown, so it stays active for the
        rest of the test (not just this helper).
        """
        calls = []
        it = iter(responses)

        def fake_post(provider, payload):
            calls.append((provider["name"], payload["model"], "response_format" in payload))
            nxt = next(it)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        patcher = mock.patch.object(self.llm, "_post", side_effect=fake_post)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls, self.llm

    def test_first_provider_succeeds_no_fallback(self):
        calls, llm = self._run([FakeResp(text='{"score": 9}')])
        out = llm.call_llm("p", "job", json_mode=True)
        self.assertEqual(json.loads(out)["score"], 9)
        self.assertEqual([c[0] for c in calls], ["groq"])

    def test_429_backs_off_then_moves_to_next_provider(self):
        calls, llm = self._run([
            FakeResp(429, headers={"Retry-After": "1"}),
            FakeResp(429),
            FakeResp(text='{"score": 5}'),   # openrouter succeeds
        ])
        out = llm.call_llm("p", "job", json_mode=True)
        self.assertEqual(json.loads(out)["score"], 5)
        self.assertEqual([c[0] for c in calls], ["groq", "groq", "openrouter"])
        self.assertTrue(self._sleep.called)  # honored Retry-After / backoff

    def test_404_skips_provider_immediately_no_retry(self):
        calls, llm = self._run([
            FakeResp(404),                    # groq model gone -> no retry
            FakeResp(text='{"score": 6}'),    # openrouter
        ])
        out = llm.call_llm("p", "job", json_mode=True)
        self.assertEqual(json.loads(out)["score"], 6)
        self.assertEqual([c[0] for c in calls], ["groq", "openrouter"])
        self.assertIn(("groq", "llama-3.3-70b-versatile"), llm._dead_models)

    def test_5xx_retries_then_next_provider(self):
        calls, llm = self._run([
            FakeResp(503), FakeResp(503),      # groq: 2 attempts
            FakeResp(text='{"score": 8}'),     # openrouter
        ])
        out = llm.call_llm("p", "job", json_mode=True)
        self.assertEqual([c[0] for c in calls], ["groq", "groq", "openrouter"])
        self.assertEqual(json.loads(out)["score"], 8)

    def test_timeout_retries_then_next_provider(self):
        from concurrent.futures import TimeoutError as FTE
        calls, llm = self._run([FTE(), FTE(), FakeResp(text='{"ok": true}')])
        out = llm.call_llm("p", "job", json_mode=True)
        self.assertEqual([c[0] for c in calls], ["groq", "groq", "openrouter"])
        self.assertTrue(json.loads(out)["ok"])

    def test_malformed_json_retries_then_fails_over(self):
        calls, llm = self._run([
            FakeResp(text="sorry, I can't do that"),   # groq attempt 1: unparseable
            FakeResp(text="still not json"),           # groq attempt 2
            FakeResp(text='{"score": 4}'),             # openrouter
        ])
        out = llm.call_llm("p", "job", json_mode=True)
        self.assertEqual(json.loads(out)["score"], 4)
        self.assertEqual([c[0] for c in calls], ["groq", "groq", "openrouter"])

    def test_json_extracted_from_prose_and_fences(self):
        calls, llm = self._run([FakeResp(text='here you go:\n```json\n{"score": 3}\n```')])
        out = llm.call_llm("p", "job", json_mode=True)
        self.assertEqual(json.loads(out)["score"], 3)

    def test_all_providers_fail_raises_AllProvidersFailed(self):
        calls, llm = self._run([
            FakeResp(429), FakeResp(429),      # groq
            FakeResp(500), FakeResp(500),      # openrouter
            FakeResp(404),                     # gemini
        ])
        with self.assertRaises(self.llm.AllProvidersFailed):
            llm.call_llm("p", "job", json_mode=True)
        self.assertEqual([c[0] for c in calls], ["groq", "groq", "openrouter", "openrouter", "gemini"])

    def test_auth_failure_skips_provider_without_retry(self):
        calls, llm = self._run([
            FakeResp(401),                     # groq bad key -> skip, no retry
            FakeResp(text='{"score": 7}'),     # openrouter
        ])
        out = llm.call_llm("p", "job", json_mode=True)
        self.assertEqual([c[0] for c in calls], ["groq", "openrouter"])
        self.assertEqual(json.loads(out)["score"], 7)

    def test_400_on_response_format_retries_without_it(self):
        calls, llm = self._run([
            FakeResp(400),                     # groq rejects response_format
            FakeResp(text='{"score": 2}'),     # groq retry, no response_format -> ok
        ])
        out = llm.call_llm("p", "job", json_mode=True)
        self.assertEqual(json.loads(out)["score"], 2)
        self.assertEqual([c[0] for c in calls], ["groq", "groq"])
        self.assertTrue(calls[0][2])       # first call sent response_format
        self.assertFalse(calls[1][2])      # retry did not

    def test_non_json_mode_returns_raw_text(self):
        calls, llm = self._run([FakeResp(text="# Tailored Resume\n\nsome markdown")])
        out = llm.call_llm("p", "job")
        self.assertIn("Tailored Resume", out)


class Reconciliation(unittest.TestCase):
    """FIX 1 -- the no-silent-failure guard (modal_app.reconcile_qualified)."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import modal_app
        self.reconcile = modal_app.reconcile_qualified

    def test_qualified_and_saved_is_clean(self):
        unmet, _ = self.reconcile(
            [{"link": "j1", "qualified": True}, {"link": "j2", "qualified": False}],
            [{"link": "j1", "status": "saved"}])
        self.assertEqual(unmet, set())

    def test_qualified_not_saved_is_flagged(self):
        unmet, reasons = self.reconcile(
            [{"link": "j1", "qualified": True}],
            [{"link": "j1", "status": "flagged_error", "reason": "TimeoutError"}])
        self.assertEqual(unmet, {"j1"})
        self.assertEqual(reasons["j1"], "TimeoutError")

    def test_qualified_missing_entirely_is_flagged(self):
        unmet, reasons = self.reconcile(
            [{"link": "j1", "qualified": True}, {"link": "j2", "qualified": True}],
            [{"link": "j1", "status": "saved"}])
        self.assertEqual(unmet, {"j2"})

    def test_zero_qualified_is_clean(self):
        unmet, _ = self.reconcile([{"link": "j1", "qualified": False}], [])
        self.assertEqual(unmet, set())

    def test_saved_on_earlier_run_still_counts(self):
        unmet, _ = self.reconcile(
            [{"link": "j1", "qualified": True}],
            [{"link": "j1", "status": "saved", "score": 9}])
        self.assertEqual(unmet, set())


if __name__ == "__main__":
    unittest.main()
