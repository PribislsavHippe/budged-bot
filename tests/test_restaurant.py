import subprocess,sys,unittest
from pathlib import Path
class RestaurantHTTPTests(unittest.TestCase):
    def test_owner_dashboard(self):
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('restaurant_http_checks.py'))],capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
