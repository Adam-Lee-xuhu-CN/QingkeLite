"""Agentic Loop - 自主任务执行引擎（观察→思考→行动→观察循环）
所有 LLM 返回都必须是 DAG 节点格式，包括回复用户的内容也通过 reply_to_user 节点执行。
支持动态重规划：节点失败时自动触发重规划，生成新的 DAG 卡片替换原卡片。
支持卡点检测：执行超时或输出模式异常时，LLM并行分析是否卡住，自动终止并重规划。
"""
import json
import os
import time
import re
import threading
import subprocess
from datetime import datetime
from typing import Optional, Generator
from core.tools import ToolRegistry


def _now_str() -> str:
    """返回当前时间的格式化字符串"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log_llm_dialogue(log_dir: str, role: str, content: str, context: str = ""):
    """记录LLM对话到MD文件（DAG执行过程中的LLM输入输出）
    每次写入后立即flush+fsync，确保程序意外退出时不丢失记录。
    """
    try:
        os.makedirs(log_dir, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = os.path.join(log_dir, f"dag_dialogue_{date_str}.md")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(file_path, 'a', encoding='utf-8') as f:
            if context:
                f.write(f"\n### {context}\n")
            f.write(f"\n**[{ts}] {role}:**\n\n")
            # 截断过长内容
            display_content = content[:3000] + "..." if len(content) > 3000 else content
            f.write(f"```\n{display_content}\n```\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


class AgenticLoop:
    """自主任务执行引擎，通过观察→思考→行动→观察的循环完成任务。
    所有操作（包括回复用户）都通过 DAG 节点执行。
    """

    def __init__(self, llm_gateway, context_mgr, config: dict = None, reminder_scheduler=None):
        self.llm = llm_gateway
        self.context_mgr = context_mgr
        self.config = config or {}
        self.tools = ToolRegistry(reminder_scheduler=reminder_scheduler)
        # LLM对话日志目录
        self._log_dir = self.config.get("log_dir", os.path.join("data", "logs"))
        # 终止标志（由外部设置）
        self._abort_requested = False
        # 连续失败跟踪（防止同一工具死循环重试）
        self._consecutive_failures = {}  # {tool_name: count}
        self._max_consecutive_failures = 3  # 同一工具最多连续失败3次
        # 当前计划步骤（用于reply_to_user时取消剩余节点）
        self._planned_steps = []
        # reply_to_user已发送标志（阻止后续自动重规划）
        self._reply_sent = False
        # 卡点检测配置（仅对 run_command 生效，超时300秒）
        self._stuck_detection_timeout = self.config.get("stuck_detection_timeout", 300)
        self._stuck_analysis_timeout = self.config.get("stuck_analysis_timeout", 30)
        self._current_process = None  # 当前运行的子进程（用于卡点终止）
        # ask_user：DAG中间节点暂停等待用户输入
        self._user_response_event = threading.Event()
        self._user_response = None
        self._waiting_for_user = False
        # 节点自审：完成后评估达标与否
        self._node_eval_max_retries = 2  # 同一节点最多重试2次
        # DAG执行记录文件路径（用于上下文传递）
        self._dag_execution_log = []  # [{node_index, node_name, query, response}]

    def run(self, user_input: str, session_id: str) -> Generator[dict, None, None]:
        """
        执行自主任务循环，通过yield返回每一步的进度。
        所有步骤统一为 DAG 节点事件流。
        支持动态重规划：节点失败时自动触发重规划。

        Yields DAG 节点事件:
            {
                "type": "dag_plan",  # 初始任务规划
                "steps": [{"index": int, "name": str, "status": "pending"}],
                "planned_at": str,   # 规划生成时间
                "plan_version": int  # 规划版本号（重规划时递增）
            }
            {
                "type": "dag_replan",  # 重规划（替换原卡片）
                "steps": [...],
                "planned_at": str,
                "plan_version": int,
                "reason": str  # 重规划原因
            }
            {
                "type": "dag_node_start",
                "index": int,
                "name": str,
                "command": str,
                "started_at": str,  # 开始执行时间
            }
            {
                "type": "dag_node_output",
                "index": int,
                "output": str,
            }
            {
                "type": "dag_node_complete",
                "index": int,
                "status": str,
                "result": str,
                "completed_at": str,  # 实际完成时间
            }
        """
        # 构建初始消息
        messages = self._build_initial_messages(user_input, session_id)
        plan_version = 0  # 规划版本号
        completed_nodes = []  # 已完成的节点记录，用于重规划

        # 记录用户输入到LLM对话日志
        _log_llm_dialogue(self._log_dir, "用户输入", user_input, "任务开始")

        # 重置终止标志
        self._abort_requested = False
        # 重置ask_user状态
        self._user_response_event.clear()
        self._user_response = None
        self._waiting_for_user = False
        # 重置DAG执行记录
        self._dag_execution_log = []
        self._reply_sent = False
        self._consecutive_failures = {}

        # 0. 初始规划：让 LLM 生成完整任务计划
        yield {"type": "dag_planning", "content": "正在规划任务..."}

        plan = self._generate_plan(user_input, messages)
        plan_version += 1
        if plan and len(plan) > 0:
            self._planned_steps = plan
            yield {
                "type": "dag_plan",
                "steps": plan,
                "planned_at": _now_str(),
                "plan_version": plan_version,
            }

        iteration = 0
        dag_node_index = 0  # DAG节点计数器
        max_iterations = self.config.get("max_iterations", 15) if isinstance(self.config.get("max_iterations"), int) else 15
        consecutive_llm_failures = 0  # 连续LLM调用失败计数
        max_consecutive_llm_failures = 3  # 最多连续失败3次则终止

        while True:
            iteration += 1

            # 最大迭代次数保护
            if iteration > max_iterations * 3:  # 允许3倍于计划节点数的迭代（含重规划）
                dag_node_index += 1
                yield {
                    "type": "dag_node_start", "index": dag_node_index,
                    "name": "达到最大迭代次数", "command": "N/A",
                    "started_at": _now_str(),
                }
                yield {
                    "type": "dag_node_output", "index": dag_node_index,
                    "output": f"已达到最大迭代次数({max_iterations * 3})，任务终止。请检查任务是否过于复杂或尝试手动终止后重新开始。"
                }
                yield {
                    "type": "dag_node_complete", "index": dag_node_index,
                    "status": "failed", "result": "达到最大迭代次数",
                    "completed_at": _now_str(),
                }
                return

            # 检查终止请求
            if self._abort_requested:
                dag_node_index += 1
                yield {
                    "type": "dag_node_start", "index": dag_node_index,
                    "name": "用户终止", "command": "abort",
                    "started_at": _now_str(),
                }
                yield {
                    "type": "dag_node_output", "index": dag_node_index,
                    "output": "用户主动终止了DAG执行"
                }
                yield {
                    "type": "dag_node_complete", "index": dag_node_index,
                    "status": "aborted", "result": "用户终止",
                    "completed_at": _now_str(),
                }
                # 取消所有剩余未执行的计划节点
                max_done_idx = completed_nodes[-1]["index"] if completed_nodes else dag_node_index
                for step in self._planned_steps:
                    if step["index"] > max_done_idx:
                        dag_node_index += 1
                        yield {
                            "type": "dag_node_start", "index": dag_node_index,
                            "name": step["name"], "command": "cancelled",
                            "started_at": _now_str(),
                        }
                        yield {
                            "type": "dag_node_complete", "index": dag_node_index,
                            "status": "cancelled", "result": "用户终止，此步骤被取消",
                            "completed_at": _now_str(),
                        }
                _log_llm_dialogue(self._log_dir, "系统", "用户终止DAG执行", "任务终止")
                return

            # 1. 思考：调用LLM决定下一步
            yield {
                "type": "step", "step": iteration,
                "action": "thinking", "content": "分析当前状态，决定下一步..."
            }

            # 注入DAG计划和节点上下文到system prompt
            self._inject_dag_context(messages, completed_nodes, dag_node_index, self._planned_steps)

            response = self._call_llm(messages)
            if not response:
                consecutive_llm_failures += 1
                if consecutive_llm_failures >= max_consecutive_llm_failures:
                    # 连续失败过多，终止
                    dag_node_index += 1
                    yield {
                        "type": "dag_node_start", "index": dag_node_index,
                        "name": "LLM连续调用失败", "command": "N/A",
                        "started_at": _now_str(),
                    }
                    yield {
                        "type": "dag_node_output", "index": dag_node_index,
                        "output": f"LLM连续{consecutive_llm_failures}次调用失败，任务终止。请检查API配置或网络连接。"
                    }
                    yield {
                        "type": "dag_node_complete", "index": dag_node_index,
                        "status": "failed", "result": f"LLM连续{consecutive_llm_failures}次失败",
                        "completed_at": _now_str(),
                    }
                    return
                dag_node_index += 1
                yield {
                    "type": "dag_node_start", "index": dag_node_index,
                    "name": "LLM调用失败", "command": "N/A",
                    "started_at": _now_str(),
                }
                yield {
                    "type": "dag_node_output", "index": dag_node_index,
                    "output": "LLM调用失败，任务终止"
                }
                yield {
                    "type": "dag_node_complete", "index": dag_node_index,
                    "status": "failed", "result": "LLM调用失败",
                    "completed_at": _now_str(),
                }
                # LLM调用失败，尝试重规划（无次数上限，由用户手动终止）
                replan_result = self._try_replan(
                    user_input, messages, completed_nodes,
                    dag_node_index, "LLM调用失败", plan_version
                )
                if replan_result:
                    plan_version, new_steps = replan_result
                    self._planned_steps = new_steps  # 更新计划
                    yield {
                        "type": "dag_replan",
                        "steps": new_steps,
                        "planned_at": _now_str(),
                        "plan_version": plan_version,
                        "reason": "LLM调用失败，正在重新规划...",
                    }
                    continue  # 继续执行循环，不 return
                return

            # LLM调用成功，重置连续失败计数
            consecutive_llm_failures = 0

            # 2. 解析LLM响应：是工具调用还是回复用户
            parsed = self._parse_response(response)
            if not parsed:
                dag_node_index += 1
                yield {
                    "type": "dag_node_start", "index": dag_node_index,
                    "name": "响应解析失败", "command": "N/A",
                    "started_at": _now_str(),
                }
                yield {
                    "type": "dag_node_output", "index": dag_node_index,
                    "output": response
                }
                yield {
                    "type": "dag_node_complete", "index": dag_node_index,
                    "status": "completed", "result": response,
                    "completed_at": _now_str(),
                }
                return

            if parsed["type"] == "tool_call":
                tool_name = parsed["tool_name"]
                tool_params = parsed["parameters"]

                # 特殊处理：task_complete 表示任务完成
                if tool_name == "task_complete":
                    dag_node_index += 1
                    yield {
                        "type": "dag_node_start", "index": dag_node_index,
                        "name": "任务完成", "command": "task_complete",
                        "started_at": _now_str(),
                    }
                    yield {
                        "type": "dag_node_output", "index": dag_node_index,
                        "output": tool_params.get("summary", "任务完成")
                    }
                    yield {
                        "type": "dag_node_complete", "index": dag_node_index,
                        "status": "completed",
                        "result": tool_params.get("summary", "任务完成"),
                        "completed_at": _now_str(),
                    }
                    # 取消所有剩余未执行的计划节点
                    max_done_idx = completed_nodes[-1]["index"] if completed_nodes else dag_node_index
                    for step in self._planned_steps:
                        if step["index"] > max_done_idx:
                            dag_node_index += 1
                            yield {
                                "type": "dag_node_start", "index": dag_node_index,
                                "name": step["name"], "command": "cancelled",
                                "started_at": _now_str(),
                            }
                            yield {
                                "type": "dag_node_complete", "index": dag_node_index,
                                "status": "cancelled", "result": "任务已提前完成，此步骤被取消",
                                "completed_at": _now_str(),
                            }
                    return

                # 特殊处理：reply_to_user 表示回复用户（也是一个DAG节点）
                # 回复用户后标记已发送，取消剩余节点并结束
                if tool_name == "reply_to_user":
                    self._reply_sent = True
                    dag_node_index += 1
                    content = tool_params.get("content", "")
                    yield {
                        "type": "dag_node_start", "index": dag_node_index,
                        "name": "回复用户", "command": "reply_to_user",
                        "started_at": _now_str(),
                    }
                    # 分段推送输出
                    chunk_size = 200
                    for i in range(0, len(content), chunk_size):
                        chunk = content[i:i + chunk_size]
                        yield {
                            "type": "dag_node_output", "index": dag_node_index,
                            "output": chunk,
                        }
                    yield {
                        "type": "dag_node_complete", "index": dag_node_index,
                        "name": "回复用户", "command": "reply_to_user",
                        "status": "completed", "result": content,
                        "completed_at": _now_str(),
                    }
                    # 记录已完成节点
                    completed_nodes.append({
                        "index": dag_node_index,
                        "name": "回复用户",
                        "status": "completed",
                        "result": content[:500],
                    })
                    # 记录到DAG执行日志
                    self._dag_execution_log.append({
                        "node_index": dag_node_index,
                        "node_name": "回复用户",
                        "query": json.dumps({"tool": "reply_to_user", "parameters": tool_params}, ensure_ascii=False),
                        "response": content[:500],
                    })
                    # 取消所有剩余未执行的计划节点
                    max_done_idx = completed_nodes[-1]["index"] if completed_nodes else dag_node_index
                    for step in self._planned_steps:
                        if step["index"] > max_done_idx:
                            dag_node_index += 1
                            yield {
                                "type": "dag_node_start", "index": dag_node_index,
                                "name": step["name"], "command": "cancelled",
                                "started_at": _now_str(),
                            }
                            yield {
                                "type": "dag_node_complete", "index": dag_node_index,
                                "status": "cancelled", "result": "已回复用户，此步骤被取消",
                                "completed_at": _now_str(),
                            }
                    return

                # 特殊处理：ask_user 表示需要用户输入才能继续
                if tool_name == "ask_user":
                    dag_node_index += 1
                    question = tool_params.get("question", "")
                    context = tool_params.get("context", "")
                    yield {
                        "type": "dag_node_start", "index": dag_node_index,
                        "name": "询问用户", "command": "ask_user",
                        "started_at": _now_str(),
                    }
                    yield {
                        "type": "dag_ask_user",
                        "index": dag_node_index,
                        "question": question,
                        "context": context,
                    }
                    # 等待用户回复（通过 provide_user_response 方法唤醒）
                    self._waiting_for_user = True
                    self._user_response_event.clear()
                    self._user_response = None
                    # 阻塞等待用户回复（最多等待10分钟）
                    got_response = self._user_response_event.wait(timeout=600)
                    self._waiting_for_user = False

                    if got_response and self._user_response:
                        answer = self._user_response
                        yield {
                            "type": "dag_node_output", "index": dag_node_index,
                            "output": f"用户回复: {answer}",
                        }
                        yield {
                            "type": "dag_node_complete", "index": dag_node_index,
                            "name": "询问用户", "command": "ask_user",
                            "status": "completed",
                            "result": f"用户回复: {answer}",
                            "completed_at": _now_str(),
                        }
                        completed_nodes.append({
                            "index": dag_node_index,
                            "name": "询问用户",
                            "status": "completed",
                            "result": f"用户回复: {answer[:200]}",
                        })
                        self._dag_execution_log.append({
                            "node_index": dag_node_index,
                            "node_name": "询问用户",
                            "query": question,
                            "response": answer[:500],
                        })
                        messages.append({
                            "role": "assistant",
                            "content": json.dumps({"tool": "ask_user", "parameters": tool_params}, ensure_ascii=False)
                        })
                        messages.append({
                            "role": "user",
                            "content": f"[用户回复]\n问题: {question}\n回答: {answer}"
                        })
                        continue  # 继续执行
                    else:
                        # 超时：创建分析节点，让AI自主决策是继续等待、跳过还是重规划
                        yield {
                            "type": "dag_node_output", "index": dag_node_index,
                            "output": "等待用户回复超时，正在分析下一步...",
                        }
                        yield {
                            "type": "dag_node_complete", "index": dag_node_index,
                            "name": "询问用户", "command": "ask_user",
                            "status": "timeout",
                            "result": "等待用户回复超时（10分钟）",
                            "completed_at": _now_str(),
                        }
                        completed_nodes.append({
                            "index": dag_node_index,
                            "name": "询问用户",
                            "status": "timeout",
                            "result": "等待用户回复超时",
                        })

                        # 分析节点：让AI自主决策超时后怎么办
                        analysis_result = self._analyze_timeout_decision(
                            user_input, messages, completed_nodes,
                            dag_node_index, question, plan_version
                        )

                        if analysis_result["action"] == "continue_without":
                            # AI判断可以跳过此问题，基于已有信息继续执行
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"[超时分析] 用户未回复问题: {question}\n"
                                    f"分析结论: {analysis_result['reason']}\n"
                                    f"决定: 跳过此问题，基于已有信息继续执行后续步骤。"
                                )
                            })
                            _log_llm_dialogue(self._log_dir, "系统",
                                f"超时分析: 跳过问题继续 - {analysis_result['reason']}", "超时决策")
                            continue
                        elif analysis_result["action"] == "replan":
                            # AI判断需要重规划
                            replan_result = self._try_replan(
                                user_input, messages, completed_nodes,
                                dag_node_index,
                                f"用户未回复问题: {question}，{analysis_result['reason']}",
                                plan_version
                            )
                            if replan_result:
                                plan_version, new_steps = replan_result
                                self._planned_steps = new_steps
                                yield {
                                    "type": "dag_replan",
                                    "steps": new_steps,
                                    "planned_at": _now_str(),
                                    "plan_version": plan_version,
                                    "reason": f"用户未回复，{analysis_result['reason']}，正在重新规划...",
                                }
                            _log_llm_dialogue(self._log_dir, "系统",
                                f"超时分析: 重新规划 - {analysis_result['reason']}", "超时决策")
                            continue
                        else:
                            # 默认：简化问题后继续等待
                            _log_llm_dialogue(self._log_dir, "系统",
                                f"超时分析: 继续等待 - {analysis_result['reason']}", "超时决策")
                            messages.append({
                                "role": "user",
                                "content": f"[超时分析] 用户未回复: {question}\n决定: 简化问题后继续等待用户。"
                            })
                            continue

                # 通用工具执行：作为 DAG 节点
                dag_node_index += 1
                node_name = f"{tool_name}: {tool_params.get('command', tool_params.get('path', tool_name))}"
                command = tool_params.get("command", "")
                if not command and tool_name == "list_directory":
                    command = f"ls {tool_params.get('path', '')}"
                yield {
                    "type": "dag_node_start", "index": dag_node_index,
                    "name": node_name,
                    "command": command or tool_name,
                    "started_at": _now_str(),
                }

                # 执行工具（带卡点检测）
                result, is_stuck, stuck_reason, stuck_action = \
                    self._execute_tool_with_stuck_detection(tool_name, tool_params)

                # 卡点处理：检测到卡住时，终止节点并触发重规划
                if is_stuck:
                    result_text = f"[卡点检测] {stuck_reason}"

                    # 分段推送输出
                    for i in range(0, len(result_text), 200):
                        yield {
                            "type": "dag_node_output", "index": dag_node_index,
                            "output": result_text[i:i + 200],
                        }

                    yield {
                        "type": "dag_node_stuck",
                        "index": dag_node_index,
                        "reason": stuck_reason,
                        "action": stuck_action,
                        "stuck_at": _now_str(),
                    }
                    yield {
                        "type": "dag_node_complete", "index": dag_node_index,
                        "status": "stuck", "result": result_text,
                        "completed_at": _now_str(),
                    }

                    # 记录已完成节点
                    completed_nodes.append({
                        "index": dag_node_index,
                        "name": node_name,
                        "status": "stuck",
                        "result": result_text[:500],
                    })

                    # 注入卡点信息到消息
                    messages.append({
                        "role": "assistant",
                        "content": json.dumps({"tool": tool_name, "parameters": tool_params}, ensure_ascii=False)
                    })
                    messages.append({
                        "role": "user",
                        "content": f"[工具结果 - 卡点]\n工具: {tool_name}\n卡点原因: {stuck_reason}\n建议: {stuck_action}"
                    })

                    # 触发重规划
                    replan_result = self._try_replan(
                        user_input, messages, completed_nodes,
                        dag_node_index,
                        f"节点 [{node_name}] 被卡点检测终止: {stuck_reason}",
                        plan_version
                    )
                    if replan_result:
                        plan_version, new_steps = replan_result
                        self._planned_steps = new_steps
                        yield {
                            "type": "dag_replan",
                            "steps": new_steps,
                            "planned_at": _now_str(),
                            "plan_version": plan_version,
                            "reason": f"卡点检测: {stuck_reason}，正在重新规划...",
                        }
                    continue

                # 获取结果文本
                result_text = result.get("result", "") if result.get("success") else f"错误: {result.get('error', '')}"
                node_success = result.get("success", False)

                # 分段推送输出
                chunk_size = 200
                for i in range(0, len(result_text), chunk_size):
                    chunk = result_text[i:i + chunk_size]
                    yield {
                        "type": "dag_node_output", "index": dag_node_index,
                        "output": chunk,
                    }

                yield {
                    "type": "dag_node_complete", "index": dag_node_index,
                    "status": "completed" if node_success else "failed",
                    "result": result_text[:1000],
                    "completed_at": _now_str(),
                }

                # 记录已完成节点
                completed_nodes.append({
                    "index": dag_node_index,
                    "name": node_name,
                    "status": "completed" if node_success else "failed",
                    "result": result_text[:500],
                })

                # 记录到DAG执行日志（③ 上下文传递）
                self._dag_execution_log.append({
                    "node_index": dag_node_index,
                    "node_name": node_name,
                    "query": json.dumps({"tool": tool_name, "parameters": tool_params}, ensure_ascii=False),
                    "response": result_text[:500],
                })

                # 将工具调用和结果添加到消息中
                messages.append({
                    "role": "assistant",
                    "content": json.dumps({"tool": tool_name, "parameters": tool_params}, ensure_ascii=False)
                })
                messages.append({
                    "role": "user",
                    "content": f"[工具结果]\n工具: {tool_name}\n结果:\n{result_text}"
                })

                # ⑧ 节点自审：评估执行结果是否达标（仅对成功的节点）
                if node_success:
                    # 通知前端：正在评估节点质量
                    yield {
                        "type": "dag_evaluating", "index": dag_node_index,
                        "name": node_name,
                        "message": f"正在评估 [{node_name}] 执行质量...",
                        "started_at": _now_str(),
                    }
                    eval_result = self._evaluate_node(
                        node_name,
                        json.dumps(tool_params, ensure_ascii=False),
                        result_text
                    )
                    if not eval_result["pass"]:
                        # 不达标：注入反馈并重试或重规划
                        retry_key = f"eval_retry_{dag_node_index}"
                        retry_count = self._consecutive_failures.get(retry_key, 0)
                        if retry_count < self._node_eval_max_retries:
                            self._consecutive_failures[retry_key] = retry_count + 1
                            feedback_msg = (
                                f"[节点自审不达标] 节点 [{node_name}] 执行结果未通过质量评估。\n"
                                f"原因: {eval_result['reason']}\n"
                                f"建议: {eval_result['suggestion']}\n"
                                f"请重新执行此步骤（第{retry_count + 1}次重试）。"
                            )
                            messages.append({"role": "user", "content": feedback_msg})
                            _log_llm_dialogue(self._log_dir, "系统", feedback_msg, "节点自审不达标")
                            # 不标记为completed，让循环继续重新执行
                            continue
                        else:
                            # 重试次数耗尽，触发重规划
                            _log_llm_dialogue(self._log_dir, "系统",
                                f"节点 [{node_name}] 自审{self._node_eval_max_retries}次不达标，触发重规划",
                                "节点自审-重规划")
                            replan_result = self._try_replan(
                                user_input, messages, completed_nodes,
                                dag_node_index,
                                f"节点 [{node_name}] 自审{self._node_eval_max_retries}次不达标: {eval_result['reason']}",
                                plan_version
                            )
                            if replan_result:
                                plan_version, new_steps = replan_result
                                self._planned_steps = new_steps
                                yield {
                                    "type": "dag_replan",
                                    "steps": new_steps,
                                    "planned_at": _now_str(),
                                    "plan_version": plan_version,
                                    "reason": f"节点 [{node_name}] 多次不达标，正在重新规划...",
                                }
                            continue
                    else:
                        # 自审通过，重置评估重试计数
                        self._consecutive_failures.pop(f"eval_retry_{dag_node_index}", None)

                # 检查所有规划节点是否已执行完，如果是则触发重规划
                # 但如果已回复用户或任务已完成，不再重规划
                if node_success and self._planned_steps and not self._reply_sent:
                    max_planned_index = max(s["index"] for s in self._planned_steps)
                    if dag_node_index >= max_planned_index:
                        # 通知前端：正在重规划
                        yield {
                            "type": "dag_replanning",
                            "message": f"所有{max_planned_index}个规划节点已执行完成，正在规划后续步骤...",
                            "started_at": _now_str(),
                        }
                        # 所有规划节点已执行完，触发重规划生成新的完整计划
                        _log_llm_dialogue(self._log_dir, "系统",
                            f"所有规划节点已执行完（共{max_planned_index}步），触发重规划",
                            "DAG计划完成-重规划")
                        replan_result = self._try_replan(
                            user_input, messages, completed_nodes,
                            dag_node_index,
                            f"所有{max_planned_index}个规划节点已执行完成，需要规划后续步骤",
                            plan_version
                        )
                        if replan_result and replan_result[1]:
                            plan_version, new_steps = replan_result
                            self._planned_steps = new_steps
                            yield {
                                "type": "dag_replan",
                                "steps": new_steps,
                                "planned_at": _now_str(),
                                "plan_version": plan_version,
                                "reason": f"规划的{max_planned_index}个节点已全部执行完成，正在规划后续步骤...",
                            }
                            continue  # 继续执行新的规划节点
                        else:
                            # 重规划失败或无新步骤，任务完成
                            _log_llm_dialogue(self._log_dir, "系统",
                                "所有规划节点已完成且无后续步骤，任务结束",
                                "DAG计划完成-结束")
                            dag_node_index += 1
                            yield {
                                "type": "dag_node_start", "index": dag_node_index,
                                "name": "任务完成", "command": "task_complete",
                                "started_at": _now_str(),
                            }
                            yield {
                                "type": "dag_node_output", "index": dag_node_index,
                                "output": f"所有{max_planned_index}个规划步骤已执行完成"
                            }
                            yield {
                                "type": "dag_node_complete", "index": dag_node_index,
                                "status": "completed",
                                "result": f"所有{max_planned_index}个规划步骤已执行完成",
                                "completed_at": _now_str(),
                            }
                            return

                # 节点失败时尝试重规划（无次数上限，由用户手动终止）
                if not node_success:
                    # 连续失败跟踪（防止同一工具无限重试）
                    self._consecutive_failures[tool_name] = \
                        self._consecutive_failures.get(tool_name, 0) + 1

                    # 同一工具连续失败超过阈值，注入强制换策略指令
                    if self._consecutive_failures[tool_name] >= self._max_consecutive_failures:
                        messages.append({
                            "role": "user",
                            "content": f"[系统警告] 工具 {tool_name} 已连续失败 "
                                       f"{self._consecutive_failures[tool_name]} 次。"
                                       f"请立即停止使用该工具，改用其他方法完成任务，"
                                       f"或者调用 reply_to_user 向用户说明情况。"
                        })

                    replan_result = self._try_replan(
                        user_input, messages, completed_nodes,
                        dag_node_index, f"节点 [{node_name}] 执行失败: {result_text[:200]}",
                        plan_version
                    )
                    if replan_result:
                        plan_version, new_steps = replan_result
                        self._planned_steps = new_steps  # 更新计划
                        yield {
                            "type": "dag_replan",
                            "steps": new_steps,
                            "planned_at": _now_str(),
                            "plan_version": plan_version,
                            "reason": f"节点 [{node_name}] 执行失败，正在重新规划...",
                        }
                        continue  # 重新进入思考阶段
                elif node_success:
                    # 成功时重置该工具的连续失败计数
                    self._consecutive_failures.pop(tool_name, None)

            elif parsed["type"] == "answer":
                # 兼容旧格式：LLM 返回了 answer 而非 reply_to_user
                # 将其包装为 reply_to_user DAG 节点，不取消后续节点，DAG继续
                dag_node_index += 1
                content = parsed["answer"]
                yield {
                    "type": "dag_node_start", "index": dag_node_index,
                    "name": "回复用户", "command": "reply_to_user",
                    "started_at": _now_str(),
                }
                chunk_size = 200
                for i in range(0, len(content), chunk_size):
                    chunk = content[i:i + chunk_size]
                    yield {
                        "type": "dag_node_output", "index": dag_node_index,
                        "output": chunk,
                    }
                yield {
                    "type": "dag_node_complete", "index": dag_node_index,
                    "name": "回复用户", "command": "reply_to_user",
                    "status": "completed", "result": content,
                    "completed_at": _now_str(),
                }
                # 记录已完成节点（不取消后续节点，DAG继续执行）
                completed_nodes.append({
                    "index": dag_node_index,
                    "name": "回复用户",
                    "status": "completed",
                    "result": content[:500],
                })
                self._dag_execution_log.append({
                    "node_index": dag_node_index,
                    "node_name": "回复用户",
                    "query": "answer类型回复",
                    "response": content[:500],
                })
                messages.append({
                    "role": "assistant",
                    "content": content,
                })
                messages.append({
                    "role": "user",
                    "content": f"[中间回复已发送给用户]\n内容: {content[:300]}"
                })
                continue  # 继续执行下一个DAG节点

    def _build_initial_messages(self, user_input: str, session_id: str) -> list:
        """构建初始消息列表。
        用户聊天消息标记 _preserve=True（永不丢弃），DAG交互消息标记 _dag=True（可压缩）。
        """
        # 获取上下文
        ctx = self.context_mgr.build_context(user_input, session_id)
        messages = []

        # 系统提示（包含工具定义）
        system_prompt = self._build_system_prompt()
        messages.append({"role": "system", "content": system_prompt, "_preserve": True})

        # 历史对话（用户聊天记录，永不丢弃）
        for msg in ctx.summarized_context:
            msg_copy = dict(msg)
            msg_copy["_preserve"] = True
            messages.append(msg_copy)
        for msg in ctx.history:
            msg_copy = dict(msg)
            msg_copy["_preserve"] = True
            messages.append(msg_copy)

        # 当前用户输入（永不丢弃）
        messages.append({"role": "user", "content": user_input, "_preserve": True})

        return messages

    def _build_system_prompt(self) -> str:
        """构建包含工具定义的系统提示"""
        # 获取原始系统提示
        sys_prompt = self.context_mgr.system_prompt or ""

        # 添加工具定义
        tool_prompt = self.tools.get_tool_prompt()

        # ⑤ 检查可用技能，生成技能匹配信息
        skill_info = self._get_skill_matching_info()

        # 添加响应格式说明（强制所有返回都必须是 DAG 节点格式）
        format_prompt = """## 强制规则（必须严格遵守）

1. **绝对禁止编造数据**：你不能凭空生成文件列表、目录内容、命令输出等任何数据。你必须通过调用工具来获取真实信息。
2. **必须先调用工具再回答**：当用户询问文件、目录、磁盘内容时，你必须先调用 `list_directory` 或 `run_command` 工具获取真实数据，然后基于工具返回的结果来回答。
3. **每次只调用一个工具**：不要在一轮中尝试同时调用多个。
4. **所有返回都必须是严格的JSON格式**：不要返回任何纯文本，必须返回JSON。
5. **必须执行完整流程**：规划 → 执行工具 → 根据结果继续执行 → 直到任务完成 → 最后用 reply_to_user 回复用户。
6. **不要提前回复**：在获取到所有必要信息之前，不要调用 reply_to_user。
7. **必须实际执行操作**：绝对不能只告诉用户"建议你做XXX"或"你可以这样做"。你必须通过工具调用实际完成用户的任务。用户要的是结果，不是建议。
8. **不要只给方案不执行**：如果你已经知道怎么做，就必须立即调用工具去做，而不是把步骤列出来让用户自己操作。
9. **自主决策，不要打扰用户**：你是自主Agent，用户只提需求和验收结果。执行过程中的所有决策（选择方案、确认操作、处理异常）都由你自主完成。遇到需要选择的情况，你自己判断最优方案并执行。
10. **禁止询问过程事项**：不要问用户"你想用哪种方案"、"要不要继续"、"确认一下"这类问题。ask_user 仅限于必须的凭证信息（密码、API密钥等）。

## 响应格式（必须严格遵循）

你必须严格按以下JSON格式之一回复，不要添加任何其他文字：

### 调用工具时（执行操作、查询数据等）：
```json
{"tool": "工具名称", "parameters": {"参数名": "参数值"}}
```

### 回复用户时（必须用此格式，不要使用 answer）：
```json
{"tool": "reply_to_user", "parameters": {"content": "你要回复用户的内容"}}
```

### 询问用户时（仅限必须的凭证信息，如密码、API密钥）：
```json
{"tool": "ask_user", "parameters": {"question": "你的问题", "context": "问题背景说明"}}
```

### 任务完成时（可选，如果已通过 reply_to_user 回复则不需要）：
```json
{"tool": "task_complete", "parameters": {"summary": "完成摘要"}}
```

## 工作原则
1. 读取文件后再编辑，不要猜测文件内容
2. 先搜索再操作，不要盲目操作
3. 每一步都要基于前一步的结果来决定下一步
4. 如果不确定文件路径，先用 list_directory 或 glob 查找
5. 获取到工具返回的真实数据后，必须通过 `reply_to_user` 将结果回复给用户
6. 如果任务复杂，需要多次工具调用，请逐步执行，不要跳过步骤
7. 回复用户中间结果后，继续执行后续步骤，不要停止
8. 遇到选择时自主判断，不要把决策推给用户"""

        # 添加日志文件路径信息，方便需要详细查询时找到
        log_path_info = f"""
## 日志文件路径
- DAG执行过程记录（所有query与答复）: {os.path.abspath(os.path.join(self._log_dir, 'llm_dialogue.md'))}
- LLM调用日志: {os.path.abspath(os.path.join(self._log_dir, 'llm_dialogue.md'))}
如需详细查看历史记录，可读取上述文件。"""

        return f"{sys_prompt}\n\n{tool_prompt}\n\n{skill_info}\n\n{format_prompt}\n\n{log_path_info}"

    def _get_skill_matching_info(self) -> str:
        """⑤ 获取技能匹配信息，检查是否有可用技能"""
        try:
            from core.skill_manager import SkillManager
            skill_mgr = SkillManager()
            skills = skill_mgr._get_existing_skill_names() if hasattr(skill_mgr, '_get_existing_skill_names') else []
            if not skills:
                return ""
            return f"""## 可用技能
系统中已安装以下技能包：{', '.join(skills)}
如果有匹配用户任务的技能，优先使用技能中的模板和流程。
如果存在多个相似技能且无明显差异，可以并行探索两个技能的内容后再决定使用哪个。"""
        except Exception:
            return ""

    def _generate_plan(self, user_input: str, messages: list) -> list:
        """让 LLM 生成完整的任务计划（带层级结构）"""
        # 构建规划专用消息
        plan_messages = messages.copy()
        plan_prompt = """请先分析用户的任务，生成一个完整的执行计划。

要求：
1. 将任务分解为 3-7 个具体步骤
2. 每个步骤用简短的描述（不超过 20 字）
3. 步骤应该按执行顺序排列
4. 最后一个步骤通常是"回复用户结果"
5. 每个步骤需要指定层级（level）：1表示顶层任务，2表示子任务，以此类推
6. 如果某些步骤是其他步骤的子任务，请合理设置层级

请严格按以下 JSON 格式返回，不要添加任何其他文字：
```json
{"steps": [{"index": 1, "name": "步骤描述", "level": 1}, {"index": 2, "name": "子步骤描述", "level": 2}]}
```"""
        
        plan_messages.append({"role": "user", "content": plan_prompt})
        
        try:
            response = self._call_llm(plan_messages)
            if not response:
                return []
            
            # 提取 JSON
            json_str = self._extract_json(response)
            if json_str:
                data = json.loads(json_str)
                if "steps" in data and isinstance(data["steps"], list):
                    # 确保每个步骤都有 level 字段，且 index 强制从1开始递增
                    # （不信任LLM返回的index，避免与执行时的dag_node_index不匹配）
                    steps = data["steps"]
                    for i, step in enumerate(steps):
                        if "level" not in step:
                            step["level"] = 1
                        step["index"] = i + 1  # 强制覆盖，确保与执行顺序一致
                    return steps
        except Exception:
            pass
        
        return []

    def _try_replan(self, user_input: str, messages: list,
                    completed_nodes: list, current_index: int,
                    fail_reason: str, current_plan_version: int
                    ) -> Optional[tuple]:
        """节点失败时尝试重新规划。
        
        返回 (new_plan_version, new_steps) 或 None（无法重规划）。
        """
        # 构建重规划上下文
        completed_summary = ""
        for node in completed_nodes:
            status_mark = "✓" if node["status"] == "completed" else "✗"
            completed_summary += f"  {status_mark} 步骤{node['index']}: {node['name']} — {node['status']}\n"

        replan_prompt = f"""任务执行过程中遇到了问题，需要重新规划。

## 原始任务
{user_input}

## 已完成的步骤
{completed_summary if completed_summary else "（无）"}

## 失败原因
{fail_reason}

## 当前节点编号
{current_index}

## 要求
1. 基于已完成的步骤和失败原因，重新生成剩余步骤的执行计划
2. 不要重复已完成的步骤
3. 新步骤的 index 从 {current_index + 1} 开始
4. 每个步骤用简短描述（不超过 20 字）
5. 包含层级信息（level）

请严格按以下 JSON 格式返回，不要添加任何其他文字：
```json
{{"steps": [{{"index": {current_index + 1}, "name": "新步骤描述", "level": 1}}]}}
```"""

        replan_messages = [m for m in messages if m.get("role") == "system"]
        replan_messages.append({"role": "user", "content": replan_prompt})

        try:
            response = self._call_llm(replan_messages)
            if not response:
                return None

            json_str = self._extract_json(response)
            if json_str:
                data = json.loads(json_str)
                if "steps" in data and isinstance(data["steps"], list):
                    steps = data["steps"]
                    for i, step in enumerate(steps):
                        if "level" not in step:
                            step["level"] = 1
                        if "index" not in step:
                            step["index"] = current_index + 1 + i
                    new_version = current_plan_version + 1
                    return (new_version, steps)
        except Exception:
            pass

        return None

    def _call_llm(self, messages: list) -> Optional[str]:
        """调用LLM并返回文本响应，输入过长时自动压缩历史并重试"""
        from core.llm_gateway import ContextLengthExceededError, LLMAPIError
        try:
            # 监控：记录system prompt传递状态（验证所有system消息）
            sys_msgs = [m for m in messages if m.get("role") == "system"]
            if sys_msgs:
                total_sys_len = sum(len(m.get("content", "")) for m in sys_msgs)
                sys_content = sys_msgs[0].get("content", "")
                _log_llm_dialogue(self._log_dir, "系统",
                    f"[System Prompt监控] 共{len(sys_msgs)}条system消息，总长度={total_sys_len}字符，"
                    f"第1条前100字={sys_content[:100]}...",
                    "System Prompt验证")
            else:
                _log_llm_dialogue(self._log_dir, "系统",
                    "[System Prompt监控] ⚠️ 未找到system message！LLM将缺少身份和工具定义",
                    "System Prompt验证")

            # 分开记录用户聊天和DAG交互
            user_msgs = []
            dag_msgs = []
            for m in messages:
                role = m.get("role", "")
                if role == "system":
                    continue
                elif m.get("_preserve"):
                    user_msgs.append(m)
                else:
                    dag_msgs.append(m)

            # 用户聊天记录
            if user_msgs:
                user_summary = "\n".join(
                    f"[{m.get('role', '?')}] {m.get('content', '')[:200]}"
                    for m in user_msgs[-3:]
                )
                _log_llm_dialogue(self._log_dir, "LLM输入-用户聊天", user_summary, "调用LLM")

            # DAG交互记录
            if dag_msgs:
                dag_summary = "\n".join(
                    f"[{m.get('role', '?')}] {m.get('content', '')[:200]}"
                    for m in dag_msgs[-4:]
                )
                _log_llm_dialogue(self._log_dir, "LLM输入-DAG交互", dag_summary, "调用LLM")

            response = self.llm.chat(messages)
            content = response.get("content", "")

            # 检测是否是服务错误消息（防止无限循环）
            if content and content.startswith("服务暂时不可用"):
                _log_llm_dialogue(self._log_dir, "系统", f"LLM返回服务错误: {content}", "服务错误")
                return None

            # 记录LLM回复
            if content:
                _log_llm_dialogue(self._log_dir, "LLM输出", content, "LLM回复")

            return content
        except ContextLengthExceededError:
            _log_llm_dialogue(self._log_dir, "系统", "输入过长，自动压缩历史后重试", "上下文压缩")
            compressed = self._compress_for_retry(messages)
            if compressed is not messages:
                messages.clear()
                messages.extend(compressed)
            try:
                response = self.llm.chat(messages)
                content = response.get("content", "")
                if content:
                    _log_llm_dialogue(self._log_dir, "LLM输出", content, "压缩后重试成功")
                return content if content else None
            except Exception as retry_e:
                _log_llm_dialogue(self._log_dir, "系统", f"压缩后重试仍失败: {retry_e}", "重试失败")
                return None
        except LLMAPIError as e:
            # 配置/URL错误，无法重试，记录并返回None
            _log_llm_dialogue(self._log_dir, "系统", f"LLM配置错误（无法重试）: {str(e)}", "配置错误")
            return None
        except Exception as e:
            _log_llm_dialogue(self._log_dir, "系统", f"LLM调用异常: {str(e)}", "调用失败")
            return None

    def _inject_dag_context(self, messages: list, completed_nodes: list,
                            dag_node_index: int, planned_steps: list):
        """在LLM调用前注入DAG上下文为独立的system message（与原始system prompt分离）。
        原始system prompt保持干净（人设+工具+规则），DAG上下文单独一条。
        """
        if not planned_steps:
            return

        # 移除旧的独立DAG上下文消息
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("_dag_context"):
                messages.pop(i)
                break

        # 构建DAG上下文
        dag_lines = [
            "[DAG执行状态]",
            f"当前正在执行第 {dag_node_index + 1} 步（共 {len(planned_steps)} 步）",
        ]

        # 已完成节点
        if completed_nodes:
            dag_lines.append("\n已完成节点:")
            for cn in completed_nodes:
                status_mark = "✓" if cn.get("status") == "completed" else (
                    "⚠" if cn.get("status") == "timeout" else "✗")
                dag_lines.append(f"  {status_mark} 第{cn['index']}步: {cn['name']} — {cn['status']}")

        # 当前节点
        current_step = next((s for s in planned_steps if s["index"] == dag_node_index + 1), None)
        if current_step:
            dag_lines.append(f"\n当前节点: 第{current_step['index']}步 - {current_step['name']}")

        # 后续节点
        remaining = [s for s in planned_steps if s["index"] > dag_node_index + 1]
        if remaining:
            dag_lines.append("\n后续节点:")
            for s in remaining:
                dag_lines.append(f"  第{s['index']}步: {s['name']}")

        # 注入DAG执行历史（③ 历史节点调LLM的query与返回）
        if self._dag_execution_log:
            dag_lines.append("\n\n[DAG执行历史]")
            for log_entry in self._dag_execution_log:
                dag_lines.append(f"  节点{log_entry['node_index']}({log_entry['node_name']}):")
                dag_lines.append(f"    输入: {log_entry['query'][:300]}")
                dag_lines.append(f"    输出: {log_entry['response'][:300]}")

        dag_context = "\n".join(dag_lines)

        # 压缩机制：超过10000字符时压缩到5000字符左右，不丢弃
        if len(dag_context) > 10000:
            dag_context = self._compress_dag_context(dag_context, target_size=5000)

        # 插入为独立的system message（在原始system prompt之后、用户消息之前）
        insert_pos = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                insert_pos = i + 1
            else:
                break
        messages.insert(insert_pos, {
            "role": "system",
            "content": dag_context,
            "_dag_context": True,  # 标记为DAG上下文，便于识别和移除
        })

    def _compress_dag_context(self, text: str, target_size: int = 5000) -> str:
        """压缩DAG上下文到目标字符数左右，不丢弃内容，只缩短每条记录。"""
        lines = text.split("\n")
        result = []
        current_size = 0
        for line in lines:
            if current_size + len(line) > target_size:
                # 截断当前行到剩余空间
                remaining = target_size - current_size
                if remaining > 20:
                    result.append(line[:remaining] + "...")
                result.append(f"\n[上下文已压缩，原始长度{text.__len__()}字符，压缩到{target_size}字符]")
                break
            result.append(line)
            current_size += len(line) + 1
        return "\n".join(result)

    def _compress_for_retry(self, messages: list) -> list:
        """LLM输入过长时压缩消息。
        核心原则：用户聊天记录（_preserve=True）永不丢弃，只压缩DAG交互记录。
        压缩阈值：超过10000字符时压缩到5000字符左右，不丢弃。
        """
        # 压缩DAG执行历史：超过10000字符时压缩到5000字符
        log_text = json.dumps(self._dag_execution_log, ensure_ascii=False)
        if len(log_text) > 10000:
            # 压缩每条记录的query和response到150字符
            for entry in self._dag_execution_log:
                if len(entry.get("query", "")) > 150:
                    entry["query"] = entry["query"][:150] + "...(已压缩)"
                if len(entry.get("response", "")) > 150:
                    entry["response"] = entry["response"][:150] + "...(已压缩)"
            _log_llm_dialogue(self._log_dir, "系统",
                f"压缩DAG执行历史：原始{len(log_text)}字符，每条记录缩短到150字符",
                "上下文压缩")

        # 分离三类消息：系统消息、用户聊天消息（保留）、DAG交互消息（可压缩）
        system_msgs = []
        preserve_msgs = []  # 用户聊天记录，永不丢弃
        dag_msgs = []       # DAG交互记录，可压缩

        for msg in messages:
            role = msg.get("role", "")
            if role == "system" and not msg.get("_dag_context"):
                system_msgs.append(msg)  # 原始system prompt，保留
            elif msg.get("_preserve"):
                preserve_msgs.append(msg)  # 用户聊天记录，保留
            elif msg.get("_dag_context"):
                dag_msgs.append(msg)  # DAG上下文，可压缩
            else:
                dag_msgs.append(msg)  # DAG交互记录，可压缩

        # 计算DAG消息总字符数
        dag_total_chars = sum(len(m.get("content", "")) for m in dag_msgs)

        # 保留最近的DAG消息（最后4条），旧的压缩（仅在超10000字符时触发，压缩到5000字符左右）
        dag_keep = min(4, len(dag_msgs))
        dag_recent = dag_msgs[-dag_keep:] if dag_keep > 0 else []
        dag_older = dag_msgs[:-dag_keep] if dag_keep < len(dag_msgs) else []

        result = list(system_msgs)
        result.extend(preserve_msgs)  # 用户聊天记录全部保留

        if dag_older and dag_total_chars > 10000:
            # 计算需要压缩到的目标：每条旧消息平均分配到5000字符以内
            target_per_msg = max(200, 5000 // len(dag_older))
            older_text = "\n".join(
                f"{'工具调用' if m.get('role') == 'assistant' else '工具结果'}: {m.get('content', '')[:target_per_msg]}"
                for m in dag_older
            )
            result.append({
                "role": "user",
                "content": f"[DAG历史摘要]\n以下是被压缩的{len(dag_older)}条DAG交互记录（原始{dag_total_chars}字符，压缩到约5000字符）:\n{older_text}"
            })
        elif dag_older:
            # 未超10000字符，不压缩，保留完整内容
            older_text = "\n".join(
                f"{'工具调用' if m.get('role') == 'assistant' else '工具结果'}: {m.get('content', '')}"
                for m in dag_older
            )
            result.append({
                "role": "user",
                "content": f"[DAG历史摘要]\n以下是{len(dag_older)}条DAG交互记录:\n{older_text}"
            })

        result.extend(dag_recent)

        if len(result) < 2:
            return messages
        return result

    def _parse_response(self, response: str) -> Optional[dict]:
        """解析LLM响应，判断是工具调用还是最终回答。
        支持解析DAG结构体（⑦ LLM可能返回完整DAG而非单个工具调用）。
        """
        if not response:
            return None

        # 剥离思考内容（<think>...</think>、<think>...</think>等），防止干扰JSON解析
        cleaned = re.sub(r'<think>[\s\S]*?</think>', '', response)
        cleaned = re.sub(r'<thinking>[\s\S]*?</thinking>', '', cleaned)
        cleaned = re.sub(r'<think>[\s\S]*?</think>', '', cleaned)
        cleaned = cleaned.strip()
        if not cleaned:
            # 整个响应都是思考内容，没有实际输出
            return {"type": "answer", "answer": "（模型仅返回了思考内容，未给出实际响应）"}

        # 尝试提取JSON
        json_str = self._extract_json(cleaned)
        if json_str:
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                return self._extract_command_from_text(response)

            # ⑦ 检查是否是DAG结构体（LLM带system prompt后可能返回完整DAG）
            if "steps" in data and isinstance(data["steps"], list):
                # LLM返回了DAG结构体，提取第一个待执行步骤作为当前工具调用
                steps = data["steps"]
                if steps:
                    first_step = steps[0]
                    if "tool" in first_step:
                        return {
                            "type": "tool_call",
                            "tool_name": first_step["tool"],
                            "parameters": first_step.get("parameters", first_step.get("params", {}))
                        }
                # 没有可执行步骤，作为回复
                return {"type": "answer", "answer": response}

            # 检查是否是工具调用
            if "tool" in data and "parameters" in data:
                return {
                    "type": "tool_call",
                    "tool_name": data["tool"],
                    "parameters": data["parameters"]
                }

            # 检查是否是最终回答
            if "answer" in data:
                return {"type": "answer", "answer": data["answer"]}

        # 无法识别JSON格式，尝试从文本中提取命令
        return self._extract_command_from_text(cleaned)

    def _extract_command_from_text(self, response: str) -> dict:
        """从纯文本响应中提取可能的命令，避免DAG流程中断"""
        # 如果响应很短（可能是简单确认），直接作为回复
        if len(response.strip()) < 30:
            return {"type": "answer", "answer": response}

        # 尝试提取代码块中的命令
        code_patterns = [
            r'```(?:bash|shell|powershell|cmd|python)?\s*\n(.+?)\n```',
            r'`(.+?)`',
        ]
        for pattern in code_patterns:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                command = match.group(1).strip()
                if command and len(command) > 5:
                    # 判断是否是Python命令
                    if 'python' in command.lower() or 'import' in command.lower():
                        return {
                            "type": "tool_call",
                            "tool_name": "run_command",
                            "parameters": {"command": command}
                        }
                    # 其他命令
                    return {
                        "type": "tool_call",
                        "tool_name": "run_command",
                        "parameters": {"command": command}
                    }

        # 尝试提取"运行"、"执行"等关键词后的命令
        command_keywords = ['运行', '执行', '命令是', '使用命令']
        for keyword in command_keywords:
            if keyword in response:
                # 提取关键词后的内容
                idx = response.index(keyword)
                rest = response[idx + len(keyword):].strip()
                # 去除冒号等标点
                rest = rest.lstrip(':：').strip()
                if rest and len(rest) > 5:
                    return {
                        "type": "tool_call",
                        "tool_name": "run_command",
                        "parameters": {"command": rest}
                    }

        # 无法提取命令，作为最终回复
        # 拦截：如果内容看起来像截断/畸形JSON（以 { 或 [ 开头），不作为回复发送给用户
        stripped = response.strip()
        if stripped and (stripped[0] in ('{', '[')):
            _log_llm_dialogue(self._log_dir, "系统",
                f"检测到疑似截断JSON响应，已拦截: {stripped[:200]}", "JSON拦截")
            return {"type": "answer", "answer": "（模型返回了格式异常的内容，已自动过滤，请继续等待后续步骤执行）"}
        return {"type": "answer", "answer": response}

    def _extract_json(self, text: str) -> Optional[str]:
        """从文本中提取JSON字符串（多种策略，防止LLM格式偏差导致解析失败）"""
        if not text or not text.strip():
            return None

        text = text.strip()

        # 策略0：整段文本直接就是JSON
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

        # 策略1：提取 ```json ... ``` 或 ``` ... ``` 代码块
        for pattern in [r'```json\s*([\s\S]*?)\s*```', r'```\s*([\s\S]*?)\s*```']:
            match = re.search(pattern, text)
            if match:
                candidate = match.group(1).strip()
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    continue

        # 策略2：逐个 `{` 位置尝试解析JSON（从最外层嵌套开始）
        start = 0
        while True:
            idx = text.find('{', start)
            if idx == -1:
                break
            # 找到匹配的闭合 `}`（处理嵌套）
            depth = 0
            end = -1
            for i in range(idx, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end != -1:
                candidate = text[idx:end + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    pass
            start = idx + 1

        return None

    def _execute_tool_with_stuck_detection(self, tool_name: str, tool_params: dict) -> tuple:
        """执行工具并检测卡点（仅对 run_command 启用超时监控和模式匹配）。
        返回 (result, is_stuck, stuck_reason, stuck_action)。
        """
        # 非 run_command 工具直接执行，不做卡点检测
        if tool_name != "run_command":
            try:
                result = self.tools.execute(tool_name, tool_params)
                return result or {"success": False, "error": "无返回"}, False, "", ""
            except Exception as e:
                return {"success": False, "error": str(e)}, False, "", ""

        # run_command：在线程中执行，支持超时终止
        start_time = time.time()
        result_holder = [None]
        exception_holder = [None]

        def _run_tool():
            try:
                result_holder[0] = self.tools.execute(tool_name, tool_params)
            except Exception as e:
                exception_holder[0] = e

        tool_thread = threading.Thread(target=_run_tool, daemon=True)
        tool_thread.start()
        tool_thread.join(timeout=self._stuck_detection_timeout)

        elapsed = time.time() - start_time

        if tool_thread.is_alive():
            # 超时：命令仍在运行，视为卡住
            reason = f"命令执行超时（已等待{int(elapsed)}秒，阈值{self._stuck_detection_timeout}秒）"
            action = "终止命令并重新规划"
            self._kill_stuck_process()
            return {"success": False, "error": reason}, True, reason, action

        if exception_holder[0]:
            return {"success": False, "error": str(exception_holder[0])}, False, "", ""

        result = result_holder[0] or {"success": False, "error": "无返回"}

        # 模式匹配检测（仅对输出内容做快速检查）
        output_text = result.get("result", "") if result.get("success") else result.get("error", "")
        is_stuck, reason, action = self._check_stuck_patterns(tool_name, output_text)

        if is_stuck:
            # LLM二次确认（避免误报）
            llm_stuck, llm_reason, llm_action = self._analyze_stuck(tool_name, tool_params, output_text)
            if llm_stuck:
                return result, True, llm_reason, llm_action
            # LLM认为没卡住，忽略模式匹配
            return result, False, "", ""

        return result, False, "", ""

    def _check_stuck_patterns(self, tool_name: str, output: str) -> tuple:
        """快速模式匹配检测卡点（不调用LLM）。返回 (is_stuck, reason, action)。
        仅用于 run_command 的输出检测，模式设计为高精度（宁可漏报不误报）。
        """
        if not output or len(output.strip()) < 10:
            return False, "", ""

        text_lower = output.lower()

        # 排除成功标志：如果输出中包含成功/完成等关键词，不认为是卡点
        success_indicators = ['成功', 'success', 'completed', '完成', 'done',
                              'finished', '已安装', 'installed', '已保存', 'saved']
        if any(ind in text_lower for ind in success_indicators):
            return False, "", ""

        # 登录页面检测（要求同时出现表单字段，排除正常提及）
        # 必须同时包含密码字段才认为是登录卡点
        login_form_patterns = [
            (r'(密码|password)\s*[:：]\s*$', "检测到登录表单（等待密码输入）"),
            (r'请输入.*(密码|password|验证码|captcha)', "检测到登录等待输入"),
            (r'(username|password|captcha)\s*=\s*["\']?\s*$', "检测到登录表单字段"),
        ]
        for pattern, reason in login_form_patterns:
            if re.search(pattern, text_lower):
                return True, reason, "尝试使用无认证API或提供认证参数"

        # 交互式输入检测（高置信度模式）
        interactive_patterns = [
            (r'\[y/n\]\s*$', "检测到交互式确认提示"),
            (r'\(yes/no\)\s*$', "检测到交互式确认提示"),
            (r'press\s+enter\s+to\s+continue', "检测到等待用户按Enter"),
            (r'press\s+any\s+key\s+to\s+continue', "检测到等待用户按键"),
        ]
        for pattern, reason in interactive_patterns:
            if re.search(pattern, text_lower):
                return True, reason, "添加 -y 或 --yes 参数自动确认，或使用非交互式模式"

        return False, "", ""

    def _analyze_stuck(self, tool_name: str, tool_params: dict, output: str) -> tuple:
        """调用LLM分析是否卡住。返回 (is_stuck, reason, action)。"""
        cmd = tool_params.get("command", str(tool_params))
        output_preview = output[:1500] if output else "(无输出)"

        prompt = f"""分析以下工具执行是否卡住了（如登录页面、验证码、交互式等待、死循环等）。

工具: {tool_name}
命令: {cmd}
输出: {output_preview}

常见卡点场景：
1. Python爬虫脚本遇到登录页面，需要cookie/token
2. 命令等待用户输入（y/n确认、密码输入等）
3. 网络请求超时但进程未退出
4. 无限循环或死锁
5. 资源不足（内存、磁盘空间）
6. 权限不足导致的交互式提示

严格按JSON格式返回：
{{"is_stuck": true/false, "reason": "简要原因", "action": "建议处理方式"}}"""

        try:
            messages = [
                {"role": "system", "content": "你是一个系统监控分析器，专门检测命令执行是否卡住。简洁回答。"},
                {"role": "user", "content": prompt}
            ]
            response = self.llm.chat(messages)
            content = response.get("content", "")

            json_str = self._extract_json(content)
            if json_str:
                data = json.loads(json_str)
                return (
                    data.get("is_stuck", False),
                    data.get("reason", ""),
                    data.get("action", "终止节点并重新规划")
                )
        except Exception:
            pass

        return False, "", ""

    def _kill_stuck_process(self):
        """终止卡住的子进程树（Windows）
        通过 wmic 查找当前 Python 进程的所有子进程并终止。
        """
        try:
            my_pid = os.getpid()
            # 使用 wmic 查找子进程
            result = subprocess.run(
                ["wmic", "process", "where", f"ParentProcessId={my_pid}",
                 "get", "ProcessId", "/format:csv"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    parts = line.strip().split(',')
                    if len(parts) >= 2 and parts[-1].strip().isdigit():
                        child_pid = parts[-1].strip()
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", child_pid],
                            capture_output=True, timeout=5
                        )
        except Exception:
            pass

    def _analyze_timeout_decision(self, user_input: str, messages: list,
                                   completed_nodes: list, current_index: int,
                                   question: str, plan_version: int) -> dict:
        """超时分析节点：让AI自主决策超时后是继续等待、跳过还是重规划。
        返回 {"action": "continue_without"|"replan"|"wait", "reason": str}
        """
        completed_summary = ""
        for cn in completed_nodes[-5:]:  # 最近5个节点
            status_mark = "✓" if cn["status"] == "completed" else (
                "⚠" if cn["status"] == "timeout" else "✗")
            completed_summary += f"  {status_mark} 第{cn['index']}步: {cn['name']} — {cn['status']}\n"

        analysis_prompt = f"""你正在执行用户的任务，中间有一个步骤需要用户回复但用户超时未回复。
请分析当前情况，自主决定下一步。

## 原始任务
{user_input[:300]}

## 超时的问题
{question}

## 已执行的步骤
{completed_summary if completed_summary else "（无）"}

## 决策要求
- **continue_without**: 如果这个问题的答案可以基于已有信息推断、使用默认值、或跳过不影响最终结果，选择此项
- **replan**: 如果这个问题的答案对后续步骤至关重要且无法推断，需要重新规划执行路径
- **wait**: 只有当这个问题是绝对必要且无法绕过时才选择继续等待

你是自主Agent，用户只提需求和验收结果，不要把决策推给用户。请自主判断。

严格按JSON格式返回：
{{"action": "continue_without", "reason": "判断依据"}}"""

        try:
            analysis_messages = [
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": analysis_prompt}
            ]
            response = self._call_llm(analysis_messages)
            if response:
                json_str = self._extract_json(response)
                if json_str:
                    data = json.loads(json_str)
                    action = data.get("action", "continue_without")
                    if action in ("continue_without", "replan", "wait"):
                        return data
        except Exception:
            pass

        # 默认：跳过问题继续执行（不阻塞用户）
        return {"action": "continue_without", "reason": "分析失败，默认跳过超时问题继续执行"}

    def provide_user_response(self, answer: str):
        """供外部（API端点）调用，提供用户对ask_user的回复"""
        self._user_response = answer
        self._user_response_event.set()

    @property
    def is_waiting_for_user(self) -> bool:
        """当前是否正在等待用户回复"""
        return self._waiting_for_user

    def _evaluate_node(self, node_name: str, task_input: str, task_output: str) -> dict:
        """节点自审：评估节点执行结果是否达标。
        返回 {"pass": bool, "reason": str, "suggestion": str}
        """
        # 截断是正常的大文件保护行为，不应评估为失败
        # 如果输出包含截断标记且内容足够长（>200字符），视为通过
        truncation_markers = ["已截断", "截断为前", "输出截断", "内容截断"]
        has_truncation = any(marker in task_output for marker in truncation_markers)
        if has_truncation and len(task_output) > 200:
            return {"pass": True, "reason": "工具输出已截断（大文件保护），但已读取有意义的数据", "suggestion": ""}

        eval_prompt = f"""评估以下任务节点的执行结果是否达标。

## 节点信息
- 节点名称: {node_name}
- 任务输入: {task_input[:500]}
- 执行结果: {task_output[:3000]}

## 评估标准
1. 任务是否被正确执行（不是只给了建议而是实际操作）
2. 输出结果是否可用（注意：输出可能被截断，这是大文件保护的正常行为，不算失败）
3. 是否有明显的错误或遗漏
4. 如果是文件操作，文件是否确实被创建/修改
5. 如果是命令执行，命令是否成功完成
6. 如果输出包含"已截断"、"截断为前"、"输出截断"、"内容截断"等标记，说明数据量大，这是正常的，不要因此判定失败

严格按JSON格式返回：
{{"pass": true/false, "reason": "判断依据", "suggestion": "如果不达标，建议如何改进"}}"""

        try:
            messages = [
                {"role": "system", "content": "你是一个严格的质量检查员，负责评估任务执行结果。简洁回答，严格按JSON格式返回。"},
                {"role": "user", "content": eval_prompt}
            ]
            response = self.llm.chat(messages)
            content = response.get("content", "")
            json_str = self._extract_json(content)
            if json_str:
                data = json.loads(json_str)
                return {
                    "pass": data.get("pass", True),
                    "reason": data.get("reason", ""),
                    "suggestion": data.get("suggestion", ""),
                }
        except Exception:
            pass
        # 评估失败时默认通过（避免阻塞流程）
        return {"pass": True, "reason": "评估失败，默认通过", "suggestion": ""}

    def run_sync(self, user_input: str, session_id: str) -> dict:
        """同步执行，返回最终结果。从 DAG 节点事件中收集最终响应。"""
        steps = []
        final_response = ""
        last_completed_result = ""

        for event in self.run(user_input, session_id):
            steps.append(event)
            # 从 dag_node_complete 事件中收集最终响应
            if event.get("type") == "dag_node_complete":
                last_completed_result = event.get("result", "")
                # 如果是回复用户节点，记录为最终响应
                # （最后一个 completed 节点通常是 reply_to_user 或 task_complete）

        # 最终响应取最后一个 completed 节点的结果
        final_response = last_completed_result

        return {
            "response": final_response,
            "steps": steps,
            "step_count": len(steps)
        }