import subprocess,sys,unittest
from pathlib import Path
class CategoryTests(unittest.TestCase):
 def test_monthly_categories(self):
  result=subprocess.run([sys.executable,str(Path(__file__).with_name('category_checks.py'))],capture_output=True,text=True,timeout=45)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr)
