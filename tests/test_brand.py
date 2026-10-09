"""Canonical FactCircuit entry points and NewsVerify compatibility."""

from pathlib import Path
import subprocess
import sys
import tomllib
import unittest

import factcircuit
import newsverify
from factcircuit.provenance import run_provenance as factcircuit_run_provenance
from newsverify.provenance import run_provenance as newsverify_run_provenance


ROOT = Path(__file__).resolve().parents[1]


class BrandCompatibilityTests(unittest.TestCase):
    def test_factcircuit_is_canonical_distribution_and_command(self):
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual("factcircuit", project["name"])
        self.assertEqual(
            "factcircuit.cli:main", project["scripts"]["factcircuit"])

    def test_newsverify_api_remains_compatible(self):
        self.assertEqual(factcircuit.__version__, newsverify.__version__)
        self.assertIs(factcircuit_run_provenance, newsverify_run_provenance)

    def test_factcircuit_module_cli_uses_new_program_name(self):
        completed = subprocess.run(
            [sys.executable, "-m", "factcircuit", "--help"],
            cwd=ROOT, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False)
        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertIn("usage: factcircuit", completed.stdout)

    def test_newsverify_module_cli_still_runs(self):
        completed = subprocess.run(
            [sys.executable, "-m", "newsverify", "--help"],
            cwd=ROOT, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False)
        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertIn("usage: newsverify", completed.stdout)


if __name__ == "__main__":
    unittest.main()


class DoubleLoopEntryPointTests(unittest.TestCase):
    def test_factcircuit_double_loop_module_is_runnable(self):
        completed = subprocess.run(
            [sys.executable, "-m", "factcircuit.double_loop", "--help"],
            capture_output=True, text=True, check=True)
        self.assertIn("--max-model-calls", completed.stdout)

    def test_factcircuit_double_loop_reexports_runner(self):
        import factcircuit.double_loop
        import newsverify.double_loop
        self.assertIs(factcircuit.double_loop.run_double_loop_trace,
                      newsverify.double_loop.run_double_loop_trace)
