"""工具系统 - 提供给Agentic Loop使用的文件操作和命令执行工具"""
import os
import re
import subprocess
import glob as glob_module
from typing import Optional


class ToolRegistry:
    """工具注册中心，管理所有可用工具"""

    def __init__(self, reminder_scheduler=None):
        self._tools = {}
        self._reminder_scheduler = reminder_scheduler
        self._register_defaults()

    def _register_defaults(self):
        """注册默认工具"""
        self.register("read_file", ToolReadFile())
        self.register("write_file", ToolWriteFile())
        self.register("edit_file", ToolEditFile())
        self.register("grep", ToolGrep())
        self.register("glob", ToolGlob())
        self.register("run_command", ToolRunCommand())
        self.register("list_directory", ToolListDirectory())
        self.register("web_fetch", ToolWebFetch())
        self.register("task_complete", ToolTaskComplete())
        # 浏览器自动化工具（需要selenium）
        self.register("browser_open", ToolBrowserOpen())
        self.register("browser_navigate", ToolBrowserNavigate())
        self.register("browser_login", ToolBrowserLogin())
        self.register("browser_get_content", ToolBrowserGetContent())
        self.register("browser_click", ToolBrowserClick())
        self.register("browser_type", ToolBrowserType())
        self.register("browser_close", ToolBrowserClose())
        self.register("browser_screenshot", ToolBrowserScreenshot())
        self.register("browser_wait", ToolBrowserWait())
        self.register("browser_execute_script", ToolBrowserExecuteScript())
        self.register("browser_drag", ToolBrowserDrag())
        self.register("browser_find_elements", ToolBrowserFindElements())
        # 桌面自动化工具（需要pyautogui）
        self.register("desktop_open_app", ToolDesktopOpenApp())
        self.register("desktop_list_windows", ToolDesktopListWindows())
        self.register("desktop_focus_window", ToolDesktopFocusWindow())
        self.register("desktop_close_window", ToolDesktopCloseWindow())
        self.register("desktop_screenshot", ToolDesktopScreenshot())
        self.register("desktop_click", ToolDesktopClick())
        self.register("desktop_type_text", ToolDesktopTypeText())
        self.register("desktop_press_key", ToolDesktopPressKey())
        self.register("desktop_scroll", ToolDesktopScroll())
        self.register("desktop_drag", ToolDesktopDrag())
        self.register("desktop_find_text", ToolDesktopFindText())
        self.register("desktop_find_image", ToolDesktopFindImage())
        self.register("desktop_get_cursor_pos", ToolDesktopGetCursorPos())
        # 定时提醒工具
        if self._reminder_scheduler:
            self.register("set_reminder", ToolSetReminder(self._reminder_scheduler))
            self.register("list_reminders", ToolListReminders(self._reminder_scheduler))
            self.register("cancel_reminder", ToolCancelReminder(self._reminder_scheduler))
        # 系统工具
        self.register("restart_app", ToolRestartApp())
        # 内置浏览器工具
        self.register("builtin_browser_open", ToolBuiltinBrowserOpen())
        self.register("builtin_browser_navigate", ToolBuiltinBrowserNavigate())
        self.register("builtin_browser_get_content", ToolBuiltinBrowserGetContent())
        self.register("builtin_browser_screenshot", ToolBuiltinBrowserScreenshot())
        self.register("builtin_browser_get_network", ToolBuiltinBrowserGetNetwork())
        self.register("builtin_browser_execute_js", ToolBuiltinBrowserExecuteJs())
        self.register("builtin_browser_close", ToolBuiltinBrowserClose())

    def register(self, name: str, tool):
        """注册工具"""
        self._tools[name] = tool

    def get(self, name: str):
        """获取工具"""
        return self._tools.get(name)

    def execute(self, name: str, parameters: dict) -> dict:
        """执行工具"""
        tool = self._tools.get(name)
        if not tool:
            return {"success": False, "error": f"未知工具: {name}"}
        try:
            result = tool.execute(**parameters)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_tool_definitions(self) -> list[dict]:
        """获取所有工具的定义（用于LLM prompt）"""
        return [t.definition for t in self._tools.values()]

    def get_tool_prompt(self) -> str:
        """生成工具定义提示文本"""
        lines = ["## 可用工具\n"]
        for tool in self._tools.values():
            defn = tool.definition
            lines.append(f"### {defn['name']}")
            lines.append(f"描述: {defn['description']}")
            lines.append("参数:")
            for param_name, param_info in defn['parameters'].items():
                required = "必填" if param_info.get("required", False) else "可选"
                lines.append(f"  - {param_name} ({required}): {param_info['description']}")
            lines.append("")
        return "\n".join(lines)


class ToolReadFile:
    """读取文件内容"""

    @property
    def definition(self):
        return {
            "name": "read_file",
            "description": "读取文件内容。用于查看代码、配置文件等。",
            "parameters": {
                "file_path": {
                    "type": "string",
                    "required": True,
                    "description": "文件的绝对路径"
                },
                "offset": {
                    "type": "integer",
                    "required": False,
                    "description": "起始行号（从1开始），不指定则从头读取"
                },
                "limit": {
                    "type": "integer",
                    "required": False,
                    "description": "最大读取行数，不指定则读取全部"
                }
            }
        }

    # 大文件阈值：超过此行数时自动截断并提示分段读取
    LARGE_FILE_LINES = 500
    # 自动截断时返回的最大行数
    AUTO_TRUNCATE_LINES = 200

    def execute(self, file_path: str, offset: Optional[int] = None, limit: Optional[int] = None) -> str:
        if not os.path.exists(file_path):
            return f"错误: 文件不存在 - {file_path}"
        if not os.path.isfile(file_path):
            return f"错误: 路径不是文件 - {file_path}"

        try:
            # 二进制文件检测：先读取前8KB检查是否为二进制内容
            try:
                with open(file_path, 'rb') as bf:
                    head = bf.read(8192)
                # 常见二进制文件签名
                _BINARY_SIGNATURES = [
                    b'PK\x03\x04',      # ZIP/DOCX/XLSX/PPTX/JAR
                    b'%PDF',             # PDF
                    b'\x89PNG',          # PNG
                    b'\xff\xd8\xff',     # JPEG
                    b'GIF8',             # GIF
                    b'RIFF',             # WEBP/AVI
                    b'\x00\x00\x01\x00', # ICO
                    b'MZ',              # EXE/DLL
                    b'\x7fELF',         # ELF
                    b'BM',              # BMP
                    b'\x1f\x8b',       # GZIP
                    b'BZh',            # BZIP2
                ]
                is_binary = False
                for sig in _BINARY_SIGNATURES:
                    if head.startswith(sig):
                        is_binary = True
                        break
                # 通用检测：前4KB中null字节超过1%，或非打印字符超过30%
                if not is_binary and head:
                    null_count = head.count(b'\x00')
                    non_print = sum(1 for b in head[:4096] if b < 32 and b not in (9, 10, 13))
                    if null_count > len(head) * 0.01 or non_print > len(head[:4096]) * 0.3:
                        is_binary = True
                if is_binary:
                    return f"错误: 这是一个二进制文件（如 .docx/.xlsx/.pdf/.exe 等），无法以文本方式读取。文件路径: {file_path}"
            except (OSError, PermissionError):
                pass  # 读取失败不影响后续文本读取尝试

            # 优先UTF-8，失败则GBK回退（兼容Windows中文环境输出的文件）
            lines = None
            for enc in ('utf-8', 'gbk', 'gb2312', 'latin-1'):
                try:
                    with open(file_path, 'r', encoding=enc) as f:
                        lines = f.readlines()
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            if lines is None:
                return f"错误: 无法以任何已知编码读取文件 - {file_path}"

            total_lines = len(lines)
            file_size = os.path.getsize(file_path)

            if offset is not None:
                offset = max(0, offset - 1)
            else:
                offset = 0

            if limit is not None:
                lines = lines[offset:offset + limit]
            else:
                lines = lines[offset:]

            # 大文件保护：未指定limit且文件超过阈值时，自动截断并提示
            large_file_warning = ""
            if limit is None and total_lines > self.LARGE_FILE_LINES:
                lines = lines[:self.AUTO_TRUNCATE_LINES]
                large_file_warning = (
                    f"\n\n⚠️ 大文件检测：该文件共 {total_lines} 行 ({file_size} 字节)，"
                    f"已自动截断为前 {self.AUTO_TRUNCATE_LINES} 行。\n"
                    f"【推荐处理方式】\n"
                    f"1. 分段读取: read_file(offset={self.AUTO_TRUNCATE_LINES + 1}, limit=200) 继续读取\n"
                    f"2. 用Python脚本提取特征: 写一个脚本读取文件，提取统计信息/摘要/关键行，输出到md文件\n"
                    f"3. 用grep搜索关键词: 定位到感兴趣的内容后再精确读取"
                )

            # 添加行号
            numbered = []
            for i, line in enumerate(lines):
                line_num = offset + i + 1
                numbered.append(f"{line_num:>4}|{line.rstrip()}")

            result = "\n".join(numbered)
            if not result:
                result = "(空文件)"
            if large_file_warning:
                result += large_file_warning
            return result
        except Exception as e:
            return f"错误: {str(e)}"


class ToolWriteFile:
    """写入文件"""

    @property
    def definition(self):
        return {
            "name": "write_file",
            "description": "创建或覆盖文件。用于创建新文件或完全重写现有文件。",
            "parameters": {
                "file_path": {
                    "type": "string",
                    "required": True,
                    "description": "文件的绝对路径"
                },
                "content": {
                    "type": "string",
                    "required": True,
                    "description": "要写入的文件内容"
                }
            }
        }

    def execute(self, file_path: str, content: str) -> str:
        try:
            # 确保目录存在
            dir_path = os.path.dirname(file_path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path, exist_ok=True)

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"文件已写入: {file_path} ({len(content)} 字符)"
        except Exception as e:
            return f"错误: {str(e)}"


class ToolEditFile:
    """编辑文件（字符串替换）"""

    @property
    def definition(self):
        return {
            "name": "edit_file",
            "description": "编辑文件，将旧字符串替换为新字符串。需要精确匹配old_string。",
            "parameters": {
                "file_path": {
                    "type": "string",
                    "required": True,
                    "description": "文件的绝对路径"
                },
                "old_string": {
                    "type": "string",
                    "required": True,
                    "description": "要被替换的原始文本（需精确匹配）"
                },
                "new_string": {
                    "type": "string",
                    "required": True,
                    "description": "替换后的新文本"
                }
            }
        }

    def execute(self, file_path: str, old_string: str, new_string: str) -> str:
        if not os.path.exists(file_path):
            return f"错误: 文件不存在 - {file_path}"

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            if old_string not in content:
                return f"错误: 在文件中未找到匹配的文本。请先使用read_file查看文件内容。"

            # 只替换第一次出现
            new_content = content.replace(old_string, new_string, 1)

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return f"文件已编辑: {file_path} (替换成功)"
        except Exception as e:
            return f"错误: {str(e)}"


class ToolGrep:
    """搜索文件内容"""

    @property
    def definition(self):
        return {
            "name": "grep",
            "description": "在文件中搜索匹配的文本（支持正则表达式）。用于查找代码中的函数、类、变量等。",
            "parameters": {
                "pattern": {
                    "type": "string",
                    "required": True,
                    "description": "搜索模式（支持正则表达式）"
                },
                "path": {
                    "type": "string",
                    "required": True,
                    "description": "搜索的文件或目录路径"
                },
                "file_filter": {
                    "type": "string",
                    "required": False,
                    "description": "文件过滤器，如 *.py, *.js, *.yaml"
                },
                "case_sensitive": {
                    "type": "boolean",
                    "required": False,
                    "description": "是否区分大小写，默认False"
                }
            }
        }

    def execute(self, pattern: str, path: str, file_filter: Optional[str] = None,
                case_sensitive: bool = False) -> str:
        if not os.path.exists(path):
            return f"错误: 路径不存在 - {path}"

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return f"错误: 无效的正则表达式 - {str(e)}"

        results = []
        files_to_search = []

        if os.path.isfile(path):
            files_to_search = [path]
        else:
            for root, dirs, files in os.walk(path):
                # 跳过隐藏目录和缓存
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('__pycache__', 'node_modules', '.git')]
                for fname in files:
                    if file_filter:
                        if not glob_module.fnmatch.fnmatch(fname, file_filter):
                            continue
                    files_to_search.append(os.path.join(root, fname))

        for fpath in files_to_search:
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    for line_num, line in enumerate(f, 1):
                        if regex.search(line):
                            results.append(f"{fpath}:{line_num}: {line.rstrip()}")
            except (UnicodeDecodeError, PermissionError):
                pass

            if len(results) >= 50:
                results.append(f"... (显示前50条，更多匹配已省略)")
                break

        if not results:
            return f"未找到匹配 '{pattern}' 的内容"
        return "\n".join(results)


class ToolGlob:
    """查找文件"""

    @property
    def definition(self):
        return {
            "name": "glob",
            "description": "按文件名模式查找文件。用于查找特定类型的文件。",
            "parameters": {
                "pattern": {
                    "type": "string",
                    "required": True,
                    "description": "glob模式，如 *.py, **/*.js, src/**/*.ts"
                },
                "path": {
                    "type": "string",
                    "required": True,
                    "description": "搜索的起始目录"
                }
            }
        }

    def execute(self, pattern: str, path: str) -> str:
        if not os.path.exists(path):
            return f"错误: 目录不存在 - {path}"

        search_path = os.path.join(path, pattern)
        results = glob_module.glob(search_path, recursive=True)

        if not results:
            return f"未找到匹配 '{pattern}' 的文件"

        # 显示前50条
        results = sorted(results)
        if len(results) > 50:
            results = results[:50] + ["... (共{}个文件)".format(len(results))]
        return "\n".join(results)


class ToolRunCommand:
    """执行命令"""

    # 输出截断阈值：超过此字符数时截断并提示
    MAX_OUTPUT_CHARS = 5000

    def __init__(self):
        """初始化工具，复用 TaskExecutor 的 UTF-8 编码处理逻辑"""
        from dispatcher.task_executor import TaskExecutor
        self._executor = TaskExecutor()

    @property
    def definition(self):
        return {
            "name": "run_command",
            "description": "执行系统命令。用于运行测试、构建、安装依赖等。路径含空格时必须用双引号包裹。",
            "parameters": {
                "command": {
                    "type": "string",
                    "required": True,
                    "description": "要执行的命令。注意：路径含空格必须用双引号包裹，如 cd \"d:\\my project\""
                },
                "cwd": {
                    "type": "string",
                    "required": False,
                    "description": "工作目录（自动传递给subprocess，无需在命令中cd）。路径含空格时必须用双引号包裹。"
                },
                "timeout": {
                    "type": "integer",
                    "required": False,
                    "description": "超时秒数，默认60"
                }
            }
        }

    def execute(self, command: str, cwd: Optional[str] = None, timeout: int = 60) -> str:
        """执行命令，复用 TaskExecutor 的 UTF-8 编码处理。
        Python脚本的执行结果写入md文件，避免编码格式导致乱码。
        """
        try:
            # 路径检测：如果命令只是文件/文件夹路径，拒绝执行并提示
            stripped = command.strip().strip('"').strip("'")
            if os.path.exists(stripped) and not any(stripped.lower().endswith(ext) for ext in ['.py', '.ps1', '.bat', '.cmd', '.sh']):
                if os.path.isdir(stripped):
                    return f"错误: 你传入的是文件夹路径而不是命令。请使用 list_directory 工具查看文件夹内容，或使用 glob_files 搜索文件。\n路径: {stripped}"
                else:
                    return f"错误: 你传入的是文件路径而不是命令。请使用 read_file 工具读取文件内容。\n路径: {stripped}"

            # 设置超时时间
            self._executor.timeout = timeout

            # 判断是否是Python脚本执行
            is_python_script = any(kw in command.lower() for kw in ['python ', 'python3 ', 'py '])

            if is_python_script:
                # Python脚本：将输出写入md文件，避免编码问题
                import datetime
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                output_file = os.path.abspath(os.path.join("data", "logs", f"python_output_{timestamp}.md"))
                os.makedirs(os.path.dirname(output_file), exist_ok=True)

                # 关键修复：设置环境变量强制Python使用UTF-8，并用Out-File -Encoding utf8替代*>
                env_prefix = "$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'; "
                redirect_cmd = f'{env_prefix}{command} 2>&1 | Out-File -Encoding utf8 "{output_file}"'
                try:
                    self._executor.execute(redirect_cmd, cwd=cwd)
                except RuntimeError as e:
                    # 即使报错也读取已有的输出
                    pass

                # 读取输出文件
                if os.path.exists(output_file):
                    with open(output_file, 'r', encoding='utf-8', errors='replace') as f:
                        output = f.read().strip()
                    if output:
                        # 超长截断保护
                        total_len = len(output)
                        if total_len > self.MAX_OUTPUT_CHARS:
                            output = output[:self.MAX_OUTPUT_CHARS]
                            output += f"\n\n... (输出截断，共 {total_len} 字符，已显示前 {self.MAX_OUTPUT_CHARS} 字符)"
                            output += "\n【提示】完整输出已保存到文件，可用read_file分段读取"
                        return f"Python脚本执行完成，输出已保存到: {output_file}\n\n{output}"
                    else:
                        # 输出文件存在但为空，尝试直接运行获取错误信息
                        try:
                            err_result = self._executor.execute(f'{env_prefix}{command}', cwd=cwd)
                            return f"Python脚本执行完成（无输出）。\n{err_result}"
                        except RuntimeError as e2:
                            return f"Python脚本执行完成（无输出），文件: {output_file}\n错误: {str(e2)}"
                else:
                    # 输出文件未生成，直接运行命令获取错误信息
                    try:
                        err_result = self._executor.execute(f'{env_prefix}{command}', cwd=cwd)
                        return f"Python脚本执行完成，Out-File未生成文件。\n直接输出:\n{err_result}"
                    except RuntimeError as e:
                        return f"Python脚本执行失败: {str(e)}"
            else:
                # 非Python命令：直接返回输出（超长截断）
                result = self._executor.execute(command, cwd=cwd)
                if len(result) > self.MAX_OUTPUT_CHARS:
                    truncated = result[:self.MAX_OUTPUT_CHARS]
                    truncated += f"\n\n... (输出截断，共 {len(result)} 字符，已显示前 {self.MAX_OUTPUT_CHARS} 字符)"
                    truncated += "\n【提示】输出过长，建议用Python脚本处理：将命令输出重定向到文件，再用脚本提取关键信息"
                    return truncated
                return result

        except subprocess.TimeoutExpired:
            raise RuntimeError(f"命令执行超时 ({timeout}秒)")
        except RuntimeError as e:
            raise  # 重新抛出，让 ToolManager 标记为失败
        except Exception as e:
            raise RuntimeError(f"命令执行错误: {str(e)}")


class ToolListDirectory:
    """列出目录内容"""

    @property
    def definition(self):
        return {
            "name": "list_directory",
            "description": "列出目录中的文件和子目录。用于查看项目结构。",
            "parameters": {
                "path": {
                    "type": "string",
                    "required": True,
                    "description": "目录的绝对路径"
                }
            }
        }

    def execute(self, path: str) -> str:
        if not os.path.exists(path):
            return f"错误: 目录不存在 - {path}"
        if not os.path.isdir(path):
            return f"错误: 路径不是目录 - {path}"

        try:
            items = sorted(os.listdir(path))
            if not items:
                return "(空目录)"

            lines = []
            for item in items:
                item_path = os.path.join(path, item)
                prefix = "[DIR] " if os.path.isdir(item_path) else "[FILE]"
                lines.append(f"{prefix} {item}")
            return "\n".join(lines)
        except PermissionError:
            return f"错误: 没有权限访问 - {path}"
        except Exception as e:
            return f"错误: {str(e)}"


class ToolWebFetch:
    """抓取网页内容 - 用于信息收集和网络数据获取"""

    @property
    def definition(self):
        return {
            "name": "web_fetch",
            "description": "抓取指定URL的网页内容，返回纯文本。用于搜索信息、获取网页数据、查阅文档等。支持HTTP/HTTPS。",
            "parameters": {
                "url": {
                    "type": "string",
                    "required": True,
                    "description": "要抓取的网页URL（必须以http://或https://开头）"
                },
                "max_length": {
                    "type": "integer",
                    "required": False,
                    "description": "返回内容的最大字符数，默认5000，最大20000"
                }
            }
        }

    def execute(self, url: str, max_length: int = 5000) -> str:
        import urllib.request
        import urllib.error
        import re

        url = url.strip()
        if not url.startswith(("http://", "https://")):
            return "错误: URL必须以http://或https://开头"

        max_length = min(max(max_length or 5000, 500), 20000)

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "identity",
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                # 检测编码
                content_type = resp.headers.get("Content-Type", "")
                charset = "utf-8"
                if "charset=" in content_type:
                    charset = content_type.split("charset=")[-1].split(";")[0].strip()
                raw = resp.read()
                try:
                    text = raw.decode(charset, errors='replace')
                except (LookupError, UnicodeDecodeError):
                    text = raw.decode('utf-8', errors='replace')

            # 简单HTML转纯文本
            # 移除script和style
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            # 把<br>、<p>、<div>、<li>、<tr>、<h1-h6>转为换行
            text = re.sub(r'<br\s*/?>|</p>|</div>|</li>|</tr>|</h[1-6]>', '\n', text, flags=re.IGNORECASE)
            # 移除所有其他HTML标签
            text = re.sub(r'<[^>]+>', '', text)
            # 解码HTML实体
            text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
            # 合并多余空行
            text = re.sub(r'\n{3,}', '\n\n', text)
            text = text.strip()

            if len(text) > max_length:
                text = text[:max_length] + f"\n\n... (内容截断，共 {len(text)} 字符，已显示前 {max_length} 字符)"

            return f"网页内容抓取成功 (URL: {url}):\n\n{text}"

        except urllib.error.HTTPError as e:
            return f"错误: HTTP {e.code} - {e.reason} (URL: {url})"
        except urllib.error.URLError as e:
            return f"错误: 无法连接 - {e.reason} (URL: {url})"
        except Exception as e:
            return f"错误: 抓取失败 - {str(e)} (URL: {url})"


class ToolBrowserOpen:
    """打开浏览器实例"""

    @property
    def definition(self):
        return {
            "name": "browser_open",
            "description": "打开一个浏览器实例，用于访问需要JavaScript渲染的网页或需要登录的网站。返回浏览器ID。默认使用Edge浏览器。",
            "parameters": {
                "browser_type": {
                    "type": "string",
                    "required": False,
                    "description": "浏览器类型：'edge'、'chrome'或'firefox'，默认edge（Windows系统自带）"
                }
            }
        }

    def execute(self, browser_type: str = "edge") -> str:
        try:
            from selenium import webdriver
            from selenium.webdriver.edge.options import Options as EdgeOptions
            from selenium.webdriver.chrome.options import Options as ChromeOptions
            from selenium.webdriver.firefox.options import Options as FirefoxOptions
            import uuid
            import atexit

            browser_id = str(uuid.uuid4())[:8]

            if browser_type.lower() == "firefox":
                options = FirefoxOptions()
                options.add_argument("--headless")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                driver = webdriver.Firefox(options=options)
            elif browser_type.lower() == "chrome":
                options = ChromeOptions()
                options.add_argument("--headless")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-gpu")
                options.add_argument("--window-size=1920,1080")
                driver = webdriver.Chrome(options=options)
            else:  # edge（默认）
                options = EdgeOptions()
                options.add_argument("--headless")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-gpu")
                options.add_argument("--window-size=1920,1080")
                driver = webdriver.Edge(options=options)

            # 设置超时，防止无限阻塞
            driver.set_page_load_timeout(30)
            driver.set_script_timeout(15)
            driver.implicitly_wait(5)

            # 存储浏览器实例
            if not hasattr(ToolBrowserOpen, '_browsers'):
                ToolBrowserOpen._browsers = {}
                # 注册退出清理钩子（仅首次）
                atexit.register(ToolBrowserOpen._cleanup_all_browsers)
            ToolBrowserOpen._browsers[browser_id] = driver

            return f"浏览器已打开 (ID: {browser_id})，类型: {browser_type}"

        except ImportError:
            return "错误: 未安装selenium，请运行 pip install selenium"
        except Exception as e:
            return f"错误: 打开浏览器失败 - {str(e)}"

    @staticmethod
    def _cleanup_all_browsers():
        """退出时清理所有浏览器实例"""
        if not hasattr(ToolBrowserOpen, '_browsers'):
            return
        for bid, drv in list(ToolBrowserOpen._browsers.items()):
            try:
                drv.quit()
            except Exception:
                pass
        ToolBrowserOpen._browsers.clear()


class ToolBrowserNavigate:
    """浏览器导航到URL"""

    @property
    def definition(self):
        return {
            "name": "browser_navigate",
            "description": "让浏览器导航到指定URL。用于访问网页、登录页面等。",
            "parameters": {
                "browser_id": {
                    "type": "string",
                    "required": True,
                    "description": "浏览器ID（由browser_open返回）"
                },
                "url": {
                    "type": "string",
                    "required": True,
                    "description": "要访问的URL"
                }
            }
        }

    def execute(self, browser_id: str, url: str) -> str:
        try:
            if not hasattr(ToolBrowserOpen, '_browsers') or browser_id not in ToolBrowserOpen._browsers:
                return f"错误: 未找到浏览器ID: {browser_id}"

            driver = ToolBrowserOpen._browsers[browser_id]
            driver.get(url)

            return f"已导航到: {url}\n页面标题: {driver.title}"

        except Exception as e:
            return f"错误: 导航失败 - {str(e)}"


class ToolBrowserLogin:
    """浏览器登录"""

    @property
    def definition(self):
        return {
            "name": "browser_login",
            "description": "在登录页面自动填写用户名和密码并提交。用于模拟登录网站。",
            "parameters": {
                "browser_id": {
                    "type": "string",
                    "required": True,
                    "description": "浏览器ID"
                },
                "username": {
                    "type": "string",
                    "required": True,
                    "description": "用户名/邮箱/手机号"
                },
                "password": {
                    "type": "string",
                    "required": True,
                    "description": "密码"
                },
                "username_selector": {
                    "type": "string",
                    "required": False,
                    "description": "用户名输入框的CSS选择器，默认自动检测"
                },
                "password_selector": {
                    "type": "string",
                    "required": False,
                    "description": "密码输入框的CSS选择器，默认自动检测"
                },
                "submit_selector": {
                    "type": "string",
                    "required": False,
                    "description": "登录按钮的CSS选择器，默认自动检测"
                }
            }
        }

    def execute(self, browser_id: str, username: str, password: str,
                username_selector: str = None, password_selector: str = None,
                submit_selector: str = None) -> str:
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            import time

            if not hasattr(ToolBrowserOpen, '_browsers') or browser_id not in ToolBrowserOpen._browsers:
                return f"错误: 未找到浏览器ID: {browser_id}"

            driver = ToolBrowserOpen._browsers[browser_id]

            # 自动检测用户名输入框
            if not username_selector:
                username_selectors = [
                    'input[name="username"]', 'input[name="email"]', 'input[name="phone"]',
                    'input[name="user"]', 'input[name="account"]', 'input[name="loginId"]',
                    'input[id="username"]', 'input[id="email"]', 'input[id="phone"]',
                    'input[type="text"]', 'input[type="email"]', 'input[type="tel"]',
                    '#loginId', '#email', '#username'
                ]
                for selector in username_selectors:
                    try:
                        element = driver.find_element(By.CSS_SELECTOR, selector)
                        if element.is_displayed():
                            username_selector = selector
                            break
                    except Exception:
                        continue

            # 自动检测密码输入框
            if not password_selector:
                password_selectors = [
                    'input[name="password"]', 'input[name="passwd"]', 'input[name="pwd"]',
                    'input[id="password"]', 'input[id="passwd"]', 'input[id="pwd"]',
                    'input[type="password"]', '#password', '#passwd'
                ]
                for selector in password_selectors:
                    try:
                        element = driver.find_element(By.CSS_SELECTOR, selector)
                        if element.is_displayed():
                            password_selector = selector
                            break
                    except Exception:
                        continue

            # 自动检测登录按钮
            if not submit_selector:
                submit_selectors = [
                    ('css', 'button[type="submit"]'), ('css', 'input[type="submit"]'),
                    ('xpath', '//button[contains(text(), "登录")]'),
                    ('xpath', '//button[contains(text(), "Login")]'),
                    ('xpath', '//button[contains(text(), "Sign in")]'),
                    ('css', '#login-btn'), ('css', '#submit-btn'), ('css', '.login-btn'), ('css', '.submit-btn'),
                    ('css', 'button.btn-primary'), ('css', 'button.login'), ('css', 'button.submit')
                ]
                for sel_type, selector in submit_selectors:
                    try:
                        if sel_type == 'xpath':
                            element = driver.find_element(By.XPATH, selector)
                        else:
                            element = driver.find_element(By.CSS_SELECTOR, selector)
                        if element.is_displayed():
                            submit_selector = selector
                            submit_sel_type = sel_type
                            break
                    except Exception:
                        continue

            if not username_selector:
                return "错误: 未找到用户名输入框，请手动指定username_selector"
            if not password_selector:
                return "错误: 未找到密码输入框，请手动指定password_selector"

            # 填写用户名
            username_field = driver.find_element(By.CSS_SELECTOR, username_selector)
            username_field.clear()
            username_field.send_keys(username)

            # 填写密码
            password_field = driver.find_element(By.CSS_SELECTOR, password_selector)
            password_field.clear()
            password_field.send_keys(password)

            # 点击登录按钮
            if submit_selector:
                try:
                    if submit_sel_type == 'xpath':
                        submit_btn = driver.find_element(By.XPATH, submit_selector)
                    else:
                        submit_btn = driver.find_element(By.CSS_SELECTOR, submit_selector)
                    submit_btn.click()
                except Exception:
                    # 尝试提交表单
                    password_field.submit()

            # 等待页面加载
            time.sleep(2)

            return f"登录操作已完成\n当前页面: {driver.title}\n当前URL: {driver.current_url}"

        except Exception as e:
            return f"错误: 登录失败 - {str(e)}"


class ToolBrowserGetContent:
    """获取浏览器页面内容"""

    @property
    def definition(self):
        return {
            "name": "browser_get_content",
            "description": "获取浏览器当前页面的内容。返回纯文本或HTML。",
            "parameters": {
                "browser_id": {
                    "type": "string",
                    "required": True,
                    "description": "浏览器ID"
                },
                "output_format": {
                    "type": "string",
                    "required": False,
                    "description": "返回格式：'text'（纯文本）或'html'（HTML源码），默认text"
                },
                "max_length": {
                    "type": "integer",
                    "required": False,
                    "description": "最大返回字符数，默认5000"
                }
            }
        }

    def execute(self, browser_id: str, output_format: str = "text", max_length: int = 5000) -> str:
        try:
            import re

            if not hasattr(ToolBrowserOpen, '_browsers') or browser_id not in ToolBrowserOpen._browsers:
                return f"错误: 未找到浏览器ID: {browser_id}"

            driver = ToolBrowserOpen._browsers[browser_id]

            if output_format.lower() == "html":
                content = driver.page_source
            else:
                # 获取纯文本
                content = driver.find_element("tag name", "body").text

            max_length = min(max(max_length or 5000, 500), 20000)
            if len(content) > max_length:
                content = content[:max_length] + f"\n\n... (内容截断，共 {len(content)} 字符，已显示前 {max_length} 字符)"

            return f"页面内容 (URL: {driver.current_url}):\n\n{content}"

        except Exception as e:
            return f"错误: 获取内容失败 - {str(e)}"


class ToolBrowserClick:
    """点击页面元素"""

    @property
    def definition(self):
        return {
            "name": "browser_click",
            "description": "点击页面上的元素。用于点击按钮、链接等。",
            "parameters": {
                "browser_id": {
                    "type": "string",
                    "required": True,
                    "description": "浏览器ID"
                },
                "selector": {
                    "type": "string",
                    "required": True,
                    "description": "元素的CSS选择器"
                }
            }
        }

    def execute(self, browser_id: str, selector: str) -> str:
        try:
            from selenium.webdriver.common.by import By
            import time

            if not hasattr(ToolBrowserOpen, '_browsers') or browser_id not in ToolBrowserOpen._browsers:
                return f"错误: 未找到浏览器ID: {browser_id}"

            driver = ToolBrowserOpen._browsers[browser_id]
            element = driver.find_element(By.CSS_SELECTOR, selector)
            element.click()

            time.sleep(1)

            return f"已点击元素: {selector}\n当前页面: {driver.title}"

        except Exception as e:
            return f"错误: 点击失败 - {str(e)}"


class ToolBrowserType:
    """在输入框中输入文本"""

    @property
    def definition(self):
        return {
            "name": "browser_type",
            "description": "在输入框中输入文本。",
            "parameters": {
                "browser_id": {
                    "type": "string",
                    "required": True,
                    "description": "浏览器ID"
                },
                "selector": {
                    "type": "string",
                    "required": True,
                    "description": "输入框的CSS选择器"
                },
                "text": {
                    "type": "string",
                    "required": True,
                    "description": "要输入的文本"
                },
                "clear_first": {
                    "type": "boolean",
                    "required": False,
                    "description": "是否先清空输入框，默认true"
                }
            }
        }

    def execute(self, browser_id: str, selector: str, text: str, clear_first: bool = True) -> str:
        try:
            from selenium.webdriver.common.by import By

            if not hasattr(ToolBrowserOpen, '_browsers') or browser_id not in ToolBrowserOpen._browsers:
                return f"错误: 未找到浏览器ID: {browser_id}"

            driver = ToolBrowserOpen._browsers[browser_id]
            element = driver.find_element(By.CSS_SELECTOR, selector)

            if clear_first:
                element.clear()

            element.send_keys(text)

            return f"已输入文本到: {selector}"

        except Exception as e:
            return f"错误: 输入失败 - {str(e)}"


class ToolBrowserClose:
    """关闭浏览器"""

    @property
    def definition(self):
        return {
            "name": "browser_close",
            "description": "关闭浏览器实例，释放资源。",
            "parameters": {
                "browser_id": {
                    "type": "string",
                    "required": True,
                    "description": "浏览器ID"
                }
            }
        }

    def execute(self, browser_id: str) -> str:
        try:
            if not hasattr(ToolBrowserOpen, '_browsers') or browser_id not in ToolBrowserOpen._browsers:
                return f"错误: 未找到浏览器ID: {browser_id}"

            driver = ToolBrowserOpen._browsers[browser_id]
            driver.quit()
            del ToolBrowserOpen._browsers[browser_id]

            return f"浏览器已关闭: {browser_id}"

        except Exception as e:
            return f"错误: 关闭浏览器失败 - {str(e)}"


class ToolBrowserScreenshot:
    """浏览器截图"""

    @property
    def definition(self):
        return {
            "name": "browser_screenshot",
            "description": "对浏览器当前页面截图并保存为图片文件。用于验证码识别、页面分析等场景。配合VL API可识别验证码。",
            "parameters": {
                "browser_id": {
                    "type": "string",
                    "required": True,
                    "description": "浏览器ID"
                },
                "save_path": {
                    "type": "string",
                    "required": False,
                    "description": "截图保存路径，默认保存到temp目录"
                },
                "element_selector": {
                    "type": "string",
                    "required": False,
                    "description": "指定元素的CSS选择器，只截图该元素（如验证码图片）"
                }
            }
        }

    def execute(self, browser_id: str, save_path: str = None, element_selector: str = None) -> str:
        try:
            import os
            import time
            from selenium.webdriver.common.by import By

            if not hasattr(ToolBrowserOpen, '_browsers') or browser_id not in ToolBrowserOpen._browsers:
                return f"错误: 未找到浏览器ID: {browser_id}"

            driver = ToolBrowserOpen._browsers[browser_id]

            if not save_path:
                temp_dir = os.path.join(os.path.expanduser("~"), ".qingke", "temp")
                os.makedirs(temp_dir, exist_ok=True)
                save_path = os.path.join(temp_dir, f"screenshot_{int(time.time())}.png")

            os.makedirs(os.path.dirname(save_path), exist_ok=True)

            if element_selector:
                element = driver.find_element(By.CSS_SELECTOR, element_selector)
                element.screenshot(save_path)
            else:
                driver.save_screenshot(save_path)

            return f"截图已保存到: {save_path}"

        except Exception as e:
            return f"错误: 截图失败 - {str(e)}"


class ToolBrowserWait:
    """浏览器等待"""

    @property
    def definition(self):
        return {
            "name": "browser_wait",
            "description": "等待指定时间或等待元素出现。用于等待页面加载、验证码出现等。",
            "parameters": {
                "browser_id": {
                    "type": "string",
                    "required": True,
                    "description": "浏览器ID"
                },
                "seconds": {
                    "type": "number",
                    "required": False,
                    "description": "等待秒数，默认2秒"
                },
                "wait_for_selector": {
                    "type": "string",
                    "required": False,
                    "description": "等待指定CSS选择器的元素出现"
                },
                "timeout": {
                    "type": "number",
                    "required": False,
                    "description": "等待元素的超时时间（秒），默认10秒"
                }
            }
        }

    def execute(self, browser_id: str, seconds: float = 2, wait_for_selector: str = None, timeout: float = 10) -> str:
        try:
            import time
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            if not hasattr(ToolBrowserOpen, '_browsers') or browser_id not in ToolBrowserOpen._browsers:
                return f"错误: 未找到浏览器ID: {browser_id}"

            driver = ToolBrowserOpen._browsers[browser_id]

            if wait_for_selector:
                try:
                    WebDriverWait(driver, timeout).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, wait_for_selector))
                    )
                    return f"元素已出现: {wait_for_selector}"
                except:
                    return f"等待超时: 元素 {wait_for_selector} 未在 {timeout} 秒内出现"
            else:
                time.sleep(seconds)
                return f"已等待 {seconds} 秒"

        except Exception as e:
            return f"错误: 等待失败 - {str(e)}"


class ToolBrowserExecuteScript:
    """执行JavaScript"""

    @property
    def definition(self):
        return {
            "name": "browser_execute_script",
            "description": "在浏览器中执行JavaScript代码。可用于模拟拖拽、复杂交互等。",
            "parameters": {
                "browser_id": {
                    "type": "string",
                    "required": True,
                    "description": "浏览器ID"
                },
                "script": {
                    "type": "string",
                    "required": True,
                    "description": "要执行的JavaScript代码"
                }
            }
        }

    def execute(self, browser_id: str, script: str) -> str:
        try:
            if not hasattr(ToolBrowserOpen, '_browsers') or browser_id not in ToolBrowserOpen._browsers:
                return f"错误: 未找到浏览器ID: {browser_id}"

            driver = ToolBrowserOpen._browsers[browser_id]
            result = driver.execute_script(script)

            return f"JavaScript执行完成\n返回值: {result}"

        except Exception as e:
            return f"错误: 执行JavaScript失败 - {str(e)}"


class ToolBrowserDrag:
    """模拟拖拽操作"""

    @property
    def definition(self):
        return {
            "name": "browser_drag",
            "description": "模拟拖拽操作。用于滑块验证码、拖拽排序等场景。",
            "parameters": {
                "browser_id": {
                    "type": "string",
                    "required": True,
                    "description": "浏览器ID"
                },
                "source_selector": {
                    "type": "string",
                    "required": True,
                    "description": "拖拽起点元素的CSS选择器"
                },
                "target_selector": {
                    "type": "string",
                    "required": False,
                    "description": "拖拽终点元素的CSS选择器（与target_offset二选一）"
                },
                "target_offset_x": {
                    "type": "integer",
                    "required": False,
                    "description": "相对于起点的X偏移像素（与target_selector二选一）"
                },
                "target_offset_y": {
                    "type": "integer",
                    "required": False,
                    "description": "相对于起点的Y偏移像素"
                }
            }
        }

    def execute(self, browser_id: str, source_selector: str, target_selector: str = None,
                target_offset_x: int = None, target_offset_y: int = None) -> str:
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.common.action_chains import ActionChains
            import time

            if not hasattr(ToolBrowserOpen, '_browsers') or browser_id not in ToolBrowserOpen._browsers:
                return f"错误: 未找到浏览器ID: {browser_id}"

            driver = ToolBrowserOpen._browsers[browser_id]
            source = driver.find_element(By.CSS_SELECTOR, source_selector)

            actions = ActionChains(driver)

            if target_selector:
                target = driver.find_element(By.CSS_SELECTOR, target_selector)
                actions.drag_and_drop(source, target).perform()
                return f"已拖拽从 {source_selector} 到 {target_selector}"
            elif target_offset_x is not None:
                offset_y = target_offset_y or 0
                actions.drag_and_drop_by_offset(source, target_offset_x, offset_y).perform()
                return f"已拖拽 {source_selector} 偏移 ({target_offset_x}, {offset_y}) 像素"
            else:
                return "错误: 必须指定target_selector或target_offset_x"

        except Exception as e:
            return f"错误: 拖拽失败 - {str(e)}"


class ToolBrowserFindElements:
    """查找页面元素"""

    @property
    def definition(self):
        return {
            "name": "browser_find_elements",
            "description": "查找页面上的元素，返回元素信息。用于分析页面结构、查找验证码位置等。",
            "parameters": {
                "browser_id": {
                    "type": "string",
                    "required": True,
                    "description": "浏览器ID"
                },
                "selector": {
                    "type": "string",
                    "required": True,
                    "description": "CSS选择器"
                },
                "max_results": {
                    "type": "integer",
                    "required": False,
                    "description": "最大返回数量，默认10"
                }
            }
        }

    def execute(self, browser_id: str, selector: str, max_results: int = 10) -> str:
        try:
            from selenium.webdriver.common.by import By

            if not hasattr(ToolBrowserOpen, '_browsers') or browser_id not in ToolBrowserOpen._browsers:
                return f"错误: 未找到浏览器ID: {browser_id}"

            driver = ToolBrowserOpen._browsers[browser_id]
            elements = driver.find_elements(By.CSS_SELECTOR, selector)

            max_results = min(max(max_results or 10, 1), 50)
            results = []
            for i, elem in enumerate(elements[:max_results]):
                info = {
                    "index": i,
                    "tag": elem.tag_name,
                    "text": elem.text[:100] if elem.text else "",
                    "displayed": elem.is_displayed(),
                    "enabled": elem.is_enabled(),
                    "location": elem.location,
                    "size": elem.size
                }
                results.append(info)

            if not results:
                return f"未找到匹配的元素: {selector}"

            output = f"找到 {len(results)} 个元素 (共 {len(elements)} 个):\n"
            for r in results:
                output += f"\n[{r['index']}] <{r['tag']}> 位置:{r['location']} 大小:{r['size']}\n"
                output += f"    显示:{r['displayed']} 可用:{r['enabled']}\n"
                if r['text']:
                    output += f"    文本: {r['text']}\n"

            return output

        except Exception as e:
            return f"错误: 查找元素失败 - {str(e)}"


class ToolTaskComplete:
    """标记任务完成"""

    @property
    def definition(self):
        return {
            "name": "task_complete",
            "description": "标记任务已完成，并提供完成摘要。当任务所有步骤都执行完毕后调用此工具。",
            "parameters": {
                "summary": {
                    "type": "string",
                    "required": True,
                    "description": "任务完成的摘要说明"
                }
            }
        }

    def execute(self, summary: str) -> str:
        return f"任务完成: {summary}"


class ToolSetReminder:
    """设置定时提醒"""

    def __init__(self, scheduler):
        self._scheduler = scheduler

    @property
    def definition(self):
        return {
            "name": "set_reminder",
            "description": "设置定时提醒。到时间后系统弹窗提醒用户。支持相对时间(如'5分钟后')和绝对时间(如'15:30'或'2026-06-30 15:30')。",
            "parameters": {
                "time": {
                    "type": "string",
                    "required": True,
                    "description": "提醒时间。相对: '5分钟后','2小时后','30min'。绝对: '15:30','下午3点','2026-06-30 10:00'"
                },
                "message": {
                    "type": "string",
                    "required": True,
                    "description": "提醒内容"
                },
                "title": {
                    "type": "string",
                    "required": False,
                    "description": "提醒标题，默认'青稞提醒'"
                }
            }
        }

    def execute(self, time: str, message: str, title: str = "青稞提醒") -> str:
        result = self._scheduler.set_reminder(time, message, title)
        if result.get("success"):
            return (f"提醒已设置！\n"
                    f"  ID: {result['reminder_id']}\n"
                    f"  时间: {result['trigger_time']}\n"
                    f"  内容: {message}")
        return f"设置失败: {result.get('error', '未知错误')}"


class ToolListReminders:
    """列出待触发提醒"""

    def __init__(self, scheduler):
        self._scheduler = scheduler

    @property
    def definition(self):
        return {
            "name": "list_reminders",
            "description": "列出所有待触发的定时提醒。",
            "parameters": {}
        }

    def execute(self) -> str:
        reminders = self._scheduler.list_reminders()
        if not reminders:
            return "当前没有待触发的提醒。"
        lines = [f"共 {len(reminders)} 个待触发提醒:\n"]
        for r in reminders:
            lines.append(f"  [{r['id']}] {r['trigger_time']} - {r['title']}: {r['message'][:60]}")
        return "\n".join(lines)


class ToolCancelReminder:
    """取消定时提醒"""

    def __init__(self, scheduler):
        self._scheduler = scheduler

    @property
    def definition(self):
        return {
            "name": "cancel_reminder",
            "description": "取消指定的定时提醒。",
            "parameters": {
                "reminder_id": {
                    "type": "string",
                    "required": True,
                    "description": "要取消的提醒ID"
                }
            }
        }

    def execute(self, reminder_id: str) -> str:
        result = self._scheduler.cancel_reminder(reminder_id)
        if result.get("success"):
            return result["message"]
        return f"取消失败: {result.get('error', '未知错误')}"


# ============================================================
# 桌面自动化工具（基于 pyautogui + Windows API）
# ============================================================

def _ensure_pyautogui():
    """确保 pyautogui 已导入，返回 (pyautogui, error_msg)"""
    try:
        import pyautogui
        pyautogui.FAILSAFE = True  # 鼠标移到左上角触发安全中断
        pyautogui.PAUSE = 0.1
        return pyautogui, None
    except ImportError:
        return None, "错误: 未安装pyautogui，请运行 pip install pyautogui"


def _list_windows_ctypes():
    """使用 Windows API 列出所有可见窗口，返回 [(hwnd, title, rect), ...]"""
    import ctypes
    import ctypes.wintypes

    user32 = ctypes.windll.user32
    results = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_callback(hwnd, lParam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if title and title not in ("Program Manager", "MSCTFIME UI", "Default IME"):
                    rect = ctypes.wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    results.append((hwnd, title, (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    user32.EnumWindows(enum_callback, 0)
    return results


class ToolDesktopOpenApp:
    """打开本地应用程序"""

    @property
    def definition(self):
        return {
            "name": "desktop_open_app",
            "description": "打开本地应用程序。可以通过可执行文件路径、文件夹路径、或系统命令（如 notepad, calc）打开。",
            "parameters": {
                "path": {
                    "type": "string",
                    "required": True,
                    "description": "应用程序路径、文件夹路径、或系统命令（如 'notepad', 'calc', 'mspaint', 'C:\\Program Files\\...'）"
                }
            }
        }

    def execute(self, path: str) -> str:
        import subprocess
        try:
            # 尝试 os.startfile（Windows 专用，支持文件/文件夹/程序）
            if os.path.exists(path):
                os.startfile(path)
                return f"已打开: {path}"
            # 尝试作为命令执行
            proc = subprocess.Popen(path, shell=True)
            return f"已启动命令: {path} (PID: {proc.pid})"
        except Exception as e:
            return f"错误: 打开失败 - {str(e)}"


class ToolDesktopListWindows:
    """列出所有打开的窗口"""

    @property
    def definition(self):
        return {
            "name": "desktop_list_windows",
            "description": "列出当前桌面上所有可见的窗口，返回窗口标题、位置和大小。用于了解当前桌面状态。",
            "parameters": {}
        }

    def execute(self) -> str:
        try:
            windows = _list_windows_ctypes()
            if not windows:
                return "当前没有可见窗口。"
            lines = [f"共 {len(windows)} 个可见窗口:\n"]
            for i, (hwnd, title, (l, t, r, b)) in enumerate(windows, 1):
                w, h = r - l, b - t
                lines.append(f"  [{i}] hwnd={hwnd} | {title}")
                lines.append(f"       位置: ({l},{t}) 大小: {w}x{h}")
            return "\n".join(lines)
        except Exception as e:
            return f"错误: 列出窗口失败 - {str(e)}"


class ToolDesktopFocusWindow:
    """聚焦指定窗口"""

    @property
    def definition(self):
        return {
            "name": "desktop_focus_window",
            "description": "激活并前置指定窗口。可以通过窗口标题（模糊匹配）或hwnd句柄指定。",
            "parameters": {
                "window": {
                    "type": "string",
                    "required": True,
                    "description": "窗口标题关键词（模糊匹配）或 hwnd 句柄（数字）"
                }
            }
        }

    def execute(self, window: str) -> str:
        import ctypes
        user32 = ctypes.windll.user32
        try:
            # 尝试作为 hwnd 数字
            hwnd = int(window)
            user32.SetForegroundWindow(hwnd)
            return f"已聚焦窗口 hwnd={hwnd}"
        except ValueError:
            pass

        # 模糊匹配窗口标题
        windows = _list_windows_ctypes()
        for hwnd, title, rect in windows:
            if window.lower() in title.lower():
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                return f"已聚焦窗口: {title} (hwnd={hwnd})"
        return f"错误: 未找到包含 '{window}' 的窗口"


class ToolDesktopCloseWindow:
    """关闭指定窗口"""

    @property
    def definition(self):
        return {
            "name": "desktop_close_window",
            "description": "关闭指定窗口。可以通过窗口标题（模糊匹配）或hwnd句柄指定。",
            "parameters": {
                "window": {
                    "type": "string",
                    "required": True,
                    "description": "窗口标题关键词（模糊匹配）或 hwnd 句柄（数字）"
                }
            }
        }

    def execute(self, window: str) -> str:
        import ctypes
        user32 = ctypes.windll.user32
        WM_CLOSE = 0x0010
        try:
            hwnd = int(window)
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return f"已发送关闭消息到 hwnd={hwnd}"
        except ValueError:
            pass

        windows = _list_windows_ctypes()
        for hwnd, title, rect in windows:
            if window.lower() in title.lower():
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                return f"已关闭窗口: {title} (hwnd={hwnd})"
        return f"错误: 未找到包含 '{window}' 的窗口"


class ToolDesktopScreenshot:
    """桌面截图"""

    def __init__(self):
        self._screenshot_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "logs", "screenshots")
        os.makedirs(self._screenshot_dir, exist_ok=True)

    @property
    def definition(self):
        return {
            "name": "desktop_screenshot",
            "description": "截取桌面屏幕截图。可截取全屏、指定窗口、或指定区域。返回截图文件路径。截图可用于VL模型分析界面布局。",
            "parameters": {
                "region": {
                    "type": "string",
                    "required": False,
                    "description": "截图区域。格式: 'x,y,width,height'（如 '100,200,800,600'）。不填则截取全屏。"
                },
                "window": {
                    "type": "string",
                    "required": False,
                    "description": "窗口标题关键词（模糊匹配），截取指定窗口。与region二选一。"
                }
            }
        }

    def execute(self, region: str = None, window: str = None) -> str:
        pyautogui, err = _ensure_pyautogui()
        if err:
            return err
        try:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            if window:
                # 截取指定窗口
                windows = _list_windows_ctypes()
                target = None
                for hwnd, title, (l, t, r, b) in windows:
                    if window.lower() in title.lower():
                        target = (l, t, r, b, title)
                        break
                if not target:
                    return f"错误: 未找到包含 '{window}' 的窗口"
                l, t, r, b, title = target
                # 裁剪到屏幕范围
                screen_w, screen_h = pyautogui.size()
                l = max(0, l)
                t = max(0, t)
                r = min(screen_w, r)
                b = min(screen_h, b)
                screenshot = pyautogui.screenshot(region=(l, t, r - l, b - t))
                filename = f"desktop_{timestamp}_{window.replace(' ', '_')}.png"
            elif region:
                # 截取指定区域
                parts = [int(x.strip()) for x in region.split(",")]
                if len(parts) != 4:
                    return "错误: region格式应为 'x,y,width,height'"
                screenshot = pyautogui.screenshot(region=tuple(parts))
                filename = f"desktop_{timestamp}_region.png"
            else:
                # 全屏截图
                screenshot = pyautogui.screenshot()
                filename = f"desktop_{timestamp}_fullscreen.png"

            filepath = os.path.join(self._screenshot_dir, filename)
            screenshot.save(filepath)
            return f"截图已保存: {filepath}"
        except Exception as e:
            return f"错误: 截图失败 - {str(e)}"


class ToolDesktopClick:
    """桌面点击操作"""

    @property
    def definition(self):
        return {
            "name": "desktop_click",
            "description": "在桌面指定坐标位置执行鼠标点击。坐标可通过截图分析或desktop_find_text获取。",
            "parameters": {
                "x": {
                    "type": "integer",
                    "required": True,
                    "description": "屏幕X坐标（像素）"
                },
                "y": {
                    "type": "integer",
                    "required": True,
                    "description": "屏幕Y坐标（像素）"
                },
                "button": {
                    "type": "string",
                    "required": False,
                    "description": "鼠标按键: 'left'（默认）、'right'、'middle'"
                },
                "clicks": {
                    "type": "integer",
                    "required": False,
                    "description": "点击次数: 1（默认）、2（双击）"
                }
            }
        }

    def execute(self, x: int, y: int, button: str = "left", clicks: int = 1) -> str:
        pyautogui, err = _ensure_pyautogui()
        if err:
            return err
        try:
            pyautogui.click(x, y, button=button, clicks=clicks)
            action = "双击" if clicks == 2 else ("右键点击" if button == "right" else "点击")
            return f"已在 ({x}, {y}) 执行{action}"
        except Exception as e:
            return f"错误: 点击失败 - {str(e)}"


class ToolDesktopTypeText:
    """输入文本"""

    @property
    def definition(self):
        return {
            "name": "desktop_type_text",
            "description": "在当前聚焦的输入框中输入文本。支持中英文。对于中文，建议使用clipboard粘贴方式。",
            "parameters": {
                "text": {
                    "type": "string",
                    "required": True,
                    "description": "要输入的文本内容"
                },
                "interval": {
                    "type": "number",
                    "required": False,
                    "description": "每个字符之间的间隔秒数，默认0.02"
                }
            }
        }

    def execute(self, text: str, interval: float = 0.02) -> str:
        pyautogui, err = _ensure_pyautogui()
        if err:
            return err
        try:
            # 检测是否包含中文字符
            has_chinese = any('\u4e00' <= c <= '\u9fff' for c in text)
            if has_chinese:
                # 中文使用剪贴板粘贴（Base64编码避免PowerShell注入）
                import subprocess, base64
                encoded = base64.b64encode(text.encode('utf-16-le')).decode('ascii')
                ps_cmd = f"[System.Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{encoded}')) | Set-Clipboard"
                subprocess.run(
                    ['powershell', '-NoProfile', '-Command', ps_cmd],
                    capture_output=True, timeout=5
                )
                pyautogui.hotkey('ctrl', 'v')
                return f"已通过粘贴方式输入 {len(text)} 个字符（含中文）"
            else:
                pyautogui.write(text, interval=interval)
                return f"已输入 {len(text)} 个字符"
        except Exception as e:
            return f"错误: 输入失败 - {str(e)}"


class ToolDesktopPressKey:
    """按键/快捷键"""

    @property
    def definition(self):
        return {
            "name": "desktop_press_key",
            "description": "按下键盘按键或快捷键组合。支持单个键和组合键。",
            "parameters": {
                "keys": {
                    "type": "string",
                    "required": True,
                    "description": "按键名称，多个用逗号分隔表示组合键。如 'enter', 'tab', 'ctrl,c', 'alt,f4', 'ctrl,shift,esc'。常用键: enter, tab, escape, space, backspace, delete, up, down, left, right, home, end, pageup, pagedown, f1-f12, win"
                }
            }
        }

    def execute(self, keys: str) -> str:
        pyautogui, err = _ensure_pyautogui()
        if err:
            return err
        try:
            key_list = [k.strip().lower() for k in keys.split(",")]
            if len(key_list) == 1:
                pyautogui.press(key_list[0])
                return f"已按下: {key_list[0]}"
            else:
                pyautogui.hotkey(*key_list)
                return f"已按下组合键: {'+'.join(key_list)}"
        except Exception as e:
            return f"错误: 按键失败 - {str(e)}"


class ToolDesktopScroll:
    """鼠标滚动"""

    @property
    def definition(self):
        return {
            "name": "desktop_scroll",
            "description": "在指定位置执行鼠标滚轮滚动。用于滚动页面内容。",
            "parameters": {
                "amount": {
                    "type": "integer",
                    "required": True,
                    "description": "滚动量。正数向上滚动，负数向下滚动。每次约滚动3行。"
                },
                "x": {
                    "type": "integer",
                    "required": False,
                    "description": "滚动位置X坐标，不填则在当前鼠标位置"
                },
                "y": {
                    "type": "integer",
                    "required": False,
                    "description": "滚动位置Y坐标，不填则在当前鼠标位置"
                }
            }
        }

    def execute(self, amount: int, x: int = None, y: int = None) -> str:
        pyautogui, err = _ensure_pyautogui()
        if err:
            return err
        try:
            if x is not None and y is not None:
                pyautogui.scroll(amount, x=x, y=y)
                return f"已在 ({x},{y}) 滚动 {amount} 格"
            else:
                pyautogui.scroll(amount)
                return f"已在当前鼠标位置滚动 {amount} 格"
        except Exception as e:
            return f"错误: 滚动失败 - {str(e)}"


class ToolDesktopDrag:
    """鼠标拖拽"""

    @property
    def definition(self):
        return {
            "name": "desktop_drag",
            "description": "从起点拖拽到终点。用于拖拽滑块、移动窗口等。",
            "parameters": {
                "from_x": {
                    "type": "integer",
                    "required": True,
                    "description": "起点X坐标"
                },
                "from_y": {
                    "type": "integer",
                    "required": True,
                    "description": "起点Y坐标"
                },
                "to_x": {
                    "type": "integer",
                    "required": True,
                    "description": "终点X坐标"
                },
                "to_y": {
                    "type": "integer",
                    "required": True,
                    "description": "终点Y坐标"
                },
                "duration": {
                    "type": "number",
                    "required": False,
                    "description": "拖拽持续时间（秒），默认0.5"
                }
            }
        }

    def execute(self, from_x: int, from_y: int, to_x: int, to_y: int, duration: float = 0.5) -> str:
        pyautogui, err = _ensure_pyautogui()
        if err:
            return err
        try:
            pyautogui.moveTo(from_x, from_y)
            pyautogui.drag(to_x - from_x, to_y - from_y, duration=duration)
            return f"已从 ({from_x},{from_y}) 拖拽到 ({to_x},{to_y})"
        except Exception as e:
            return f"错误: 拖拽失败 - {str(e)}"


class ToolDesktopFindText:
    """在屏幕上查找文字（OCR）"""

    @property
    def definition(self):
        return {
            "name": "desktop_find_text",
            "description": "使用OCR在屏幕截图中查找指定文字，返回文字所在的坐标位置。需要安装pytesseract和Tesseract-OCR。如果OCR不可用，建议先desktop_screenshot截图，然后用VL模型分析。",
            "parameters": {
                "text": {
                    "type": "string",
                    "required": True,
                    "description": "要查找的文字"
                },
                "region": {
                    "type": "string",
                    "required": False,
                    "description": "搜索区域，格式: 'x,y,width,height'，不填则搜索全屏"
                }
            }
        }

    def execute(self, text: str, region: str = None) -> str:
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            return "错误: 未安装pytesseract或Pillow。建议先用 desktop_screenshot 截图，然后用VL模型分析界面布局和坐标。"

        # 检查Tesseract-OCR引擎是否已安装
        try:
            pytesseract.get_tesseract_version()
        except Exception:
            return "错误: 未安装Tesseract-OCR引擎。请从 https://github.com/UB-Mannheim/tesseract/wiki 下载安装，并确保添加到PATH环境变量。"

        pyautogui, err = _ensure_pyautogui()
        if err:
            return err

        try:
            if region:
                parts = [int(x.strip()) for x in region.split(",")]
                screenshot = pyautogui.screenshot(region=tuple(parts))
                offset_x, offset_y = parts[0], parts[1]
            else:
                screenshot = pyautogui.screenshot()
                offset_x, offset_y = 0, 0

            # 使用 pytesseract 进行 OCR
            data = pytesseract.image_to_data(screenshot, lang='chi_sim+eng', output_type=pytesseract.Output.DICT)

            matches = []
            for i, word in enumerate(data['text']):
                if text.lower() in word.lower() and word.strip():
                    x = data['left'][i] + offset_x
                    y = data['top'][i] + offset_y
                    w = data['width'][i]
                    h = data['height'][i]
                    cx, cy = x + w // 2, y + h // 2
                    matches.append(f"'{word}' 在 ({cx},{cy})，区域: ({x},{y},{w},{h})")

            if matches:
                return f"找到 {len(matches)} 处匹配:\n" + "\n".join(f"  {m}" for m in matches[:10])
            else:
                return f"未在屏幕上找到文字 '{text}'。建议先 desktop_screenshot 截图，然后用VL模型分析。"
        except Exception as e:
            return f"错误: OCR查找失败 - {str(e)}。建议先 desktop_screenshot 截图，然后用VL模型分析。"


class ToolDesktopFindImage:
    """在屏幕上查找图像"""

    @property
    def definition(self):
        return {
            "name": "desktop_find_image",
            "description": "在屏幕中查找指定图像的位置。用于定位按钮、图标等UI元素。图像文件需预先保存。",
            "parameters": {
                "image_path": {
                    "type": "string",
                    "required": True,
                    "description": "要查找的图像文件路径（PNG格式）"
                },
                "confidence": {
                    "type": "number",
                    "required": False,
                    "description": "匹配置信度，0-1之间，默认0.8。值越低匹配越宽松。"
                }
            }
        }

    def execute(self, image_path: str, confidence: float = 0.8) -> str:
        pyautogui, err = _ensure_pyautogui()
        if err:
            return err

        if not os.path.exists(image_path):
            return f"错误: 图像文件不存在 - {image_path}"

        try:
            # 优先使用 OpenCV（更准确）
            try:
                import cv2
                import numpy as np
                screenshot = pyautogui.screenshot()
                screen_gray = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2GRAY)
                template = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
                if template is None:
                    return f"错误: 无法读取图像文件 - {image_path}"
                result = cv2.matchTemplate(screen_gray, template, cv2.TM_CCOEFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
                if max_val >= confidence:
                    h, w = template.shape
                    cx = max_loc[0] + w // 2
                    cy = max_loc[1] + h // 2
                    return f"找到匹配图像！位置: ({cx},{cy})，置信度: {max_val:.2f}，区域: ({max_loc[0]},{max_loc[1]},{w},{h})"
                else:
                    return f"未找到匹配图像（最高置信度: {max_val:.2f}，阈值: {confidence}）"
            except ImportError:
                # fallback: 使用 pyautogui 内置的图像匹配
                location = pyautogui.locateOnScreen(image_path, confidence=confidence)
                if location:
                    cx, cy = pyautogui.center(location)
                    return f"找到匹配图像！位置: ({cx},{cy})，区域: ({location.left},{location.top},{location.width},{location.height})"
                else:
                    return f"未找到匹配图像（置信度阈值: {confidence}）"
        except Exception as e:
            return f"错误: 图像查找失败 - {str(e)}"


class ToolDesktopGetCursorPos:
    """获取鼠标位置"""

    @property
    def definition(self):
        return {
            "name": "desktop_get_cursor_pos",
            "description": "获取当前鼠标光标的屏幕坐标。在截图分析后可用于确认元素位置。",
            "parameters": {}
        }

    def execute(self) -> str:
        pyautogui, err = _ensure_pyautogui()
        if err:
            return err
        try:
            x, y = pyautogui.position()
            return f"当前鼠标位置: ({x}, {y})"
        except Exception as e:
            return f"错误: 获取鼠标位置失败 - {str(e)}"


class ToolRestartApp:
    """重启青稞lite应用（重载前端页面，不关闭后端服务）"""

    @property
    def definition(self):
        return {
            "name": "restart_app",
            "description": "重启青稞lite应用。当用户要求重启、重新加载、刷新应用时使用。会重载前端页面并清理缓存。",
            "parameters": {}
        }

    def execute(self) -> str:
        import urllib.request
        import urllib.error
        # 尝试读取端口配置
        port = 2253
        try:
            import sys as _sys
            if getattr(_sys, 'frozen', False):
                config_dir = os.path.dirname(_sys.executable)
            else:
                config_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(config_dir, 'config', 'settings.yaml')
            if os.path.exists(config_path):
                import yaml
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = yaml.safe_load(f)
                port = cfg.get('flask', {}).get('port', 2253)
        except Exception:
            pass

        try:
            url = f"http://127.0.0.1:{port}/api/system/restart"
            req = urllib.request.Request(url, method='POST',
                                        headers={'Content-Type': 'application/json'},
                                        data=b'{}')
            resp = urllib.request.urlopen(req, timeout=5)
            return "重启信号已发送，青稞lite即将重载。"
        except Exception as e:
            return f"重启信号发送失败: {str(e)}，请手动刷新页面。"


# ============================================================
# 内置浏览器工具（TUI内置Chromium浏览器 + 网络捕获）
# ============================================================

class ToolBuiltinBrowserOpen:
    """打开TUI内置浏览器"""

    @property
    def definition(self):
        return {
            "name": "builtin_browser_open",
            "description": "打开TUI内置浏览器窗口。内置浏览器基于Chromium内核，可自动捕获所有网络请求和响应信息。适用于需要监控网络交互的网页操作场景。",
            "parameters": {
                "url": {
                    "type": "string",
                    "required": False,
                    "description": "初始加载的网址（可选，不填则打开空白页）"
                }
            }
        }

    def execute(self, url: str = '') -> str:
        from core.builtin_browser import request_open_browser
        result = request_open_browser(url=url)
        if result.get('success'):
            msg = "内置浏览器已打开"
            if url:
                msg += f"，正在加载 {url}"
            return msg
        return f"打开内置浏览器失败: {result.get('error', '未知错误')}"


class ToolBuiltinBrowserNavigate:
    """导航到指定URL"""

    @property
    def definition(self):
        return {
            "name": "builtin_browser_navigate",
            "description": "在内置浏览器中导航到指定URL。需要先用builtin_browser_open打开浏览器。",
            "parameters": {
                "url": {
                    "type": "string",
                    "required": True,
                    "description": "要导航到的网址"
                }
            }
        }

    def execute(self, url: str) -> str:
        from core.builtin_browser import send_command
        result = send_command('navigate', url=url)
        if result.get('success'):
            return f"正在导航到: {url}"
        return f"导航失败: {result.get('error', '未知错误')}"


class ToolBuiltinBrowserGetContent:
    """获取页面内容"""

    @property
    def definition(self):
        return {
            "name": "builtin_browser_get_content",
            "description": "获取内置浏览器当前页面的HTML内容。用于分析页面结构、提取文本信息等。",
            "parameters": {
                "max_length": {
                    "type": "integer",
                    "required": False,
                    "description": "返回内容的最大字符数，默认10000。过大的页面会截断。"
                }
            }
        }

    def execute(self, max_length: int = 10000) -> str:
        from core.builtin_browser import send_command
        result = send_command('get_content')
        if result.get('success'):
            html = result.get('html', '')
            if len(html) > max_length:
                html = html[:max_length] + f"\n\n... [内容已截断，总长度: {len(result.get('html', ''))} 字符]"
            return html if html else "页面内容为空"
        return f"获取内容失败: {result.get('error', '未知错误')}"


class ToolBuiltinBrowserScreenshot:
    """截取浏览器页面截图"""

    @property
    def definition(self):
        return {
            "name": "builtin_browser_screenshot",
            "description": "截取内置浏览器当前页面的截图并保存为PNG文件。用于视觉分析页面布局、验证码识别等。",
            "parameters": {}
        }

    def execute(self) -> str:
        import base64 as _b64
        from core.builtin_browser import send_command
        result = send_command('screenshot')
        if result.get('success'):
            b64_data = result.get('screenshot', '')
            if not b64_data:
                return "截图数据为空"
            # 保存到文件
            screenshot_dir = os.path.join(os.getcwd(), 'data', 'screenshots')
            os.makedirs(screenshot_dir, exist_ok=True)
            import time as _time
            filename = f"builtin_browser_{int(_time.time())}.png"
            filepath = os.path.join(screenshot_dir, filename)
            with open(filepath, 'wb') as f:
                f.write(_b64.b64decode(b64_data))
            return f"截图已保存: {filepath}"
        return f"截图失败: {result.get('error', '未知错误')}"


class ToolBuiltinBrowserGetNetwork:
    """获取捕获的网络日志"""

    @property
    def definition(self):
        return {
            "name": "builtin_browser_get_network",
            "description": "获取内置浏览器捕获的所有网络请求和响应日志。包括请求URL、方法、状态码、内容类型、响应时间等。用于分析网页的网络交互过程。",
            "parameters": {
                "since": {
                    "type": "number",
                    "required": False,
                    "description": "只返回此时间戳之后的日志（Unix时间戳）。不填则返回最近的500条。"
                },
                "limit": {
                    "type": "integer",
                    "required": False,
                    "description": "最多返回的日志条数，默认500。"
                }
            }
        }

    def execute(self, since: float = 0, limit: int = 500) -> str:
        from core.builtin_browser import get_network_logs
        logs = get_network_logs(since=since, limit=limit)
        if not logs:
            return "暂无网络日志记录。请先使用 builtin_browser_open 打开浏览器并访问网页。"

        # 格式化输出
        lines = [f"共 {len(logs)} 条网络日志:\n"]
        for i, log in enumerate(logs):
            direction = log.get('direction', 'unknown')
            url = log.get('url', '')
            method = log.get('method', '')

            if direction == 'request':
                res_type = log.get('resource_type', '')
                lines.append(f"  [{i+1}] REQUEST  {method} {url}  ({res_type})")
            elif direction == 'resp':
                status = log.get('status', '')
                ct = log.get('content_type', '')
                ms = log.get('duration_ms', 0)
                lines.append(f"  [{i+1}] RESPONSE {method} {url}  → {status}  {ct}  ({ms}ms)")
            elif direction == 'err':
                error = log.get('error', '')
                ms = log.get('duration_ms', 0)
                lines.append(f"  [{i+1}] ERROR    {method} {url}  → {error}  ({ms}ms)")

        return "\n".join(lines)


class ToolBuiltinBrowserExecuteJs:
    """在浏览器页面中执行JavaScript"""

    @property
    def definition(self):
        return {
            "name": "builtin_browser_execute_js",
            "description": "在内置浏览器当前页面中执行JavaScript代码并返回结果。可用于提取页面特定数据、操作DOM元素等。",
            "parameters": {
                "code": {
                    "type": "string",
                    "required": True,
                    "description": "要执行的JavaScript代码。可以使用return语句返回结果。"
                }
            }
        }

    def execute(self, code: str) -> str:
        from core.builtin_browser import send_command
        # 包装为立即执行函数，确保return生效
        wrapped = f"(function(){{ {code} }})()"
        result = send_command('execute_js', code=wrapped)
        if result.get('success'):
            val = result.get('result')
            if val is None:
                return "JavaScript执行完成（返回值为null）"
            return f"JavaScript执行结果: {val}"
        return f"JavaScript执行失败: {result.get('error', '未知错误')}"


class ToolBuiltinBrowserClose:
    """关闭内置浏览器"""

    @property
    def definition(self):
        return {
            "name": "builtin_browser_close",
            "description": "关闭内置浏览器窗口并释放资源。操作完成后应及时关闭。",
            "parameters": {}
        }

    def execute(self) -> str:
        from core.builtin_browser import send_command, is_browser_open
        if not is_browser_open():
            return "内置浏览器已处于关闭状态"
        result = send_command('close', timeout=5)
        if result.get('success'):
            return "内置浏览器已关闭"
        return f"关闭浏览器失败: {result.get('error', '未知错误')}"