from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from app.core.config import load_config


class ProviderPreferencesConfigTests(unittest.TestCase):
    def _write_config(self, content: str) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        handle.write(textwrap.dedent(content))
        handle.flush()
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def test_load_config_accepts_known_provider_preferences(self):
        path = self._write_config(
            """
            modes:
              fast:
                max_provider_attempts: 1
                max_queries: 1
                max_pages_to_fetch: 1
            providers:
              - name: searxng
                kind: searxng
            provider_preferences:
              fast:
                prefer: [searxng]
            """
        )

        config = load_config(path)

        self.assertEqual(config.provider_preferences["fast"].prefer, ["searxng"])

    def test_load_config_rejects_unknown_provider_preferences(self):
        path = self._write_config(
            """
            modes:
              fast:
                max_provider_attempts: 1
                max_queries: 1
                max_pages_to_fetch: 1
            providers:
              - name: searxng
                kind: searxng
            provider_preferences:
              research:
                prefer: [exa]
                avoid: [ghost]
            """
        )

        with self.assertRaisesRegex(ValueError, "unknown providers: exa, ghost"):
            load_config(path)

    def test_litellm_provider_derives_path_and_default_api_key_env(self):
        path = self._write_config(
            """
            modes:
              fast:
                max_provider_attempts: 1
                max_queries: 1
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
        self.assertEqual(config.providers[0].api_key_env, "LITELLM_API_KEY")

    def test_research_llm_ready_when_compiler_is_configured(self):
        path = self._write_config(
            """
            modes:
              fast:
                max_provider_attempts: 1
                max_queries: 1
                max_pages_to_fetch: 1
            providers:
              - name: searxng
                kind: searxng
            compiler:
              enabled: true
              base_url: http://litellm.local/v1
              model_id: gpt-4o-mini
            """
        )

        config = load_config(path)

        self.assertTrue(config.research_llm_ready)

    def test_research_llm_ready_when_vane_is_configured(self):
        path = self._write_config(
            """
            modes:
              fast:
                max_provider_attempts: 1
                max_queries: 1
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

        self.assertTrue(config.research_llm_ready)

    def test_research_llm_not_ready_without_vane_or_compiler(self):
        path = self._write_config(
            """
            modes:
              fast:
                max_provider_attempts: 1
                max_queries: 1
                max_pages_to_fetch: 1
            providers:
              - name: searxng
                kind: searxng
            """
        )

        config = load_config(path)

        self.assertFalse(config.research_llm_ready)
        self.assertIn("research mode requires LLM support", config.research_llm_requirement_error)


if __name__ == "__main__":
    unittest.main()
