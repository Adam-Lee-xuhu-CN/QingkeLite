"""任务执行器 - 通过PowerShell执行命令"""
import os
import re
import subprocess
import sys


class TaskExecutor:
    """任务执行器，通过PowerShell执行CLI命令"""

    def __init__(self, shell: str = "powershell", timeout: int = 30):
        self.shell = shell
        self.timeout = timeout

    def execute(self, command: str, cwd: str = None) -> str:
        """执行命令并返回输出。
        强制 PowerShell 使用 UTF-8 输出编码，解决中文 Windows 下 GBK 乱码问题。
        自动检测并引用含空格的路径，解决"无法将xxx识别为cmdlet"报错。
        """
        # 自动引用含空格的路径
        command = self._quote_paths(command)

        # 在现有环境变量基础上添加UTF-8编码设置
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        # 关键修复：在命令前注入 PowerShell UTF-8 编码设置 + chcp切换代码页
        utf8_prefix = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$OutputEncoding = [System.Text.Encoding]::UTF8; "
            "chcp 65001 > $null; "
        )
        full_command = utf8_prefix + command

        result = subprocess.run(
            [self.shell, "-Command", full_command],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            encoding='utf-8',
            errors='replace',
            env=env,
            cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"命令执行失败，返回码 {result.returncode}")
        return result.stdout.strip()

    @staticmethod
    def _quote_paths(command: str) -> str:
        """检测命令中未引用的含空格路径，自动添加引号。
        解决 PowerShell "无法将xxx识别为cmdlet、函数、脚本文件" 报错。
        """
        if not command or ' ' not in command:
            return command

        path_re = re.compile(
            r'([a-zA-Z]:)'       # 盘符
            r'((?:[^\\/\s"]+\\?)*)'  # 路径片段（可含空格）
        )

        matches = list(path_re.finditer(command))
        if not matches:
            return command

        fixes = []
        for m in matches:
            full_path = m.group(0)
            if ' ' not in full_path:
                continue
            # 检查引号状态
            prefix = command[:m.start()]
            quote_count = prefix.count('"') - prefix.count('\\"')
            if quote_count % 2 == 1:
                continue  # 已在引号内
            # 向后扫描，拼接含空格的路径尾部
            after = command[m.end():]
            rest_parts = []
            for seg in after.split():
                if seg.startswith(('-', '/', '--')):
                    break
                rest_parts.append(seg)
                test_path = full_path + ' ' + ' '.join(rest_parts)
                if os.path.exists(test_path):
                    full_path = test_path
                elif not os.path.exists(full_path):
                    full_path = test_path
                else:
                    break
            # 仅对实际存在且含空格的路径添加引号
            if ' ' in full_path and os.path.exists(full_path):
                fixes.append((full_path, f'"{full_path}"'))

        # 去重并按长度降序排列（避免子串替换冲突）
        seen = set()
        unique_fixes = []
        for old, new in fixes:
            if old not in seen:
                seen.add(old)
                unique_fixes.append((old, new))
        unique_fixes.sort(key=lambda x: -len(x[0]))

        for old, new in unique_fixes:
            command = command.replace(old, new, 1)
        return command
