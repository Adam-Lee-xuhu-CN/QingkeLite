# -*- coding: utf-8 -*-
"""DAG任务调度器"""
import time
from typing import Generator, Callable, Optional
from .schemas import DAG, TaskNode


class DAGScheduler:
    """DAG任务调度器，按依赖顺序执行任务节点"""
    
    def __init__(self, dag: DAG, executor: Callable[[str], str], max_retries: int = 2):
        self.dag = dag
        self.executor = executor  # 执行函数，接收命令字符串，返回输出字符串
        self.max_retries = max_retries
    
    def execute(self) -> Generator[TaskNode, None, None]:
        """执行DAG，yield每个节点的状态变化"""
        self.dag.status = "running"
        
        while not self._is_complete():
            ready_nodes = self._get_ready_nodes()
            if not ready_nodes:
                # 没有可执行节点但DAG未完成，说明有节点失败了
                break
            
            for node in ready_nodes:
                yield from self._execute_node(node)
        
        # 更新DAG状态
        if any(n.status == "failed" for n in self.dag.nodes.values()):
            self.dag.status = "failed"
        else:
            self.dag.status = "completed"
    
    def _execute_node(self, node: TaskNode) -> Generator[TaskNode, None, None]:
        """执行单个节点，包含自动重试机制"""
        node.status = "running"
        node.start_time = time.time()
        yield node
        
        for attempt in range(self.max_retries + 1):
            try:
                node.result = self.executor(node.command)
                node.status = "completed"
                node.end_time = time.time()
                yield node
                return
            except Exception as e:
                if attempt < self.max_retries:
                    node.status = "retrying"
                    node.retry_count = attempt + 1
                    yield node
                else:
                    node.status = "failed"
                    node.result = f"失败（重试{self.max_retries}次）: {str(e)}"
                    node.end_time = time.time()
                    yield node
                    return
    
    def retry_node(self, node_id: str) -> Generator[TaskNode, None, None]:
        """手动重试指定失败节点"""
        node = self.dag.nodes.get(node_id)
        if not node:
            raise ValueError(f"Node {node_id} not found")
        
        # 重置节点状态
        node.status = "pending"
        node.retry_count = 0
        node.result = None
        
        # 重新执行
        yield from self._execute_node(node)
        
        # 如果成功，继续执行后续依赖节点
        if node.status == "completed":
            ready_nodes = self._get_ready_nodes()
            for n in ready_nodes:
                if n.status == "pending":
                    yield from self._execute_node(n)
    
    def _is_complete(self) -> bool:
        """检查DAG是否执行完成"""
        for node in self.dag.nodes.values():
            if node.status in ("pending", "running", "retrying"):
                # 检查是否可以执行（依赖都完成或失败）
                deps_done = all(
                    self.dag.nodes[dep].status in ("completed", "failed")
                    for dep in node.dependencies
                )
                if deps_done and node.status in ("pending",):
                    return False  # 还有可执行节点
                if node.status in ("running", "retrying"):
                    return False
        return True
    
    def _get_ready_nodes(self) -> list[TaskNode]:
        """获取可以执行的节点（依赖都已完成）"""
        ready = []
        for node in self.dag.nodes.values():
            if node.status != "pending":
                continue
            
            # 检查所有依赖是否已完成
            deps_completed = True
            for dep_id in node.dependencies:
                dep_node = self.dag.nodes.get(dep_id)
                if not dep_node or dep_node.status != "completed":
                    deps_completed = False
                    break
            
            if deps_completed:
                ready.append(node)
        
        return ready
    
    def get_wbs_data(self) -> dict:
        """生成WBS展示数据"""
        return {
            "id": self.dag.id,
            "name": self.dag.name,
            "status": self.dag.status,
            "nodes": [
                {
                    "id": n.id,
                    "name": n.name,
                    "status": n.status,
                    "dependencies": n.dependencies,
                    "retry_count": n.retry_count,
                    "result": n.result,
                }
                for n in self.dag.nodes.values()
            ]
        }
    
    def get_progress(self) -> dict:
        """获取执行进度"""
        total = len(self.dag.nodes)
        completed = sum(1 for n in self.dag.nodes.values() if n.status == "completed")
        failed = sum(1 for n in self.dag.nodes.values() if n.status == "failed")
        running = sum(1 for n in self.dag.nodes.values() if n.status in ("running", "retrying"))
        
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "running": running,
            "percentage": int(completed / total * 100) if total > 0 else 0,
        }


class DispatchScheduler:
    """DAG调度中心，管理多个DAG任务的生命周期"""
    
    def __init__(self, config: dict, executor, dag_parser, llm_gateway=None):
        self.max_retries = config.get("max_retries", 2)
        self.executor = executor
        self.dag_parser = dag_parser
        self.llm = llm_gateway
        self.active_dags = {}  # {dag_id: DAGScheduler}
    
    def submit_dag(self, dag_id: str, tasks: list, user_input: str) -> dict:
        """提交DAG任务"""
        try:
            # 通过LLM生成DAG结构（如果有LLM）
            if self.llm:
                dag_data = self.llm.generate_dag(user_input, tasks)
            else:
                # 简单生成顺序执行的DAG
                dag_data = self._generate_simple_dag(dag_id, tasks, user_input)
            
            dag_data["id"] = dag_id
            
            # 解析并保存DAG
            dag = self.dag_parser.parse_dict(dag_data)
            self.dag_parser.save(dag)
            
            # 创建调度器
            scheduler = DAGScheduler(
                dag=dag,
                executor=self.executor.execute,
                max_retries=self.max_retries
            )
            self.active_dags[dag_id] = scheduler
            
            return {"success": True, "dag_id": dag_id, "status": "submitted"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _generate_simple_dag(self, dag_id: str, tasks: list, user_input: str) -> dict:
        """生成简单的顺序执行DAG"""
        nodes = {}
        for i, task in enumerate(tasks):
            node_id = f"task_{i+1}"
            nodes[node_id] = {
                "id": node_id,
                "name": task,
                "description": task,
                "command": f"echo '{task}'",
                "dependencies": [f"task_{i}"] if i > 0 else []
            }
        
        return {
            "id": dag_id,
            "name": user_input[:50],
            "description": user_input,
            "nodes": nodes,
            "created_at": time.time()
        }
    
    def run_dag(self, dag_id: str):
        """执行DAG并yield状态变化"""
        scheduler = self.active_dags.get(dag_id)
        if not scheduler:
            dag = self.dag_parser.load(dag_id)
            if not dag:
                raise ValueError(f"DAG {dag_id} not found")
            scheduler = DAGScheduler(
                dag=dag,
                executor=self.executor.execute,
                max_retries=self.max_retries
            )
            self.active_dags[dag_id] = scheduler
        
        for node in scheduler.execute():
            yield node
    
    def get_dag_status(self, dag_id: str) -> dict:
        """获取DAG执行状态"""
        scheduler = self.active_dags.get(dag_id)
        if scheduler:
            return scheduler.get_wbs_data()
        
        dag = self.dag_parser.load(dag_id)
        if dag:
            return {
                "id": dag.id,
                "name": dag.name,
                "status": dag.status,
                "nodes": [
                    {
                        "id": n.id,
                        "name": n.name,
                        "status": n.status,
                        "dependencies": n.dependencies,
                        "retry_count": n.retry_count,
                        "result": n.result,
                    }
                    for n in dag.nodes.values()
                ]
            }
        return {"error": "DAG not found"}
    
    def retry_dag_node(self, dag_id: str, node_id: str = None):
        """重试DAG节点"""
        scheduler = self.active_dags.get(dag_id)
        if not scheduler:
            dag = self.dag_parser.load(dag_id)
            if not dag:
                raise ValueError(f"DAG {dag_id} not found")
            scheduler = DAGScheduler(
                dag=dag,
                executor=self.executor.execute,
                max_retries=self.max_retries
            )
            self.active_dags[dag_id] = scheduler
        
        if node_id:
            yield from scheduler.retry_node(node_id)
        else:
            # 重试所有失败节点
            for node in scheduler.dag.nodes.values():
                if node.status == "failed":
                    yield from scheduler.retry_node(node.id)
