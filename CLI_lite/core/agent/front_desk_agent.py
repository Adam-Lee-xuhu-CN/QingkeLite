"""前台接待Agent - 分发/快速答复/上下文筛选"""


class FrontDeskAgent:
    """前台接待Agent，负责快速分发与上下文筛选"""

    # 需要实际执行工具的操作关键词（必须走 Agentic Loop）
    _TOOL_REQUIRED_PATTERNS = [
        "查一下", "查看", "列出", "浏览", "搜索", "查找",
        "D盘", "C盘", "E盘", "F盘",
        "目录", "文件夹", "文件列表",
        "创建", "删除", "复制", "移动", "重命名",
        "运行", "执行", "安装", "构建", "测试",
        "读取文件", "写入文件", "编辑文件",
    ]

    def __init__(self, llm_gateway, context_mgr):
        self.llm = llm_gateway
        self.context_mgr = context_mgr

    def process(self, user_input: str, session_id: str) -> dict:
        """
        处理用户输入，所有请求统一走 DAG/Agentic Loop 执行
        返回: {
            "action": "need_dag" | "need_info",
            "tasks": list,
            "reason": str
        }
        """
        # 1. 快速检查（仅用于纯寒）
        quick_response = self._quick_check(user_input)
        if quick_response:
            return {"action": "direct_reply", "reply": quick_response, "dag_suggested": False}

        # 2. 信息补足判断
        missing = self._check_missing_info(user_input)
        if missing:
            return {"action": "need_info", "missing_info": missing, "dag_suggested": False}

        # 3. 所有请求统一走 DAG/Agentic Loop
        analysis = self.llm.analyze_task(user_input)
        return {
            "action": "need_dag",
            "dag_suggested": True,
            "tasks": analysis.get("tasks", ["执行用户请求"]),
            "reason": analysis.get("reason", "所有操作通过DAG执行")
        }

    def _requires_tool_execution(self, user_input: str) -> bool:
        """检查用户输入是否需要实际执行工具（而非LLM直接回答）"""
        lower_input = user_input.lower()
        for pattern in self._TOOL_REQUIRED_PATTERNS:
            if pattern in lower_input:
                return True
        return False

    def _quick_check(self, user_input: str) -> str | None:
        """快速检查：纯寒暄直接回复"""
        greetings = {
            "你好": "你好！我是青稞，有什么可以帮你的？不管是写代码、处理文件、分析数据还是其他任务，都可以交给我。",
            "hello": "Hello! I'm 青稞, your AI assistant. Feel free to ask me anything — coding, file management, data analysis, or any other tasks.",
            "hi": "Hi! I'm 青稞. What can I help you with today?",
            "谢谢": "不客气！如果还有其他需要，随时告诉我。比如后续的数据分析、代码优化等，我都可以帮忙。",
            "再见": "再见！下次有需要随时找我。",
            "bye": "Goodbye! Feel free to come back anytime.",
            "帮助": "我可以帮你完成各种任务，比如：\n1. 编写和执行代码\n2. 文件管理（创建、编辑、分析）\n3. 数据处理和分析\n4. 浏览器自动化操作\n5. 桌面应用操作\n6. 网页信息采集\n\n有什么想法直接告诉我就行！",
        }

        # 精确匹配
        stripped = user_input.strip()
        if stripped in greetings:
            return greetings[stripped]

        # 模糊匹配（包含关键词）——仅在输入很短且接近关键词时才匹配
        for key, reply in greetings.items():
            if key in stripped and len(stripped) <= len(key) + 4 and len(stripped) <= 10:
                return reply

        return None

    def _check_missing_info(self, user_input: str) -> list[str]:
        """信息补足判断：判断是否需要补充信息"""
        missing = []

        # 如果包含"创建项目"但没有指定技术栈
        if "创建项目" in user_input or "初始化项目" in user_input:
            tech_stacks = ["python", "java", "node", "react", "vue", "flask", "django", "spring"]
            if not any(stack in user_input.lower() for stack in tech_stacks):
                missing.append("项目技术栈（如Python/Java/Node.js等）")

        # 仅当"创建"出现在极短的模糊指令时才追问（如单独说"创建"）
        stripped = user_input.strip()
        if stripped == "创建" or stripped == "生成" or stripped == "删除" or stripped == "初始化":
            missing.append("具体名称或路径")

        return missing
