"""青稞 Flask Web Application Entry Point"""
import os
import sys
import socket

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask
from core.engine import CoreEngine
from web.routes.api import create_api_blueprint
from web.routes.pages import create_page_blueprint
from web.routes.events import create_events_blueprint


# ==================== 启动预检 ====================

def _check_dependencies():
    """检查依赖是否完整，返回缺失列表"""
    required = {
        'flask': 'flask',
        'yaml': 'pyyaml',
        'requests': 'requests',
    }
    optional = {
        'openpyxl': 'openpyxl',
    }
    missing_required = []
    missing_optional = []

    for module, package in required.items():
        try:
            __import__(module)
        except ImportError:
            missing_required.append(package)

    for module, package in optional.items():
        try:
            __import__(module)
        except ImportError:
            missing_optional.append(package)

    return missing_required, missing_optional


def _check_port(host: str, port: int) -> bool:
    """检查端口是否被占用，返回 True 表示可用"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.bind((host, port))
            return True
    except OSError:
        return False


def _check_config(config_path: str) -> list:
    """校验配置文件，返回问题列表（空列表表示正常）"""
    issues = []

    # 1. 文件是否存在
    if not os.path.exists(config_path):
        issues.append(f"配置文件不存在: {config_path}")
        return issues

    # 2. YAML 语法是否正确
    try:
        import yaml
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        issues.append(f"配置文件YAML语法错误: {e}")
        return issues

    if not isinstance(config, dict):
        issues.append("配置文件内容格式错误（顶层应为字典）")
        return issues

    # 3. 必要配置段（llm 段不检查具体内容，用户启动后在界面中配置）
    required_sections = ['context', 'cli', 'flask']
    for section in required_sections:
        if section not in config:
            issues.append(f"缺少必要配置段: {section}")

    # 4. 系统提示词文件
    sys_prompt = config.get('context', {}).get('system_prompt_file', '')
    if sys_prompt and not os.path.isabs(sys_prompt):
        # 相对路径，基于项目根目录（config文件的上一级）
        base = os.path.dirname(os.path.dirname(os.path.abspath(config_path)))
        sys_prompt = os.path.join(base, sys_prompt)
    if sys_prompt and not os.path.exists(sys_prompt):
        issues.append(f"系统提示词文件不存在: {sys_prompt}")

    # 6. 端口（Flask reloader 子进程跳过端口检查，因为父进程已占用端口）
    flask_cfg = config.get('flask', {})
    port = flask_cfg.get('port', 5000)
    host = flask_cfg.get('host', '0.0.0.0')
    is_reloader_subprocess = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    if not isinstance(port, int) or port < 1 or port > 65535:
        issues.append(f"flask.port 值无效: {port}")
    elif not is_reloader_subprocess and not _check_port(host, port):
        issues.append(f"端口 {port} 已被占用（host={host}），请关闭占用程序或修改 flask.port")

    return issues


def _print_check_results(config_issues: list, missing_required: list, missing_optional: list):
    """打印启动检查结果"""
    print("\n" + "=" * 50)
    print("  青稞·Lite 启动预检")
    print("=" * 50)

    # 配置检查
    print("\n[1/3] 配置文件检查 (config/settings.yaml)")
    if not config_issues:
        print("  ✓ 配置文件正常")
    else:
        for issue in config_issues:
            print(f"  ✗ {issue}")

    # 端口检查（已包含在 config_issues 中，这里只做标注）
    print("\n[2/3] 端口检查")
    port_ok = not any("端口" in i and "被占用" in i for i in config_issues)
    if port_ok:
        print("  ✓ 端口可用")
    else:
        print("  ✗ 端口已被占用（见上方配置检查详情）")

    # 依赖检查
    print("\n[3/3] 依赖检查")
    if not missing_required:
        print("  ✓ 核心依赖完整")
    else:
        for pkg in missing_required:
            print(f"  ✗ 缺少核心依赖: {pkg}  →  pip install {pkg}")

    if missing_optional:
        for pkg in missing_optional:
            print(f"  ⚠ 可选依赖缺失: {pkg}  →  pip install {pkg}（Excel文件解析需要）")
    else:
        print("  ✓ 可选依赖完整")

    # 汇总
    has_error = bool(config_issues) or bool(missing_required)
    print("\n" + "-" * 50)
    if has_error:
        print("  ✗ 存在启动阻断问题，请修复后重试")
    else:
        print("  ✓ 所有检查通过，正在启动...")
    print("-" * 50 + "\n")

    return not has_error


# ==================== 资源路径 ====================

def _get_resource_base():
    """获取资源文件基础目录（模板、静态文件）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 单文件模式：资源在临时目录的 CLI_lite 子目录下
        return os.path.join(sys._MEIPASS, 'CLI_lite')
    return os.path.dirname(os.path.abspath(__file__))


def _get_config_base():
    """获取配置文件基础目录（单文件exe模式下为exe所在目录）"""
    if getattr(sys, 'frozen', False):
        # 单文件exe模式：配置在exe同级目录（可读写）
        return os.path.dirname(sys.executable)
    else:
        # 开发环境：配置在CLI_lite目录下
        return os.path.dirname(os.path.abspath(__file__))


# ==================== Flask 应用 ====================

def create_app():
    """创建Flask应用"""
    resource_base = _get_resource_base()
    config_base = _get_config_base()

    app = Flask(
        __name__,
        template_folder=os.path.join(resource_base, "web", "templates"),
        static_folder=os.path.join(resource_base, "web", "static"),
        static_url_path="/static"
    )

    # 初始化核心引擎（配置从exe目录读取，支持用户修改）
    config_path = os.path.join(config_base, "config", "settings.yaml")
    engine = CoreEngine(config_path)
    app.config['engine'] = engine

    # 显示检测到的用户名
    print(f"  系统用户: {engine.username}")

    # 注册蓝图
    api_bp = create_api_blueprint(engine)
    app.register_blueprint(api_bp)

    pages_bp = create_page_blueprint()
    app.register_blueprint(pages_bp)

    events_bp = create_events_blueprint()
    app.register_blueprint(events_bp)

    return app


if __name__ == "__main__":
    config_base = _get_config_base()
    config_path = os.path.join(config_base, "config", "settings.yaml")

    # 启动预检
    config_issues = _check_config(config_path)
    missing_required, missing_optional = _check_dependencies()
    can_start = _print_check_results(config_issues, missing_required, missing_optional)

    if not can_start:
        print("启动已中止，请根据上方提示修复问题后重新运行。")
        sys.exit(1)

    app = create_app()

    flask_config = app.config['engine'].config.get("flask", {})
    app.run(
        host=flask_config.get("host", "0.0.0.0"),
        port=flask_config.get("port", 5000),
        debug=flask_config.get("debug", True),
        use_reloader=False  # 禁用自动重载，避免Agentic Loop生成文件导致服务器重启
    )
