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
        # 定时提醒工具
        if self._reminder_scheduler:
            self.register("set_reminder", ToolSetReminder(self._reminder_scheduler))
            self.register("list_reminders", ToolListReminders(self._reminder_scheduler))
            self.register("cancel_reminder", ToolCancelReminder(self._reminder_scheduler))

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
                results.append(f"... (显示前50条，共{len(results)}个匹配)")
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