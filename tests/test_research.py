import subprocess,sys,unittest
from pathlib import Path
class ResearchIntegrationTests(unittest.TestCase):
    def test_http_and_bot(self):
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('research_http_checks.py'))],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_learn_by_doing(self):
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('ux_flow_checks.py'))],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
