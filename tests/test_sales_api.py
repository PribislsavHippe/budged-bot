import subprocess
import sys
import unittest
from pathlib import Path


class SalesHTTPTests(unittest.TestCase):
    def test_http_workflow(self):
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('sales_http_checks.py'))],capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
