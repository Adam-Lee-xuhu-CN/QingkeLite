"""LLM服务网关 - 支持 Dify 和 OpenAI 兼容 API"""
import json
import time
import re
import logging

import requests
from typing import Optional

logger = logging.getLogger('llm_gateway')


class ContextLengthExceededError(Exception):
    """LLM输入过长异常"""
    pass


class PayloadTooLargeError(ContextLengthExceededError):
    """请求体过大（HTTP 400/413/429 中payload相关错误），继承ContextLengthExceededError以兼容现有catch"""
    pass


class LLMAPIError(Exception):
    """LLM API调用异常"""
    pass


class LLMGateway:
    """LLM服务网关，支持 Dify 和 OpenAI 兼容 API"""

    def __init__(self, config: dict, username: str = "青稞"):
        self.mode = config.get("mode", "real")
        self.provider = config.get("provider", "dify")  # dify | openai
        self.dify_config = config.get("dify", {})
        self.openai_config = config.get("openai", {})
        self.username = username  # 系统登录用户名，用于 Dify API 调用

    def update_config(self, config: dict):
        """动态更新配置"""
        self.mode = config.get("mode", self.mode)
        self.provider = config.get("provider", self.provider)
        if "dify" in config:
            self.dify_config.update(config["dify"])
        if "openai" in config:
            self.openai_config.update(config["openai"])

    # ==================== Dify API ====================

    def _get_api_url(self) -> str:
        """获取 Dify API 地址"""
        base_url = self.dify_config.get("api_url", "").rstrip("/")
        if base_url.endswith("/chat-messages"):
            return base_url
        if base_url.endswith("/v1"):
            return f"{base_url}/chat-messages"
        return f"{base_url}/v1/chat-messages"

    def _get_api_key(self) -> str:
        return self.dify_config.get("api_key", "")

    def _get_timeout(self) -> int:
        return self.dify_config.get("timeout", 60)

    def _dify_request(self, query: str, conversation_id: str = "", user: str = None) -> dict:
        """发送 Dify 请求（streaming 模式），返回标准化响应"""
        url = self._get_api_url()
        api_key = self._get_api_key()
        timeout = self._get_timeout()

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "inputs": {},
            "query": query,
            "response_mode": "streaming",
            "conversation_id": conversation_id,
            "user": user or self.username,
            "files": []
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout, stream=True)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            resp_text = e.response.text.lower() if e.response is not None else ""
            if "too large" in resp_text or "exceed" in resp_text or "context" in resp_text or "too many tokens" in resp_text:
                raise ContextLengthExceededError(f"Dify输入过长: {e}") from e
            raise

        # 解析 SSE 流式响应
        full_answer = ""
        final_conversation_id = conversation_id
        for line in response.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8") if isinstance(line, bytes) else line
            if line_str.startswith("data: "):
                data_str = line_str[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    event = data.get("event", "")
                    if event == "message":
                        full_answer += data.get("answer", "")
                    if data.get("conversation_id"):
                        final_conversation_id = data["conversation_id"]
                except json.JSONDecodeError:
                    continue

        return {
            "answer": full_answer,
            "conversation_id": final_conversation_id,
            "message_id": ""
        }

    # ==================== OpenAI 兼容 API ====================

    def _get_openai_url(self) -> str:
        """获取 OpenAI 兼容 API 地址"""
        base_url = self.openai_config.get("api_url", "").rstrip("/")
        if not base_url:
            raise LLMAPIError("OpenAI API URL 未配置，请在 settings.yaml 中设置 llm.openai.api_url")
        if not base_url.startswith(("http://", "https://")):
            raise LLMAPIError(f"OpenAI API URL 格式无效（缺少 http/https 协议）: {base_url}")
        if base_url.endswith("/chat/completions"):
            return base_url
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"

    def _get_openai_key(self) -> str:
        return self.openai_config.get("api_key", "")

    def _get_openai_model(self) -> str:
        return self.openai_config.get("model", "qwen3.5-plus")

    def _get_openai_timeout(self) -> int:
        return self.openai_config.get("timeout", 300)

    def _openai_request(self, messages: list, max_retries: int = 3) -> dict:
        """发送 OpenAI 兼容 API 请求，支持网络错误自动重试"""
        url = self._get_openai_url()
        api_key = self._get_openai_key()
        model = self._get_openai_model()
        timeout = self._get_openai_timeout()

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 12000
        }

        last_error = None
        for attempt in range(max_retries):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=timeout)
                response.raise_for_status()
                break  # 成功则跳出重试循环
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if e.response is not None else 0
                resp_text = e.response.text.lower() if e.response is not None else ""
                if "context_length_exceeded" in resp_text or "maximum context length" in resp_text or "too many tokens" in resp_text:
                    raise ContextLengthExceededError(f"OpenAI输入过长: {e}") from e
                # 识别payload过大：400/413/429中包含请求体过大相关关键词
                _payload_keywords = ["request too large", "payload too large", "body too large",
                                     "max context", "reduce the length", "reduce your message",
                                     "token limit", "maximum token", "input is too long"]
                if status_code in (400, 413, 429) and any(kw in resp_text for kw in _payload_keywords):
                    raise PayloadTooLargeError(f"请求体过大(HTTP {status_code}): {resp_text[:200]}") from e
                raise  # 其他HTTP错误不重试
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, 
                    requests.exceptions.RequestException) as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = 5 * (2 ** attempt)  # 5秒、10秒、20秒
                    logger.warning(f"LLM API网络错误 (第{attempt+1}次): {str(e)[:100]}，{wait_time}秒后重试...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"LLM API网络错误，{max_retries}次重试均失败: {str(e)[:100]}")
                    raise LLMAPIError(f"LLM API网络错误，{max_retries}次重试均失败: {str(e)[:100]}") from e
        
        result = response.json()
        message = result.get("choices", [{}])[0].get("message", {})
        content = message.get("content", "") or ""

        # 处理思考模型返回的reasoning_content（如DeepSeek-R1、QwQ等）
        reasoning = message.get("reasoning_content", "") or message.get("thinking_content", "") or ""
        if reasoning and not content:
            # 模型只返回了思考内容，没有实际输出，将思考内容作为输出
            content = reasoning
        elif reasoning and content:
            # 同时有思考和输出，记录日志但只用content
            logger.debug(f"模型返回了reasoning_content ({len(reasoning)}字符) 和 content ({len(content)}字符)")

        return {"answer": content}

    def chat(self, messages: list, **kwargs) -> dict:
        """发送对话请求，返回标准化响应"""
        if self.mode == "mock":
            return self._mock_chat(messages)

        if self.provider == "openai":
            try:
                return self._openai_chat(messages)
            except ContextLengthExceededError:
                raise  # 向上传递，由调用方处理压缩重试
            except LLMAPIError:
                raise  # 配置/URL错误，向上传递
            except Exception as e:
                return {"content": f"服务暂时不可用，请稍后重试。错误: {str(e)[:50]}", "role": "assistant"}
        else:
            try:
                return self._dify_chat(messages)
            except ContextLengthExceededError:
                raise  # 向上传递，由调用方处理压缩重试
            except LLMAPIError:
                raise  # 配置/URL错误，向上传递
            except Exception as e:
                return {"content": f"服务暂时不可用，请稍后重试。错误: {str(e)[:50]}", "role": "assistant"}

    def _openai_chat(self, messages: list) -> dict:
        """通过 OpenAI 兼容 API 发送对话"""
        result = self._openai_request(messages)
        return {"content": result["answer"], "role": "assistant"}

    def _mock_chat(self, messages: list) -> dict:
        """Mock模式：返回预设响应"""
        user_msg = ""
        for msg in reversed(messages):
            if msg["role"] == "user":
                user_msg = msg["content"]
                break

        if "你好" in user_msg or "hello" in user_msg.lower():
            return {"content": "你好！我是青稞助手，有什么可以帮你的？", "role": "assistant"}
        elif "你是谁" in user_msg:
            return {"content": "我是青稞，一个轻量级AI助手。", "role": "assistant"}
        elif "查看" in user_msg or "列出" in user_msg:
            return {"content": "我来帮你查看文件列表。", "role": "assistant"}
        elif "创建" in user_msg or "生成" in user_msg:
            return {"content": "好的，我来帮你创建项目。", "role": "assistant"}
        else:
            return {"content": f"收到你的消息：{user_msg}", "role": "assistant"}

    def _dify_chat(self, messages: list) -> dict:
        """通过 Dify API 发送对话 - 确保system prompt始终在首位，历史截断防溢出"""
        system_parts = []
        non_system = []

        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])  # 收集所有system消息，不覆盖
            else:
                non_system.append(msg)

        # 合并所有system消息（原始system prompt + DAG执行状态等）
        system_prompt = "\n\n".join(system_parts) if system_parts else ""

        # 截断历史：保留最近N轮对话，每条消息最多500字符，防止query过长导致Dify截断system prompt
        MAX_HISTORY_TURNS = 8
        MAX_MSG_LEN = 500
        recent = non_system[-MAX_HISTORY_TURNS * 2:] if len(non_system) > MAX_HISTORY_TURNS * 2 else non_system

        # 构建query：system prompt始终在最前面
        query_parts = []
        if system_prompt:
            query_parts.append(system_prompt)

        # 历史对话
        history_lines = []
        for msg in recent[:-1] if len(recent) > 1 else []:
            role_tag = "用户" if msg["role"] == "user" else "助手"
            content = msg["content"]
            if len(content) > MAX_MSG_LEN:
                content = content[:MAX_MSG_LEN] + "..."
            history_lines.append(f"[{role_tag}] {content}")

        if history_lines:
            query_parts.append("[对话历史]\n" + "\n".join(history_lines))

        # 最后一条用户消息作为当前问题
        if recent:
            last_msg = recent[-1]
            query_parts.append(last_msg["content"])

        query = "\n\n".join(query_parts)

        result = self._dify_request(query)
        return {"content": result["answer"], "role": "assistant"}

    def analyze_task(self, user_input: str) -> dict:
        """分析用户输入，提取任务列表（不再判断是否需要 DAG，所有请求统一走 Agentic Loop）"""
        # 简单提取任务列表，不再做复杂的 DAG 判断
        return {
            "tasks": [user_input],
            "reason": "所有请求统一通过 Agentic Loop 执行"
        }

    def extract_preferences(self, conversations: list) -> list[dict]:
        """从对话中提取用户偏好（三级分类）"""
        try:
            return self._dify_extract_preferences(conversations)
        except Exception:
            return []

    def _dify_extract_preferences(self, conversations: list) -> list[dict]:
        """通过 Dify API 提取偏好"""
        conv_text = "\n".join([f"{c['role']}: {c['content']}" for c in conversations])
        prompt = f"""从以下对话中提取用户偏好，按三级分类（开发/运维/文档/测试/沟通/其他）返回JSON数组：
对话内容:
{conv_text}
返回格式: [{{"level1": "分类", "level2": "子分类", "level3": "具体偏好描述", "confidence": 0.8}}]"""

        result = self._dify_request(prompt)
        content = result["answer"]

        json_match = re.search(r'\[[\s\S]*\]', content)
        if json_match:
            return json.loads(json_match.group())
        return []

    def compare_preferences(self, new_prefs: list, old_prefs: list) -> dict:
        """对比新旧偏好，判断是否需要更新"""
        updates = []
        old_keys = {(p.get("level1", ""), p.get("level2", "")): p for p in old_prefs}

        for new_p in new_prefs:
            key = (new_p.get("level1", ""), new_p.get("level2", ""))
            if key not in old_keys:
                updates.append({"type": "new", "preference": new_p})
            elif old_keys[key].get("level3") != new_p.get("level3"):
                updates.append({
                    "type": "modified",
                    "old": old_keys[key],
                    "new": new_p
                })

        return {
            "need_update": len(updates) > 0,
            "updates": updates,
            "reason": "检测到偏好变更" if updates else "无变更"
        }

    def generate_dag(self, user_input: str, tasks: list) -> dict:
        """根据任务列表生成DAG结构"""
        try:
            return self._dify_generate_dag(user_input, tasks)
        except Exception:
            return self._default_generate_dag(user_input, tasks)

    def _default_generate_dag(self, user_input: str, tasks: list) -> dict:
        """默认生成DAG结构"""
        nodes = {}
        for i, task in enumerate(tasks):
            node_id = f"task_{i+1}"
            nodes[node_id] = {
                "id": node_id,
                "name": task,
                "command": f"echo '{task}'",
                "dependencies": [f"task_{i}"] if i > 0 else []
            }

        return {
            "id": f"dag_{int(time.time())}",
            "name": "自动生成的DAG",
            "description": f"根据用户输入生成: {user_input}",
            "nodes": nodes,
            "created_at": time.time()
        }

    def _dify_generate_dag(self, user_input: str, tasks: list) -> dict:
        """通过 Dify API 生成DAG"""
        tasks_text = ", ".join(tasks)
        prompt = f"""根据以下用户输入和任务列表，生成DAG JSON结构（包含id、name、description、nodes，每个node包含id、name、description、command、dependencies）：
用户输入: {user_input}
任务列表: {tasks_text}
返回纯JSON格式。"""

        result = self._dify_request(prompt)
        content = result["answer"]

        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            return json.loads(json_match.group())
        return self._default_generate_dag(user_input, tasks)

    def summarize_text(self, text: str, max_length: int = 500) -> str:
        """调用LLM进行文本摘要"""
        try:
            prompt = f"请将以下文本摘要到{max_length}字以内，保留关键信息：\n{text}"
            result = self._dify_request(prompt)
            content = result["answer"]
            return content if content else (text[:max_length] + "..." if len(text) > max_length else text)
        except Exception:
            return text[:max_length] + "..." if len(text) > max_length else text
