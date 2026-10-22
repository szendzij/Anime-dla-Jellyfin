import os
import sys
import unittest
from pathlib import Path

# Add client/ to sys.path
CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))

from providers import ProviderError, CdaError, ShindenError  # noqa: E402
from oa import OgladajAnimeError, AuthError  # noqa: E402


class TestExceptionHierarchy(unittest.TestCase):
    def test_provider_errors_inherit_from_base(self):
        self.assertTrue(issubclass(OgladajAnimeError, ProviderError))
        self.assertTrue(issubclass(CdaError, ProviderError))
        self.assertTrue(issubclass(ShindenError, ProviderError))
        self.assertTrue(issubclass(AuthError, OgladajAnimeError))

    def test_polymorphic_catch(self):
        try:
            raise ShindenError("błąd shindena")
        except ProviderError as e:
            self.assertEqual(str(e), "błąd shindena")


if __name__ == "__main__":
    unittest.main()
