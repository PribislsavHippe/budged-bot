import subprocess,sys,unittest
from pathlib import Path
class TableRecognitionTests(unittest.TestCase):
 def test_checks(self):
  result=subprocess.run([sys.executable,str(Path(__file__).with_name('report_table_checks.py'))],capture_output=True,text=True,timeout=45)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr)
