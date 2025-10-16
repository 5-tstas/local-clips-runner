"""Aggregated entry point for unittest discovery."""

from __future__ import annotations

import importlib
import unittest

MODULES = (
    "tests.e2e.overlay.spec",
    "tests.e2e.abc.spec",
    "tests.e2e.chat.spec",
)


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str) -> unittest.TestSuite:  # noqa: D401
    """Load Playwright export specs from dedicated modules."""
    suite = unittest.TestSuite()
    for dotted_path in MODULES:
        module = importlib.import_module(dotted_path)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
