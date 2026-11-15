"""Exercise imports from only the files copied by each source installer, and check that every module a build or
installer needs is shipped, including modules imported only inside functions (which a smoke import never reaches)."""
import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).parent


def import_graph():
    """{module: (imported when the module loads, imported only inside functions)} for every lifetime_*.py."""
    modules = {path.stem: path for path in PROJECT.glob('lifetime_*.py')}
    graph = {}
    for name, path in modules.items():
        eager, lazy = set(), set()

        def visit(node, nested):
            for child in ast.iter_child_nodes(node):
                found = []
                if isinstance(child, ast.Import):
                    found = [alias.name.split('.')[0] for alias in child.names]
                elif isinstance(child, ast.ImportFrom) and child.module and not child.level:
                    found = [child.module.split('.')[0]]
                (lazy if nested else eager).update(item for item in found if item in modules)
                visit(child, nested or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)))

        visit(ast.parse(path.read_text(encoding='utf-8')), False)
        graph[name] = (eager, lazy)
    return graph


def closure(graph, starts, *, lazy=True):
    seen, pending = set(), list(starts)
    while pending:
        name = pending.pop()
        if name not in seen:
            seen.add(name)
            eager, inner = graph[name]
            pending.extend(eager | (inner if lazy else set()))
    return seen


def pyinstaller_command(script, name):
    """(entry module, hidden imports, whole command text) of one PyInstaller call, with splatted @variables expanded."""
    lines = script.splitlines()
    line = next(text for text in lines if 'PyInstaller' in text and f'--name {name} ' in text)
    text = line
    for variable in re.findall(r'@(\w+)', line):
        start = next(index for index, value in enumerate(lines) if re.match(rf'\s*\${variable}\s*=', value))
        end = start
        while lines[end].rstrip().endswith(','):
            end += 1
        text += '\n' + '\n'.join(lines[start:end + 1])
    hidden = set(re.findall(r"--hidden-import'?,?\s+'?([\w.]+)", text))
    hidden |= {item for block in re.findall(r"@\(([^)]*)\)\s*\|\s*ForEach-Object\s*\{\s*'--hidden-import'", text)
               for item in re.findall(r"'([\w.]+)'", block)}
    entry = re.findall(r'(\w+)\.py\b', line)[-1]
    return entry, hidden, text


class SourceInstallationTests(unittest.TestCase):
    def test_source_install_lists_include_modules_imported_inside_functions(self):
        graph = import_graph()
        for installer in ('install_linux.sh', 'install_windows_common.ps1'):
            with self.subTest(installer=installer):
                listed = {name[:-3] for name in re.findall(r'lifetime_[a-z0-9_]+\.py', (PROJECT/installer).read_text(encoding='utf-8'))}
                self.assertEqual(closure(graph, listed) - listed, set())

    def test_server_builds_bundle_every_module_imported_inside_functions(self):
        """PyInstaller follows imports inside functions too, but the server builds name them explicitly so that a
        lazily imported module can never be left out; this fails when one is added to the code but not to the build."""
        graph = import_graph()
        script = (PROJECT/'build_server_windows.ps1').read_text(encoding='utf-8')
        for name in ('LifetimeServerRuntime', 'LifetimeServerDashboard', 'LifetimeServerService'):
            with self.subTest(build=name):
                entry, hidden, text = pyinstaller_command(script, name)
                self.assertEqual({item for item in hidden if item.startswith('lifetime_')} - set(graph), set(), 'unknown module')
                bundled = closure(graph, {entry} | (hidden & set(graph)), lazy=False)
                missing = {target for module in bundled for target in graph[module][1]} - bundled
                self.assertEqual(missing, set(), f'{name}: add --hidden-import for these')
                if name != 'LifetimeServerDashboard':
                    # Windows Python has no time zone database: zoneinfo loads it from tzdata through importlib.
                    self.assertIn('--collect-all tzdata', text)
                    self.assertIn('--exclude-module PyQt6', text)

    def test_server_packages_copy_every_module(self):
        for path, pattern in (('build_server_windows.ps1', "-Filter 'lifetime_*.py'"),
                              ('install_server_windows.ps1', "-Filter 'lifetime_*.py'"),
                              ('build_windows.ps1', 'Copy-Item lifetime_*.py package'),
                              ('.github/workflows/windows.yml', 'cp lifetime_*.py ')):
            with self.subTest(path=path):
                self.assertIn(pattern, (PROJECT/path).read_text(encoding='utf-8'))


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
