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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        # 节点重试计数（用于质量评估）
        self._node_retry_counts = {}  # {node_name: count}
        # 总重规划次数跟踪（防止无限重规划）
        self._total_replan_failures = 0
        self._max_total_replan_failures = 10  # 最多触发10次重规划
        self._replan_limit_reached = False  # 重规划上限标志
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
                "reason": str,  # 重规划原因
                "reflection": str  # LLM对失败原因的反思分析（可选）
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
        self._total_replan_failures = 0
        self._replan_limit_reached = False

        # 0. 需求理解 → 初始规划
        yield {"type": "dag_planning", "content": "正在理解任务需求..."}

        # 需求理解阶段：分析任务需求，提高规划质量
        requirement_analysis = self._understand_requirements(user_input, messages)
        if requirement_analysis:
            _log_llm_dialogue(self._log_dir, "系统",
                f"需求分析完成，开始生成执行计划", "规划流程")
            # 将需求分析结果加入执行对话，确保LLM在执行节点时能看到完整上下文
            messages.append({
                "role": "system",
                "content": f"## 需求分析（执行前已分析）\n{requirement_analysis}\n\n请基于以上需求分析和用户的原始问题执行任务。始终围绕用户的原始需求展开，不要偏离。",
                "_preserve": True,
                "_dag_context": True
            })
        else:
            _log_llm_dialogue(self._log_dir, "系统",
                "需求分析跳过（简单任务或分析失败），直接规划", "规划流程")

        plan = self._generate_plan(user_input, messages, requirement_analysis)
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
        consecutive_llm_failures = 0  # 连续LLM调用失败计数
        max_consecutive_llm_failures = 3  # 最多连续失败3次则终止
        # 迭代检查点配置：达到阈值后询问用户是否继续（无硬限制）
        _iteration_checkpoint_threshold = 120
        _iteration_checkpoint_triggered = False  # 每次达到阈值只触发一次

        while True:
            iteration += 1

            # 迭代检查点：达到120次后通过交互节点询问用户
            if iteration > _iteration_checkpoint_threshold and not _iteration_checkpoint_triggered:
                _iteration_checkpoint_triggered = True
                dag_node_index += 1
                yield {
                    "type": "dag_node_start", "index": dag_node_index,
                    "name": "迭代检查点", "command": "iteration_checkpoint",
                    "started_at": _now_str(),
                }
                yield {
                    "type": "dag_ask_user",
                    "index": dag_node_index,
                    "question": f"任务已执行 {iteration - 1} 轮迭代，耗时较长。请选择后续处理方式：",
                    "context": "当前执行状态已保存，您可以选择继续、终止或换一个处理方向。",
                    "interaction_type": "confirm",
                    "options": [
                        {"label": "继续执行", "value": "continue", "style": "primary"},
                        {"label": "换一个方向", "value": "replan", "style": "warning"},
                        {"label": "终止任务", "value": "terminate", "style": "danger"},
                    ],
                }
                # 等待用户回复
                self._waiting_for_user = True
                self._user_response_event.clear()
                self._user_response = None
                got_response = self._user_response_event.wait(timeout=600)
                self._waiting_for_user = False

                if got_response and self._user_response:
                    user_choice = self._user_response.strip().lower()
                    # 支持用户输入中文或英文
                    if user_choice in ("继续执行", "continue", "继续"):
                        yield {
                            "type": "dag_node_output", "index": dag_node_index,
                            "output": "用户选择继续执行，重置迭代计数器。",
                        }
                        yield {
                            "type": "dag_node_complete", "index": dag_node_index,
                            "name": "迭代检查点", "command": "iteration_checkpoint",
                            "status": "completed",
                            "result": "用户选择继续执行",
                            "completed_at": _now_str(),
                        }
                        completed_nodes.append({
                            "index": dag_node_index,
                            "name": "迭代检查点",
                            "status": "completed",
                            "result": "用户选择继续执行",
                        })
                        # 重置检查点，120轮后再次触发
                        iteration = 0
                        _iteration_checkpoint_triggered = False
                        continue
                    elif user_choice in ("换一个方向", "replan", "换方向", "重新规划"):
                        yield {
                            "type": "dag_node_output", "index": dag_node_index,
                            "output": "用户选择换一个处理方向，正在重新规划...",
                        }
                        yield {
                            "type": "dag_node_complete", "index": dag_node_index,
                            "name": "迭代检查点", "command": "iteration_checkpoint",
                            "status": "completed",
                            "result": "用户选择换方向",
                            "completed_at": _now_str(),
                        }
                        completed_nodes.append({
                            "index": dag_node_index,
                            "name": "迭代检查点",
                            "status": "completed",
                            "result": "用户选择换方向",
                        })
                        replan_result = self._try_replan(
                            user_input, messages, completed_nodes,
                            dag_node_index,
                            "用户在迭代检查点选择换一个处理方向",
                            plan_version
                        )
                        if replan_result:
                            plan_version, new_steps, replan_reflection = replan_result
                            self._planned_steps = new_steps
                            yield {
                                "type": "dag_replan",
                                "steps": new_steps,
                                "planned_at": _now_str(),
                                "plan_version": plan_version,
                                "reason": "用户选择换方向，正在重新规划...",
                                "reflection": replan_reflection,
                            }
                        # 重置检查点
                        iteration = 0
                        _iteration_checkpoint_triggered = False
                        continue
                    else:
                        # 终止
                        yield {
                            "type": "dag_node_output", "index": dag_node_index,
                            "output": "用户选择终止任务。",
                        }
                        yield {
                            "type": "dag_node_complete", "index": dag_node_index,
                            "name": "迭代检查点", "command": "iteration_checkpoint",
                            "status": "aborted",
                            "result": "用户选择终止任务",
                            "completed_at": _now_str(),
                        }
                        return
                else:
                    # 超时：默认继续执行
                    yield {
                        "type": "dag_node_output", "index": dag_node_index,
                        "output": "等待用户回复超时（10分钟），默认继续执行。",
                    }
                    yield {
                        "type": "dag_node_complete", "index": dag_node_index,
                        "name": "迭代检查点", "command": "iteration_checkpoint",
                        "status": "completed",
                        "result": "超时默认继续",
                        "completed_at": _now_str(),
                    }
                    completed_nodes.append({
                        "index": dag_node_index,
                        "name": "迭代检查点",
                        "status": "completed",
                        "result": "超时默认继续",
                    })
                    # 重置检查点
                    iteration = 0
                    _iteration_checkpoint_triggered = False
                    continue

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

            # 检查重规划上限（防止无限重规划循环）
            if self._replan_limit_reached:
                dag_node_index += 1
                yield {
                    "type": "dag_node_start", "index": dag_node_index,
                    "name": "重规划上限", "command": "replan_limit",
                    "started_at": _now_str(),
                }
                yield {
                    "type": "dag_node_output", "index": dag_node_index,
                    "output": f"已重规划{self._total_replan_failures}次仍失败，任务终止。请检查任务可行性或手动调整。",
                }
                yield {
                    "type": "dag_node_complete", "index": dag_node_index,
                    "status": "failed",
                    "result": f"重规划{self._total_replan_failures}次上限",
                    "completed_at": _now_str(),
                }
                _log_llm_dialogue(self._log_dir, "系统",
                    f"重规划上限{self._total_replan_failures}次，任务终止", "重规划-终止")
                return

            # 0.5 并行组检测：如果下一个节点属于 parallel_group，整组并行执行
            if self._planned_steps:
                next_idx = dag_node_index + 1
                pg_nodes = self._find_parallel_group_nodes(next_idx)
                if pg_nodes:
                    # 标记并行开始
                    dag_node_index += len(pg_nodes)
                    try:
                        # 使用 helper 生成器处理并行执行
                        has_failure = False
                        for ev in self._execute_parallel_batch(
                                pg_nodes, next_idx, messages, completed_nodes, plan_version):
                            yield ev
                            # 检测并行组中是否有节点失败
                            if ev.get("type") == "dag_node_complete" and ev.get("status") in ("failed", "error"):
                                has_failure = True

                        # 并行组执行完毕，如果有失败节点则触发质量评估
                        if has_failure:
                            fail_names = [n.get("name", "?") for n in completed_nodes[-len(pg_nodes):]
                                          if n.get("status") in ("failed", "error")]
                            fail_summary = ", ".join(fail_names[:3]) if fail_names else "并行节点"
                            
                            # 质量评估：决定错误处理方式
                            retry_count = self._node_retry_counts.get(f"并行组_{fail_summary}", 0)
                            eval_result = self._evaluate_error_action(
                                f"并行组_{fail_summary}", "parallel_group", {},
                                f"并行组 [{fail_summary}] 执行失败", retry_count
                            )
                            action = eval_result.get("action", "replan")
                            reason = eval_result.get("reason", "")
                            suggestion = eval_result.get("suggestion", "")
                            
                            # 质量评估结果记录到日志，不输出到前端（防止泄露）
                            _log_llm_dialogue(self._log_dir, "系统",
                                f"质量评估 并行组失败: 操作={action}, 原因={reason}", "质量评估")
                            
                            if action == "retry":
                                # 重试：增加计数，注入反馈消息
                                self._node_retry_counts[f"并行组_{fail_summary}"] = retry_count + 1
                                messages.append({
                                    "role": "user",
                                    "content": f"[系统内部-错误已自动处理] 并行组 [{fail_summary}] 执行失败\n"
                                               f"建议: {suggestion}\n"
                                               f"请根据建议调整后重试。此信息仅供内部决策，不要向用户报告此错误。"
                                })
                                continue  # 重新执行
                            
                            elif action == "skip":
                                # 跳过：继续执行下一个节点
                                self._node_retry_counts[f"并行组_{fail_summary}"] = 0
                                messages.append({
                                    "role": "user",
                                    "content": f"[系统内部-节点已跳过] 并行组 [{fail_summary}] 执行失败\n"
                                               f"质量评估决定跳过此节点: {reason}\n"
                                               f"此信息仅供内部决策，不要向用户报告。请继续执行后续任务。"
                                })
                                continue  # 跳过，执行下一个
                            
                            else:  # replan
                                # 重规划：重置计数，触发重规划
                                self._node_retry_counts[f"并行组_{fail_summary}"] = 0
                                replan_result = self._try_replan(
                                    user_input, messages, completed_nodes,
                                    dag_node_index,
                                    f"并行组 [{fail_summary}] 执行失败。\n"
                                    f"重要提示：在重新规划时请考虑：\n"
                                    f"1. 是否有其他不依赖失败节点的任务可以并行执行？\n"
                                    f"2. 标记可并行的步骤为同一 parallel_group（如 'A'、'B'）\n"
                                    f"3. 避免再次使用可能失败的工具或命令",
                                    plan_version
                                )
                                if replan_result:
                                    plan_version, new_steps, replan_reflection = replan_result
                                    self._planned_steps = new_steps
                                    yield {
                                        "type": "dag_replan",
                                        "steps": new_steps,
                                        "planned_at": _now_str(),
                                        "plan_version": plan_version,
                                        "reason": f"并行组节点失败，正在重新规划（含并行任务）...",
                                        "reflection": replan_reflection,
                                    }
                    except Exception as pg_err:
                        # 并行执行整体异常
                        error_msg = f"并行组执行异常: {type(pg_err).__name__}: {str(pg_err)[:300]}"
                        _log_llm_dialogue(self._log_dir, "系统", error_msg, "并行异常")
                        for nd in pg_nodes:
                            nd_idx = nd.get("index", dag_node_index)
                            yield {
                                "type": "dag_node_complete", "index": nd_idx,
                                "name": nd.get("name", "?"), "status": "failed",
                                "result": error_msg, "completed_at": _now_str(),
                            }
                            completed_nodes.append({
                                "index": nd_idx, "name": nd.get("name", "?"),
                                "status": "failed", "result": error_msg[:500],
                            })
                        
                        # 质量评估：决定错误处理方式
                        retry_count = self._node_retry_counts.get("并行组异常", 0)
                        eval_result = self._evaluate_error_action(
                            "并行组异常", "parallel_group", {},
                            error_msg, retry_count
                        )
                        action = eval_result.get("action", "replan")
                        reason = eval_result.get("reason", "")
                        suggestion = eval_result.get("suggestion", "")
                        
                        # 质量评估结果记录到日志，不输出到前端（防止泄露）
                        _log_llm_dialogue(self._log_dir, "系统",
                            f"质量评估 并行组异常: 操作={action}, 原因={reason}", "质量评估")
                        
                        if action == "retry":
                            # 重试：增加计数，注入反馈消息
                            self._node_retry_counts["并行组异常"] = retry_count + 1
                            messages.append({
                                "role": "user",
                                "content": f"[系统内部-错误已自动处理] 并行组执行异常: {error_msg}\n"
                                           f"建议: {suggestion}\n"
                                           f"请根据建议调整后重试。此信息仅供内部决策，不要向用户报告此错误。"
                            })
                            continue  # 重新执行
                        
                        elif action == "skip":
                            # 跳过：继续执行下一个节点
                            self._node_retry_counts["并行组异常"] = 0
                            messages.append({
                                "role": "user",
                                "content": f"[系统内部-节点已跳过] 并行组执行异常: {error_msg}\n"
                                           f"质量评估决定跳过此节点: {reason}\n"
                                           f"此信息仅供内部决策，不要向用户报告。请继续执行后续任务。"
                            })
                            continue  # 跳过，执行下一个
                        
                        else:  # replan
                            # 重规划：重置计数，触发重规划
                            self._node_retry_counts["并行组异常"] = 0
                            replan_result = self._try_replan(
                                user_input, messages, completed_nodes,
                                dag_node_index,
                                f"{error_msg}\n"
                                f"重要提示：在重新规划时请考虑：\n"
                                f"1. 是否有其他不依赖失败节点的任务可以并行执行？\n"
                                f"2. 标记可并行的步骤为同一 parallel_group（如 'A'、'B'）\n"
                                f"3. 避免再次使用可能导致问题的工具或命令",
                                plan_version
                            )
                            if replan_result:
                                plan_version, new_steps, replan_reflection = replan_result
                                self._planned_steps = new_steps
                                yield {
                                    "type": "dag_replan", "steps": new_steps,
                                    "planned_at": _now_str(), "plan_version": plan_version,
                                    "reason": f"并行组异常，正在重新规划（含并行任务）...",
                                    "reflection": replan_reflection,
                                }
                    continue  # 跳过本轮LLM调用

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
                    plan_version, new_steps, replan_reflection = replan_result
                    self._planned_steps = new_steps  # 更新计划
                    yield {
                        "type": "dag_replan",
                        "steps": new_steps,
                        "planned_at": _now_str(),
                        "plan_version": plan_version,
                        "reason": "LLM调用失败，正在重新规划...",
                        "reflection": replan_reflection,
                    }
                    continue  # 继续执行循环，不 return
                return

            # LLM调用成功，重置连续失败计数
            consecutive_llm_failures = 0

            # 2. 解析LLM响应：是工具调用还是回复用户（带异常捕获）
            try:
                parsed = self._parse_response(response)
            except Exception as parse_err:
                # 响应解析异常：标记节点失败，触发重规划
                dag_node_index += 1
                error_msg = f"LLM响应解析异常: {type(parse_err).__name__}: {str(parse_err)[:200]}"
                yield {
                    "type": "dag_node_start", "index": dag_node_index,
                    "name": "响应解析异常", "command": "N/A",
                    "started_at": _now_str(),
                }
                yield {
                    "type": "dag_node_output", "index": dag_node_index,
                    "output": error_msg,
                }
                yield {
                    "type": "dag_node_complete", "index": dag_node_index,
                    "status": "failed", "result": error_msg,
                    "completed_at": _now_str(),
                }
                completed_nodes.append({
                    "index": dag_node_index, "name": "响应解析异常",
                    "status": "failed", "result": error_msg[:500],
                })
                replan_result = self._try_replan(
                    user_input, messages, completed_nodes,
                    dag_node_index, error_msg, plan_version
                )
                if replan_result:
                    plan_version, new_steps, replan_reflection = replan_result
                    self._planned_steps = new_steps
                    yield {
                        "type": "dag_replan", "steps": new_steps,
                        "planned_at": _now_str(), "plan_version": plan_version,
                        "reason": f"响应解析异常，正在重新规划...",
                        "reflection": replan_reflection,
                    }
                continue

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
                        "name": "任务完成", "command": "task_complete",
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
                # 支持 interaction_type: "input"(文本输入) | "confirm"(按钮选择) | "authorize"(授权审批)
                if tool_name == "ask_user":
                    dag_node_index += 1
                    question = tool_params.get("question", "")
                    context = tool_params.get("context", "")
                    interaction_type = tool_params.get("interaction_type", "input")
                    options = tool_params.get("options", [])
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
                        "interaction_type": interaction_type,
                        "options": options,
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
                                plan_version, new_steps, replan_reflection = replan_result
                                self._planned_steps = new_steps
                                yield {
                                    "type": "dag_replan",
                                    "steps": new_steps,
                                    "planned_at": _now_str(),
                                    "plan_version": plan_version,
                                    "reason": f"用户未回复，{analysis_result['reason']}，正在重新规划...",
                                    "reflection": replan_reflection,
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

                # 执行工具（带卡点检测 + 全局异常捕获）
                try:
                    result, is_stuck, stuck_reason, stuck_action = \
                        self._execute_tool_with_stuck_detection(tool_name, tool_params)
                except Exception as tool_exec_err:
                    # 兜底异常捕获：防止任何未预期的异常导致整个DAG崩溃
                    error_msg = f"工具执行异常: {type(tool_exec_err).__name__}: {str(tool_exec_err)[:300]}"
                    _log_llm_dialogue(self._log_dir, "系统",
                        f"节点{dag_node_index} [{node_name}] 执行异常: {error_msg}", "节点异常")

                    # 工具执行异常记录到日志，不输出到前端（防止泄露）
                    _log_llm_dialogue(self._log_dir, "系统",
                        f"工具执行异常 节点{node_name}: {error_msg}", "工具异常")
                    yield {
                        "type": "dag_node_complete", "index": dag_node_index,
                        "status": "failed", "result": error_msg,
                        "completed_at": _now_str(),
                    }

                    # 记录失败节点
                    completed_nodes.append({
                        "index": dag_node_index,
                        "name": node_name,
                        "status": "failed",
                        "result": error_msg[:500],
                    })
                    self._dag_execution_log.append({
                        "node_index": dag_node_index,
                        "node_name": node_name,
                        "query": json.dumps({"tool": tool_name, "parameters": tool_params}, ensure_ascii=False),
                        "response": error_msg[:500],
                    })

                    # 质量评估：决定错误处理方式
                    self._consecutive_failures[tool_name] = \
                        self._consecutive_failures.get(tool_name, 0) + 1
                    retry_count = self._node_retry_counts.get(node_name, 0)
                    
                    eval_result = self._evaluate_error_action(
                        node_name, tool_name, tool_params,
                        error_msg, retry_count
                    )
                    action = eval_result.get("action", "replan")
                    reason = eval_result.get("reason", "")
                    suggestion = eval_result.get("suggestion", "")
                    
                    # 质量评估结果记录到日志，不输出到前端（防止泄露）
                    _log_llm_dialogue(self._log_dir, "系统",
                        f"质量评估 节点{node_name}: 操作={action}, 原因={reason}", "质量评估")
                    
                    if action == "retry":
                        # 重试：增加计数，注入反馈消息，让LLM重新尝试
                        self._node_retry_counts[node_name] = retry_count + 1
                        messages.append({
                            "role": "assistant",
                            "content": json.dumps({"tool": tool_name, "parameters": tool_params}, ensure_ascii=False)
                        })
                        messages.append({
                            "role": "user",
                            "content": f"[系统内部-错误已自动处理] 工具 {tool_name} 执行异常: {error_msg}\n"
                                       f"建议: {suggestion}\n"
                                       f"请根据建议调整参数后重试。此信息仅供内部决策，不要向用户报告此错误。"
                        })
                        continue  # 重新执行当前节点
                    
                    elif action == "skip":
                        # 跳过：记录跳过，继续执行下一个节点
                        self._node_retry_counts[node_name] = 0  # 重置计数
                        messages.append({
                            "role": "assistant",
                            "content": json.dumps({"tool": tool_name, "parameters": tool_params}, ensure_ascii=False)
                        })
                        messages.append({
                            "role": "user",
                            "content": f"[系统内部-节点已跳过] 工具 {tool_name} 执行异常: {error_msg}\n"
                                       f"质量评估决定跳过此节点: {reason}\n"
                                       f"此信息仅供内部决策，不要向用户报告此错误。请继续执行后续任务。"
                        })
                        continue  # 跳过当前节点，执行下一个
                    
                    else:  # replan
                        # 重规划：重置计数，触发重规划
                        self._node_retry_counts[node_name] = 0
                        # 连续失败跟踪
                        if self._consecutive_failures[tool_name] >= self._max_consecutive_failures:
                            messages.append({
                                "role": "user",
                                "content": f"[系统警告] 工具 {tool_name} 已连续失败 "
                                           f"{self._consecutive_failures[tool_name]} 次。"
                                           f"请立即停止使用该工具，改用其他方法完成任务。"
                            })
                        
                        # 注入错误信息到消息上下文（防止泄露）
                        messages.append({
                            "role": "assistant",
                            "content": json.dumps({"tool": tool_name, "parameters": tool_params}, ensure_ascii=False)
                        })
                        messages.append({
                            "role": "user",
                            "content": f"[系统内部-错误已自动处理] 工具 {tool_name} 执行异常: {error_msg}\n"
                                       f"质量评估建议: {suggestion}\n"
                                       f"此信息仅供内部决策，不要向用户报告此错误。"
                        })
                        
                        replan_result = self._try_replan(
                            user_input, messages, completed_nodes,
                            dag_node_index,
                            f"节点 [{node_name}] 执行异常: {error_msg[:200]}。\n"
                            f"重要提示：在重新规划时请考虑：\n"
                            f"1. 是否有其他不依赖当前节点结果的任务可以并行执行？\n"
                            f"2. 标记可并行的步骤为同一 parallel_group（如 'A'、'B'）\n"
                            f"3. 避免再次使用可能失败的工具或命令",
                            plan_version
                        )
                        if replan_result:
                            plan_version, new_steps, replan_reflection = replan_result
                            self._planned_steps = new_steps
                            yield {
                                "type": "dag_replan",
                                "steps": new_steps,
                                "planned_at": _now_str(),
                                "plan_version": plan_version,
                                "reason": f"节点 [{node_name}] 执行异常，正在重新规划（含并行任务）...",
                                "reflection": replan_reflection,
                            }
                        continue  # 跳过后续处理，进入下一轮循环

                # 卡点处理：检测到卡住时，终止节点并触发重规划
                if is_stuck:
                    result_text = f"[卡点检测] {stuck_reason}"

                    # 卡点检测结果记录到日志，不输出到前端（防止泄露）
                    _log_llm_dialogue(self._log_dir, "系统",
                        f"卡点检测 节点{node_name}: {stuck_reason}", "卡点检测")

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

                    # 注入卡点信息到消息（防止泄露）
                    messages.append({
                        "role": "assistant",
                        "content": json.dumps({"tool": tool_name, "parameters": tool_params}, ensure_ascii=False)
                    })
                    messages.append({
                        "role": "user",
                        "content": f"[系统内部-卡点已自动处理] 工具: {tool_name}\n卡点原因: {stuck_reason}\n建议: {stuck_action}\n"
                                   f"此信息仅供内部决策，不要向用户报告此卡点。请根据建议调整策略继续执行。"
                    })

                    # 质量评估：决定错误处理方式
                    retry_count = self._node_retry_counts.get(node_name, 0)
                    eval_result = self._evaluate_error_action(
                        node_name, tool_name, tool_params,
                        f"卡点检测: {stuck_reason}", retry_count
                    )
                    action = eval_result.get("action", "replan")
                    reason = eval_result.get("reason", "")
                    suggestion = eval_result.get("suggestion", "")
                    
                    # 质量评估结果记录到日志，不输出到前端（防止泄露）
                    _log_llm_dialogue(self._log_dir, "系统",
                        f"质量评估 卡点检测 {node_name}: 操作={action}, 原因={reason}", "质量评估")
                    
                    if action == "retry":
                        # 重试：增加计数，注入反馈消息，重新执行当前节点
                        self._node_retry_counts[node_name] = retry_count + 1
                        messages.append({
                            "role": "user",
                            "content": f"[系统内部-错误已自动处理] 节点 [{node_name}] 卡点检测: {stuck_reason}\n"
                                       f"建议: {suggestion}\n"
                                       f"请根据建议调整后重试。此信息仅供内部决策，不要向用户报告此错误。"
                        })
                        continue  # 重新执行当前节点
                    
                    elif action == "skip":
                        # 跳过：继续执行下一个节点
                        self._node_retry_counts[node_name] = 0
                        messages.append({
                            "role": "user",
                            "content": f"[系统内部-节点已跳过] 节点 [{node_name}] 卡点检测: {stuck_reason}\n"
                                       f"质量评估决定跳过此节点: {reason}\n"
                                       f"此信息仅供内部决策，不要向用户报告。请继续执行后续任务。"
                        })
                        continue  # 跳过当前节点，执行下一个
                    
                    else:  # replan
                        # 重规划：重置计数，触发重规划
                        # 提示LLM：在重新规划时，考虑并行执行不依赖当前节点的其他任务
                        self._node_retry_counts[node_name] = 0
                        replan_result = self._try_replan(
                            user_input, messages, completed_nodes,
                            dag_node_index,
                            f"节点 [{node_name}] 被卡点检测终止: {stuck_reason}。\n"
                            f"重要提示：当前节点因超时被终止，在重新规划时请考虑：\n"
                            f"1. 是否有其他不依赖当前节点结果的任务可以并行执行？\n"
                            f"2. 标记可并行的步骤为同一 parallel_group（如 'A'、'B'）\n"
                            f"3. 避免再次使用可能导致超时的工具或命令",
                            plan_version
                        )
                        if replan_result:
                            plan_version, new_steps, replan_reflection = replan_result
                            self._planned_steps = new_steps
                            yield {
                                "type": "dag_replan",
                                "steps": new_steps,
                                "planned_at": _now_str(),
                                "plan_version": plan_version,
                                "reason": f"卡点检测: {stuck_reason}，正在重新规划（含并行任务）...",
                                "reflection": replan_reflection,
                            }
                        continue

                # 获取结果文本（带全局异常捕获，防止结果处理异常导致DAG崩溃）
                try:
                    result_text = result.get("result", "") if result.get("success") else f"错误: {result.get('error', '')}"
                    node_success = result.get("success", False)

                    # 二进制内容过滤：防止二进制数据泄漏到前端
                    result_text = self._sanitize_output(result_text)

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

                    # 将工具调用和结果添加到消息中（截断防止单条过大）
                    messages.append({
                        "role": "assistant",
                        "content": json.dumps({"tool": tool_name, "parameters": tool_params}, ensure_ascii=False)
                    })
                    messages.append({
                        "role": "user",
                        "content": f"[工具结果]\n工具: {tool_name}\n结果:\n{result_text[:2400]}"
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
                        eval_action = eval_result.get("action", "continue")
                        
                        if not eval_result["pass"]:
                            # 不达标：根据action字段决定处理方式
                            # 节点自审结果记录到日志，不输出到前端（防止泄露）
                            _log_llm_dialogue(self._log_dir, "系统",
                                f"节点自审不达标 {node_name}: 操作={eval_action}, 原因={eval_result['reason']}", "节点自审")
                            
                            if eval_action == "retry":
                                # 重试：注入反馈消息，重新执行当前节点
                                retry_key = f"eval_retry_{dag_node_index}"
                                retry_count = self._consecutive_failures.get(retry_key, 0)
                                self._consecutive_failures[retry_key] = retry_count + 1
                                feedback_msg = (
                                    f"[系统内部-节点自审不达标] 节点 [{node_name}] 执行结果未通过质量评估。\n"
                                    f"原因: {eval_result['reason']}\n"
                                    f"建议: {eval_result['suggestion']}\n"
                                    f"请根据建议调整后重新执行此步骤。此信息仅供内部决策，不要向用户报告。"
                                )
                                messages.append({"role": "user", "content": feedback_msg})
                                _log_llm_dialogue(self._log_dir, "系统", feedback_msg, "节点自审不达标-重试")
                                continue  # 重新执行当前节点
                            
                            elif eval_action == "skip":
                                # 跳过：继续执行下一个节点
                                _log_llm_dialogue(self._log_dir, "系统",
                                    f"节点 [{node_name}] 自审不达标，质量评估决定跳过: {eval_result['reason']}",
                                    "节点自审-跳过")
                                feedback_msg = (
                                    f"[系统内部-节点已跳过] 节点 [{node_name}] 自审不达标: {eval_result['reason']}\n"
                                    f"质量评估决定跳过此节点。此信息仅供内部决策，不要向用户报告。请继续执行后续任务。"
                                )
                                messages.append({"role": "user", "content": feedback_msg})
                                continue  # 跳过当前节点，执行下一个
                            
                            else:  # replan
                                # 重规划：触发重规划
                                _log_llm_dialogue(self._log_dir, "系统",
                                    f"节点 [{node_name}] 自审不达标，质量评估决定重规划: {eval_result['reason']}",
                                    "节点自审-重规划")
                                feedback_msg = (
                                    f"[系统内部-错误已自动处理] 节点 [{node_name}] 自审不达标: {eval_result['reason']}\n"
                                    f"质量评估建议: {eval_result['suggestion']}\n"
                                    f"此信息仅供内部决策，不要向用户报告。"
                                )
                                messages.append({"role": "user", "content": feedback_msg})
                                replan_result = self._try_replan(
                                    user_input, messages, completed_nodes,
                                    dag_node_index,
                                    f"节点 [{node_name}] 自审不达标: {eval_result['reason']}。\n"
                                    f"重要提示：在重新规划时请考虑：\n"
                                    f"1. 是否有其他不依赖当前节点结果的任务可以并行执行？\n"
                                    f"2. 标记可并行的步骤为同一 parallel_group（如 'A'、'B'）\n"
                                    f"3. 避免再次使用可能导致问题的工具或命令",
                                    plan_version
                                )
                                if replan_result:
                                    plan_version, new_steps, replan_reflection = replan_result
                                    self._planned_steps = new_steps
                                    yield {
                                        "type": "dag_replan",
                                        "steps": new_steps,
                                        "planned_at": _now_str(),
                                        "plan_version": plan_version,
                                        "reason": f"节点 [{node_name}] 自审不达标，正在重新规划（含并行任务）...",
                                        "reflection": replan_reflection,
                                    }
                                continue
                        else:
                            # 自审通过，重置评估重试计数
                            self._consecutive_failures.pop(f"eval_retry_{dag_node_index}", None)

                    # 检查所有规划节点是否已执行完，如果是则触发重规划
                    # 但如果已回复用户或任务已完成，不再重规划
                    if node_success and self._planned_steps and not self._reply_sent:
                        max_planned_index = max(s["index"] for s in self._planned_steps)
                        # 安全检查：确保当前节点确实达到了计划的最大index
                        # 防止重规划后新计划index被LLM覆盖为小数字导致误判
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
                                plan_version, new_steps, replan_reflection = replan_result
                                self._planned_steps = new_steps
                                yield {
                                    "type": "dag_replan",
                                    "steps": new_steps,
                                    "planned_at": _now_str(),
                                    "plan_version": plan_version,
                                    "reason": f"规划的{max_planned_index}个节点已全部执行完成，正在规划后续步骤...",
                                    "reflection": replan_reflection,
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
                                    "name": "任务完成", "command": "task_complete",
                                    "status": "completed",
                                    "result": f"所有{max_planned_index}个规划步骤已执行完成",
                                    "completed_at": _now_str(),
                                }
                                return

                    # 节点失败时尝试质量评估（总次数有上限，防止无限重规划）
                    if not node_success:
                        # 连续失败跟踪（防止同一工具无限重试）
                        self._consecutive_failures[tool_name] = \
                            self._consecutive_failures.get(tool_name, 0) + 1

                        # 同一工具连续失败超过阈值，注入强制换策略指令
                        if self._consecutive_failures[tool_name] >= self._max_consecutive_failures:
                            messages.append({
                                "role": "user",
                                "content": f"[系统内部-工具连续失败] 工具 {tool_name} 已连续失败 "
                                           f"{self._consecutive_failures[tool_name]} 次。"
                                           f"请立即停止使用该工具，改用其他方法完成任务。"
                                           f"此信息仅供内部决策，不要向用户报告。"
                            })

                        # 质量评估：决定错误处理方式
                        retry_count = self._node_retry_counts.get(node_name, 0)
                        eval_result = self._evaluate_error_action(
                            node_name, tool_name, tool_params,
                            f"节点执行失败: {result_text[:500]}", retry_count
                        )
                        action = eval_result.get("action", "replan")
                        reason = eval_result.get("reason", "")
                        suggestion = eval_result.get("suggestion", "")
                        
                        # 质量评估结果记录到日志，不输出到前端（防止泄露）
                        _log_llm_dialogue(self._log_dir, "系统",
                            f"质量评估 节点{node_name}: 操作={action}, 原因={reason}", "质量评估")
                        
                        if action == "retry":
                            # 重试：增加计数，注入反馈消息，重新执行当前节点
                            self._node_retry_counts[node_name] = retry_count + 1
                            messages.append({
                                "role": "user",
                                "content": f"[系统内部-错误已自动处理] 节点 [{node_name}] 执行失败: {result_text[:200]}\n"
                                           f"建议: {suggestion}\n"
                                           f"请根据建议调整后重试。此信息仅供内部决策，不要向用户报告此错误。"
                            })
                            continue  # 重新执行当前节点
                        
                        elif action == "skip":
                            # 跳过：继续执行下一个节点
                            self._node_retry_counts[node_name] = 0
                            messages.append({
                                "role": "user",
                                "content": f"[系统内部-节点已跳过] 节点 [{node_name}] 执行失败: {result_text[:200]}\n"
                                           f"质量评估决定跳过此节点: {reason}\n"
                                           f"此信息仅供内部决策，不要向用户报告。请继续执行后续任务。"
                            })
                            continue  # 跳过当前节点，执行下一个
                        
                        else:  # replan
                            # 重规划：重置计数，触发重规划
                            self._node_retry_counts[node_name] = 0
                            replan_result = self._try_replan(
                                user_input, messages, completed_nodes,
                                dag_node_index,
                                f"节点 [{node_name}] 执行失败: {result_text[:200]}。\n"
                                f"重要提示：在重新规划时请考虑：\n"
                                f"1. 是否有其他不依赖当前节点结果的任务可以并行执行？\n"
                                f"2. 标记可并行的步骤为同一 parallel_group（如 'A'、'B'）\n"
                                f"3. 避免再次使用可能失败的工具或命令",
                                plan_version
                            )
                            if replan_result:
                                plan_version, new_steps, replan_reflection = replan_result
                                self._planned_steps = new_steps  # 更新计划
                                yield {
                                    "type": "dag_replan",
                                    "steps": new_steps,
                                    "planned_at": _now_str(),
                                    "plan_version": plan_version,
                                    "reason": f"节点 [{node_name}] 执行失败，正在重新规划（含并行任务）...",
                                    "reflection": replan_reflection,
                                }
                            continue  # 重新进入思考阶段
                    elif node_success:
                        # 成功时重置该工具的连续失败计数
                        self._consecutive_failures.pop(tool_name, None)

                except Exception as result_err:
                    # 结果处理异常：标记节点失败，触发质量评估
                    error_msg = f"结果处理异常: {type(result_err).__name__}: {str(result_err)[:300]}"
                    _log_llm_dialogue(self._log_dir, "系统",
                        f"节点{dag_node_index} [{node_name}] 结果处理异常: {error_msg}", "节点异常")

                    # 结果处理异常记录到日志，不输出到前端（防止泄露）
                    _log_llm_dialogue(self._log_dir, "系统",
                        f"结果处理异常 节点{node_name}: {error_msg}", "结果异常")
                    yield {
                        "type": "dag_node_complete", "index": dag_node_index,
                        "status": "failed", "result": error_msg,
                        "completed_at": _now_str(),
                    }

                    # 记录失败节点
                    completed_nodes.append({
                        "index": dag_node_index,
                        "name": node_name,
                        "status": "failed",
                        "result": error_msg[:500],
                    })
                    self._dag_execution_log.append({
                        "node_index": dag_node_index,
                        "node_name": node_name,
                        "query": json.dumps({"tool": tool_name, "parameters": tool_params}, ensure_ascii=False),
                        "response": error_msg[:500],
                    })

                    # 质量评估：决定错误处理方式
                    self._consecutive_failures[tool_name] = \
                        self._consecutive_failures.get(tool_name, 0) + 1
                    retry_count = self._node_retry_counts.get(node_name, 0)
                    
                    eval_result = self._evaluate_error_action(
                        node_name, tool_name, tool_params,
                        error_msg, retry_count
                    )
                    action = eval_result.get("action", "replan")
                    reason = eval_result.get("reason", "")
                    suggestion = eval_result.get("suggestion", "")
                    
                    # 质量评估结果记录到日志，不输出到前端（防止泄露）
                    _log_llm_dialogue(self._log_dir, "系统",
                        f"质量评估 结果处理异常 {node_name}: 操作={action}, 原因={reason}", "质量评估")
                    
                    if action == "retry":
                        # 重试：增加计数，注入反馈消息，让LLM重新尝试
                        self._node_retry_counts[node_name] = retry_count + 1
                        messages.append({
                            "role": "assistant",
                            "content": json.dumps({"tool": tool_name, "parameters": tool_params}, ensure_ascii=False)
                        })
                        messages.append({
                            "role": "user",
                            "content": f"[系统内部-错误已自动处理] 工具 {tool_name} 结果处理异常: {error_msg}\n"
                                       f"建议: {suggestion}\n"
                                       f"请根据建议调整后重试。此信息仅供内部决策，不要向用户报告此错误。"
                        })
                        continue  # 重新执行当前节点
                    
                    elif action == "skip":
                        # 跳过：记录跳过，继续执行下一个节点
                        self._node_retry_counts[node_name] = 0  # 重置计数
                        messages.append({
                            "role": "assistant",
                            "content": json.dumps({"tool": tool_name, "parameters": tool_params}, ensure_ascii=False)
                        })
                        messages.append({
                            "role": "user",
                            "content": f"[系统内部-节点已跳过] 工具 {tool_name} 结果处理异常: {error_msg}\n"
                                       f"质量评估决定跳过此节点: {reason}\n"
                                       f"此信息仅供内部决策，不要向用户报告此错误。请继续执行后续任务。"
                        })
                        continue  # 跳过当前节点，执行下一个
                    
                    else:  # replan
                        # 重规划：重置计数，触发重规划
                        self._node_retry_counts[node_name] = 0
                        # 连续失败跟踪
                        if self._consecutive_failures[tool_name] >= self._max_consecutive_failures:
                            messages.append({
                                "role": "user",
                                "content": f"[系统警告] 工具 {tool_name} 已连续失败 "
                                           f"{self._consecutive_failures[tool_name]} 次。"
                                           f"请立即停止使用该工具，改用其他方法完成任务。"
                            })
                        
                        # 注入错误信息到消息上下文（防止泄露）
                        messages.append({
                            "role": "assistant",
                            "content": json.dumps({"tool": tool_name, "parameters": tool_params}, ensure_ascii=False)
                        })
                        messages.append({
                            "role": "user",
                            "content": f"[系统内部-错误已自动处理] 工具 {tool_name} 结果处理异常: {error_msg}\n"
                                       f"质量评估建议: {suggestion}\n"
                                       f"此信息仅供内部决策，不要向用户报告此错误。"
                        })
                        
                        replan_result = self._try_replan(
                            user_input, messages, completed_nodes,
                            dag_node_index,
                            f"节点 [{node_name}] 结果处理异常: {error_msg[:200]}。\n"
                            f"重要提示：在重新规划时请考虑：\n"
                            f"1. 是否有其他不依赖当前节点结果的任务可以并行执行？\n"
                            f"2. 标记可并行的步骤为同一 parallel_group（如 'A'、'B'）\n"
                            f"3. 避免再次使用可能导致问题的工具或命令",
                            plan_version
                        )
                        if replan_result:
                            plan_version, new_steps, replan_reflection = replan_result
                            self._planned_steps = new_steps
                            yield {
                                "type": "dag_replan",
                                "steps": new_steps,
                                "planned_at": _now_str(),
                                "plan_version": plan_version,
                                "reason": f"节点 [{node_name}] 结果处理异常，正在重新规划（含并行任务）...",
                                "reflection": replan_reflection,
                            }
                        continue
            elif parsed["type"] == "answer":
                # 兼容旧格式：LLM 返回了 answer 而非 reply_to_user
                # 将其包装为 reply_to_user DAG 节点，不取消后续节点，DAG继续
                try:
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
                        "content": f"[系统内部-中间回复已发送] 内容: {content[:300]}\n"
                                   f"此信息仅供内部决策，不要向用户报告此消息。"
                    })
                except Exception as answer_err:
                    error_msg = f"回复处理异常: {type(answer_err).__name__}: {str(answer_err)[:200]}"
                    _log_llm_dialogue(self._log_dir, "系统", error_msg, "回复异常")
                    yield {
                        "type": "dag_node_complete", "index": dag_node_index,
                        "name": "回复处理异常", "status": "failed",
                        "result": error_msg, "completed_at": _now_str(),
                    }
                    completed_nodes.append({
                        "index": dag_node_index, "name": "回复处理异常",
                        "status": "failed", "result": error_msg[:500],
                    })
                    
                    # 质量评估：决定错误处理方式
                    retry_count = self._node_retry_counts.get("回复处理异常", 0)
                    eval_result = self._evaluate_error_action(
                        "回复处理异常", "reply_to_user", {},
                        error_msg, retry_count
                    )
                    action = eval_result.get("action", "replan")
                    reason = eval_result.get("reason", "")
                    suggestion = eval_result.get("suggestion", "")
                    
                    yield {
                        "type": "dag_node_output", "index": dag_node_index,
                        "output": f"[质量评估] 操作: {action}, 原因: {reason}"
                    }
                    
                    if action == "retry":
                        # 重试：增加计数，注入反馈消息，重新执行
                        self._node_retry_counts["回复处理异常"] = retry_count + 1
                        messages.append({
                            "role": "user",
                            "content": f"[系统内部-错误已自动处理] 回复处理异常: {error_msg}\n"
                                       f"建议: {suggestion}\n"
                                       f"请根据建议调整后重试。此信息仅供内部决策，不要向用户报告此错误。"
                        })
                        continue  # 重新执行
                    
                    elif action == "skip":
                        # 跳过：继续执行下一个节点
                        self._node_retry_counts["回复处理异常"] = 0
                        messages.append({
                            "role": "user",
                            "content": f"[系统内部-节点已跳过] 回复处理异常: {error_msg}\n"
                                       f"质量评估决定跳过此节点: {reason}\n"
                                       f"此信息仅供内部决策，不要向用户报告。请继续执行后续任务。"
                        })
                        continue  # 跳过，执行下一个
                    
                    else:  # replan
                        # 重规划：重置计数，触发重规划
                        self._node_retry_counts["回复处理异常"] = 0
                        replan_result = self._try_replan(
                            user_input, messages, completed_nodes,
                            dag_node_index, error_msg, plan_version
                        )
                        if replan_result:
                            plan_version, new_steps, replan_reflection = replan_result
                            self._planned_steps = new_steps
                            yield {
                                "type": "dag_replan", "steps": new_steps,
                                "planned_at": _now_str(), "plan_version": plan_version,
                                "reason": "回复处理异常，正在重新规划...",
                                "reflection": replan_reflection,
                            }
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
8. **不要只给方案不执行**：如果你已经知道怎么做，就必须立即调用工具去做，而不是把步骤列出来让用户自己操作。任务完成后，可以在reply_to_user的内容末尾简要附上相关的优化建议或注意事项。
9. **自主决策优先**：执行过程中的所有决策（选择方案、确认操作、处理异常）都由你自主完成。完成任务后，如果发现有相关的优化、风险提示或关联需求，可以在回复末尾简要提及（1-3句话）。
10. **ask_user 使用场景**：(1) 必须的凭证信息（密码、API密钥等）使用 interaction_type="input"；(2) 涉及敏感操作（删除文件、修改系统配置等）使用 interaction_type="authorize"；(3) 需要用户从多个方向中选择时使用 interaction_type="confirm"。不要用于日常决策。

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

### 询问用户时（支持多种交互类型）：
```json
{"tool": "ask_user", "parameters": {"question": "你的问题", "context": "问题背景说明", "interaction_type": "input|confirm|authorize", "options": [{"label": "选项1", "value": "v1"}, {"label": "选项2", "value": "v2"}]}}
```
- interaction_type="input"：文本输入框（默认，用于密码、API密钥等）
- interaction_type="confirm"：按钮选择（用于让用户选择方向、确认操作等）
- interaction_type="authorize"：授权审批（用于敏感操作前征求用户批准/拒绝）

### 任务完成时（可选，如果已通过 reply_to_user 回复则不需要）：
```json
{"tool": "task_complete", "parameters": {"summary": "完成摘要"}}
```

## 工作原则
1. **始终围绕用户的原始问题**：你的一切行动都必须服务于用户最初提出的问题。回复用户时，必须直接回答用户的问题，不要偏离主题。
2. 读取文件后再编辑，不要猜测文件内容
3. 先搜索再操作，不要盲目操作
4. 每一步都要基于前一步的结果来决定下一步
5. 如果不确定文件路径，先用 list_directory 或 glob 查找
6. 获取到工具返回的真实数据后，必须通过 `reply_to_user` 将结果回复给用户
7. 如果任务复杂，需要多次工具调用，请逐步执行，不要跳过步骤
8. 回复用户中间结果后，继续执行后续步骤，不要停止
9. 遇到选择时自主判断，不要把决策推给用户
10. **多角度思考**：分析用户请求时，从多个视角思考——横向看关联需求，纵向看深层问题，发现风险主动提示，有更优方案时简要建议
11. **禁止报告内部错误**：系统内部的错误处理信息（包括"[系统内部-错误已自动处理]"、"[系统内部-节点自审不达标]"、"[系统内部-节点已跳过]"、"[系统内部-卡点已自动处理]"等标记的内容）仅供内部决策使用，绝对不能向用户报告。用户看到的应该是正常的任务执行过程和结果，而不是系统内部的错误处理细节。"""

        # 添加日志文件路径信息，方便需要详细查询时找到
        log_path_info = f"""
## 日志文件路径
- DAG执行过程记录（所有query与答复）: {os.path.abspath(os.path.join(self._log_dir, 'llm_dialogue.md'))}
- LLM调用日志: {os.path.abspath(os.path.join(self._log_dir, 'llm_dialogue.md'))}
如需详细查看历史记录，可读取上述文件。"""

        return f"{sys_prompt}\n\n{tool_prompt}\n\n{skill_info}\n\n{format_prompt}\n\n{log_path_info}"

    def _get_skill_matching_info(self) -> str:
        """⑤ 获取技能匹配信息：核心技能清单 + 文件位置 + 规划前查询指引"""
        try:
            from core.skill_manager import SkillManager
            skill_mgr = SkillManager()
            # 获取技能详情（名称+描述）
            skill_details = []
            if os.path.exists(skill_mgr.skill_dir):
                for name in sorted(os.listdir(skill_mgr.skill_dir)):
                    folder = os.path.join(skill_mgr.skill_dir, name)
                    if not os.path.isdir(folder):
                        continue
                    skill_name = name
                    skill_desc = ""
                    # 读取skill.json
                    json_file = os.path.join(folder, "skill.json")
                    if os.path.exists(json_file):
                        try:
                            with open(json_file, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                            skill_name = data.get("name", name)
                            skill_desc = data.get("description", "")[:60]
                        except Exception:
                            pass
                    # 读取SKILL.md
                    elif os.path.exists(os.path.join(folder, "SKILL.md")):
                        try:
                            with open(os.path.join(folder, "SKILL.md"), 'r', encoding='utf-8') as f:
                                content = f.read(500)
                            import re as _re
                            fm = _re.match(r'^---\s*\n(.*?)\n---', content, _re.DOTALL)
                            if fm:
                                for line in fm.group(1).split('\n'):
                                    if line.startswith('name:'):
                                        skill_name = line.split(':', 1)[1].strip().strip('"\'')
                                    elif line.startswith('description:'):
                                        skill_desc = line.split(':', 1)[1].strip().strip('"\'')[:60]
                        except Exception:
                            pass
                    detail = f"- **{skill_name}**：{skill_desc}" if skill_desc else f"- **{skill_name}**"
                    skill_details.append(detail)

            if not skill_details:
                return ""

            catalog_path = os.path.join(skill_mgr.skill_dir, "技能清单.md")
            skills_text = "\n".join(skill_details)

            return f"""## 技能清单（核心索引）
{skills_text}

**完整技能文件位置**：`skill/技能清单.md`（完整索引）、`skill/{{技能名称}}/`（技能详情文件夹）

**规划前必须查询技能**：在生成执行计划前，先读取 `skill/技能清单.md` 检查是否有匹配用户任务的已有技能。如果有，读取对应技能的 SKILL.md 或 skill.md 了解完整用法，按技能文档中的流程执行，避免重复造轮子。"""
        except Exception:
            return ""

    def _understand_requirements(self, user_input: str, messages: list) -> str:
        """需求理解阶段：在规划前先分析任务需求，提高规划质量。
        
        返回需求分析文本，供 _generate_plan 使用。
        分析失败时返回空字符串（不影响后续流程）。
        """
        analysis_prompt = f"""请分析以下任务的需求，帮助制定更好的执行计划。

## 用户任务
{user_input}

## 分析要求
1. **核心目标**：用户真正想要完成什么？
2. **关键约束**：有哪些限制条件或要求？
3. **所需资源**：需要哪些文件、工具、数据？
4. **潜在风险**：可能遇到什么问题？
5. **成功标准**：怎样算完成任务？

请用简洁的中文回答（不超过300字），重点突出对执行计划有指导意义的信息。"""

        analysis_messages = [m for m in messages if m.get("role") == "system"]
        analysis_messages.append({"role": "user", "content": analysis_prompt})

        try:
            response = self._call_llm(analysis_messages)
            if response and len(response) > 20:
                _log_llm_dialogue(self._log_dir, "系统",
                    f"需求分析完成（{len(response)}字）: {response[:200]}...", "需求理解")
                return response
        except Exception as e:
            _log_llm_dialogue(self._log_dir, "系统",
                f"需求分析失败: {str(e)}", "需求理解-异常")

        return ""

    def _generate_plan(self, user_input: str, messages: list, requirement_analysis: str = "") -> list:
        """让 LLM 生成完整的任务计划（带层级结构）。
        
        失败时自动重试1次，仍失败则返回兜底最小计划，确保前端始终有规划卡片。
        """
        plan_messages = messages.copy()
        
        # 构建规划提示词（如果有需求分析结果，加入上下文）
        analysis_section = ""
        if requirement_analysis:
            analysis_section = f"""
## 需求分析
{requirement_analysis}

"""

        plan_prompt = f"""{analysis_section}请基于以上分析，生成一个完整的执行计划。

要求：
1. **先查技能再规划**：如果系统提示中有"技能清单"，先判断任务是否匹配已有技能。如有匹配技能，参考其执行步骤和经验提示来规划，避免重复造轮子
2. 将任务分解为 5-12 个具体步骤（复杂任务可适当增加）
3. 每个步骤用简短的描述（不超过 30 字）
4. 步骤应该按执行顺序排列
5. 最后一个步骤通常是"回复用户结果"
6. 每个步骤需要指定层级（level）：1表示顶层任务，2表示子任务，以此类推
7. 如果某些步骤是其他步骤的子任务，请合理设置层级
8. 确保步骤覆盖任务的所有方面，不要遗漏关键环节
9. **可并行步骤**：如果某些步骤互不依赖（如同时读取多个文件、同时创建多个独立文件、同时执行多个独立操作），用 `parallel_group` 标记为同一组（如 "A"、"B"）。同一组的步骤会并行执行，显著提高效率。相互依赖的步骤不要标记。

请严格按以下 JSON 格式返回，不要添加任何其他文字：
```json
{{"steps": [{{"index": 1, "name": "步骤描述", "level": 1, "parallel_group": ""}}, {{"index": 2, "name": "步骤描述", "level": 1, "parallel_group": ""}}]}}
```"""
        
        plan_messages.append({"role": "user", "content": plan_prompt})
        
        # 最多重试2次（首次 + 1次重试）
        for attempt in range(2):
            try:
                response = self._call_llm(plan_messages)
                if not response:
                    continue
                
                # 提取 JSON
                json_str = self._extract_json(response)
                if json_str:
                    data = json.loads(json_str)
                    if "steps" in data and isinstance(data["steps"], list) and len(data["steps"]) > 0:
                        # 确保每个步骤都有 level 字段，且 index 强制从1开始递增
                        # （不信任LLM返回的index，避免与执行时的dag_node_index不匹配）
                        steps = data["steps"]
                        for i, step in enumerate(steps):
                            if "level" not in step:
                                step["level"] = 1
                            if "parallel_group" not in step:
                                step["parallel_group"] = ""
                            step["index"] = i + 1  # 强制覆盖，确保与执行顺序一致
                        return steps
            except Exception:
                continue
        
        # 兜底：LLM规划两次都失败，返回最小计划确保前端有规划卡片
        _log_llm_dialogue(self._log_dir, "系统",
            "LLM规划失败（2次尝试），使用兜底最小计划", "规划兜底")
        return [
            {"index": 1, "name": "分析任务需求", "level": 1, "parallel_group": ""},
            {"index": 2, "name": "执行任务", "level": 1, "parallel_group": ""},
            {"index": 3, "name": "回复用户结果", "level": 1, "parallel_group": ""},
        ]

    def _try_replan(self, user_input: str, messages: list,
                    completed_nodes: list, current_index: int,
                    fail_reason: str, current_plan_version: int
                    ) -> Optional[tuple]:
        """节点失败时尝试重新规划。
        
        返回 (new_plan_version, new_steps) 或 None（无法重规划）。
        改进：提供完整执行上下文，要求至少生成3个步骤。
        """
        # 总重规划次数检查
        self._total_replan_failures += 1
        if self._total_replan_failures > self._max_total_replan_failures:
            self._replan_limit_reached = True
            _log_llm_dialogue(self._log_dir, "系统",
                f"总重规划次数已达{self._total_replan_failures}次上限，停止重规划",
                "重规划-上限")
            return None
        # 构建重规划上下文（带完整执行日志）
        completed_summary = ""
        execution_log = ""
        for node in completed_nodes:
            status_mark = "✓" if node["status"] == "completed" else "✗"
            completed_summary += f"  {status_mark} 步骤{node['index']}: {node['name']} — {node['status']}\n"
            
            # 添加执行日志（最近5个节点，截断到合理长度）
            if node["index"] >= current_index - 4:  # 只取最近5个节点
                result_preview = node.get("result", "")[:200]
                execution_log += f"  节点{node['index']}: {node['name']}\n"
                execution_log += f"    状态: {node['status']}\n"
                execution_log += f"    结果: {result_preview}...\n\n"

        replan_prompt = f"""任务执行过程中需要重新规划。请基于完整上下文生成高质量的后续计划。

## 原始任务
{user_input}

## 已完成的步骤摘要
{completed_summary if completed_summary else "（无）"}

## 执行日志（最近节点的详细结果）
{execution_log if execution_log else "（无）"}

## 失败/重规划原因
{fail_reason}

## 当前节点编号
{current_index}

## 要求
1. **先反思，再规划**：在生成新计划前，先分析失败的根本原因——是方案有误、工具使用不当、还是信息不足？新计划如何避免同样的问题？
2. 基于完整的执行上下文，重新生成剩余步骤的执行计划
3. 不要重复已完成的步骤
4. 新步骤的 index 从 {current_index + 1} 开始
5. 每个步骤用简短描述（不超过 30 字）
6. 包含层级信息（level）
7. **重要：至少生成 3 个步骤**，确保计划覆盖剩余工作的主要方面
8. 如果任务接近完成，可包含"检查结果"、"整理输出"、"回复用户"等收尾步骤
9. **可并行步骤**：如果某些步骤互不依赖（如同时读取多个文件、同时执行多个独立操作），用 `parallel_group` 标记为同一组（如 "A"、"B"）

请严格按以下 JSON 格式返回，不要添加任何其他文字：
```json
{{"reflection": "失败原因分析和改进思路（1-2句话）", "steps": [{{"index": {current_index + 1}, "name": "新步骤描述", "level": 1, "parallel_group": ""}}, {{"index": {current_index + 2}, "name": "新步骤描述", "level": 1, "parallel_group": ""}}, {{"index": {current_index + 3}, "name": "新步骤描述", "level": 1, "parallel_group": ""}}]}}
```"""

        replan_messages = [m for m in messages if m.get("role") == "system"]
        replan_messages.append({"role": "user", "content": replan_prompt})

        # 重规划步骤数下限：至少生成3个步骤，最多重试3次
        min_steps = 3
        max_attempts = 3
        
        for attempt in range(max_attempts):
            try:
                response = self._call_llm(replan_messages)
                if not response:
                    continue

                json_str = self._extract_json(response)
                if json_str:
                    data = json.loads(json_str)
                    if "steps" in data and isinstance(data["steps"], list):
                        steps = data["steps"]
                        
                        # 检查步骤数是否满足下限
                        if len(steps) < min_steps:
                            _log_llm_dialogue(self._log_dir, "系统",
                                f"重规划只生成{len(steps)}个步骤（要求至少{min_steps}个），重试第{attempt+1}次",
                                "重规划-步骤不足")
                            continue
                        
                        for i, step in enumerate(steps):
                            if "level" not in step:
                                step["level"] = 1
                            if "parallel_group" not in step:
                                step["parallel_group"] = ""
                            # 强制覆盖index：LLM经常返回index:1，不覆盖会导致
                            # dag_node_index >= max_planned_index 误判为"所有节点已完成"
                            step["index"] = current_index + 1 + i
                        new_version = current_plan_version + 1
                        reflection = data.get("reflection", "")
                        if reflection:
                            _log_llm_dialogue(self._log_dir, "系统",
                                f"重规划反思: {reflection}", "重规划-反思")
                        return (new_version, steps, reflection)
            except Exception:
                continue
        
        # 3次尝试都失败（步骤数不足或LLM调用失败）
        _log_llm_dialogue(self._log_dir, "系统",
            f"重规划3次尝试均未生成足够步骤（至少{min_steps}个），放弃重规划",
            "重规划-失败")
        return None

    def _call_llm(self, messages: list) -> Optional[str]:
        """调用LLM并返回文本响应，支持多级压缩重试。

        处理策略：
        1. 预防性压缩：240K字符时主动压缩
        2. ContextLengthExceededError/PayloadTooLargeError：逐级压缩(1→2→3)后重试
        3. 通用Exception：尝试level 1压缩重试1次（可能是未识别的payload过大）
        4. LLMAPIError：配置/URL错误，不重试
        """
        from core.llm_gateway import ContextLengthExceededError, LLMAPIError

        # 预防性压缩：总消息超过240K字符时主动压缩
        total_chars = sum(len(m.get("content", "")) for m in messages)
        if total_chars > 240000:
            _log_llm_dialogue(self._log_dir, "系统",
                f"消息总量{total_chars}字符，预防性压缩(level 1)", "上下文压缩")
            compressed = self._compress_for_retry(messages, level=1)
            if compressed is not messages:
                messages.clear()
                messages.extend(compressed)

        # 日志监控（只记录一次，不在重试中重复）
        self._log_llm_call_messages(messages)

        # --- 主调用 + 多级压缩重试 ---
        max_compress_level = 3
        last_error = None

        for attempt in range(max_compress_level + 1):
            # attempt 0 = 原始调用，attempt 1/2/3 = level 1/2/3 压缩后重试
            try:
                if attempt > 0:
                    level = attempt  # 1, 2, 3
                    _log_llm_dialogue(self._log_dir, "系统",
                        f"第{attempt}次压缩重试（level {level}），消息数={len(messages)}", "上下文压缩")
                    compressed = self._compress_for_retry(messages, level=level)
                    if compressed is not messages:
                        messages.clear()
                        messages.extend(compressed)

                response = self.llm.chat(messages)
                content = response.get("content", "")

                # 检测是否是服务错误消息（防止无限循环）
                if content and content.startswith("服务暂时不可用"):
                    _log_llm_dialogue(self._log_dir, "系统",
                        f"LLM返回服务错误: {content}", "服务错误")
                    return None

                if content:
                    tag = "LLM回复" if attempt == 0 else f"压缩L{attempt}重试成功"
                    _log_llm_dialogue(self._log_dir, "LLM输出", content, tag)

                return content if content else None

            except ContextLengthExceededError as e:
                last_error = e
                _log_llm_dialogue(self._log_dir, "系统",
                    f"上下文过长（第{attempt}次），准备level {attempt + 1}压缩重试: {str(e)[:100]}",
                    "上下文压缩")
                continue  # 进入下一轮压缩重试

            except LLMAPIError as e:
                _log_llm_dialogue(self._log_dir, "系统",
                    f"LLM配置错误（无法重试）: {str(e)}", "配置错误")
                return None

            except Exception as e:
                last_error = e
                # 通用异常：尝试level 1压缩重试1次（可能是未识别的payload过大）
                if attempt == 0:
                    _log_llm_dialogue(self._log_dir, "系统",
                        f"LLM调用异常，尝试level 1压缩重试: {str(e)[:100]}", "调用异常")
                    continue
                else:
                    _log_llm_dialogue(self._log_dir, "系统",
                        f"LLM调用异常（压缩重试后仍失败）: {str(e)[:100]}", "调用失败")
                    return None

        # 所有压缩级别都尝试过仍失败
        _log_llm_dialogue(self._log_dir, "系统",
            f"3级压缩重试均失败: {str(last_error)[:100]}", "重试失败")
        return None

    def _log_llm_call_messages(self, messages: list):
        """记录LLM调用前的消息摘要（监控用，仅记录一次）"""
        sys_msgs = [m for m in messages if m.get("role") == "system"]
        if sys_msgs:
            total_sys_len = sum(len(m.get("content", "")) for m in sys_msgs)
            sys_content = sys_msgs[0].get("content", "")
            _log_llm_dialogue(self._log_dir, "系统",
                f"[System Prompt监控] 共{len(sys_msgs)}条system消息，总长度={total_sys_len}字符，"
                f"第1条前100字={sys_content[:100]}...", "System Prompt验证")
        else:
            _log_llm_dialogue(self._log_dir, "系统",
                "[System Prompt监控] ⚠️ 未找到system message！LLM将缺少身份和工具定义",
                "System Prompt验证")

        user_msgs = [m for m in messages if m.get("_preserve")]
        dag_msgs = [m for m in messages if m.get("role") != "system" and not m.get("_preserve")]

        if user_msgs:
            user_summary = "\n".join(
                f"[{m.get('role', '?')}] {m.get('content', '')[:200]}" for m in user_msgs[-3:])
            _log_llm_dialogue(self._log_dir, "LLM输入-用户聊天", user_summary, "调用LLM")

        if dag_msgs:
            dag_summary = "\n".join(
                f"[{m.get('role', '?')}] {m.get('content', '')[:200]}" for m in dag_msgs[-4:])
            _log_llm_dialogue(self._log_dir, "LLM输入-DAG交互", dag_summary, "调用LLM")

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
                dag_lines.append(f"    输入: {log_entry['query'][:900]}")
                dag_lines.append(f"    输出: {log_entry['response'][:900]}")

        dag_context = "\n".join(dag_lines)

        # 压缩机制：超过30000字符时压缩到15000字符左右，不丢弃
        if len(dag_context) > 30000:
            dag_context = self._compress_dag_context(dag_context, target_size=15000)

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

    def _compress_for_retry(self, messages: list, level: int = 1) -> list:
        """LLM输入过长时分级压缩消息。
        核心原则：用户聊天记录（_preserve=True）在level 1-2时永不丢弃，level 3时保留最近2轮。

        level 1 (mild):      DAG历史截断到450字符，保留最近12条DAG消息，超30000字符压缩旧消息
        level 2 (moderate):  DAG历史截断到250字符，保留最近6条DAG消息，压缩所有旧消息
        level 3 (aggressive):DAG历史截断到120字符，保留最近2条DAG消息，压缩所有旧消息+裁剪旧用户消息
        """
        # --- 参数表：根据压缩等级调整 ---
        params = {
            1: {"log_trunc": 450,  "log_threshold": 30000, "dag_keep": 12, "dag_threshold": 30000,
                "older_target": 15000, "per_msg_min": 600, "trim_user": False},
            2: {"log_trunc": 250,  "log_threshold": 15000, "dag_keep": 6,  "dag_threshold": 0,
                "older_target": 8000,  "per_msg_min": 300, "trim_user": False},
            3: {"log_trunc": 120,  "log_threshold": 5000,  "dag_keep": 2,  "dag_threshold": 0,
                "older_target": 3000,  "per_msg_min": 100, "trim_user": True},
        }
        p = params.get(level, params[1])

        _log_llm_dialogue(self._log_dir, "系统",
            f"上下文压缩 level={level}，消息数={len(messages)}", "上下文压缩")

        # --- 1. 压缩DAG执行历史日志 ---
        log_text = json.dumps(self._dag_execution_log, ensure_ascii=False)
        if len(log_text) > p["log_threshold"]:
            trunc = p["log_trunc"]
            for entry in self._dag_execution_log:
                if len(entry.get("query", "")) > trunc:
                    entry["query"] = entry["query"][:trunc] + "...(已压缩)"
                if len(entry.get("response", "")) > trunc:
                    entry["response"] = entry["response"][:trunc] + "...(已压缩)"
            _log_llm_dialogue(self._log_dir, "系统",
                f"压缩DAG执行历史(level {level})：原始{len(log_text)}字符，每条截断到{trunc}字符",
                "上下文压缩")

        # --- 2. 分离消息类型 ---
        system_msgs = []
        preserve_msgs = []  # 用户聊天记录
        dag_msgs = []       # DAG交互记录

        for msg in messages:
            role = msg.get("role", "")
            if role == "system" and not msg.get("_dag_context"):
                system_msgs.append(msg)
            elif msg.get("_preserve"):
                preserve_msgs.append(msg)
            elif msg.get("_dag_context"):
                dag_msgs.append(msg)
            else:
                dag_msgs.append(msg)

        # --- 3. Level 3: 裁剪旧用户消息，只保留最近2轮(4条) ---
        if p["trim_user"] and len(preserve_msgs) > 4:
            trimmed = preserve_msgs[-4:]
            dropped = len(preserve_msgs) - len(trimmed)
            preserve_msgs = trimmed
            _log_llm_dialogue(self._log_dir, "系统",
                f"Level 3压缩：裁剪用户消息{dropped}条，保留最近4条", "上下文压缩")

        # --- 4. DAG消息滑动窗口 ---
        dag_keep = min(p["dag_keep"], len(dag_msgs))
        dag_recent = dag_msgs[-dag_keep:] if dag_keep > 0 else []
        dag_older = dag_msgs[:-dag_keep] if dag_keep < len(dag_msgs) else []

        result = list(system_msgs)
        result.extend(preserve_msgs)

        # --- 5. 压缩旧DAG消息 ---
        if dag_older:
            threshold = p["dag_threshold"]
            dag_total = sum(len(m.get("content", "")) for m in dag_older)
            # level 1 仅在超过阈值时压缩，level 2/3 始终压缩
            if threshold == 0 or dag_total > threshold:
                target = p["older_target"]
                per_msg = max(p["per_msg_min"], target // len(dag_older))
                older_text = "\n".join(
                    f"{'调用' if m.get('role') == 'assistant' else '结果'}: {m.get('content', '')[:per_msg]}"
                    for m in dag_older
                )
                result.append({
                    "role": "user",
                    "content": f"[DAG历史摘要-L{level}]\n压缩{len(dag_older)}条记录（{dag_total}字符→约{target}字符）:\n{older_text}"
                })
                _log_llm_dialogue(self._log_dir, "系统",
                    f"压缩旧DAG消息：{len(dag_older)}条，{dag_total}→~{target}字符", "上下文压缩")
            else:
                # 未超阈值，保留完整
                older_text = "\n".join(
                    f"{'调用' if m.get('role') == 'assistant' else '结果'}: {m.get('content', '')}"
                    for m in dag_older
                )
                result.append({
                    "role": "user",
                    "content": f"[DAG历史摘要]\n{len(dag_older)}条DAG交互记录:\n{older_text}"
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
        # 非 run_command 工具：添加超时保护
        if tool_name != "run_command":
            general_timeout = self.config.get("tool_general_timeout", 120)  # 默认120秒超时
            result_holder = [None]
            exception_holder = [None]
            
            def _run_general_tool():
                try:
                    result_holder[0] = self.tools.execute(tool_name, tool_params)
                except Exception as e:
                    exception_holder[0] = e
            
            tool_thread = threading.Thread(target=_run_general_tool, daemon=True)
            tool_thread.start()
            tool_thread.join(timeout=general_timeout)
            
            if tool_thread.is_alive():
                # 超时：工具执行时间过长
                reason = f"工具 {tool_name} 执行超时（已等待{general_timeout}秒）"
                return {"success": False, "error": reason}, False, "", ""
            
            if exception_holder[0]:
                return {"success": False, "error": str(exception_holder[0])}, False, "", ""
            
            return result_holder[0] or {"success": False, "error": "无返回"}, False, "", ""

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

    @staticmethod
    def _sanitize_output(text: str) -> str:
        """过滤二进制内容，防止泄漏到前端聊天栏。"""
        if not text:
            return text
        # 检测常见二进制文件签名
        _BINARY_HEADS = ('PK\x03\x04', '%PDF', '\x89PNG', '\xff\xd8\xff',
                         'GIF8', 'RIFF', 'MZ', '\x7fELF', 'BM',
                         '\x00\x00\x01\x00', '\x1f\x8b', 'BZh')
        text_stripped = text.lstrip()
        for sig in _BINARY_HEADS:
            if text_stripped.startswith(sig):
                return "[二进制内容已过滤]"
        # 通用检测：前500字符中null字节或大量非打印字符
        head = text[:500]
        if '\x00' in head:
            return "[二进制内容已过滤]"
        non_print = sum(1 for c in head if ord(c) < 32 and c not in '\t\n\r')
        if len(head) > 0 and non_print / len(head) > 0.3:
            return "[二进制内容已过滤]"
        return text

    @staticmethod
    def _extract_tool_from_step(step: dict) -> tuple:
        """从步骤描述中提取工具名和参数。
        返回 (tool_name, params_dict) 或 ("", {})。
        """
        name = step.get("name", "").lower()
        if not name:
            return "", {}
        # 文件读取
        for kw in ("读取", "读取文件", "查看", "查看文件", "read"):
            if kw in name:
                import re as _re
                m = _re.search(r'[\\/:\w\-\.]+\.\w+', step.get("name", ""))
                path = m.group(0) if m else step.get("name", "").split(kw)[-1].strip()[:200]
                return "read_file", {"path": path}
        # 运行命令
        for kw in ("运行", "执行", "run", "执行命令", "运行命令", "运行脚本", "执行脚本"):
            if kw in name:
                cmd = step.get("name", "").split(kw)[-1].strip()[:200]
                return "run_command", {"command": cmd or "echo placeholder"}
        # 写文件
        for kw in ("写入", "创建文件", "write", "保存"):
            if kw in name:
                import re as _re
                m = _re.search(r'[\\/:\w\-\.]+\.\w+', step.get("name", ""))
                path = m.group(0) if m else ""
                return "write_file", {"path": path, "content": ""}
        # 列目录
        for kw in ("列出", "查看目录", "list", "目录"):
            if kw in name:
                import re as _re
                m = _re.search(r'[\\/:\w\-\.]+[\\/:\w\-\.]*', step.get("name", ""))
                path = m.group(0) if m else "."
                return "list_directory", {"path": path}
        # 搜索
        for kw in ("搜索", "查找", "search", "grep"):
            if kw in name:
                query = step.get("name", "").split(kw)[-1].strip()[:100]
                return "search_files", {"query": query, "path": "."}
        # 无法识别
        return "", {}

    def _find_parallel_group_nodes(self, current_node_index: int) -> list:
        """从当前节点开始，找到同一 parallel_group 的所有连续节点。
        返回节点列表（dict，含 name/tool/command/parallel_group/index）。
        如果当前节点不在任何并行组中，返回空列表。
        """
        if not self._planned_steps:
            return []
        # 1. 找到当前节点的 parallel_group
        pg = ""
        for step in self._planned_steps:
            if step.get("index", 0) == current_node_index:
                pg = step.get("parallel_group", "")
                break
        if not pg:
            return []
        # 2. 收集所有连续同组节点（从 current_node_index 开始）
        group_nodes = []
        for step in self._planned_steps:
            idx = step.get("index", 0)
            if idx < current_node_index:
                continue
            if step.get("parallel_group", "") != pg:
                break
            group_nodes.append(step)
        return group_nodes if len(group_nodes) > 1 else []

    def _execute_parallel_batch(self, group_nodes: list, dag_node_index: int,
                                messages: list, completed_nodes: list,
                                plan_version: int) -> Generator:
        """并行执行一组 parallel_group 节点，yield 所有事件。
        线程池中执行工具，主 Generator 顺序 yield 事件。
        """
        pg = group_nodes[0].get("parallel_group", "?")
        first_idx = group_nodes[0].get("index", dag_node_index)
        last_idx = group_nodes[-1].get("index", dag_node_index)
        yield {
            "type": "dag_node_parallel_start",
            "group": pg,
            "node_count": len(group_nodes),
            "node_indices": [s.get("index", 0) for s in group_nodes],
            "started_at": _now_str(),
        }

        def _exec_one(step_info):
            tool_name = step_info.get("tool", "")
            tool_params = step_info.get("params", {})
            idx = step_info.get("index", 0)
            name = step_info.get("name", "")
            if not tool_name:
                tool_name, tool_params = self._extract_tool_from_step(step_info)
            if not tool_name:
                return {"index": idx, "name": name, "status": "error",
                        "result": f"无法从步骤描述中提取工具调用: {name}"}
            try:
                result = self.tools.execute(tool_name, tool_params)
                result_text = self._sanitize_output(
                    result.get("result", "") if result.get("success")
                    else f"错误: {result.get('error', '')}")
                return {"index": idx, "name": name, "status": "completed",
                        "result": result_text, "success": result.get("success", False),
                        "tool": tool_name, "params": tool_params}
            except Exception as e:
                return {"index": idx, "name": name, "status": "error",
                        "result": str(e), "success": False,
                        "tool": tool_name, "params": tool_params}

        task_futures = {}
        with ThreadPoolExecutor(max_workers=min(len(group_nodes), 5)) as pool:
            for nd in group_nodes:
                future = pool.submit(_exec_one, nd)
                task_futures[future] = nd
            for future in as_completed(task_futures):
                nd = task_futures[future]
                try:
                    res = future.result(timeout=300)
                except Exception as e:
                    res = {"index": nd.get("index", 0), "name": nd.get("name", ""),
                           "status": "error", "result": str(e), "success": False}
                idx = res.get("index", 0)
                nm = res.get("name", "")
                rst = res.get("result", "")
                st = res.get("status", "error")
                yield {
                    "type": "dag_node_start", "index": idx,
                    "name": nm, "command": res.get("tool", "parallel"),
                    "started_at": _now_str(), "parallel_group": pg,
                }
                for i in range(0, len(rst), 200):
                    yield {
                        "type": "dag_node_output", "index": idx,
                        "output": rst[i:i + 200],
                    }
                yield {
                    "type": "dag_node_complete", "index": idx,
                    "name": nm, "status": st,
                    "result": rst[:500], "completed_at": _now_str(),
                    "parallel_group": pg,
                }
                completed_nodes.append({
                    "index": idx, "name": nm, "status": st,
                    "result": rst[:500],
                })
                if res.get("tool"):
                    messages.append({
                        "role": "assistant",
                        "content": json.dumps(
                            {"tool": res["tool"], "parameters": res.get("params", {})},
                            ensure_ascii=False)
                    })
                    messages.append({
                        "role": "user",
                        "content": f"[工具结果 - 并行节点 {idx}]\n工具: {res['tool']}\n结果:\n{rst[:2400]}"
                    })

        yield {
            "type": "dag_node_parallel_end",
            "group": pg,
            "completed_count": len(group_nodes),
            "completed_at": _now_str(),
        }

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
        """终止卡住的子进程树（Windows）- 静默版本，不弹出系统窗口。
        通过 wmic 查找当前 Python 进程的所有子进程并终止。
        使用 CREATE_NO_WINDOW 标志避免弹出 Windows 系统对话框。
        """
        _CREATE_NO_WINDOW = 0x08000000
        try:
            my_pid = os.getpid()
            # 使用 wmic 查找子进程（静默模式）
            result = subprocess.run(
                ["wmic", "process", "where", f"ParentProcessId={my_pid}",
                 "get", "ProcessId", "/format:csv"],
                capture_output=True, text=True, timeout=5,
                creationflags=_CREATE_NO_WINDOW
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    parts = line.strip().split(',')
                    if len(parts) >= 2 and parts[-1].strip().isdigit():
                        child_pid = parts[-1].strip()
                        try:
                            subprocess.run(
                                ["taskkill", "/F", "/T", "/PID", child_pid],
                                capture_output=True, timeout=5,
                                creationflags=_CREATE_NO_WINDOW
                            )
                        except Exception:
                            pass  # 单个进程终止失败不影响其他进程
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
        返回 {"pass": bool, "reason": str, "suggestion": str, "action": str}
        """
        # 截断是正常的大文件保护行为，不应评估为失败
        # 如果输出包含截断标记且内容足够长（>200字符），视为通过
        truncation_markers = ["已截断", "截断为前", "输出截断", "内容截断"]
        has_truncation = any(marker in task_output for marker in truncation_markers)
        if has_truncation and len(task_output) > 200:
            return {"pass": True, "reason": "工具输出已截断（大文件保护），但已读取有意义的数据", "suggestion": "", "action": "continue"}

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

## 失败处理方式
如果评估不通过，请同时指定建议的处理方式：
- **retry**: 重新执行同一节点（适用于执行不完整、输出有误但可修正的情况）
- **replan**: 重新规划后续步骤（适用于方法不可行、需要更换策略的情况）
- **skip**: 跳过此节点继续（适用于非关键步骤、不影响整体任务的情况）

请严格按以下JSON格式返回，不要添加任何其他内容：
{{"pass": true/false, "reason": "判断依据", "suggestion": "具体改进建议", "action": "continue"|"retry"|"replan"|"skip"}}"""

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
                pass_result = data.get("pass", True)
                action = data.get("action", "continue")
                
                # 验证action值的有效性
                if action not in ["continue", "retry", "replan", "skip"]:
                    action = "continue" if pass_result else "replan"
                
                # 逻辑校验：pass=True时action应为continue
                if pass_result and action != "continue":
                    action = "continue"
                
                # 逻辑校验：pass=False时action不应为continue
                if not pass_result and action == "continue":
                    action = "replan"
                
                return {
                    "pass": pass_result,
                    "reason": data.get("reason", ""),
                    "suggestion": data.get("suggestion", ""),
                    "action": action,
                }
        except Exception:
            pass
        # 评估失败时默认通过（避免阻塞流程）
        return {"pass": True, "reason": "评估失败，默认通过", "suggestion": "", "action": "continue"}

    def _evaluate_error_action(self, node_name: str, tool_name: str, tool_params: dict,
                              error_msg: str, retry_count: int) -> dict:
        """评估节点错误，决定处理方式（重试/重规划/跳过）。
        
        Args:
            node_name: 节点名称
            tool_name: 工具名称
            tool_params: 工具参数
            error_msg: 错误信息
            retry_count: 已重试次数
            
        Returns:
            {"action": "retry"|"replan"|"skip", "reason": str, "suggestion": str}
        """
        # 短路：超时类错误且重试<2次直接retry
        if "超时" in error_msg and retry_count < 2:
            return {"action": "retry", "reason": "工具执行超时，可重试", "suggestion": f"重试第{retry_count+1}次"}
        
        # 短路：重试次数已达上限直接replan
        if retry_count >= 2:
            return {"action": "replan", "reason": f"已重试{retry_count}次仍失败", "suggestion": "需要重新规划执行方案"}
        
        try:
            # 本地化导入避免循环依赖
            from .llm import LLM
            llm = LLM(self.config)
            
            # 截断过长的参数和错误信息
            params_str = json.dumps(tool_params, ensure_ascii=False)[:500]
            error_display = error_msg[:1000]
            
            eval_prompt = f"""评估以下DAG节点执行失败的情况，决定最佳处理方式。

## 节点信息
- 节点名称: {node_name}
- 工具: {tool_name}
- 参数: {params_str}
- 错误信息: {error_display}
- 已重试次数: {retry_count}

## 可选操作（三选一）
1. **retry**: 重新执行同一节点
   - 适用场景: 临时性错误、超时、网络波动、资源暂时不可用等可恢复错误
   - 条件: 重试次数 < 2次
   
2. **replan**: 重新规划后续步骤
   - 适用场景: 工具选择错误、参数配置错误、方案不可行、重试已达上限
   - 条件: 任何情况均可
   
3. **skip**: 跳过此节点继续执行
   - 适用场景: 非关键步骤、可选操作、不影响核心任务流程
   - 条件: 必须是明确可跳过的步骤

请严格按以下JSON格式返回，不要添加任何其他内容：
{{"action": "retry"|"replan"|"skip", "reason": "判断依据", "suggestion": "具体建议"}}"""

            messages = [
                {"role": "system", "content": "你是DAG执行质量评估专家。分析错误类型并选择最佳处理方式。"},
                {"role": "user", "content": eval_prompt}
            ]
            
            response = llm.chat(messages)
            content = response.get("content", "")
            json_str = self._extract_json(content)
            
            if json_str:
                data = json.loads(json_str)
                action = data.get("action", "replan")
                
                # 验证action值的有效性
                if action not in ["retry", "replan", "skip"]:
                    action = "replan"
                
                # 二次校验：retry但重试次数已满则强制replan
                if action == "retry" and retry_count >= 2:
                    action = "replan"
                    data["reason"] = f"重试次数已达上限({retry_count}次)，转为重新规划"
                
                return {
                    "action": action,
                    "reason": data.get("reason", ""),
                    "suggestion": data.get("suggestion", "")
                }
        except Exception as e:
            # LLM调用失败时记录日志
            print(f"[质量评估] 错误评估失败: {e}")
        
        # 评估失败时的默认策略：重试<2次则retry，否则replan
        default_action = "retry" if retry_count < 2 else "replan"
        return {
            "action": default_action,
            "reason": f"错误评估失败，默认{'重试' if default_action == 'retry' else '重新规划'}",
            "suggestion": ""
        }

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