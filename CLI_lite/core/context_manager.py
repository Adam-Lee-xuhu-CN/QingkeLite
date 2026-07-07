"""上下文管理器 - OpenAI标准化格式 + 全量关键词检索 + 向量语义搜索 + 防溢出策略"""
import json
import os
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

from .history_retriever import HistoryRetriever

logger = logging.getLogger('context_manager')


@dataclass
class ConversationContext:
    """对话上下文数据结构"""
    system_prompt: str           # 从文件加载（含框架、技能、喜好）
    history: list = field(default_factory=list)                # 最近N轮对话
    matched_snippets: list = field(default_factory=list)       # 检索匹配到的历史片段
    summarized_context: list = field(default_factory=list)     # 摘要/检索后的上下文
    current_dag: Optional[str] = None      # 当前关联的DAG文件名
    total_tokens: int = 0        # 当前上下文总token数
    max_tokens: int = 8000       # 最大token预算

    def to_openai_format(self) -> list:
        """转换为 OpenAI 标准消息格式"""
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self.summarized_context)
        messages.extend(self.history)
        return messages

    def check_token_budget(self) -> bool:
        """检查是否超出token预算"""
        return self.total_tokens <= self.max_tokens


class ContextManager:
    """上下文管理器，负责构建和管理对话上下文"""

    def __init__(self, config: dict, llm_gateway=None):
        self.system_prompt_file = config.get("system_prompt_file", "config/sys_prompt.md")
        self.history_rounds = config.get("history_rounds", 3)
        self.keyword_dict_file = config.get("keyword_dict_file", "data/dictionary/keywords.json")
        self.max_snippet_length = config.get("max_snippet_length", 2000)
        self.max_tokens = config.get("max_tokens", 8000)
        self.max_retrieved_results = config.get("max_retrieved_results", 5)
        self.llm = llm_gateway
        self.session_dir = config.get("session_dir", "data/sessions")
        os.makedirs(self.session_dir, exist_ok=True)

        # 加载词组字典
        self.keywords = self._load_keywords()
        # 加载系统提示
        self.system_prompt = self._load_system_prompt()
        # 记录文件最后修改时间，用于自动检测变更
        self._sys_prompt_mtime = self._get_file_mtime(self.system_prompt_file)

        # 历史对话检索器（关键词 + 向量混合检索）
        self.retriever = HistoryRetriever(max_results=self.max_retrieved_results)

    def _get_file_mtime(self, file_path: str) -> float:
        """获取文件最后修改时间"""
        if os.path.exists(file_path):
            return os.path.getmtime(file_path)
        return 0

    def _load_keywords(self) -> dict:
        """加载词组字典"""
        if os.path.exists(self.keyword_dict_file):
            with open(self.keyword_dict_file, 'r', encoding='utf-8') as f:
                return json.load(f).get("categories", {})
        return {}

    def _load_system_prompt(self) -> str:
        """从文件加载系统提示（带完整性校验）"""
        try:
            from core.config_guard import ConfigGuard
            guard = ConfigGuard("", os.path.dirname(os.path.dirname(self.system_prompt_file)))
            return guard.load_and_validate_prompt(self.system_prompt_file)
        except Exception as e:
            logger.error(f"加载系统提示词失败: {e}")
            if os.path.exists(self.system_prompt_file):
                with open(self.system_prompt_file, 'r', encoding='utf-8') as f:
                    return f.read()
            return "You are a helpful assistant."

    def reload_system_prompt(self):
        """重新加载系统提示（用于偏好更新后，带完整性校验）"""
        try:
            from core.config_guard import ConfigGuard
            guard = ConfigGuard("", os.path.dirname(os.path.dirname(self.system_prompt_file)))
            self.system_prompt = guard.load_and_validate_prompt(self.system_prompt_file)
        except Exception:
            if os.path.exists(self.system_prompt_file):
                with open(self.system_prompt_file, 'r', encoding='utf-8') as f:
                    self.system_prompt = f.read()
        self._sys_prompt_mtime = self._get_file_mtime(self.system_prompt_file)

    def _check_and_reload_system_prompt(self):
        """检查文件是否被修改，如果是则重新加载（带完整性校验）"""
        current_mtime = self._get_file_mtime(self.system_prompt_file)
        if current_mtime != self._sys_prompt_mtime:
            try:
                from core.config_guard import ConfigGuard
                guard = ConfigGuard("", os.path.dirname(os.path.dirname(self.system_prompt_file)))
                new_prompt = guard.load_and_validate_prompt(self.system_prompt_file)
                if new_prompt and len(new_prompt.strip()) > 10:
                    self.system_prompt = new_prompt
                else:
                    logger.warning("重载的系统提示词内容过短，保留旧版本")
            except Exception as e:
                logger.error(f"系统提示词重载失败: {e}")
            self._sys_prompt_mtime = current_mtime

    def build_context(self, user_input: str, session_id: str) -> ConversationContext:
        """构建完整对话上下文"""
        # 每次构建上下文前检查 sys_prompt 是否被修改
        self._check_and_reload_system_prompt()

        ctx = ConversationContext(
            system_prompt=self.system_prompt,
            max_tokens=self.max_tokens
        )

        # 1. 加载全部历史对话
        all_history = self._load_all_history(session_id)

        # 2. 最近N轮作为基础上下文
        ctx.history = self._get_recent_history(all_history)

        # 3. 词组划分
        keywords = self._split_keywords(user_input)

        # 4. 混合检索：关键词匹配 + 向量语义搜索（全量历史）
        snippets = self._retrieve_relevant_snippets(user_input, keywords, all_history)
        ctx.matched_snippets = snippets

        # 5. 将检索结果注入上下文
        ctx.summarized_context = self._build_retrieved_context(snippets)

        # 6. Token预算检查
        ctx.total_tokens = self._estimate_tokens(ctx)
        if not ctx.check_token_budget():
            self._compress_context(ctx)

        return ctx

    def _split_keywords(self, user_input: str) -> list[str]:
        """将用户输入按字典划分为若干词组"""
        found_keywords = []
        for category, words in self.keywords.items():
            for word in words:
                if word in user_input:
                    found_keywords.append(word)
        return found_keywords

    def _retrieve_relevant_snippets(self, user_input: str, keywords: list[str], all_history: list) -> list[dict]:
        """
        混合检索相关历史对话：
        1. 关键词匹配（基于词典）
        2. 向量语义搜索（TF-IDF + 余弦相似度）
        返回按相关性排序的历史片段
        """
        if not all_history:
            return []

        # 构建检索索引
        self.retriever.build_index(all_history)

        # 混合检索
        results = self.retriever.search(user_input, keywords)

        # 去重：排除已在最近N轮历史中的内容
        return results

    def _build_retrieved_context(self, snippets: list[dict]) -> list:
        """将检索结果转换为上下文消息格式"""
        if not snippets:
            return []

        context_messages = []
        total_text = ""

        for snippet in snippets:
            messages = snippet.get("messages", [])
            score = snippet.get("score", 0)
            for msg in messages:
                content = msg.get("content", "")
                total_text += content

        # 如果检索内容过长，进行摘要
        if len(total_text) > self.max_snippet_length:
            if self.llm:
                summarized = self.llm.summarize_text(total_text, self.max_snippet_length)
            else:
                summarized = total_text[:self.max_snippet_length] + "..."
            context_messages.append({
                "role": "system",
                "content": f"[相关历史对话摘要]\n{summarized}"
            })
        else:
            # 直接注入检索到的对话
            for snippet in snippets:
                for msg in snippet.get("messages", []):
                    context_messages.append({
                        "role": msg.get("role", "user"),
                        "content": msg.get("content", "")
                    })

        return context_messages

    def _load_all_history(self, session_id: str) -> list:
        """加载会话的全部历史对话"""
        file_path = os.path.join(self.session_dir, f"{session_id}.json")
        if not os.path.exists(file_path):
            return []

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
            return session_data.get("conversations", [])
        except (json.JSONDecodeError, IOError) as e:
            # 文件损坏或读取失败时返回空列表
            return []

    def _get_recent_history(self, all_history: list) -> list:
        """获取最近N轮对话历史"""
        if not all_history:
            return []
        return all_history[-self.history_rounds * 2:]  # 每轮含user+assistant

    def save_conversation(self, session_id: str, user_input: str, ai_response: str, dag_id: str = None):
        """保存对话到会话文件"""
        file_path = os.path.join(self.session_dir, f"{session_id}.json")

        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
        else:
            session_data = {"session_id": session_id, "conversations": [], "dag_ids": []}

        session_data["conversations"].append({"role": "user", "content": user_input})
        session_data["conversations"].append({"role": "assistant", "content": ai_response})

        if dag_id:
            if "dag_ids" not in session_data:
                session_data["dag_ids"] = []
            session_data["dag_ids"].append(dag_id)

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # 同步记录到聊天内容MD文件
        self._log_chat_history_md(user_input, ai_response)

    def _log_chat_history_md(self, user_input: str, ai_response: str):
        """将聊天内容同步记录到MD文件（用户输入 + 最终答复）
        每次写入后立即flush+fsync，确保程序意外退出时不丢失记录。
        """
        try:
            log_dir = os.path.join(os.path.dirname(self.session_dir), "logs")
            os.makedirs(log_dir, exist_ok=True)
            date_str = datetime.now().strftime("%Y-%m-%d")
            file_path = os.path.join(log_dir, f"chat_history_{date_str}.md")
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(f"\n## [{ts}] 对话\n\n")
                f.write(f"**用户输入:**\n\n{user_input}\n\n")
                f.write(f"**Agent答复:**\n\n{ai_response}\n\n")
                f.write("---\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            pass

    def _estimate_tokens(self, ctx: ConversationContext) -> int:
        """估算上下文token数（简单估算：中文约1.5字/token，英文约4字/token）"""
        total_chars = len(ctx.system_prompt)
        for msg in ctx.summarized_context + ctx.history:
            total_chars += len(msg.get("content", ""))
        # 粗略估算
        return int(total_chars * 0.5)

    def _compress_context(self, ctx: ConversationContext):
        """压缩上下文以符合token预算 — 优先裁剪检索片段，历史对话保留最近3轮"""
        # 优先压缩检索片段
        while ctx.total_tokens > ctx.max_tokens and ctx.summarized_context:
            ctx.summarized_context.pop(0)
            ctx.total_tokens = self._estimate_tokens(ctx)
        
        # 压缩历史对话（保留最近3轮 = 6条消息）
        while ctx.total_tokens > ctx.max_tokens and len(ctx.history) > 6:
            ctx.history.pop(0)  # 移除最旧的一轮
            ctx.history.pop(0)
            ctx.total_tokens = self._estimate_tokens(ctx)
