# -*- mode: python ; coding: utf-8 -*-
"""blf2mdf 打包配置：onefile 窗口程序，只收集实际导入的依赖。

关键事实（由导入表分析核实）：
- conda-forge PySide6 的 Qt6Core/Qt6Gui/Qt6Widgets.dll 只依赖彼此 + 系统库，
  但 collect_module 的链接链分析会把 pyside6.abi3.dll 引出的全部 Qt 模块 DLL
  （Qml/Quick/Network/Svg/Pdf/OpenGL/VirtualKeyboard）和 opengl32sw.dll
  （软件渲染 19.7MB）一并收集——本应用从未导入这些模块，全部删掉。
- Qt6Core.dll 静态导入 icuuc.dll，但 PySide6 pip wheel 不自带 ICU、conda 的
  ICU 73.1 与 Qt6Core 不兼容（PROC_NOT_FOUND 实测）——正确做法是不打包
  ICU，让 Windows 加载 System32 自带 ICU（Win10 1709+ / Win11 全系有，
  dev 环境也一直如此运行，行为一致）。省 33MB。
- 裁剪清单均为「包内无任何保留文件静态引用」项，删了安全。

inputs/（DBC 数据）与 outputs/ 不打包：发布时与 exe 同目录分发，
main_window._app_root() 在冻结环境下解析为 exe 所在目录。
"""
import re

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 本应用绝不使用的 Qt 模块（兜底；模块级收集本就拉不到它们）
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

# 未使用的 Qt 模块 DLL（collect_module 链接链误带，包内无保留文件引用）
_UNUSED_QT_DLL = re.compile(
    r'/Qt6(Qml|QmlMeta|QmlModels|QmlWorkerScript|Quick|Network|Svg|Pdf'
    r'|OpenGL|VirtualKeyboard)\.dll$', re.I
)
# 未使用插件：软件渲染/离屏/直接2D 平台、触摸输入、SVG 图标、虚拟键盘
_UNUSED_PLUGINS = re.compile(
    r'/plugins/(platforms/(qdirect2d|qminimal|qoffscreen)|generic/'
    r'|iconengines/|platforminputcontexts/)', re.I
)


def _keep_dest(dest: str) -> bool:
    """收集结果过滤：dest 为包内目标路径（正反斜杠统一）。True=保留。"""
    dest = dest.replace('\\', '/')
    if '/translations/' in dest or dest.endswith('/translations'):
        return False  # Qt 翻译全丢（未安装 QTranslator，qt_*.qm 纯冗余 ≈15MB）
    if '/plugins/imageformats/' in dest:
        return False  # 不加载磁盘图片，qjpeg/qgif/qsvg/qwebp 等全丢
    if _UNUSED_QT_DLL.search(dest):
        return False  # 未导入的 Qt 模块 DLL（≈21MB）
    if dest.endswith('opengl32sw.dll'):
        return False  # 软件 OpenGL 渲染器（19.7MB）；QWidgets+Fusion 不触 GL
    if _UNUSED_PLUGINS.search(dest):
        return False  # 多余平台/输入插件
    if dest.endswith('numpy/_core/_multiarray_tests.pyd'):
        return False  # numpy 自测专用二进制（无任何运行时导入）
    return True


a.binaries = [b for b in a.binaries if _keep_dest(b[0])]
a.datas = [d for d in a.datas if _keep_dest(d[0])]


def _drop_root_icu(bins):
    """bindepend 可能从 PATH（base 环境的 Library/bin）解析到 ICU 并打进根目录
    ——与 Qt6Core 不兼容且无需打包，全部丢弃（System32 自带 ICU）。"""
    return [b for b in bins if 'icu' not in b[0].replace('\\', '/').lower()]


a.binaries = _drop_root_icu(a.binaries)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='blf2mdf',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # 环境无 UPX；onefile 内置 zlib 已压缩。装 UPX 可再省 5~15%，有杀软误报风险
    console=False,  # 窗口程序（不弹控制台）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
