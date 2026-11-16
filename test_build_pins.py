"""Release builds use exact, checked inputs (security audit M3): no floating versions, tags or silent overwrites."""
from pathlib import Path
import re
import unittest

PROJECT = Path(__file__).parent
PIN = re.compile(r"^[A-Za-z0-9_.\-]+==[0-9][A-Za-z0-9.]*( *; *.+)?$")


def text(name):
    return (PROJECT / name).read_text(encoding='utf-8')


class BuildPinTests(unittest.TestCase):
    def test_every_requirement_is_an_exact_version(self):
        files = sorted(PROJECT.glob('requirements*.txt'))
        self.assertGreaterEqual(len(files), 3)
        for path in files:
            for line in path.read_text(encoding='utf-8').splitlines():
                line = line.split('#')[0].strip()
                if line and not line.startswith('-r requirements-'):
                    self.assertRegex(line, PIN, f'{path.name}: pin {line!r} to an exact version')

    def test_build_scripts_install_only_pinned_packages(self):
        for name in ('build_windows.ps1', 'build_server_windows.ps1', 'packaging/linux/build_linux.sh', '.github/workflows/windows.yml'):
            script = text(name).replace('\\\r\n', ' ').replace('\\\n', ' ')
            for line in script.splitlines():
                if '-m pip install' not in line:
                    continue
                self.assertNotIn('--upgrade', line, name)
                words = line.split('-m pip install', 1)[1].split('#')[0].split()
                packages = [word.strip('\'"') for index, word in enumerate(words)
                            if not word.startswith('-') and words[index - 1] != '-r']
                for package in packages:
                    self.assertRegex(package, PIN, f'{name}: {package!r} is not pinned')

    def test_downloads_in_the_linux_build_are_checked_against_a_recorded_sha256(self):
        script = text('packaging/linux/build_linux.sh')
        self.assertIn('set -euo pipefail', script)
        downloads = [match.start() for match in re.finditer(r'curl ', script)]
        self.assertTrue(downloads)
        self.assertGreater(script.index('sha256sum -c -'), max(downloads))
        for variable in ('$tool_sum', '$runtime_sum'):
            self.assertIn(variable, script[max(downloads):script.index('sha256sum -c -')])
        self.assertNotIn('continuous', script.replace('(not "continuous")', ''))

    def test_actions_are_pinned_to_commits_and_a_published_release_is_never_overwritten(self):
        workflow = text('.github/workflows/windows.yml')
        uses = re.findall(r'uses: *(\S+)', workflow)
        self.assertTrue(uses)
        for action in uses:
            self.assertRegex(action, r'@[0-9a-f]{40}$', f'{action}: pin to a commit, not a tag')
        self.assertNotIn('--clobber', workflow)
        self.assertNotIn('gh release upload', workflow)
        self.assertNotIn('pull_request_target', workflow)


if __name__ == '__main__':
    unittest.main()
