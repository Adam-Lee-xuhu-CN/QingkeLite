"""CLI_lite 单元测试"""
import unittest
import os
import json
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dag.schemas import DAG, TaskNode
from dag.dag_parser import DAGParser
from dag.dag_scheduler import DAGScheduler
from core.llm_gateway import LLMGateway
from core.context_manager import ContextManager, ConversationContext
from core.agent.front_desk_agent import FrontDeskAgent
from core.logger import Logger
from dispatcher.task_executor import TaskExecutor


class TestTaskNode(unittest.TestCase):
    """TaskNode数据结构测试"""

    def test_create_node(self):
        """测试创建节点"""
        node = TaskNode(
            id="task_1",
            name="Test Task",
            description="Test description",
            command="echo 'hello'"
        )
        self.assertEqual(node.id, "task_1")
        self.assertEqual(node.status, "pending")
        self.assertEqual(node.retry_count, 0)
        self.assertIsNone(node.result)

    def test_node_defaults(self):
        """测试默认值"""
        node = TaskNode(id="1", name="Test", description="", command="echo test")
        self.assertEqual(node.status, "pending")
        self.assertEqual(node.retry_count, 0)


class TestDAG(unittest.TestCase):
    """DAG数据结构测试"""

    def test_create_dag(self):
        """测试创建DAG"""
        dag = DAG(
            id="dag_1",
            name="Test DAG",
            description="Test description"
        )
        self.assertEqual(dag.id, "dag_1")
        self.assertEqual(dag.status, "pending")
        self.assertEqual(len(dag.nodes), 0)


class TestDAGParser(unittest.TestCase):
    """DAGParser测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.parser = DAGParser(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_parse_dict(self):
        """测试解析字典"""
        data = {
            "id": "dag_test",
            "name": "测试DAG",
            "description": "Test DAG",
            "nodes": {
                "task_1": {
                    "id": "task_1",
                    "name": "Task 1",
                    "description": "First task",
                    "command": "echo 'task1'",
                    "dependencies": []
                },
                "task_2": {
                    "id": "task_2",
                    "name": "Task 2",
                    "description": "Second task",
                    "command": "echo 'task2'",
                    "dependencies": ["task_1"]
                }
            },
            "created_at": 1720000000
        }
        dag = self.parser.parse_dict(data)
        self.assertEqual(dag.id, "dag_test")
        self.assertEqual(len(dag.nodes), 2)
        self.assertIn("task_1", dag.nodes)
        self.assertIn("task_2", dag.nodes)

    def test_save_and_load(self):
        """测试保存和加载"""
        data = {
            "id": "dag_test",
            "name": "Test",
            "description": "Test",
            "nodes": {
                "task_1": {
                    "id": "task_1",
                    "name": "Task 1",
                    "description": "",
                    "command": "echo test",
                    "dependencies": []
                }
            },
            "created_at": 1720000000
        }
        dag = self.parser.parse_dict(data)
        self.parser.save(dag)

        loaded = self.parser.load("dag_test")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.id, "dag_test")

    def test_list_dags(self):
        """测试列出DAG"""
        # 创建多个DAG
        for i in range(3):
            data = {
                "id": f"dag_{i}",
                "name": f"Test DAG {i}",
                "description": "",
                "nodes": {},
                "created_at": 1720000000 + i
            }
            dag = self.parser.parse_dict(data)
            self.parser.save(dag)

        dags = self.parser.list_dags()
        self.assertEqual(len(dags), 3)

    def test_validate_success(self):
        """测试验证通过"""
        data = {
            "id": "dag_test",
            "name": "Test",
            "description": "",
            "nodes": {
                "task_1": {
                    "id": "task_1",
                    "name": "Task 1",
                    "description": "",
                    "command": "echo test",
                    "dependencies": []
                }
            },
            "created_at": 1720000000
        }
        dag = self.parser.parse_dict(data)
        valid, msg = self.parser.validate(dag)
        self.assertTrue(valid)

    def test_validate_missing_dependency(self):
        """测试验证缺失依赖"""
        data = {
            "id": "dag_test",
            "name": "Test",
            "description": "",
            "nodes": {
                "task_1": {
                    "id": "task_1",
                    "name": "Task 1",
                    "description": "",
                    "command": "echo test",
                    "dependencies": ["nonexistent"]
                }
            },
            "created_at": 1720000000
        }
        dag = self.parser.parse_dict(data)
        valid, msg = self.parser.validate(dag)
        self.assertFalse(valid)
        self.assertIn("依赖不存在", msg)

    def test_no_cycle(self):
        """测试无循环依赖"""
        data = {
            "id": "dag_test",
            "name": "Test",
            "description": "",
            "nodes": {
                "task_1": {
                    "id": "task_1",
                    "name": "Task 1",
                    "description": "",
                    "command": "echo test",
                    "dependencies": []
                },
                "task_2": {
                    "id": "task_2",
                    "name": "Task 2",
                    "description": "",
                    "command": "echo test",
                    "dependencies": ["task_1"]
                }
            },
            "created_at": 1720000000
        }
        dag = self.parser.parse_dict(data)
        valid, msg = self.parser.validate(dag)
        self.assertTrue(valid)

    def test_cycle_detection(self):
        """测试循环依赖检测"""
        data = {
            "id": "dag_cycle",
            "name": "Cycle Test",
            "description": "",
            "nodes": {
                "task_1": {
                    "id": "task_1",
                    "name": "Task 1",
                    "description": "",
                    "command": "echo test",
                    "dependencies": ["task_2"]
                },
                "task_2": {
                    "id": "task_2",
                    "name": "Task 2",
                    "description": "",
                    "command": "echo test",
                    "dependencies": ["task_1"]
                }
            },
            "created_at": 1720000000
        }
        dag = self.parser.parse_dict(data)
        valid, msg = self.parser.validate(dag)
        self.assertFalse(valid)
        self.assertIn("循环依赖", msg)


class TestDAGScheduler(unittest.TestCase):
    """DAGScheduler测试"""

    def test_execute_simple_dag(self):
        """测试执行简单DAG"""
        dag = DAG(
            id="dag_test",
            name="Test DAG",
            description="Test",
            nodes={
                "task_1": TaskNode(
                    id="task_1",
                    name="Task 1",
                    description="",
                    command="echo 'hello'",
                    dependencies=[]
                ),
                "task_2": TaskNode(
                    id="task_2",
                    name="Task 2",
                    description="",
                    command="echo 'world'",
                    dependencies=["task_1"]
                )
            }
        )

        def mock_executor(cmd):
            return "success"

        scheduler = DAGScheduler(dag, mock_executor, max_retries=0)
        nodes_executed = []
        for node in scheduler.execute():
            nodes_executed.append(node.id)

        self.assertEqual(dag.status, "completed")
        self.assertIn("task_1", nodes_executed)
        self.assertIn("task_2", nodes_executed)

    def test_get_wbs_data(self):
        """测试获取WBS数据"""
        dag = DAG(
            id="dag_test",
            name="Test DAG",
            description="Test",
            nodes={
                "task_1": TaskNode(
                    id="task_1",
                    name="Task 1",
                    description="",
                    command="echo test",
                    dependencies=[]
                )
            }
        )

        scheduler = DAGScheduler(dag, lambda cmd: "ok", max_retries=0)
        wbs = scheduler.get_wbs_data()

        self.assertEqual(wbs["id"], "dag_test")
        self.assertEqual(len(wbs["nodes"]), 1)
        self.assertEqual(wbs["nodes"][0]["id"], "task_1")

    def test_get_progress(self):
        """测试获取进度"""
        dag = DAG(
            id="dag_test",
            name="Test DAG",
            description="Test",
            nodes={
                "task_1": TaskNode(id="task_1", name="T1", description="", command="echo test", dependencies=[]),
                "task_2": TaskNode(id="task_2", name="T2", description="", command="echo test", dependencies=["task_1"]),
            }
        )

        scheduler = DAGScheduler(dag, lambda cmd: "ok", max_retries=0)
        for node in scheduler.execute():
            pass

        progress = scheduler.get_progress()
        self.assertEqual(progress["total"], 2)
        self.assertEqual(progress["completed"], 2)
        self.assertEqual(progress["percentage"], 100)


class TestLLMGateway(unittest.TestCase):
    """LLMGateway测试"""

    def setUp(self):
        self.config = {
            "dify": {
                "api_url": "http://localhost:8080/v1/chat-messages",
                "api_key": "",
                "timeout": 10
            }
        }
        self.gateway = LLMGateway(self.config)

    def test_analyze_simple(self):
        """测试简单分析"""
        result = self.gateway._default_analyze("你好")
        self.assertFalse(result["need_dag"])

    def test_analyze_complex(self):
        """测试复杂分析"""
        result = self.gateway._default_analyze("创建项目")
        self.assertTrue(result["need_dag"])
        self.assertIn("tasks", result)

    def test_compare_preferences(self):
        """测试偏好对比"""
        old_prefs = [{"level1": "开发", "level2": "代码", "level3": "Python"}]
        new_prefs = [{"level1": "开发", "level2": "代码", "level3": "Python"}, {"level1": "运维", "level2": "部署", "level3": "Docker"}]
        result = self.gateway._compare_preferences(new_prefs, old_prefs)
        self.assertTrue(result["need_update"])


class TestContextManager(unittest.TestCase):
    """ContextManager测试"""

    def setUp(self):
        self.config = {
            "system_prompt_file": os.path.join(
                os.path.dirname(__file__), "..", "config", "sys_prompt.md"
            ),
            "history_rounds": 3,
            "keyword_dict_file": os.path.join(
                os.path.dirname(__file__), "..", "data", "dictionary", "keywords.json"
            ),
            "max_snippet_length": 2000,
            "max_tokens": 8000,
        }
        self.temp_dir = tempfile.mkdtemp()
        self.config["session_dir"] = self.temp_dir

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_build_context(self):
        """测试构建上下文"""
        cm = ContextManager(self.config)
        ctx = cm.build_context("你好", "test_session")
        self.assertIsInstance(ctx, ConversationContext)
        self.assertIsNotNone(ctx.system_prompt)

    def test_save_conversation(self):
        """测试保存对话"""
        cm = ContextManager(self.config)
        cm.save_conversation("test_session", "Hello", "Hi there!")

        file_path = os.path.join(self.temp_dir, "test_session.json")
        self.assertTrue(os.path.exists(file_path))

        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            self.assertEqual(len(data["conversations"]), 2)

    def test_keyword_split(self):
        """测试词组划分"""
        cm = ContextManager(self.config)
        keywords = cm._split_keywords("创建文件目录")
        self.assertIn("创建", keywords)
        self.assertIn("文件", keywords)


class TestFrontDeskAgent(unittest.TestCase):
    """FrontDeskAgent测试"""

    def test_quick_check_greeting(self):
        """测试快速检查-问候语"""
        from core.llm_gateway import LLMGateway
        from core.context_manager import ContextManager

        config = {"dify": {"api_url": "http://localhost:8080/v1/chat-messages", "api_key": ""}}
        llm = LLMGateway(config)
        cm = ContextManager({})
        agent = FrontDeskAgent(llm, cm)

        result = agent.process("你好", "test_session")
        self.assertEqual(result["action"], "direct_reply")
        self.assertFalse(result["dag_suggested"])


class TestLogger(unittest.TestCase):
    """Logger测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.logger = Logger(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_log_session_start(self):
        """测试记录会话开始"""
        self.logger.log_session_start("test_session", "Hello")
        logs = self.logger.get_logs()
        self.assertEqual(len(logs), 1)

    def test_get_log_content(self):
        """测试获取日志内容"""
        self.logger.log_session_start("test_session", "Hello")
        content = self.logger.get_log_content()
        self.assertIn("test_session", content)


class TestTaskExecutor(unittest.TestCase):
    """TaskExecutor测试"""

    def test_execute_command(self):
        """测试执行命令"""
        executor = TaskExecutor(shell="powershell", timeout=5)
        result = executor.execute("echo 'hello'")
        self.assertEqual(result.strip(), "hello")

    def test_execute_failed_command(self):
        """测试执行失败命令"""
        executor = TaskExecutor(shell="powershell", timeout=5)
        with self.assertRaises(RuntimeError):
            executor.execute("exit 1")


if __name__ == "__main__":
    unittest.main()
