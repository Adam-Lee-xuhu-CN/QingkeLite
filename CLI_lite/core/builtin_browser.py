"""内置浏览器共享状态 - 用于TUI浏览器窗口与后端工具之间的线程安全通信"""
import queue
import threading
import time
import uuid
import logging

logger = logging.getLogger('builtin_browser')

# 结果存储过期时间（秒）
_RESULT_TTL = 120

# 命令队列：工具 → 浏览器窗口
_cmd_queue = queue.Queue()

# 结果存储：浏览器窗口 → 工具 {cmd_id: (result_dict, timestamp)}
_result_store = {}
_result_lock = threading.Lock()

# 网络日志：浏览器窗口写入，工具读取
_network_logs = []
_network_lock = threading.Lock()

# 浏览器开关状态
_browser_open = False
_browser_lock = threading.Lock()

# 打开浏览器请求（MainWindow轮询处理GUI创建，必须在Qt主线程）
_open_request = {'pending': False, 'url': ''}
_open_request_lock = threading.Lock()


def request_open_browser(url='', timeout=15):
    """请求打开内置浏览器窗口（阻塞等待直到窗口打开或超时）"""
    if is_browser_open():
        return {'success': True, 'message': '浏览器已处于打开状态'}

    with _open_request_lock:
        _open_request['pending'] = True
        _open_request['url'] = url

    # 等待MainWindow完成窗口创建
    start = time.time()
    while time.time() - start < timeout:
        if is_browser_open():
            # 等待一小段时间确保窗口完全初始化
            time.sleep(0.3)
            return {'success': True}
        time.sleep(0.2)

    return {'success': False, 'error': f'打开浏览器超时 ({timeout}s)'}


def check_open_request():
    """检查是否有打开浏览器的请求（由MainWindow的QTimer调用）"""
    with _open_request_lock:
        if _open_request['pending']:
            _open_request['pending'] = False
            return _open_request.get('url', '')
    return None


def send_command(cmd_type, timeout=30, **params):
    """发送命令到浏览器窗口并等待结果（阻塞调用，由工具线程调用）"""
    if cmd_type != 'close' and not is_browser_open():
        return {'error': '内置浏览器未打开，请先使用 builtin_browser_open 打开', 'success': False}

    cmd_id = str(uuid.uuid4())[:8]
    cmd = {'id': cmd_id, 'type': cmd_type, **params}
    _cmd_queue.put(cmd)

    start = time.time()
    while time.time() - start < timeout:
        with _result_lock:
            if cmd_id in _result_store:
                result, _ = _result_store.pop(cmd_id)
                return result
        time.sleep(0.1)

    # 超时后清理可能的残留结果
    with _result_lock:
        _result_store.pop(cmd_id, None)

    return {'error': f'命令执行超时 ({timeout}s)', 'success': False}


def store_result(cmd_id, result):
    """存储命令执行结果（由浏览器窗口的QTimer回调调用）"""
    with _result_lock:
        _result_store[cmd_id] = (result, time.time())
        # 清理过期结果（防止内存泄漏）
        now = time.time()
        expired = [k for k, (_, ts) in _result_store.items() if now - ts > _RESULT_TTL]
        for k in expired:
            del _result_store[k]


def get_command():
    """获取队列中的下一个命令（非阻塞，由浏览器窗口调用）"""
    try:
        return _cmd_queue.get_nowait()
    except queue.Empty:
        return None


def add_network_log(entry):
    """添加一条网络日志（由浏览器窗口调用）"""
    with _network_lock:
        entry.setdefault('timestamp', time.time())
        _network_logs.append(entry)
        # 限制内存占用：最多保留5000条
        if len(_network_logs) > 5000:
            del _network_logs[:len(_network_logs) - 5000]


def get_network_logs(since=0, limit=500):
    """获取网络日志（由工具调用）"""
    with _network_lock:
        logs = [log for log in _network_logs if log.get('timestamp', 0) >= since]
        return logs[-limit:]


def clear_network_logs():
    """清空网络日志"""
    with _network_lock:
        _network_logs.clear()


def set_browser_open(is_open):
    """设置浏览器开关状态"""
    with _browser_lock:
        global _browser_open
        _browser_open = is_open


def is_browser_open():
    """检查浏览器是否打开"""
    with _browser_lock:
        return _browser_open
