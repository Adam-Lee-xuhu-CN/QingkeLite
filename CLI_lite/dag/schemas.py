# -*- coding: utf-8 -*-
"""DAG 数据结构定义"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TaskNode:
    """DAG任务节点"""
    id: str                    # 节点唯一标识
    name: str                  # 任务名称
    description: str           # 任务描述
    command: str               # 执行命令（CLI/PowerShell）
    dependencies: list[str] = field(default_factory=list)  # 依赖的前置节点ID
    status: str = "pending"    # pending | running | completed | failed | retrying
    result: Optional[str] = None  # 执行结果
    retry_count: int = 0       # 当前重试次数
    start_time: Optional[float] = None
    end_time: Optional[float] = None


@dataclass
class DAG:
    """DAG任务图"""
    id: str                    # DAG唯一标识
    name: str                  # DAG名称
    description: str           # DAG描述
    nodes: dict[str, TaskNode] = field(default_factory=dict)  # 节点集合
    created_at: float = 0.0
    status: str = "pending"    # pending | running | completed | failed
