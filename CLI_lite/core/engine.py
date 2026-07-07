"""核心引擎 - LLM分析+DAG调度编排 + Agentic Loop自主执行"""
import os
import time
import yaml
import logging
import getpass
from typing import Optional

# 项目根目录：exe 所在目录（打包模式，可读写）或 engine.py 上一级（开发模式）
if getattr(__import__('sys'), 'frozen', False):
    # PyInstaller 打包模式：项目根目录指向 exe 所在目录（用户可读写）
    # 不能用 __file__ 推导，否则会指向 sys._MEIPASS 临时只读目录
    _PROJECT_ROOT = os.path.dirname(__import__('sys').executable)
else:
    # 开发模式：项目根目录为 engine.py 所在目录的上一级（CLI_lite/）
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 运行时目录：与项目根目录一致
_RUNTIME_DIR = _PROJECT_ROOT

logger = logging.getLogger('engine')


def _abs(rel_path: str) -> str:
    """将相对路径转换为基于项目根目录的绝对路径"""
    return os.path.join(_PROJECT_ROOT, rel_path)


def _scan_project_directory(root: str, max_depth: int = 2) -> str:
    """扫描项目目录结构，生成可读的目录树文本（含文件用途说明）"""
    # 目录用途说明
    dir_purpose = {
        'config': '配置文件目录（系统设置、提示词、技能定义）',
        'core': '核心引擎模块（引擎、LLM网关、上下文管理、Agent）',
        'core/agent': 'Agent模块（前台接待Agent）',
        'web': 'Web界面模块（路由、模板、静态资源）',
        'web/routes': 'Web路由（API接口、页面路由、SSE事件）',
        'web/templates': 'HTML模板文件',
        'web/static': '静态资源（CSS样式、JavaScript脚本）',
        'data': '数据存储目录（会话、日志、偏好）',
        'data/sessions': '会话记录存储（JSON格式的对话历史）',
        'data/logs': '系统日志存储（MD格式的执行日志、LLM对话记录、聊天历史）',
        'data/preferences': '用户偏好存储（JSON格式的偏好数据）',
        'dag': 'DAG任务管理目录（任务定义、调度）',
        'dag/dags': 'DAG任务定义文件（JSON格式）',
        'cli': 'CLI命令行模块',
        'dispatcher': '任务调度与执行模块',
        'skills': '技能定义文件（Markdown格式）',
        'skill': '扩展技能库（技能清单+技能子文件夹，由自我学习系统自动维护）',
        '青稞lite': '桌面应用打包相关（PyInstaller打包脚本）',
    }

    # 关键文件用途说明（用于告诉LLM如何自我优化）
    file_purpose = {
        'app.py': 'Flask应用入口，创建Web服务、初始化引擎、启动预检',
        'settings.yaml': '核心配置文件（LLM提供商、端口、上下文参数）',
        'sys_prompt.md': '系统提示词——你（LLM）的身份定义、行为规则、工作目录信息',
        'requirements.txt': 'Python依赖清单',
        'main.js': '前端主脚本（聊天交互、DAG渲染、文件上传、SSE事件处理）',
        'style.css': '前端样式（UI布局、DAG卡片、Markdown渲染样式）',
        'index.html': '前端HTML模板（页面结构、Tab导航）',
        'config.js': '前端配置管理脚本',
        'engine.py': '核心引擎——协调所有模块，处理用户请求，管理DAG执行',
        'llm_gateway.py': 'LLM网关——封装Dify和OpenAI API调用',
        'context_manager.py': '上下文管理器——构建对话上下文、检索历史、管理系统提示词',
        'agentic_loop.py': 'Agentic Loop——自主任务执行引擎（观察→思考→行动循环）',
        'tools.py': '工具注册表——定义LLM可调用的工具（文件操作、命令执行等）',
        'config_guard.py': '配置校验器——配置文件完整性检查、自动修复、备份恢复',
        'history_retriever.py': '历史检索器——关键词+向量混合检索历史对话',
        'preference_learner.py': '偏好学习器——从对话中提取用户偏好',
        'logger.py': '日志管理器——MD格式记录执行日志',
        'dag_parser.py': 'DAG解析器——解析DAG定义文件',
        'dag_scheduler.py': 'DAG调度器——调度和执行DAG任务',
        'task_executor.py': '任务执行器——执行Shell命令',
        'front_desk_agent.py': '前台接待Agent——分析用户意图，决定处理策略',
        'api.py': 'API路由——定义所有REST接口和SSE流式端点',
        'pages.py': '页面路由——HTML页面路由',
        'events.py': '事件路由——SSE事件推送路由',
        'PROJECT_STRUCTURE.md': '项目结构说明——供LLM参考如何自我优化',
    }

    lines = [f"应用根目录：{root}"]
    lines.append("")

    for depth in range(1, max_depth + 1):
        for dirpath, dirnames, filenames in os.walk(root):
            # 跳过隐藏目录和缓存目录
            dirnames[:] = [d for d in dirnames if not d.startswith('.') and d != '__pycache__' and d != 'node_modules']

            rel_dir = os.path.relpath(dirpath, root)
            current_depth = rel_dir.count(os.sep) + 1 if rel_dir != '.' else 0

            if current_depth >= depth:
                continue
            if current_depth < depth - 1:
                continue

            # 列出当前深度的子目录
            for d in sorted(dirnames):
                sub_rel = os.path.join(rel_dir, d) if rel_dir != '.' else d
                sub_path = os.path.join(dirpath, d)
                purpose = dir_purpose.get(sub_rel.replace('\\', '/'), '')
                indent = "  " * (depth - 1)

                # 统计子目录中的文件数
                file_count = 0
                try:
                    for _, _, files in os.walk(sub_path):
                        file_count += len(files)
                except OSError:
                    pass

                if purpose:
                    lines.append(f"{indent}- {d}/ - {purpose}（{file_count}个文件）")
                else:
                    lines.append(f"{indent}- {d}/（{file_count}个文件）")

    # 列出根目录下的关键文件（含用途说明）
    lines.append("")
    lines.append("根目录关键文件：")
    try:
        for f in sorted(os.listdir(root)):
            fpath = os.path.join(root, f)
            if os.path.isfile(fpath):
                ext = os.path.splitext(f)[1].lower()
                if ext in ('.py', '.yaml', '.yml', '.json', '.md', '.txt', '.bat', '.exe'):
                    size = os.path.getsize(fpath)
                    size_str = f"{size / 1024:.1f}KB" if size > 1024 else f"{size}B"
                    purpose = file_purpose.get(f, '')
                    if purpose:
                        lines.append(f"  - {f}（{size_str}）— {purpose}")
                    else:
                        lines.append(f"  - {f}（{size_str}）")
    except OSError:
        pass

    return "\n".join(lines)


class CoreEngine:
    """核心引擎，协调所有模块完成用户请求"""

    def __init__(self, config_path: str = "config/settings.yaml"):
        # 统一使用基于项目根目录的绝对路径
        if not os.path.isabs(config_path):
            config_path = _abs(config_path)
        self.config_path = config_path

        # 初始化配置防护器
        from core.config_guard import ConfigGuard
        self.config_guard = ConfigGuard(self.config_path, _PROJECT_ROOT)

        # 记录配置文件修改时间，用于热重载检测
        self._config_mtime = self._get_config_mtime()

        # 使用 ConfigGuard 加载配置（自动校验 + 默认值回退 + 备份恢复）
        self.config = self.config_guard.load_and_validate_config()

        # 将所有配置中的相对路径转换为绝对路径（确保读写一致）
        self._resolve_config_paths()

        # 获取当前系统登录用户名（用于 Dify 等 API 调用的 user 标识）
        self.username = self._detect_username()

        # 初始化各模块
        from core.llm_gateway import LLMGateway
        from core.context_manager import ContextManager
        from core.agent.front_desk_agent import FrontDeskAgent
        from core.preference_learner import PreferenceLearner
        from core.skill_manager import SkillManager
        from core.logger import Logger
        from core.agentic_loop import AgenticLoop
        from core.reminder_scheduler import ReminderScheduler
        from dag.dag_parser import DAGParser
        from dag.dag_scheduler import DAGScheduler
        from dispatcher.task_executor import TaskExecutor
        from dag.dag_scheduler import DispatchScheduler

        # 日志
        self.logger = Logger(
            log_dir=self.config.get("logging", {}).get("log_dir", _abs("data/logs")),
            level=self.config.get("logging", {}).get("level", "DEBUG")
        )

        # LLM网关（传入用户名，用于 Dify API 调用）
        self.llm = LLMGateway(self.config.get("llm", {}), username=self.username)

        # 上下文管理器
        self.context_mgr = ContextManager(
            self.config.get("context", {}),
            llm_gateway=self.llm
        )

        # 前台Agent
        self.front_desk = FrontDeskAgent(self.llm, self.context_mgr)

        # DAG解析器
        self.dag_parser = DAGParser(
            self.config.get("cli", {}).get("dag_dir", _abs("dag/dags"))
        )

        # 任务执行器
        self.executor = TaskExecutor(
            shell=self.config.get("cli", {}).get("shell", "powershell"),
            timeout=self.config.get("cli", {}).get("timeout", 30)
        )

        # 调度中心
        self.scheduler = DispatchScheduler(
            config=self.config.get("dispatcher", {}),
            executor=self.executor,
            dag_parser=self.dag_parser,
            llm_gateway=self.llm
        )

        # 技能管理器
        self.skill_manager = SkillManager(
            llm_gateway=self.llm,
            skill_dir=_abs("skill"),
            logger_instance=self.logger
        )

        # 偏好学习器
        self.pref_learner = PreferenceLearner(
            self.config.get("preference", {}),
            self.llm,
            self.logger,
            self.context_mgr,
            self.skill_manager
        )

        # 定时提醒调度器
        self.reminder_scheduler = ReminderScheduler(
            data_dir=_abs("data")
        )
        self.reminder_scheduler.start()

        # Agentic Loop（自主执行引擎）
        self.agentic_loop = AgenticLoop(
            llm_gateway=self.llm,
            context_mgr=self.context_mgr,
            config=self.config.get("agentic_loop", {}),
            reminder_scheduler=self.reminder_scheduler
        )

        # 启动时自动更新系统提示词中的应用目录信息
        self._update_sys_prompt_directory()

    @staticmethod
    def _detect_username() -> str:
        """获取当前系统登录用户名（优先从C盘用户目录推断）"""
        try:
            # 方法1：通过 C:\Users\ 下的目录匹配当前用户
            users_dir = r"C:\Users"
            if os.path.isdir(users_dir):
                # getpass.getuser() 在某些环境下可能返回不准确的值
                # 但结合 C:\Users 目录可以更可靠地获取实际登录用户
                current = getpass.getuser()
                user_profile = os.path.join(users_dir, current)
                if os.path.isdir(user_profile):
                    return current
                # 如果 getpass 返回的不在 C:\Users 下，尝试 USERNAME 环境变量
                env_user = os.environ.get('USERNAME', '')
                if env_user and os.path.isdir(os.path.join(users_dir, env_user)):
                    return env_user
            # 方法2：环境变量回退
            return os.environ.get('USERNAME', getpass.getuser())
        except Exception:
            return '青稞'

    def _update_sys_prompt_directory(self):
        """扫描项目目录并更新系统提示词中的[app_directory]和[runtime_directory]区域（仅内容变化时才写入）"""
        sys_prompt_file = self.config.get("context", {}).get("system_prompt_file", "")
        if not sys_prompt_file or not os.path.exists(sys_prompt_file):
            return

        try:
            with open(sys_prompt_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # 扫描项目目录
            dir_info = _scan_project_directory(_PROJECT_ROOT, max_depth=2)

            # 构建运行时目录信息
            is_packaged = getattr(__import__('sys'), 'frozen', False)
            runtime_info_lines = [
                f"运行时目录：{_RUNTIME_DIR}",
                f"运行模式：{'exe打包模式' if is_packaged else '源码开发模式'}",
                f"系统用户：{self.username}",
                f"项目根目录：{_PROJECT_ROOT}",
            ]
            runtime_info = "\n".join(runtime_info_lines)

            changed = False

            # 替换 [app_directory_start] ... [app_directory_end] 区域
            for start_tag, end_tag, new_section in [
                ("[app_directory_start]", "[app_directory_end]", dir_info),
                ("[runtime_directory_start]", "[runtime_directory_end]", runtime_info),
            ]:
                if start_tag in content and end_tag in content:
                    start_idx = content.index(start_tag) + len(start_tag)
                    end_idx = content.index(end_tag)
                    old_section = content[start_idx:end_idx].strip()
                    if old_section != new_section.strip():
                        content = content[:start_idx] + "\n" + new_section + "\n" + content[end_idx:]
                        changed = True
                        logger.info(f"系统提示词中 {start_tag} 区域已更新")

            if changed:
                with open(sys_prompt_file, 'w', encoding='utf-8') as f:
                    f.write(content)
                # 重新加载系统提示
                self.context_mgr.system_prompt = content
                logger.info("系统提示词已更新并重新加载")
            else:
                logger.debug("系统提示词目录信息无变化，跳过更新")
        except Exception as e:
            logger.warning(f"更新系统提示词目录信息失败: {e}")

    def _get_config_mtime(self) -> float:
        """获取配置文件修改时间"""
        try:
            return os.path.getmtime(self.config_path)
        except OSError:
            return 0

    def _check_and_reload_config(self):
        """检查配置文件是否被修改，如果是则热重载（带校验）"""
        current_mtime = self._get_config_mtime()
        if current_mtime != self._config_mtime:
            try:
                # 使用 ConfigGuard 加载并校验（自动合并默认值 + 类型修正）
                new_config = self.config_guard.load_and_validate_config()

                # 检查关键字段是否存在（防止校验后仍然缺失）
                if not new_config.get("llm"):
                    logger.error("热重载失败: 配置缺少 llm 段，保留旧配置")
                    return

                # 备份当前配置后再覆盖
                old_config = self.config
                self.config = new_config
                self._resolve_config_paths()

                try:
                    # 将新配置同步到 LLM 网关
                    self.llm.update_config(self.config.get("llm", {}))
                    # 更新 Agentic Loop 配置
                    self.agentic_loop.config = self.config.get("agentic_loop", {})
                    # 更新卡点检测超时
                    self.agentic_loop._stuck_detection_timeout = self.agentic_loop.config.get(
                        "stuck_detection_timeout", 300
                    )
                except Exception as sync_err:
                    # 同步失败，回滚到旧配置
                    logger.error(f"配置同步到模块失败，回滚: {sync_err}")
                    self.config = old_config
                    self._resolve_config_paths()
                    return

                self._config_mtime = current_mtime
                logger.info("配置热重载成功")
            except Exception as e:
                logger.error(f"配置热重载异常，保留旧配置: {e}")

    def _resolve_config_paths(self):
        """将配置中所有相对路径统一转换为基于项目根目录的绝对路径"""
        # context 配置
        ctx = self.config.setdefault("context", {})
        if "system_prompt_file" in ctx:
            ctx["system_prompt_file"] = _abs(ctx["system_prompt_file"]) if not os.path.isabs(ctx["system_prompt_file"]) else ctx["system_prompt_file"]
        if "keyword_dict_file" in ctx:
            ctx["keyword_dict_file"] = _abs(ctx["keyword_dict_file"]) if not os.path.isabs(ctx["keyword_dict_file"]) else ctx["keyword_dict_file"]
        if "session_dir" in ctx:
            ctx["session_dir"] = _abs(ctx["session_dir"]) if not os.path.isabs(ctx["session_dir"]) else ctx["session_dir"]

        # preference 配置
        pref = self.config.setdefault("preference", {})
        if "preference_file" in pref:
            pref["preference_file"] = _abs(pref["preference_file"]) if not os.path.isabs(pref["preference_file"]) else pref["preference_file"]
        if "preference_history_dir" in pref:
            pref["preference_history_dir"] = _abs(pref["preference_history_dir"]) if not os.path.isabs(pref["preference_history_dir"]) else pref["preference_history_dir"]
        if "sys_prompt_file" in pref:
            pref["sys_prompt_file"] = _abs(pref["sys_prompt_file"]) if not os.path.isabs(pref["sys_prompt_file"]) else pref["sys_prompt_file"]

        # logging 配置
        log_cfg = self.config.setdefault("logging", {})
        if "log_dir" in log_cfg:
            log_cfg["log_dir"] = _abs(log_cfg["log_dir"]) if not os.path.isabs(log_cfg["log_dir"]) else log_cfg["log_dir"]

        # cli 配置
        cli_cfg = self.config.setdefault("cli", {})
        if "dag_dir" in cli_cfg:
            cli_cfg["dag_dir"] = _abs(cli_cfg["dag_dir"]) if not os.path.isabs(cli_cfg["dag_dir"]) else cli_cfg["dag_dir"]

    def process(self, user_input: str, session_id: str) -> dict:
        """处理用户输入，所有请求统一走 DAG/Agentic Loop 执行"""
        import datetime

        # 热重载：检测配置文件是否被修改
        self._check_and_reload_config()

        today = datetime.datetime.now().strftime("%Y-%m-%d")

        # 1. 前台接待Agent处理
        agent_result = self.front_desk.process(user_input, session_id)

        # 2. 记录会话开始
        self.logger.log_session_start(session_id, user_input, today)
        self.logger.log_agent_analysis(agent_result["action"], agent_result, today)

        # 3. 所有请求统一走 DAG/Agentic Loop
        if agent_result["action"] == "need_info":
            missing = agent_result.get("missing_info", [])
            response = f"需要补充以下信息：{', '.join(missing)}"
            self.context_mgr.save_conversation(session_id, user_input, response)
            self.pref_learner.record_turn(user_input, response)
            self.logger.log_final_response(response, today)
            return {"response": response, "need_dag": False}

        # need_dag 或 direct_reply 都走 Agentic Loop
        use_agentic = self.config.get("agentic_loop", {}).get("enabled", True)
        if use_agentic:
            return self._handle_agentic_flow(user_input, session_id, agent_result, today)
        else:
            return self._handle_dag_flow(user_input, session_id, agent_result, today)

    def _handle_agentic_flow(self, user_input: str, session_id: str, agent_result: dict, date: str) -> dict:
        """使用 Agentic Loop 自主执行复杂任务，并将执行记录保存为 DAG"""
        import uuid as _uuid
        steps = []
        final_response = ""

        for step in self.agentic_loop.run(user_input, session_id):
            steps.append(step)
            if step.get("type") == "dag_node_complete":
                final_response = step.get("result", "")

        # 保存对话
        self.context_mgr.save_conversation(session_id, user_input, final_response)

        # 记录对话轮次
        self.pref_learner.record_turn(user_input, final_response)
        triggered = self.pref_learner.round_count % 10 == 0
        self.logger.log_turn(self.pref_learner.round_count, triggered, date)
        self.logger.log_final_response(final_response, date)

        # 记录Agentic Loop执行日志
        self.logger.log_agentic_loop(session_id, steps, date)

        # 将 agentic loop 的执行步骤保存为 DAG 记录（供 DAG 模块展示）
        self._save_agentic_as_dag(session_id, user_input, steps)

        return {
            "response": final_response,
            "need_dag": True,
            "agentic_steps": steps,
            "step_count": len(steps),
            "mode": "agentic_loop"
        }

    def _save_agentic_as_dag(self, session_id: str, user_input: str, steps: list):
        """将 Agentic Loop 的执行步骤保存为 DAG JSON 文件，供 DAG 模块展示"""
        import json
        dag_id = f"agentic_{session_id}"
        dag_dir = self.config.get("cli", {}).get("dag_dir", _abs("dag/dags"))
        os.makedirs(dag_dir, exist_ok=True)
        file_path = os.path.join(dag_dir, f"{dag_id}.json")

        # 从步骤中提取 DAG 节点
        nodes = {}
        node_counter = 0
        current_node = None
        status = "completed"

        for step in steps:
            t = step.get("type", "")
            if t == "dag_node_start":
                node_counter += 1
                nid = f"node_{node_counter}"
                current_node = {
                    "id": nid,
                    "name": step.get("name", ""),
                    "description": "",
                    "command": step.get("command", ""),
                    "dependencies": [f"node_{node_counter - 1}"] if node_counter > 1 else [],
                    "status": "running",
                    "result": None,
                    "retry_count": 0,
                    "start_time": time.time(),
                    "end_time": None,
                }
                nodes[nid] = current_node
            elif t == "dag_node_complete":
                if current_node:
                    current_node["status"] = step.get("status", "completed")
                    current_node["result"] = step.get("result", "")
                    current_node["end_time"] = time.time()
                    if step.get("status") == "failed":
                        status = "failed"

        dag_data = {
            "id": dag_id,
            "name": user_input[:60],
            "description": f"Agentic Loop 执行记录 - {session_id}",
            "nodes": nodes,
            "created_at": time.time(),
            "status": status,
        }

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(dag_data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            pass

    def _incremental_save(self, session_id: str, user_input: str,
                          steps: list, latest_event: dict, date: str):
        """每个 DAG 节点完成后即时保存增量数据，确保程序意外退出时不丢失已有记录。
        
        包括：
        1. 即时追加当前节点到执行日志（MD格式）
        2. 即时保存会话文件（JSON格式，含所有已完成节点）
        3. 即时保存 DAG 记录文件（JSON格式）
        """
        try:
            # 1. 即时追加当前节点到执行日志
            if latest_event.get("type") == "dag_node_complete":
                node_info = (
                    f"\n### 节点 {latest_event.get('index', '?')}: {latest_event.get('name', '')}\n"
                    f"- 状态: {latest_event.get('status', '')}\n"
                    f"- 完成时间: {latest_event.get('completed_at', '')}\n"
                    f"- 结果: {str(latest_event.get('result', ''))[:300]}\n"
                )
                self.logger._append(node_info, date)

            # 2. 即时保存会话文件（增量版本，记录当前所有已完成节点的结果）
            completed_results = []
            for s in steps:
                if s.get("type") == "dag_node_complete":
                    completed_results.append(
                        f"[{s.get('name', '')}] {str(s.get('result', ''))[:200]}"
                    )
            interim_response = "\n".join(completed_results) if completed_results else "执行中..."
            self.context_mgr.save_conversation(
                session_id, user_input,
                f"（执行中，已完成{len(completed_results)}个节点）\n{interim_response}"
            )

            # 3. 即时保存 DAG 记录文件
            self._save_agentic_as_dag(session_id, user_input, steps)

        except Exception:
            pass  # 增量保存失败不影响主流程

    def _handle_dag_flow(self, user_input: str, session_id: str, agent_result: dict, date: str) -> dict:
        """处理需要DAG的流程"""
        import uuid
        dag_id = f"dag_{uuid.uuid4().hex[:8]}"
        tasks = agent_result.get("tasks", [])

        result = self.scheduler.submit_dag(dag_id, tasks, user_input)

        if not result.get("success", False):
            # submit失败，提前返回错误
            error_msg = f"DAG提交失败: {result.get('error', '未知错误')}"
            self.logger.log_error(error_msg, date)
            return {"response": error_msg, "need_dag": True, "dag_id": dag_id}

        # 2. 执行DAG
        node_results = []
        try:
            for idx, node in enumerate(self.scheduler.run_dag(dag_id), 1):
                duration = 0
                if node.start_time and node.end_time:
                    duration = round(node.end_time - node.start_time, 2)
                node_results.append({
                    "index": idx,
                    "name": node.name,
                    "command": node.command,
                    "status": node.status,
                    "result": node.result,
                    "duration": duration,
                })
        except Exception as e:
            self.logger.log_error(str(e), date)
            return {"response": f"DAG执行出错: {str(e)}", "need_dag": True, "dag_id": dag_id}

        self.logger.log_dag_execution(dag_id, node_results, date)

        completed = sum(1 for n in node_results if n["status"] == "completed")
        failed = sum(1 for n in node_results if n["status"] == "failed")
        response = f"DAG执行完成：{completed}个节点成功，{failed}个节点失败。"

        self.context_mgr.save_conversation(session_id, user_input, response, dag_id)
        self.pref_learner.record_turn(user_input, response, dag_id)
        triggered = self.pref_learner.round_count % 10 == 0
        self.logger.log_turn(self.pref_learner.round_count, triggered, date)
        self.logger.log_final_response(response, date)

        return {
            "response": response,
            "need_dag": True,
            "dag_id": dag_id,
            "node_results": node_results
        }
