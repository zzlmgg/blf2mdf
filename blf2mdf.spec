# -*- mode: python ; coding: utf-8 -*-
"""独立 PySide/QSS onefile 发布配置；不覆盖旧 blf2mdf.exe。"""

import re
import sys
from pathlib import Path

_CONDA_RUNTIME_NAMES = (
    'ffi-8.dll',
    'libcrypto-3-x64.dll',
    'libssl-3-x64.dll',
    'liblzma.dll',
    'libbz2.dll',
    'libexpat.dll',
    'sqlite3.dll',
)
_CONDA_BIN = Path(sys.executable).resolve().parent / 'Library' / 'bin'
_CONDA_RUNTIME_BINARIES = []
for _name in _CONDA_RUNTIME_NAMES:
    _source = _CONDA_BIN / _name
    if not _source.is_file():
        raise FileNotFoundError(f'缺少 conda Python 运行库：{_source}')
    _CONDA_RUNTIME_BINARIES.append((str(_source), '.'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_CONDA_RUNTIME_BINARIES,
    datas=[('assets/blf2mdf_icon.png', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick',
        'PySide6.QtQuick3D', 'PySide6.QtQuickWidgets',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineQuick', 'PySide6.QtWebChannel',
        'PySide6.QtWebSockets', 'PySide6.QtWebView',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtSql', 'PySide6.QtSvg', 'PySide6.QtSvgWidgets',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
        'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic', 'PySide6.Qt3DExtras', 'PySide6.Qt3DAnimation',
        'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtPositioning',
        'PySide6.QtLocation', 'PySide6.QtSensors',
        'PySide6.QtSerialPort', 'PySide6.QtSerialBus',
        'PySide6.QtTest', 'PySide6.QtDesigner', 'PySide6.QtHelp',
        'PySide6.QtUiTools', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
        'PySide6.QtXml', 'PySide6.QtXmlPatterns', 'PySide6.QtDBus',
        'PySide6.QtScxml', 'PySide6.QtTextToSpeech', 'PySide6.QtRemoteObjects',
        'PySide6.QtStateMachine', 'PySide6.QtConcurrent',
        'PySide6.QtCore5Compat', 'PySide6.QtHttpServer', 'PySide6.QtOpcUa',
        'PySide6.QtShaderTools',
    ],
    noarchive=False,
)

_UNUSED_QT_DLL = re.compile(
    r'/Qt6(Qml|QmlMeta|QmlModels|QmlWorkerScript|Quick|Network|Svg|Pdf'
    r'|OpenGL|VirtualKeyboard)\.dll$', re.I
)
_UNUSED_PLUGINS = re.compile(
    r'/plugins/(platforms/(qdirect2d|qminimal|qoffscreen)|generic/'
    r'|iconengines/|platforminputcontexts/)', re.I
)


def _keep_dest(dest: str) -> bool:
    dest = dest.replace('\\', '/')
    if '/translations/' in dest or dest.endswith('/translations'):
        return False
    if '/plugins/imageformats/' in dest:
        return False
    if _UNUSED_QT_DLL.search(dest):
        return False
    if dest.endswith('opengl32sw.dll'):
        return False
    if _UNUSED_PLUGINS.search(dest):
        return False
    if dest.endswith('numpy/_core/_multiarray_tests.pyd'):
        return False
    return True


a.binaries = [item for item in a.binaries if _keep_dest(item[0])]
a.datas = [item for item in a.datas if _keep_dest(item[0])]
a.binaries = [
    item
    for item in a.binaries
    if 'icu' not in item[0].replace('\\', '/').lower()
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BLF2MDF',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/blf2mdf_icon.ico',
)
