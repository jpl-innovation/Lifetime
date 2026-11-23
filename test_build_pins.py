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
        self.assertEqual(script.count('curl -'), 3)      # downloads: all inside the two functions below
        # Each function's downloads (the AppImage tools; rustup-init for the Rust accelerator) end in one check.
        for function, sums in (('package', ('$tool_sum', '$runtime_sum')), ('rust_accelerator', ('$init_sum',))):
            with self.subTest(function=function):
                start = script.index(f'\n{function}() {{')
                body = script[start:script.index('\n}\n', start)]
                downloads = [match.start() for match in re.finditer(r'curl -', body)]
                self.assertTrue(downloads)
                self.assertGreater(body.index('sha256sum -c -'), max(downloads))
                for variable in sums:
                    self.assertIn(variable, body[max(downloads):body.index('sha256sum -c -')])
        self.assertIn('--default-toolchain 1.91.0', script)
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


class WindowsVersionInfoTests(unittest.TestCase):
    """Code signing (SignPath) needs product name and version metadata in every Windows program."""

    def fields(self, text):
        found = {}
        def record(name):
            def build(*args, **kwargs):
                found.setdefault(name, []).append((args, kwargs))
                return name
            return build
        names = ('VSVersionInfo', 'FixedFileInfo', 'StringFileInfo', 'StringTable', 'StringStruct', 'VarFileInfo', 'VarStruct')
        eval(text, {'__builtins__': {}}, {name: record(name) for name in names})      # the file is our own text
        return found

    def test_every_program_is_product_lifetime_with_its_own_description_and_the_release_version(self):
        import sys
        sys.path.insert(0, str(PROJECT / 'tools'))
        try:
            import windows_version
        finally:
            sys.path.remove(str(PROJECT / 'tools'))
        from lifetime_reports import VERSION
        found = self.fields(windows_version.version_info('LifetimeServerRuntime', 'Lifetime Server', '1.3.0-rc1'))
        strings = dict(args for args, _ in found['StringStruct'])
        self.assertEqual(strings['ProductName'], 'Lifetime')
        self.assertEqual(strings['CompanyName'], 'JPL Innovation')
        self.assertEqual(strings['FileDescription'], 'Lifetime Server')
        self.assertEqual(strings['OriginalFilename'], 'LifetimeServerRuntime.exe')
        self.assertEqual((strings['FileVersion'], strings['ProductVersion']), ('1.3.0-rc1', '1.3.0-rc1'))
        fixed = found['FixedFileInfo'][0][1]
        self.assertEqual((fixed['filevers'], fixed['prodvers']), ((1, 3, 0, 0), (1, 3, 0, 0)))
        default = dict(args for args, _ in self.fields(windows_version.version_info('Lifetime', 'Lifetime'))['StringStruct'])
        self.assertEqual(default['ProductVersion'], VERSION)
        try:
            from PyInstaller.utils.win32.versioninfo import load_version_info_from_text_file
        except ImportError:
            return      # the build job checks it with PyInstaller itself
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'version.txt'
            path.write_text(windows_version.version_info('Lifetime', 'Lifetime'), encoding='utf-8')
            self.assertIn("StringStruct('ProductName', 'Lifetime')", str(load_version_info_from_text_file(str(path))))

    def test_both_windows_builds_give_every_program_its_version_file(self):
        for name in ('build_windows.ps1', 'build_server_windows.ps1'):
            builds = [line for line in text(name).splitlines() if '-m PyInstaller' in line]
            self.assertGreaterEqual(len(builds), 4, name)
            for line in builds:
                program = re.search(r'--name (\w+)', line).group(1)
                self.assertIn(f'--version-file (New-VersionFile {program} ', line)
            self.assertIn('windows_version.py', text(name))


if __name__ == '__main__':
    unittest.main()
