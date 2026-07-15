# -*- mode: python ; coding: utf-8 -*-
"""
青稞·lite - PyInstaller 打包配置
用法: pyinstaller 青稞lite/build.spec
"""
import os
import sys
import glob
from PyInstaller.utils.hooks import collect_submodules

# 项目路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))
CLI_LITE_DIR = os.path.join(BASE_DIR, 'CLI_lite')

# 确保 collect_submodules 能找到 CLI_lite 下的包
if CLI_LITE_DIR not in sys.path:
    sys.path.insert(0, CLI_LITE_DIR)

# 自动收集项目包的所有子模块（确保打包完整）
_core_subs = collect_submodules('core')
_dag_subs = collect_submodules('dag')
_dispatcher_subs = collect_submodules('dispatcher')
_web_subs = collect_submodules('web')
DESKTOP_DIR = os.path.join(BASE_DIR, '青稞lite')

# 动态查找 charset_normalizer 的 mypyc 编译扩展（位于 site-packages 根目录）
# 这些 .pyd 文件不在包目录内，PyInstaller 无法自动发现，需手动作为 binary 包含
_site_packages = os.path.join(sys.prefix, 'Lib', 'site-packages')
_extra_binaries = []
for _pyd in glob.glob(os.path.join(_site_packages, '*__mypyc*.pyd')):
    _extra_binaries.append((_pyd, '.'))
    print(f"[build.spec] 发现 mypyc 扩展: {os.path.basename(_pyd)}")

a = Analysis(
    [os.path.join(DESKTOP_DIR, 'main.py')],
    pathex=[
        CLI_LITE_DIR,
        DESKTOP_DIR,
    ],
    binaries=_extra_binaries,
    datas=[
        # Web 应用模板和静态文件
        (os.path.join(CLI_LITE_DIR, 'web', 'templates'), os.path.join('CLI_lite', 'web', 'templates')),
        (os.path.join(CLI_LITE_DIR, 'web', 'static'), os.path.join('CLI_lite', 'web', 'static')),
        # 配置文件
        (os.path.join(CLI_LITE_DIR, 'config'), os.path.join('CLI_lite', 'config')),
        # Python 源码目录（PYZ 可能丢失部分模块，以文件形式兜底）
        (os.path.join(CLI_LITE_DIR, 'core'), os.path.join('CLI_lite', 'core')),
        (os.path.join(CLI_LITE_DIR, 'dag'), os.path.join('CLI_lite', 'dag')),
        (os.path.join(CLI_LITE_DIR, 'dispatcher'), os.path.join('CLI_lite', 'dispatcher')),
        (os.path.join(CLI_LITE_DIR, 'web', 'routes'), os.path.join('CLI_lite', 'web', 'routes')),
    ],
    hiddenimports=[
        # Flask 相关
        'flask',
        'flask.blueprints',
        'jinja2',
        'markupsafe',
        'werkzeug',
        'werkzeug.serving',
        'werkzeug.debug',
        # YAML
        'yaml',
        # 核心模块
        'core',
        'core.engine',
        'core.llm_gateway',
        'core.context_manager',
        'core.agentic_loop',
        'core.preference_learner',
        'core.history_retriever',
        'core.config_guard',
        'core.logger',
        'core.agent',
        'core.agent.front_desk_agent',
        'core.tools',
        'core.builtin_browser',
        'core.reminder_scheduler',
        'core.skill_manager',
        # Web 路由
        'web.routes.pages',
        # DAG 模块
        'dag',
        'dag.dag_parser',
        'dag.dag_scheduler',
        'dag.schemas',
        # Dispatcher 模块
        'dispatcher',
        'dispatcher.task_executor',
        # Web 路由
        'web',
        'web.routes',
        'web.routes.api',
        'web.routes.events',
        # PyQt5
        'PyQt5',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'PyQt5.QtWebEngineWidgets',
        'PyQt5.QtWebEngineCore',
        'PyQt5.QtNetwork',
        'PyQt5.QtWebChannel',
        'PyQt5.QtWebEngine',
        # requests 及其完整依赖链
        'requests',
        'requests.adapters',
        'requests.auth',
        'requests.cookies',
        'requests.exceptions',
        'requests.hooks',
        'requests.models',
        'requests.sessions',
        'requests.structures',
        'requests.utils',
        'requests.packages',
        'chardet',
        'urllib3',
        'urllib3.connection',
        'urllib3.connectionpool',
        'urllib3.exceptions',
        'urllib3.poolmanager',
        'urllib3.response',
        'urllib3.util',
        'urllib3.util.retry',
        'urllib3.util.url',
        'certifi',
        'charset_normalizer',
        'charset_normalizer.api',
        'charset_normalizer.cd',
        'charset_normalizer.legacy',
        'charset_normalizer.models',
        'charset_normalizer.utils',
        'idna',
        'idna.core',
        'idna.idnadata',
        'idna.intranges',
        'idna.package_data',
        'idna.uts46data',
    ] + _core_subs + _dag_subs + _dispatcher_subs + _web_subs,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(DESKTOP_DIR, 'runtime_hook.py')],
    excludes=[
        # 排除不需要的大型模块
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'cv2',
        'torch',
        'tensorflow',
        # 排除开发文档
        'docs',
        'tests',
        'test_*',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='QingkeLite',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,  # 单文件模式不使用临时目录
    console=False,  # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(CLI_LITE_DIR, 'web', 'static', 'favicon.ico') if os.path.exists(
        os.path.join(CLI_LITE_DIR, 'web', 'static', 'favicon.ico')
    ) else None,
)
