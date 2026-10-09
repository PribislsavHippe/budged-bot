"""One release gate: Python, browser logic, and real SQL in an ephemeral database."""
import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    if not shutil.which('node'):
        raise SystemExit('Install Node.js and run npm ci --ignore-scripts first.')
    env = dict(os.environ, PYTHONPATH=str(ROOT), BUDGET_TEST_PYTHON=sys.executable,
               BOT_TOKEN='123456:abcdefghijklmnopqrstuvwxyzABCDE',
               SUPABASE_URL='https://example.invalid', SUPABASE_KEY='test',
               WEBHOOK_HOST='', INBOX_ENCRYPTION_KEY='local-test-only', ADMIN_ID='1')

    def run(command):
        print('Checking:', ' '.join(str(x) for x in command), flush=True)
        subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=180)

    for path in [*ROOT.glob('*.py'), *ROOT.joinpath('tests').glob('*.py')]:
        ast.parse(path.read_text(), filename=str(path))
    for path in ROOT.joinpath('webapp').glob('*.js'):
        run(['node', '--check', path])
    html = ROOT.joinpath('webapp/index.html').read_text()
    with tempfile.TemporaryDirectory(prefix='budget-bot-check-') as directory:
        for i, source in enumerate(re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)):
            script = Path(directory, f'inline-{i}.js')
            script.write_text(source)
            run(['node', '--check', script])

    run([sys.executable, '-m', 'pip', 'check'])
    run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v'])
    scripts = ['calendar_time_checks.mjs', 'calendar_subscription_ui_checks.mjs', 'help_ui_checks.mjs', 'private_device_check.js',
               'private_concurrency_check.cjs', 'private_finance_check.cjs']
    scripts += [p.name for p in sorted(ROOT.joinpath('tests').glob('*sql_checks.mjs'))]
    for script in scripts:
        run(['node', ROOT / 'tests' / script])
    print('All release checks passed.', flush=True)


if __name__ == '__main__':
    main()
