"""定时提醒调度器 - 支持定时提醒、系统弹窗通知"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta


class ReminderScheduler:
    """定时提醒调度器

    功能：
    - 设置定时提醒（绝对时间或相对时间）
    - 后台线程每30秒检查到期提醒
    - 到期时通过系统弹窗通知用户
    - 提醒记录持久化到 JSON 文件
    """

    def __init__(self, data_dir: str = None):
        self._data_dir = data_dir or os.path.join("data")
        self._file = os.path.join(self._data_dir, "reminders.json")
        self._reminders: list = []
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

        os.makedirs(self._data_dir, exist_ok=True)
        self._load()

    def start(self):
        """启动后台提醒检查线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._check_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止后台线程"""
        self._running = False

    def set_reminder(self, time_str: str, message: str, title: str = "青稞提醒") -> dict:
        """设置定时提醒

        Args:
            time_str: 时间字符串，支持：
                - 绝对时间: "2026-06-30 15:30", "15:30", "下午3点"
                - 相对时间: "5分钟后", "2小时后", "30min", "1h"
            message: 提醒内容
            title: 提醒标题，默认"青稞提醒"

        Returns:
            {"success": bool, "reminder_id": str, "trigger_time": str, "message": str}
        """
        trigger_time = self._parse_time(time_str)
        if not trigger_time:
            return {"success": False, "error": f"无法解析时间: {time_str}"}

        if trigger_time <= datetime.now():
            return {"success": False, "error": f"提醒时间已过: {trigger_time.strftime('%Y-%m-%d %H:%M:%S')}"}

        reminder = {
            "id": str(uuid.uuid4())[:8],
            "title": title,
            "message": message,
            "trigger_time": trigger_time.strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "pending",
        }

        with self._lock:
            self._reminders.append(reminder)
            self._save()

        return {
            "success": True,
            "reminder_id": reminder["id"],
            "trigger_time": reminder["trigger_time"],
            "title": title,
            "message": message,
        }

    def list_reminders(self) -> list:
        """列出所有活跃提醒"""
        with self._lock:
            return [r for r in self._reminders if r["status"] == "pending"]

    def cancel_reminder(self, reminder_id: str) -> dict:
        """取消指定提醒"""
        with self._lock:
            for r in self._reminders:
                if r["id"] == reminder_id and r["status"] == "pending":
                    r["status"] = "cancelled"
                    self._save()
                    return {"success": True, "message": f"已取消提醒: {r['message'][:50]}"}
        return {"success": False, "error": f"未找到提醒: {reminder_id}"}

    def get_pending_count(self) -> int:
        """获取待触发提醒数量"""
        with self._lock:
            return sum(1 for r in self._reminders if r["status"] == "pending")

    # ==================== 内部方法 ====================

    def _check_loop(self):
        """后台检查循环"""
        while self._running:
            try:
                self._check_due_reminders()
            except Exception:
                pass
            time.sleep(30)

    def _check_due_reminders(self):
        """检查并触发到期提醒"""
        now = datetime.now()
        due = []
        with self._lock:
            for r in self._reminders:
                if r["status"] != "pending":
                    continue
                trigger = datetime.strptime(r["trigger_time"], "%Y-%m-%d %H:%M:%S")
                if now >= trigger:
                    due.append(r)
                    r["status"] = "delivered"

        for r in due:
            self._show_popup(r["title"], r["message"])

        if due:
            self._save()

    def _show_popup(self, title: str, message: str):
        """显示系统弹窗通知"""
        # 方案1: PowerShell WPF 弹窗（带置顶和声音提示）
        ps_script = f'''
Add-Type -AssemblyName System.Windows.Forms
[System.Media.SystemSounds]::Exclamation.Play()
[System.Windows.Forms.MessageBox]::Show(
    "{message.replace('"', '`"').replace("'", "''")}",
    "{title.replace('"', '`"').replace("'", "''")}",
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Information
)
'''
        try:
            subprocess.Popen(
                ["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except Exception:
            # 降级方案: 原生 PowerShell 简单弹窗
            try:
                fallback = (
                    f'Add-Type -AssemblyName System.Windows.Forms; '
                    f'[System.Windows.Forms.MessageBox]::Show("{message[:200]}", "{title}")'
                )
                subprocess.Popen(
                    ["powershell", "-Command", fallback],
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
            except Exception:
                pass

    def _parse_time(self, time_str: str) -> datetime:
        """解析时间字符串为 datetime 对象"""
        now = datetime.now()
        s = time_str.strip()

        # 相对时间: "5分钟后", "2小时后", "30min", "1h", "3天后"
        m = re.search(r'(\d+)\s*(分钟后|分钟|min|m)', s, re.IGNORECASE)
        if m:
            return now + timedelta(minutes=int(m.group(1)))

        m = re.search(r'(\d+)\s*(小时后|小时|h|hr)', s, re.IGNORECASE)
        if m:
            return now + timedelta(hours=int(m.group(1)))

        m = re.search(r'(\d+)\s*(天后|天|d|day)', s, re.IGNORECASE)
        if m:
            return now + timedelta(days=int(m.group(1)))

        m = re.search(r'(\d+)\s*(秒后|秒|s|sec)', s, re.IGNORECASE)
        if m:
            return now + timedelta(seconds=int(m.group(1)))

        # 中文时间: "下午3点", "上午10:30", "晚上8点"
        m = re.search(r'(上午|早上|am)?\s*(\d{1,2})[点时:](\d{0,2})\s*分?', s, re.IGNORECASE)
        if m:
            hour = int(m.group(2))
            minute = int(m.group(3)) if m.group(3) else 0
            if m.group(1) and hour < 12:
                pass  # 上午不变
            elif '下午' in s or '晚上' in s or 'pm' in s.lower():
                if hour < 12:
                    hour += 12
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target

        # 绝对时间格式: "2026-06-30 15:30" 或 "2026-06-30 15:30:00"
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"]:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue

        # 纯时间: "15:30", "15:30:00"
        for fmt in ["%H:%M:%S", "%H:%M"]:
            try:
                t = datetime.strptime(s, fmt)
                target = now.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                return target
            except ValueError:
                continue

        return None

    def _load(self):
        """从文件加载提醒"""
        if os.path.exists(self._file):
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    self._reminders = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._reminders = []

    def _save(self):
        """保存提醒到文件"""
        try:
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(self._reminders, f, ensure_ascii=False, indent=2)
        except IOError:
            pass
