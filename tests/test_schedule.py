import subprocess,sys,unittest
from pathlib import Path
class ScheduleIntegrationTests(unittest.TestCase):
    def test_time_and_import_workflow(self):
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('schedule_checks.py'))],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
