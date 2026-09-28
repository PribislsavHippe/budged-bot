import subprocess
import sys
import unittest
from pathlib import Path

class IdentityIntegrationTests(unittest.TestCase):
    def test_identity_workflow(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('identity_checks.py'))],
                                capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
