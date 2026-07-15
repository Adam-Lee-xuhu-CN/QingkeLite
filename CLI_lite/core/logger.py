"""日志管理器 - MD文档格式记录"""
import os
import time
from datetime import datetime


class Logger:
    """日志管理器，以Markdown格式记录会话和执行日志"""

    def __init__(self, log_dir: str, level: str = "DEBUG"):
        self.log_dir = log_dir
        self.level = level
        os.makedirs(log_dir, exist_ok=True)
        self._levels = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}

    def _get_log_file(self, date: str = None) -> str:
        """获取今日日志文件路径"""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"{date}.md")

    def _append(self, content: str, date: str = None):
        """追加日志内容（每次写入后立即flush+fsync，确保程序意外退出时不丢失）"""
        file_path = self._get_log_file(date)
        with open(file_path, 'a', encoding='utf-8') as f:
            f.write(content + "\n")
            f.flush()
            os.fsync(f.fileno())

    def log_session_start(self, session_id: str, user_input: str, date: str = None):
        """记录会话开始"""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._append(f"""
## 会话信息
- 会话ID: {session_id}
- 开始时间: {ts}
- 用户输入: {user_input}
""", date)

    def log_agent_analysis(self, action: str, detail: dict, date: str = None):
        """记录前台Agent分析结果"""
        self._append(f"""
## 前台接待Agent分析
- 处理结果: {action}
- 详情: {detail}
""", date)

    def log_dag_execution(self, dag_id: str, node_results: list, date: str = None):
        """记录DAG执行详情"""
        content = f"\n## DAG执行: {dag_id}\n"
        for r in node_results:
            content += f"""
### 任务{r['index']}: {r['name']}
- 命令: {r['command']}
- 状态: {r['status']}
- 输出: {r.get('result', '')}
- 耗时: {r.get('duration', 0)}s
"""
        self._append(content, date)

    def log_agentic_loop(self, session_id: str, steps: list, date: str = None):
        """记录Agentic Loop执行详情"""
        content = f"\n## Agentic Loop执行: {session_id}\n"
        content += f"- 总步骤数: {len(steps)}\n"
        for step in steps:
            # 兼容 DAG 节点事件格式
            step_type = step.get('type', 'unknown')
            step_index = step.get('index', 0)
            
            if step_type == 'dag_node_start':
                content += f"""
### 节点 {step_index} 开始
- 名称: {step.get('name', '')}
- 命令: {step.get('command', '')}
"""
            elif step_type == 'dag_node_output':
                content += f"- 输出: {step.get('output', '')[:200]}\n"
            elif step_type == 'dag_node_complete':
                content += f"""- 状态: {step.get('status', '')}
- 结果: {step.get('result', '')[:200]}
"""
            else:
                # 兼容旧格式
                content += f"""
### 步骤 {step.get('step', step_index)}
- 动作: {step.get('action', step_type)}
- 描述: {step.get('content', '')}
"""
                if step.get('tool_name'):
                    content += f"- 工具: {step['tool_name']}\n"
                if step.get('tool_params'):
                    content += f"- 参数: {step['tool_params']}\n"
                if step.get('tool_result'):
                    content += f"- 结果: {step['tool_result'][:200]}\n"
        self._append(content, date)

    def log_preference_update(self, updates: list, date: str = None):
        """记录偏好更新事件"""
        content = "\n## 偏好更新\n"
        for u in updates:
            p = u.get('preference', {})
            content += f"- {u['type']}: {p.get('level1', '')} > {p.get('level2', '')} > {p.get('level3', '')}\n"
        self._append(content, date)

    def log_turn(self, turn_count: int, triggered: bool, date: str = None):
        """记录对话轮次"""
        self._append(f"""
## 对话轮次记录
- 当前轮次: {turn_count}
- 触发偏好学习: {'是' if triggered else '否'}
""", date)

    def log_final_response(self, response: str, date: str = None):
        """记录最终响应"""
        self._append(f"\n## 最终响应\n{response}\n\n---\n", date)

    def log_error(self, error: str, date: str = None):
        """记录错误信息"""
        self._append(f"\n## 错误\n{error}\n", date)

    def get_logs(self, date: str = None) -> list:
        """获取日志文件列表，返回包含日期和记录条数的字典列表"""
        if date:
            file_path = self._get_log_file(date)
            if os.path.exists(file_path):
                entries = self._count_entries(file_path)
                return [{"date": date, "entries": entries}]
            return []

        logs = []
        for filename in os.listdir(self.log_dir):
            if filename.endswith('.md'):
                log_date = filename.replace('.md', '')
                file_path = os.path.join(self.log_dir, filename)
                entries = self._count_entries(file_path)
                logs.append({"date": log_date, "entries": entries})
        return sorted(logs, key=lambda x: x["date"], reverse=True)

    def _count_entries(self, file_path: str) -> int:
        """统计日志文件中的记录条数（以 ## 标题为计数单位）"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return content.count('\n## ')
        except Exception:
            return 0

    def get_log_content(self, date: str = None) -> str:
        """获取指定日期日志内容"""
        file_path = self._get_log_file(date)
        if not os.path.exists(file_path):
            return ""
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
