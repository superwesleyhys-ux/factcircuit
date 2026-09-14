"""Provider configuration tests without optional dependencies or API requests."""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

CLIENT = Path(__file__).resolve().parents[1] / "integrations/news-tracing-master/agent/llm_client.py"


class ClientSelectionTests(unittest.TestCase):
    def setUp(self):
        self.factory = Mock()
        optional = {"dotenv": SimpleNamespace(load_dotenv=lambda: None),
                    "openai": SimpleNamespace(AsyncOpenAI=self.factory)}
        with patch.dict(sys.modules, optional):
            spec = importlib.util.spec_from_file_location("standalone_api_client_fixture", CLIENT)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.client = module.LLMClient

    @patch.dict(os.environ, {}, clear=True)
    def test_api_has_no_implicit_local_astra_default(self):
        with self.assertRaisesRegex(ValueError, "API mode requires --model"):
            self.client()
        self.factory.assert_not_called()

    @patch.dict(os.environ, {"OPENAI_MODEL": "provider-model", "OPENAI_SEARCH_MODEL": "provider-search"}, clear=True)
    def test_explicit_provider_models_are_preserved(self):
        client = self.client()
        self.assertEqual("provider-model", client.model)
        self.assertEqual("provider-search", client.search_model)
        self.factory.assert_called_once()

    @patch.dict(os.environ, {"OPENAI_MODEL": "provider-model", "OPENAI_SEARCH_MODEL": "provider-search"}, clear=True)
    def test_model_argument_overrides_environment(self):
        self.assertEqual("explicit-provider-model", self.client(model="explicit-provider-model").model)
        with self.assertRaises(ValueError):
            self.client(model=" ")

    @patch.dict(os.environ, {"OPENAI_MODEL": "provider-model"}, clear=True)
    def test_search_model_is_also_explicit(self):
        with self.assertRaisesRegex(ValueError, "OPENAI_SEARCH_MODEL"):
            self.client()
        self.factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
