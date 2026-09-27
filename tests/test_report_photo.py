import subprocess
import sys
import unittest
from pathlib import Path

class PhotoIntegrationTests(unittest.TestCase):
    def test_photo_workflow(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('report_photo_checks.py'))],
                                capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
