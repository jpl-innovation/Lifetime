"""Windows version information for one PyInstaller program (code signing needs the product name and version).

    python tools/windows_version.py PROGRAM "DESCRIPTION" OUTPUT

writes a file for PyInstaller's --version-file: ProductName "Lifetime" for every program, the program's own
FileDescription, CompanyName "JPL Innovation", and FileVersion/ProductVersion from lifetime_reports.VERSION.
"""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PRODUCT = 'Lifetime'
COMPANY = 'JPL Innovation'


def version_info(program, description, version=None):
    if version is None:
        from lifetime_reports import VERSION as version
    numbers = [int(part) for part in re.findall(r'\d+', re.split(r'[-+]', version)[0])[:4]]
    numbers = tuple(numbers + [0] * (4 - len(numbers)))      # 1.3.0-rc1 -> (1, 3, 0, 0); the text keeps "-rc1"
    strings = [('CompanyName', COMPANY), ('FileDescription', description), ('FileVersion', version),
               ('InternalName', program), ('LegalCopyright', f'{COMPANY}. GNU General Public License v3'),
               ('OriginalFilename', program + '.exe'), ('ProductName', PRODUCT), ('ProductVersion', version)]
    table = ',\n'.join(f'        StringStruct({key!r}, {value!r})' for key, value in strings)
    return (f'VSVersionInfo(\n'
            f'  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,\n'
            f'                    fileType=0x1, subtype=0x0, date=(0, 0)),\n'
            f'  kids=[\n'
            f"    StringFileInfo([StringTable('040904B0', [\n{table}])]),\n"
            f"    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
            f'  ]\n'
            f')\n')


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit('usage: windows_version.py PROGRAM DESCRIPTION OUTPUT')
    Path(sys.argv[3]).parent.mkdir(parents=True, exist_ok=True)
    Path(sys.argv[3]).write_text(version_info(sys.argv[1], sys.argv[2]), encoding='utf-8')
