"""
CLI_lite 桌面应用 - 基于 PyQt5 + QWebEngineView
嵌入 Flask Web 服务器，提供独立的桌面体验
"""
import sys
import os
import time
import signal
import socket
import threading
import logging
from datetime import date

# ============================================================
# 版本信息
# ============================================================
APP_VERSION = "v1.9.5"

# ============================================================
# 路径处理：兼容 PyInstaller 打包和开发环境
# ============================================================
if getattr(sys, 'frozen', False):
    # PyInstaller 单文件模式：资源文件在临时目录中
    _BUNDLE_DIR = sys._MEIPASS
    # exe 所在目录（用于存放用户可修改的配置和数据）
    _APP_DIR = os.path.dirname(sys.executable)
    # CLI_lite 资源被打包到临时目录
    _CLI_LITE_ROOT = os.path.join(_BUNDLE_DIR, 'CLI_lite')
else:
    # 开发环境：青稞lite/ 和 CLI_lite/ 是同级目录
    _BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    _APP_DIR = os.path.dirname(_BUNDLE_DIR)
    _CLI_LITE_ROOT = os.path.join(_APP_DIR, 'CLI_lite')

# 确保 CLI_lite 在搜索路径中（用于导入模块）
if _CLI_LITE_ROOT not in sys.path:
    sys.path.insert(0, _CLI_LITE_ROOT)

# 切换工作目录：单文件模式用exe目录（可读写），开发环境用CLI_lite目录
if getattr(sys, 'frozen', False):
    os.chdir(_APP_DIR)  # exe所在目录，配置和数据在此
else:
    os.chdir(_CLI_LITE_ROOT)

# ============================================================
# PyQt5 导入（环境检测和主窗口共用）
# ============================================================
from PyQt5.QtCore import Qt, QUrl, QTimer, QSize, pyqtSignal
from PyQt5.QtGui import QIcon, QFont
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QWidget,
    QLabel, QProgressBar, QSplashScreen, QMessageBox,
    QDialog, QFrame, QTextEdit, QPushButton, QHBoxLayout, QSizePolicy,
    QLineEdit
)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings, QWebEngineProfile, QWebEnginePage
from PyQt5.QtWebChannel import QWebChannel

# 网络请求拦截器（PyQt5 >= 5.12 在 QtWebEngineCore 中）
try:
    from PyQt5.QtWebEngineCore import QWebEngineUrlRequestInterceptor
except ImportError:
    QWebEngineUrlRequestInterceptor = None

# ============================================================
# 环境检测与自动安装向导
# ============================================================
class EnvSetupDialog(QDialog):
    """环境检测与自动安装对话框"""
    
    # 信号：跨线程更新UI
    _progress_sig = pyqtSignal(str, int, str)  # component, percent, message
    _log_sig = pyqtSignal(str)                  # message
    _install_done_sig = pyqtSignal(str, bool)   # component, success
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"青稞·lite {APP_VERSION} - 环境配置")
        self.setMinimumSize(600, 500)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        self._components_status = {}  # {name: {"installed": bool, "version": str}}
        self._installing = False
        self._installer = None
        
        self._init_ui()
        self._connect_signals()
        
        # 启动环境检测
        QTimer.singleShot(300, self._start_check)
    
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)
        
        # 标题
        title = QLabel("环境检测与配置")
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        title.setStyleSheet("color: #4fc3f7;")
        layout.addWidget(title)
        
        desc = QLabel("青稞·lite 需要以下运行环境，缺失的组件将自动安装：")
        desc.setStyleSheet("color: #b0b0b0; font-size: 12px;")
        layout.addWidget(desc)
        
        # 组件状态区域
        self._status_frame = QFrame()
        self._status_frame.setStyleSheet("QFrame { border: 1px solid #3a3a3a; border-radius: 8px; padding: 12px; }")
        status_layout = QVBoxLayout(self._status_frame)
        status_layout.setSpacing(8)
        
        # Python状态
        self._python_label = QLabel("Python: 检测中...")
        self._python_label.setFont(QFont("Microsoft YaHei", 11))
        self._python_progress = QProgressBar()
        self._python_progress.setRange(0, 100)
        self._python_progress.setValue(0)
        self._python_progress.setFixedHeight(6)
        self._python_progress.setStyleSheet("""
            QProgressBar { background: #2d2d2d; border: none; border-radius: 3px; }
            QProgressBar::chunk { background: #4fc3f7; border-radius: 3px; }
        """)
        status_layout.addWidget(self._python_label)
        status_layout.addWidget(self._python_progress)
        
        # Node.js状态
        self._node_label = QLabel("Node.js: 检测中...")
        self._node_label.setFont(QFont("Microsoft YaHei", 11))
        self._node_progress = QProgressBar()
        self._node_progress.setRange(0, 100)
        self._node_progress.setValue(0)
        self._node_progress.setFixedHeight(6)
        self._node_progress.setStyleSheet("""
            QProgressBar { background: #2d2d2d; border: none; border-radius: 3px; }
            QProgressBar::chunk { background: #66bb6a; border-radius: 3px; }
        """)
        status_layout.addWidget(self._node_label)
        status_layout.addWidget(self._node_progress)
        
        layout.addWidget(self._status_frame)
        
        # 安装日志
        log_label = QLabel("安装日志：")
        log_label.setStyleSheet("color: #b0b0b0; font-size: 11px;")
        layout.addWidget(log_label)
        
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setFont(QFont("Consolas", 9))
        self._log_text.setStyleSheet("""
            QTextEdit { background: #1a1a1a; color: #d0d0d0; border: 1px solid #3a3a3a; 
                        border-radius: 6px; padding: 8px; }
        """)
        layout.addWidget(self._log_text, 1)
        
        # 按钮区域
        btn_layout = QHBoxLayout()
        
        self._skip_btn = QPushButton("跳过安装")
        self._skip_btn.setStyleSheet("""
            QPushButton { background: #3a3a3a; color: #b0b0b0; border: none; 
                         padding: 8px 20px; border-radius: 6px; font-size: 12px; }
            QPushButton:hover { background: #4a4a4a; }
        """)
        self._skip_btn.clicked.connect(self._on_skip)
        
        self._install_btn = QPushButton("开始安装")
        self._install_btn.setStyleSheet("""
            QPushButton { background: #4fc3f7; color: #1e1e1e; border: none; 
                         padding: 8px 20px; border-radius: 6px; font-size: 12px; font-weight: bold; }
            QPushButton:hover { background: #81d4fa; }
            QPushButton:disabled { background: #3a3a3a; color: #666; }
        """)
        self._install_btn.clicked.connect(self._on_install)
        self._install_btn.setEnabled(False)
        
        self._continue_btn = QPushButton("启动青稞")
        self._continue_btn.setStyleSheet("""
            QPushButton { background: #66bb6a; color: #1e1e1e; border: none; 
                         padding: 8px 20px; border-radius: 6px; font-size: 12px; font-weight: bold; }
            QPushButton:hover { background: #81c784; }
            QPushButton:disabled { background: #3a3a3a; color: #666; }
        """)
        self._continue_btn.clicked.connect(self.accept)
        self._continue_btn.setEnabled(False)
        
        btn_layout.addWidget(self._skip_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self._install_btn)
        btn_layout.addWidget(self._continue_btn)
        
        layout.addLayout(btn_layout)
    
    def _connect_signals(self):
        self._progress_sig.connect(self._on_progress)
        self._log_sig.connect(self._on_log)
        self._install_done_sig.connect(self._on_install_done)
    
    def _start_check(self):
        """开始环境检测"""
        self._log("开始检测系统环境...")
        thread = threading.Thread(target=self._check_env_thread, daemon=True)
        thread.start()
    
    def _check_env_thread(self):
        """后台线程：检测环境"""
        from env_setup import EnvChecker, COMPONENTS, EnvInstaller
        checker = EnvChecker()
        
        # 检测每个组件
        for name in COMPONENTS:
            comp = COMPONENTS[name]
            result = checker.check_component(name)
            self._components_status[name] = result
            
            if result["installed"]:
                self._progress_sig.emit(name, 100, f"已安装 v{result['version']}")
                self._log_sig.emit(f"✓ {comp.display_name} {result['version']} 已安装")
            else:
                self._progress_sig.emit(name, 0, "未安装")
                self._log_sig.emit(f"✗ {comp.display_name} 未安装")
        
        # 获取安装包信息（离线优先）
        self._log_sig.emit("\n=== 安装包状态 ===")
        installer = EnvInstaller()
        install_info = installer.get_install_info()
        
        for name, info in install_info.items():
            if info["source"] == "local":
                self._log_sig.emit(f"✓ {name}: {info['desc']}")
            else:
                self._log_sig.emit(f"⚠ {name}: {info['desc']}")
        
        installer.cleanup()
        
        # 检测完成
        missing = [name for name, r in self._components_status.items() if not r["installed"]]
        if missing:
            local_available = [n for n in missing if n in install_info and install_info[n]["source"] == "local"]
            if local_available:
                self._log_sig.emit(f"\n需要安装：{', '.join(COMPONENTS[n].display_name for n in missing)}")
                self._log_sig.emit(f"本地安装包可用：{', '.join(COMPONENTS[n].display_name for n in local_available)}")
            else:
                self._log_sig.emit(f"\n需要安装：{', '.join(COMPONENTS[n].display_name for n in missing)}")
                self._log_sig.emit("所有安装包需要联网下载")
            self._install_done_sig.emit("_check", False)
        else:
            self._log_sig.emit("\n所有环境组件已就绪！")
            self._install_done_sig.emit("_check", True)
    
    def _on_install(self):
        """开始安装"""
        if self._installing:
            return
        
        self._installing = True
        self._install_btn.setEnabled(False)
        self._skip_btn.setEnabled(False)
        self._install_btn.setText("安装中...")
        
        missing = [name for name, r in self._components_status.items() if not r["installed"]]
        
        thread = threading.Thread(target=self._install_thread, args=(missing,), daemon=True)
        thread.start()
    
    def _install_thread(self, components):
        """后台线程：安装组件"""
        from env_setup import EnvInstaller
        
        self._installer = EnvInstaller(
            progress_callback=lambda c, p, m: self._progress_sig.emit(c, p, m),
            log_callback=lambda m: self._log_sig.emit(m)
        )
        
        all_success = True
        for name in components:
            self._log_sig.emit(f"\n{'='*40}")
            self._log_sig.emit(f"开始安装 {name}...")
            success = self._installer.install_component(name)
            if success:
                self._install_done_sig.emit(name, True)
            else:
                self._install_done_sig.emit(name, False)
                all_success = False
        
        self._installer.cleanup()
        self._install_done_sig.emit("_all", all_success)
    
    def _on_skip(self):
        """跳过安装"""
        self._log("用户跳过安装，部分功能可能不可用")
        self.accept()
    
    def _on_progress(self, component, percent, message):
        """更新进度条"""
        if component == "python":
            self._python_progress.setValue(percent)
            self._python_label.setText(f"Python: {message}")
        elif component == "node":
            self._node_progress.setValue(percent)
            self._node_label.setText(f"Node.js: {message}")
    
    def _on_log(self, message):
        """添加日志"""
        self._log_text.append(message)
        # 自动滚动到底部
        scrollbar = self._log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def _on_install_done(self, component, success):
        """安装完成回调"""
        if component == "_check":
            # 环境检测完成
            if success:
                self._continue_btn.setEnabled(True)
                self._install_btn.setText("无需安装")
            else:
                self._install_btn.setEnabled(True)
                self._install_btn.setText("开始安装")
        elif component == "_all":
            # 全部安装完成
            self._installing = False
            self._continue_btn.setEnabled(True)
            if success:
                self._install_btn.setText("安装完成")
                self._log("\n所有组件安装完成！可以启动青稞了。")
            else:
                self._install_btn.setText("部分失败")
                self._log("\n部分组件安装失败，可以跳过后手动安装。")
        elif component in self._components_status:
            # 单个组件安装完成
            self._components_status[component]["installed"] = success
            if success:
                self._progress_sig.emit(component, 100, "安装成功")
    
    def _log(self, message):
        """线程安全的日志添加"""
        self._log_sig.emit(message)


# ============================================================
# Flask 服务器（后台线程）
# ============================================================
_server_thread = None
_flask_app = None
_server_port = 2253
_server_error = ""  # 后台线程启动失败时的详细错误


def _find_free_port(start=2253, end=2353):
    """查找可用端口，优先使用配置文件中的端口"""
    import socket
    # 尝试从配置文件读取端口
    config_path = os.path.join(_APP_DIR, 'config', 'settings.yaml') if getattr(sys, 'frozen', False) \
        else os.path.join(_CLI_LITE_ROOT, 'config', 'settings.yaml')
    try:
        import yaml
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        configured_port = config.get('flask', {}).get('port', start)
        if isinstance(configured_port, int) and 1024 <= configured_port <= 65535:
            start = configured_port
            end = configured_port + 100
    except Exception:
        pass

    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    return start


def _start_flask_server():
    """在后台线程启动 Flask 服务器，逐阶段捕获异常"""
    global _flask_app, _server_port, _server_error
    import traceback

    # 打包模式下确保 CLI_lite 可被导入（app.py 中 from core.engine ... 依赖此路径）
    if getattr(sys, 'frozen', False):
        _cli_root = os.path.join(sys._MEIPASS, 'CLI_lite')
        if _cli_root not in sys.path:
            sys.path.insert(0, _cli_root)

    # 阶段1: 导入核心引擎
    try:
        from app import create_app
    except Exception as e:
        _server_error = f"[初始化阶段] 导入核心模块失败:\n{traceback.format_exc()}"
        return

    # 阶段2: 创建Flask应用（加载配置、初始化各模块）
    try:
        _flask_app = create_app()
    except Exception as e:
        _server_error = f"[应用创建阶段] 创建Flask应用失败:\n{traceback.format_exc()}"
        return

    # 阶段3: 寻找可用端口
    try:
        _server_port = _find_free_port()
    except Exception as e:
        _server_error = f"[端口分配阶段] 寻找可用端口失败:\n{traceback.format_exc()}"
        return

    # 阶段4: 启动Flask服务
    try:
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)

        _flask_app.run(
            host='127.0.0.1',
            port=_server_port,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    except Exception as e:
        _server_error = f"[服务启动阶段] Flask服务监听端口 {_server_port} 失败:\n{traceback.format_exc()}"


def _wait_for_server(timeout=15):
    """等待 Flask 服务器就绪"""
    import urllib.request
    url = f"http://127.0.0.1:{_server_port}/"
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


# ============================================================
# 启动预检
# ============================================================

def _run_startup_checks():
    """执行启动预检，返回 (can_start: bool, error_detail: str)"""
    issues = []

    # --- 1. 配置文件检查 ---
    config_path = os.path.join(_APP_DIR, 'config', 'settings.yaml') if getattr(sys, 'frozen', False) \
        else os.path.join(_CLI_LITE_ROOT, 'config', 'settings.yaml')

    if not os.path.exists(config_path):
        issues.append(f"[配置] 配置文件不存在: {config_path}")
    else:
        try:
            import yaml
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
        except Exception as e:
            issues.append(f"[配置] YAML语法错误: {e}")
            config = None

        if config and isinstance(config, dict):
            # 必要配置段（llm 不检查，用户启动后在界面中配置）
            for section in ['context', 'cli', 'flask']:
                if section not in config:
                    issues.append(f"[配置] 缺少必要配置段: {section}")

    # --- 2. 端口检查 ---
    configured_port = 2253
    try:
        import yaml
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        configured_port = config.get('flask', {}).get('port', 2253)
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.bind(('127.0.0.1', configured_port))
    except OSError:
        # 端口被占用，但桌面版会自动寻找备用端口，仅作警告
        pass  # 不阻断启动

    # --- 3. 依赖检查 ---
    required_deps = {'flask': 'flask', 'yaml': 'pyyaml', 'requests': 'requests'}
    for module, package in required_deps.items():
        try:
            __import__(module)
        except Exception as e:
            issues.append(f"[依赖] {package} 导入失败: {type(e).__name__}: {e}")

    try:
        __import__('PyQt5')
    except ImportError:
        issues.append(f"[依赖] 缺少桌面框架: PyQt5  →  pip install PyQt5 PyQtWebEngine")

    # --- 汇总 ---
    if issues:
        detail = "\n".join(f"• {i}" for i in issues)
        return False, detail
    return True, ""


# ============================================================
# 内置浏览器窗口
# ============================================================

# 网络请求拦截器（记录所有请求）
if QWebEngineUrlRequestInterceptor:
    class _NetworkInterceptor(QWebEngineUrlRequestInterceptor):
        """拦截并记录浏览器发出的每一个网络请求"""

        _RESOURCE_TYPES = {
            0: 'main_frame', 1: 'sub_frame', 2: 'stylesheet', 3: 'script',
            4: 'image', 5: 'font', 6: 'object', 7: 'media',
            8: 'websocket', 9: 'other',
        }

        def __init__(self):
            super().__init__()
            # 缓存函数引用，避免每次请求都import
            from core.builtin_browser import add_network_log
            self._add_log = add_network_log

        def interceptRequest(self, info):
            try:
                url = info.requestUrl().toString()
                method = bytes(info.requestMethod()).decode('utf-8', errors='replace')
                res_type = self._RESOURCE_TYPES.get(info.resourceType(), 'unknown')
                self._add_log({
                    'direction': 'request',
                    'url': url,
                    'method': method,
                    'resource_type': res_type,
                })
            except Exception:
                pass  # 拦截器中绝不能抛异常，否则会影响页面加载


class BrowserWindow(QMainWindow):
    """内置浏览器窗口 - 基于 QWebEngineView（Chromium内核）"""

    # 页面JS注入脚本：捕获 fetch / XMLHttpRequest 响应
    _JS_NETWORK_MONITOR = """
    (function() {
        if (window.__qkeMon) return;
        window.__qkeMon = true;
        window.__qkeLogs = [];

        // ---- fetch 拦截 ----
        var _f = window.fetch;
        window.fetch = function() {
            var url = typeof arguments[0] === 'string' ? arguments[0]
                    : (arguments[0] && arguments[0].url || '');
            var m   = (arguments[1] && arguments[1].method) || 'GET';
            var t0  = Date.now();
            return _f.apply(this, arguments).then(function(r) {
                window.__qkeLogs.push({
                    d:'resp', url:url, method:m, status:r.status,
                    ct: r.headers.get('content-type')||'',
                    ms: Date.now()-t0
                });
                return r;
            }).catch(function(e) {
                window.__qkeLogs.push({
                    d:'err', url:url, method:m, error:e.message||String(e),
                    ms: Date.now()-t0
                });
                throw e;
            });
        };

        // ---- XMLHttpRequest 拦截 ----
        var _o = XMLHttpRequest.prototype.open;
        var _s = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.open = function(m,u) {
            this._m=m; this._u=u; this._t=Date.now();
            return _o.apply(this,arguments);
        };
        XMLHttpRequest.prototype.send = function() {
            var self=this;
            if (!self.__qkeHooked) {
                self.__qkeHooked = true;
                self.addEventListener('load',function(){
                    window.__qkeLogs.push({
                        d:'resp', url:self._u, method:self._m,
                        status:self.status, ct:self.getResponseHeader('content-type')||'',
                        ms:Date.now()-self._t
                    });
                });
                self.addEventListener('error',function(){
                    window.__qkeLogs.push({
                        d:'err', url:self._u, method:self._m,
                        error:'Network Error', ms:Date.now()-self._t
                    });
                });
            }
            return _s.apply(this,arguments);
        };
    })();
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"青稞·lite {APP_VERSION} - 内置浏览器")
        self.setMinimumSize(1024, 700)
        self.resize(1280, 800)

        # 图标
        icon_path = os.path.join(_BUNDLE_DIR, 'web', 'static', 'favicon.ico')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # ---------- 地址栏 ----------
        toolbar = QWidget()
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 4, 8, 4)

        self._url_bar = QLineEdit()
        self._url_bar.setPlaceholderText("输入网址并按回车...")
        self._url_bar.returnPressed.connect(self._on_go)
        self._url_bar.setStyleSheet(
            "QLineEdit{background:#2d2d2d;color:#e0e0e0;border:1px solid #3a3a3a;"
            "border-radius:4px;padding:6px 10px;font-size:13px;}"
        )

        go_btn = QPushButton("前往")
        go_btn.setFixedWidth(60)
        go_btn.clicked.connect(self._on_go)
        go_btn.setStyleSheet(
            "QPushButton{background:#4fc3f7;color:#1e1e1e;border:none;"
            "padding:6px 12px;border-radius:4px;font-weight:bold;}"
            "QPushButton:hover{background:#81d4fa;}"
        )

        clear_btn = QPushButton("清除日志")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self._on_clear_logs)
        clear_btn.setStyleSheet(
            "QPushButton{background:#3a3a3a;color:#b0b0b0;border:none;"
            "padding:6px 10px;border-radius:4px;}"
            "QPushButton:hover{background:#4a4a4a;}"
        )

        tb_layout.addWidget(self._url_bar, 1)
        tb_layout.addWidget(go_btn)
        tb_layout.addWidget(clear_btn)

        # ---------- WebEngine 视图 ----------
        self._web_view = QWebEngineView()

        # 独立Profile（不与主窗口共享Cookie/缓存）
        self._profile = QWebEngineProfile()

        # 设置网络拦截器
        self._interceptor = None
        if QWebEngineUrlRequestInterceptor:
            try:
                self._interceptor = _NetworkInterceptor()
                self._profile.setUrlRequestInterceptor(self._interceptor)
            except Exception as e:
                logging.warning(f"设置网络拦截器失败: {e}")

        # 模拟标准Chrome User-Agent
        self._profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/114.0.0.0 Safari/537.36"
        )

        # 使用自定义Page（绑定到自定义Profile）
        page = QWebEnginePage(self._profile, self._web_view)
        self._web_view.setPage(page)

        # 配置WebEngine
        ws = self._web_view.settings()
        ws.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        ws.setAttribute(QWebEngineSettings.LocalStorageEnabled, True)
        ws.setAttribute(QWebEngineSettings.PluginsEnabled, True)
        ws.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)

        # 信号连接
        self._web_view.loadFinished.connect(self._on_load_finished)
        self._web_view.urlChanged.connect(lambda u: self._url_bar.setText(u.toString()))

        # ---------- 布局 ----------
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(toolbar)
        layout.addWidget(self._web_view, 1)
        self.setCentralWidget(central)

        # ---------- 定时器 ----------
        # 命令处理（每100ms检查一次工具命令）
        self._cmd_timer = QTimer(self)
        self._cmd_timer.timeout.connect(self._process_commands)
        self._cmd_timer.start(100)

        # JS网络日志提取（每2秒从页面提取一次）
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._extract_js_logs)
        self._log_timer.start(2000)

        # 标记浏览器已打开
        from core.builtin_browser import set_browser_open
        set_browser_open(True)
        logging.info("内置浏览器窗口已创建")

    # ---- 地址栏操作 ----

    def _on_go(self):
        url = self._url_bar.text().strip()
        if url:
            if not url.startswith(('http://', 'https://', 'file://', 'data:')):
                url = 'https://' + url
            self._web_view.load(QUrl(url))

    def _on_clear_logs(self):
        from core.builtin_browser import clear_network_logs
        clear_network_logs()

    # ---- 页面加载回调 ----

    def _on_load_finished(self, ok):
        if ok:
            self._inject_network_monitor()

    def _inject_network_monitor(self):
        """页面加载完成后注入fetch/XHR拦截脚本"""
        self._web_view.page().runJavaScript(self._JS_NETWORK_MONITOR)

    # ---- JS日志提取 ----

    def _extract_js_logs(self):
        """从页面JS世界中提取网络响应日志"""
        self._web_view.page().runJavaScript(
            "(function(){var l=window.__qkeLogs||[];window.__qkeLogs=[];return JSON.stringify(l);})()",
            self._on_js_logs
        )

    def _on_js_logs(self, json_str):
        if not json_str:
            return
        try:
            import json
            logs = json.loads(json_str)
            if logs:
                from core.builtin_browser import add_network_log
                for entry in logs:
                    add_network_log({
                        'direction': entry.get('d', 'resp'),
                        'url': entry.get('url', ''),
                        'method': entry.get('method', ''),
                        'status': entry.get('status'),
                        'content_type': entry.get('ct', ''),
                        'error': entry.get('error', ''),
                        'duration_ms': entry.get('ms', 0),
                    })
        except Exception:
            pass

    # ---- 命令队列处理 ----

    def _process_commands(self):
        """处理来自工具线程的命令"""
        from core.builtin_browser import get_command, store_result

        for _ in range(10):  # 每轮最多处理10条命令
            cmd = get_command()
            if not cmd:
                break

            cmd_id = cmd.get('id')
            cmd_type = cmd.get('type')

            try:
                if cmd_type == 'navigate':
                    url = cmd.get('url', 'about:blank')
                    self._web_view.load(QUrl(url))
                    store_result(cmd_id, {'success': True, 'url': url})

                elif cmd_type == 'get_content':
                    def _html_cb(html, cid=cmd_id):
                        store_result(cid, {'success': True, 'html': html or ''})
                    self._web_view.page().toHtml(_html_cb)

                elif cmd_type == 'get_url':
                    store_result(cmd_id, {
                        'success': True,
                        'url': self._web_view.url().toString()
                    })

                elif cmd_type == 'screenshot':
                    from PyQt5.QtCore import QBuffer, QIODevice
                    import base64 as _b64
                    pixmap = self._web_view.grab()
                    buf = QBuffer()
                    buf.open(QIODevice.WriteOnly)
                    pixmap.save(buf, "PNG")
                    b64 = _b64.b64encode(buf.data().data()).decode('ascii')
                    store_result(cmd_id, {'success': True, 'screenshot': b64})

                elif cmd_type == 'execute_js':
                    js_code = cmd.get('code', '')

                    def _js_cb(result, cid=cmd_id):
                        store_result(cid, {'success': True, 'result': result})
                    self._web_view.page().runJavaScript(js_code, _js_cb)

                elif cmd_type == 'close':
                    store_result(cmd_id, {'success': True})
                    QTimer.singleShot(0, self.close)
                    return  # 停止处理后续命令

                else:
                    store_result(cmd_id, {'error': f'未知命令: {cmd_type}', 'success': False})

            except Exception as e:
                store_result(cmd_id, {'error': str(e), 'success': False})

    # ---- 窗口事件 ----

    def closeEvent(self, event):
        from core.builtin_browser import set_browser_open
        self._cmd_timer.stop()
        self._log_timer.stop()
        self._web_view.setHtml("")
        # 释放Chromium资源
        self._web_view.setPage(None)
        self._web_view.deleteLater()
        self._profile.deleteLater()
        set_browser_open(False)
        logging.info("内置浏览器窗口已关闭")
        event.accept()


# ============================================================
# PyQt5 主窗口
# ============================================================


class MainWindow(QMainWindow):
    """青稞·lite 桌面主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"青稞·lite {APP_VERSION} - AI 助手")
        self.setMinimumSize(QSize(1100, 750))
        self.resize(1280, 850)

        # 设置窗口图标（如果存在）
        icon_path = os.path.join(_BUNDLE_DIR, 'web', 'static', 'favicon.ico')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # 中心部件
        self._central = QWidget()
        self.setCentralWidget(self._central)
        self._layout = QVBoxLayout(self._central)
        self._layout.setContentsMargins(0, 0, 0, 0)

        # 加载提示
        self._loading_label = QLabel("正在启动 青稞·lite 服务...")
        self._loading_label.setAlignment(Qt.AlignCenter)
        self._loading_label.setFont(QFont("Microsoft YaHei", 14))
        self._loading_label.setStyleSheet("color: #666; background: #1e1e1e;")

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # 不确定进度
        self._progress.setMaximumHeight(3)
        self._progress.setStyleSheet("""
            QProgressBar { border: none; background: #1e1e1e; }
            QProgressBar::chunk { background: #4285f4; }
        """)

        self._layout.addWidget(self._loading_label)
        self._layout.addWidget(self._progress)

        # WebEngine 视图（延迟创建）
        self._web_view = None

        # 内置浏览器窗口引用
        self._browser_window = None

        # 启动 Flask 服务器
        self._start_server()

    def _start_server(self):
        """执行启动预检，通过后启动后台 Flask 服务器"""
        # 先执行预检
        ok, detail = _run_startup_checks()
        if not ok:
            self._loading_label.setText("启动预检未通过，请修复以下问题")
            QMessageBox.critical(self, "启动预检失败",
                                 "青稞·Lite 启动检查未通过：\n\n"
                                 f"{detail}\n\n"
                                 "请修复后重新启动。")
            sys.exit(1)

        # 预检通过，启动 Flask
        global _server_thread
        _server_thread = threading.Thread(target=_start_flask_server, daemon=True)
        _server_thread.start()

        # 定时检查服务器是否就绪
        self._check_timer = QTimer(self)
        self._check_timer.timeout.connect(self._on_check_server)
        self._check_timer.start(500)
        self._wait_start = time.time()

    def _on_check_server(self):
        """检查服务器是否启动完成"""
        global _server_error

        # 先检查后台线程是否已报错
        if _server_error:
            self._check_timer.stop()
            self._loading_label.setText("服务启动失败")
            # 截取关键信息（避免弹窗过长）
            error_text = _server_error[:2000]
            QMessageBox.critical(self, "启动失败",
                                 f"青稞·Lite 服务启动失败：\n\n{error_text}")
            return

        if _wait_for_server(timeout=2):
            self._check_timer.stop()
            self._init_web_view()
        elif time.time() - self._wait_start > 20:
            self._check_timer.stop()
            # 超时也检查一次是否有错误
            if _server_error:
                self._loading_label.setText("服务启动失败")
                error_text = _server_error[:2000]
                QMessageBox.critical(self, "启动失败",
                                     f"青稞·Lite 服务启动失败：\n\n{error_text}")
            else:
                self._loading_label.setText("服务启动超时")
                QMessageBox.critical(self, "启动超时",
                                     "服务在20秒内未就绪，请检查：\n\n"
                                     "1. config/settings.yaml 文件是否存在且格式正确\n"
                                     "2. 端口5000-5100是否全部被占用\n"
                                     "3. 核心依赖是否完整（flask, pyyaml, requests）")

    def _init_web_view(self):
        """初始化 WebEngine 视图并加载页面"""
        self._web_view = QWebEngineView()

        # 配置 WebEngine
        settings = self._web_view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.LocalStorageEnabled, True)

        # 自定义 User-Agent
        profile = self._web_view.page().profile()
        profile.setHttpUserAgent("QingkeLite-Desktop/1.0")

        # 页面崩溃自动恢复
        self._web_view.renderProcessTerminated.connect(self._on_render_crash)

        # 页面加载完成后移除 loading 界面
        self._web_view.loadFinished.connect(self._on_load_finished)

        # 加载页面
        url = f"http://127.0.0.1:{_server_port}/"
        self._web_view.load(QUrl(url))

        # 替换 UI
        self._layout.removeWidget(self._loading_label)
        self._layout.removeWidget(self._progress)
        self._loading_label.hide()
        self._progress.hide()
        self._layout.addWidget(self._web_view)

        # 定期健康检查：检测黑屏并自动恢复
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._check_health)
        self._health_timer.start(30000)  # 每30秒检查一次
        self._last_load_ok = True
        self._consecutive_failures = 0

        # 重启信号轮询：检测AI触发的重启请求
        self._restart_poll_timer = QTimer(self)
        self._restart_poll_timer.timeout.connect(self._poll_restart_status)
        self._restart_poll_timer.start(2000)  # 每2秒轮询一次
        self._restart_in_progress = False

        # 内置浏览器请求轮询：检测工具触发的打开浏览器请求
        self._browser_poll_timer = QTimer(self)
        self._browser_poll_timer.timeout.connect(self._check_browser_request)
        self._browser_poll_timer.start(300)  # 每300ms检查一次

    def _on_render_crash(self, termination_status, exit_code):
        """页面渲染进程崩溃时自动恢复"""
        self._last_load_ok = False
        self._consecutive_failures += 1
        # 崩溃后延迟重新加载（避免频繁重载）
        delay = min(3000 * self._consecutive_failures, 15000)
        QTimer.singleShot(delay, self._reload_page)

    def _check_health(self):
        """定期健康检查：通过JS检测页面是否存活，支持服务断开后自动重连"""
        if not self._web_view:
            return
        # 检查Flask服务是否还在运行
        import urllib.request
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{_server_port}/", timeout=2)
            server_alive = True
        except Exception:
            server_alive = False

        if not server_alive:
            # 服务挂了，显示提示并尝试自动重连
            self._loading_label.setText("服务已断开，正在重连...")
            self._loading_label.show()
            self._consecutive_failures += 1
            # 如果连续失败超过3次（90秒），尝试重新加载页面
            if self._consecutive_failures >= 3:
                logging.warning("服务持续不可用，尝试重新加载页面...")
                self._reload_page()
                self._consecutive_failures = 0
            return

        # 服务恢复了，隐藏加载提示
        if self._loading_label.isVisible():
            self._loading_label.hide()
            logging.info("服务已恢复连接")

        # 服务正常但页面可能黑屏，执行JS检测
        self._web_view.page().runJavaScript(
            "document.body ? document.body.innerHTML.length : 0",
            self._on_health_check_result
        )

    def _on_health_check_result(self, content_length):
        """健康检查回调"""
        if content_length is not None and content_length < 10:
            # 页面内容为空，可能黑屏，重新加载
            self._consecutive_failures += 1
            self._reload_page()
        else:
            self._consecutive_failures = 0
            self._loading_label.hide()
            # 定期清理WebEngine缓存，防止长时间运行后内存溢出
            if hasattr(self, '_health_check_count'):
                self._health_check_count += 1
            else:
                self._health_check_count = 1
            if self._health_check_count % 10 == 0:  # 每5分钟清理一次
                profile = self._web_view.page().profile()
                profile.clearHttpCache()

    def _reload_page(self):
        """重新加载页面"""
        if self._web_view:
            url = f"http://127.0.0.1:{_server_port}/"
            self._web_view.load(QUrl(url))

    def _poll_restart_status(self):
        """轮询重启信号：当AI触发restart_app工具时，TUI检测到后自动重载页面"""
        if self._restart_in_progress:
            return
        try:
            import urllib.request
            import json
            url = f"http://127.0.0.1:{_server_port}/api/system/restart-status"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('restart_requested'):
                    self._restart_in_progress = True
                    logging.info("检测到重启请求，正在重载页面...")
                    # 延迟1秒后重载，给后端处理留时间
                    QTimer.singleShot(1000, self._execute_restart)
        except Exception:
            pass  # 服务未就绪时静默忽略

    def _execute_restart(self):
        """执行重启：重载WebEngineView页面"""
        try:
            if self._web_view:
                # 清理缓存后重载
                profile = self._web_view.page().profile()
                profile.clearHttpCache()
                url = f"http://127.0.0.1:{_server_port}/"
                self._web_view.load(QUrl(url))
                logging.info("页面重载完成")
        except Exception as e:
            logging.error(f"重启失败: {e}")
        finally:
            # 3秒后允许再次检测重启信号
            QTimer.singleShot(3000, self._reset_restart_flag)

    def _reset_restart_flag(self):
        """重置重启标记"""
        self._restart_in_progress = False

    def _on_load_finished(self, ok):
        """页面加载完成"""
        if not ok:
            self._loading_label.setText("页面加载失败，请检查服务状态")
            self._loading_label.show()

    def _check_browser_request(self):
        """轮询检查是否有打开内置浏览器的请求（来自AI工具线程）"""
        from core.builtin_browser import check_open_request
        url = check_open_request()
        if url is not None:
            self._open_builtin_browser(url)

    def _open_builtin_browser(self, url=''):
        """创建并显示内置浏览器窗口（必须在Qt主线程调用）"""
        if self._browser_window is not None and not self._browser_window.isVisible():
            # 旧窗口已关闭，显式释放资源
            self._browser_window.deleteLater()
            self._browser_window = None
        if self._browser_window is None:
            self._browser_window = BrowserWindow()
            self._browser_window.show()
        if url:
            self._browser_window._web_view.load(QUrl(url))
        self._browser_window.activateWindow()
        self._browser_window.raise_()

    def closeEvent(self, event):
        """窗口关闭时清理"""
        # 关闭内置浏览器
        if self._browser_window:
            self._browser_window.close()
            self._browser_window = None
        if self._web_view:
            self._web_view.setHtml("")
        event.accept()


# ============================================================
# 入口
# ============================================================
def main():
    # 高 DPI 支持
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    # 禁用GPU加速，防止多轮任务后黑屏（QWebEngineView Chromium GPU渲染问题）
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-gpu-compositing")
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("青稞·lite")
    app.setApplicationDisplayName("青稞·lite - AI 助手")
    app.setFont(QFont("Microsoft YaHei", 9))

    # 版本过期检查
    if date.today() > date(2026, 7, 30):
        QMessageBox.critical(None, "版本已过期",
                             "此版本已过期，无法继续使用。\n"
                             "请联系开发者获取最新版本。")
        sys.exit(1)

    # 暗色主题样式
    app.setStyleSheet("""
        QMainWindow { background: #1e1e1e; }
        QWidget { background: #1e1e1e; color: #e0e0e0; }
        QMenuBar { background: #2d2d2d; color: #e0e0e0; }
        QMenuBar::item:selected { background: #404040; }
    """)

    # 环境检测与自动安装
    from env_setup import EnvChecker
    checker = EnvChecker()
    results = checker.check_all()
    missing = [name for name, r in results.items() if not r["installed"]]
    
    if missing:
        # 有缺失组件，显示安装向导
        dialog = EnvSetupDialog()
        dialog.exec_()  # 用户可以跳过或等待安装完成
    
    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
