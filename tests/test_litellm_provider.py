import unittest

from row_bot.providers.catalog import PROVIDER_DEFINITIONS, get_provider_definition
from row_bot.providers.models import AuthMethod, TransportMode


class TestLiteLLMProvider(unittest.TestCase):
    def test_provider_registered(self):
        self.assertIn("litellm", PROVIDER_DEFINITIONS)

    def test_provider_definition(self):
        defn = get_provider_definition("litellm")
        self.assertIsNotNone(defn)
        self.assertEqual(defn.id, "litellm")
        self.assertEqual(defn.display_name, "LiteLLM")
        self.assertEqual(defn.default_transport, TransportMode.OPENAI_CHAT)
        self.assertEqual(defn.base_url, "http://localhost:4000/v1")
        self.assertIn(AuthMethod.API_KEY, defn.auth_methods)

    def test_provider_uses_openai_compatible_transport(self):
        defn = get_provider_definition("litellm")
        self.assertEqual(defn.default_transport, TransportMode.OPENAI_CHAT)
