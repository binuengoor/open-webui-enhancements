from __future__ import annotations

import tempfile
import textwrap
import unittest
import unittest.mock
from pathlib import Path

from app.core.config import load_config


class ConfigLoadingTests(unittest.TestCase):
    def _write_config(self, content: str) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        handle.write(textwrap.dedent(content))
        handle.flush()
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def test_litellm_provider_derives_path_and_default_api_key_env(self):
        path = self._write_config(
            """
            search_limits:
              max_provider_attempts: 1
              max_pages_to_fetch: 1
            providers:
              - name: brave-search
                kind: litellm-search
                base_url: http://litellm.local
                litellm_provider: brave-search
            """
        )

        config = load_config(path)

        self.assertEqual(len(config.providers), 1)
        self.assertEqual(config.providers[0].path, "/search/brave-search")
        self.assertEqual(config.providers[0].api_key_env, "EWS_LITELLM_API_KEY")

    def test_research_llm_ready_when_vane_is_fully_configured(self):
        path = self._write_config(
            """
            search_limits:
              max_provider_attempts: 1
              max_pages_to_fetch: 1
            providers:
              - name: searxng
                kind: searxng
            vane:
              enabled: true
              url: http://vane.local
              chat_provider_id: openai
              chat_model_key: test-chat-model
              embedding_provider_id: ollama
              embedding_model_key: test-embed-model
            """
        )

        config = load_config(path)

        self.assertTrue(config.research_llm_ready)

    def test_research_llm_not_ready_without_vane_config(self):
        path = self._write_config(
            """
            search_limits:
              max_provider_attempts: 1
              max_pages_to_fetch: 1
            providers:
              - name: searxng
                kind: searxng
            """
        )

        config = load_config(path)

        self.assertFalse(config.research_llm_ready)
        self.assertIn("research mode requires Vane proxy configuration", config.research_llm_requirement_error)

    def test_research_llm_not_ready_without_vane_provider_ids(self):
        path = self._write_config(
            """
            search_limits:
              max_provider_attempts: 1
              max_pages_to_fetch: 1
            providers:
              - name: searxng
                kind: searxng
            vane:
              enabled: true
              url: http://vane.local
            """
        )

        config = load_config(path)

        self.assertFalse(config.research_llm_ready)

    def test_compiler_env_vars_do_not_override_config(self):
        path = self._write_config(
            """
            search_limits:
              max_provider_attempts: 1
              max_pages_to_fetch: 1
            providers:
              - name: searxng
                kind: searxng
            compiler:
              enabled: false
              base_url: ""
              timeout_s: 20
              model_id: ""
            planner:
              llm_fallback_enabled: false
            """
        )

        with unittest.mock.patch.dict(
            "os.environ",
            {
                "EWS_COMPILER_ENABLED": "true",
                "EWS_COMPILER_BASE_URL": "http://litellm.local/v1",
                "EWS_COMPILER_TIMEOUT": "99",
                "EWS_COMPILER_MODEL_ID": "gpt-4o-mini",
                "EWS_PLANNER_LLM_FALLBACK_ENABLED": "true",
            },
            clear=False,
        ):
            config = load_config(path)

        self.assertFalse(config.compiler.enabled)
        self.assertEqual(config.compiler.base_url, "")
        self.assertEqual(config.compiler.timeout_s, 20)
        self.assertEqual(config.compiler.model_id, "")
        self.assertFalse(config.planner.llm_fallback_enabled)


if __name__ == "__main__":
    unittest.main()
