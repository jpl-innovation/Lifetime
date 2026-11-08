"""Exercise imports from only the files copied by each source installer."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


class SourceInstallationTests(unittest.TestCase):
    def test_install_lists_provide_complete_headless_and_desktop_dependencies(self):
        project = Path(__file__).parent
        for installer in ('install_linux.sh', 'install_windows_common.ps1'):
            with self.subTest(installer=installer), tempfile.TemporaryDirectory() as directory:
                installed = Path(directory)
                names = set(re.findall(r'lifetime_[a-z0-9_]+\.py', (project/installer).read_text(encoding='utf-8')))
                for name in names:
                    shutil.copy2(project/name, installed/name)
                code = ('import sys; sys.path.insert(0,sys.argv[1]); '
                        'import lifetime_tracker as t; import lifetime_gui; import lifetime_reports; '
                        'import lifetime_ledger; value=t.new_ledger(); t.validate_ledger(value); '
                        'print("INSTALLED_IMPORTS_OK")')
                environment = {**os.environ, 'LIFETIME_DATA_DIR': str(installed/'data'), 'QT_QPA_PLATFORM': 'offscreen'}
                result = subprocess.run([sys.executable, '-I', '-c', code, str(installed)],
                                        cwd=installed, env=environment, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('INSTALLED_IMPORTS_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
