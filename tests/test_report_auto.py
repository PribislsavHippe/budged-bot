import subprocess,sys,unittest
from pathlib import Path
class AutomaticReportTests(unittest.TestCase):
 def test_flow(self):
  result=subprocess.run([sys.executable,str(Path(__file__).with_name('report_auto_checks.py'))],capture_output=True,text=True,timeout=45)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr)
