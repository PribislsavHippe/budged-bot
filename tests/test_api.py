"""Run the legacy API harness in isolation, without replacing process-wide modules."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

class ApiChecks(unittest.TestCase):
    def test_api_checks(self):
        root=Path(__file__).resolve().parent.parent
        env=dict(os.environ,PYTHONPATH=str(root))
        completed=subprocess.run([sys.executable,str(root/'tests/api_checks.py')],
                                 cwd=root,env=env,capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)

if __name__=='__main__':unittest.main()
