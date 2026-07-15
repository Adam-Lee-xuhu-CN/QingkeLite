# -*- coding: utf-8 -*-
"""DAG文件解析器"""
import json
import os
from typing import Optional
from .schemas import DAG, TaskNode


class DAGParser:
    """DAG文件解析器，负责JSON与DAG对象之间的转换"""
    
    def __init__(self, dag_dir: str):
        self.dag_dir = dag_dir
        os.makedirs(dag_dir, exist_ok=True)
    
    def parse_file(self, file_path: str) -> DAG:
        """从JSON文件解析DAG"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return self._parse_dict(data)
        except (json.JSONDecodeError, IOError) as e:
            raise ValueError(f"解析DAG文件失败: {file_path}, 错误: {str(e)}")
    
    def parse_dict(self, data: dict) -> DAG:
        """从字典解析DAG"""
        dag = self._parse_dict(data)
        self.validate(dag)
        return dag
    
    def _parse_dict(self, data: dict) -> DAG:
        """内部：从字典解析DAG对象"""
        nodes = {}
        for node_id, node_data in data.get('nodes', {}).items():
            nodes[node_id] = TaskNode(
                id=node_data['id'],
                name=node_data['name'],
                description=node_data.get('description', ''),
                command=node_data['command'],
                dependencies=node_data.get('dependencies', []),
                status=node_data.get('status', 'pending'),
                result=node_data.get('result'),
                retry_count=node_data.get('retry_count', 0),
                start_time=node_data.get('start_time'),
                end_time=node_data.get('end_time'),
            )
        
        return DAG(
            id=data['id'],
            name=data['name'],
            description=data.get('description', ''),
            nodes=nodes,
            created_at=data.get('created_at', 0.0),
            status=data.get('status', 'pending'),
        )
    
    def to_dict(self, dag: DAG) -> dict:
        """将DAG对象转换为字典"""
        nodes = {}
        for node_id, node in dag.nodes.items():
            nodes[node_id] = {
                'id': node.id,
                'name': node.name,
                'description': node.description,
                'command': node.command,
                'dependencies': node.dependencies,
                'status': node.status,
                'result': node.result,
                'retry_count': node.retry_count,
                'start_time': node.start_time,
                'end_time': node.end_time,
            }
        return {
            'id': dag.id,
            'name': dag.name,
            'description': dag.description,
            'nodes': nodes,
            'created_at': dag.created_at,
            'status': dag.status,
        }
    
    def save(self, dag: DAG) -> str:
        """保存DAG到JSON文件"""
        file_path = os.path.join(self.dag_dir, f"{dag.id}.json")
        data = self.to_dict(dag)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return file_path
    
    def load(self, dag_id: str) -> Optional[DAG]:
        """从文件加载DAG"""
        file_path = os.path.join(self.dag_dir, f"{dag_id}.json")
        if not os.path.exists(file_path):
            return None
        return self.parse_file(file_path)
    
    def list_dags(self) -> list[dict]:
        """列出所有DAG文件"""
        dags = []
        for filename in os.listdir(self.dag_dir):
            if filename.endswith('.json'):
                file_path = os.path.join(self.dag_dir, filename)
                try:
                    dag = self.parse_file(file_path)
                    dags.append({
                        'id': dag.id,
                        'name': dag.name,
                        'status': dag.status,
                        'created_at': dag.created_at,
                    })
                except Exception:
                    continue
        return sorted(dags, key=lambda x: x['created_at'], reverse=True)
    
    def validate(self, dag: DAG) -> tuple[bool, str]:
        """验证DAG合法性"""
        # 检查必需字段
        if not dag.id:
            return False, "DAG ID不能为空"
        if not dag.name:
            return False, "DAG名称不能为空"
        
        # 检查节点依赖是否存在
        node_ids = set(dag.nodes.keys())
        for node_id, node in dag.nodes.items():
            for dep in node.dependencies:
                if dep not in node_ids:
                    return False, f"节点 {node_id} 依赖不存在的节点 {dep}"
        
        # 检查循环依赖
        if self._has_cycle(dag):
            return False, "检测到循环依赖"
        
        return True, "验证通过"
    
    def _has_cycle(self, dag: DAG) -> bool:
        """检测循环依赖（拓扑排序）"""
        visited = set()
        rec_stack = set()
        
        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)
            
            # 查找依赖此节点的所有节点
            for nid, node in dag.nodes.items():
                if node_id in node.dependencies:
                    if nid not in visited:
                        if dfs(nid):
                            return True
                    elif nid in rec_stack:
                        return True
            
            rec_stack.discard(node_id)
            return False
        
        for node_id in dag.nodes:
            if node_id not in visited:
                if dfs(node_id):
                    return True
        return False
