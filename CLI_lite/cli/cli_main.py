"""CLI命令体系 - 各子命令实现"""
import sys
import json
import os


def cmd_dag_list(engine, args):
    """列出所有DAG"""
    dags = engine.dag_parser.list_dags()
    if not dags:
        print("没有DAG任务")
        return
    print(f"{'ID':<30} {'名称':<20} {'状态':<10}")
    print("-" * 60)
    for dag in dags:
        print(f"{dag['id']:<30} {dag['name']:<20} {dag['status']:<10}")


def cmd_dag_show(engine, args):
    """查看DAG详情"""
    if len(args) < 1:
        print("用法: dag show <dag_id>")
        return
    dag_id = args[0]
    dag = engine.dag_parser.load(dag_id)
    if not dag:
        print(f"DAG {dag_id} 不存在")
        return
    print(f"ID: {dag.id}")
    print(f"名称: {dag.name}")
    print(f"描述: {dag.description}")
    print(f"状态: {dag.status}")
    print(f"\n节点:")
    for node_id, node in dag.nodes.items():
        print(f"  - {node_id}: {node.name} [{node.status}] -> {node.command}")


def cmd_dag_run(engine, args):
    """执行DAG"""
    if len(args) < 1:
        print("用法: dag run <dag_id>")
        return
    dag_id = args[0]
    dag = engine.dag_parser.load(dag_id)
    if not dag:
        print(f"DAG {dag_id} 不存在")
        return
    print(f"开始执行DAG: {dag.name}")
    for node in engine.scheduler.run_dag(dag_id):
        print(f"  [{node.status}] {node.name}")
    print("执行完成")


def cmd_dag_retry(engine, args):
    """重试DAG节点"""
    if len(args) < 1:
        print("用法: dag retry <dag_id> [node_id]")
        return
    dag_id = args[0]
    node_id = args[1] if len(args) > 1 else None
    print(f"重试DAG: {dag_id}" + (f" 节点: {node_id}" if node_id else ""))
    for node in engine.scheduler.retry_dag_node(dag_id, node_id):
        print(f"  [{node.status}] {node.name}")
    print("重试完成")


def cmd_dag_delete(engine, args):
    """删除DAG"""
    if len(args) < 1:
        print("用法: dag delete <dag_id>")
        return
    dag_id = args[0]
    dag = engine.dag_parser.load(dag_id)
    if not dag:
        print(f"DAG {dag_id} 不存在")
        return
    dag_path = os.path.join(engine.dag_parser.dag_dir, f"{dag_id}.json")
    os.remove(dag_path)
    print(f"DAG {dag_id} 已删除")


def cmd_task_run(engine, args):
    """执行单个任务"""
    if len(args) < 1:
        print("用法: task run <command>")
        return
    command = " ".join(args)
    try:
        result = engine.executor.execute(command)
        print(result)
    except Exception as e:
        print(f"执行失败: {e}")


def cmd_file_list(engine, args):
    """列出目录"""
    path = args[0] if args else "."
    try:
        items = os.listdir(path)
        for item in items:
            item_path = os.path.join(path, item)
            prefix = "[D]" if os.path.isdir(item_path) else "[F]"
            print(f"{prefix} {item}")
    except Exception as e:
        print(f"列出目录失败: {e}")


def cmd_file_read(engine, args):
    """读取文件"""
    if len(args) < 1:
        print("用法: file read <path>")
        return
    path = args[0]
    try:
        with open(path, 'r', encoding='utf-8') as f:
            print(f.read())
    except Exception as e:
        print(f"读取文件失败: {e}")


def cmd_preference_show(engine, args):
    """显示当前偏好"""
    prefs = engine.pref_learner.get_preferences()
    if not prefs:
        print("暂无偏好")
        return
    print("当前用户偏好:")
    for p in prefs:
        print(f"  - {p['level1']} > {p['level2']} > {p['level3']} (confidence: {p.get('confidence', 0)})")


def cmd_preference_history(engine, args):
    """显示偏好变更历史"""
    history = engine.pref_learner.get_history()
    if not history:
        print("暂无历史记录")
        return
    print("偏好变更历史:")
    for h in history:
        print(f"  - {h.get('timestamp', 0)}: {len(h.get('updates', []))} 条更新")


def cmd_preference_rollback(engine, args):
    """回滚偏好"""
    if len(args) < 1:
        print("用法: preference rollback <timestamp>")
        return
    timestamp = args[0]
    success = engine.pref_learner.rollback(timestamp)
    if success:
        print(f"偏好已回滚到 {timestamp}")
    else:
        print(f"回滚失败，找不到时间戳 {timestamp} 的备份")


def cmd_log_show(engine, args):
    """查看日志"""
    date = args[0] if args else None
    if date:
        content = engine.logger.get_log_content(date)
        if content:
            print(content)
        else:
            print(f"日志 {date} 不存在")
    else:
        logs = engine.logger.get_logs()
        if logs:
            print("可用日志:")
            for log in logs:
                print(f"  - {log}")
        else:
            print("暂无日志")


COMMANDS = {
    "dag": {
        "list": cmd_dag_list,
        "show": cmd_dag_show,
        "run": cmd_dag_run,
        "retry": cmd_dag_retry,
        "delete": cmd_dag_delete,
    },
    "task": {
        "run": cmd_task_run,
    },
    "file": {
        "list": cmd_file_list,
        "read": cmd_file_read,
    },
    "preference": {
        "show": cmd_preference_show,
        "history": cmd_preference_history,
        "rollback": cmd_preference_rollback,
    },
    "log": {
        "show": cmd_log_show,
    },
}


def cli_main():
    """CLI入口"""
    from core.engine import CoreEngine
    engine = CoreEngine()

    args = sys.argv[1:]
    if not args:
        print("用法: 青稞 <command> [subcommand] [args]")
        print("命令: dag, task, file, preference, log")
        return

    command = args[0]
    subcommand = args[1] if len(args) > 1 else None
    cmd_args = args[2:] if len(args) > 2 else []

    if command in COMMANDS:
        if subcommand and subcommand in COMMANDS[command]:
            COMMANDS[command][subcommand](engine, cmd_args)
        else:
            print(f"未知子命令: {subcommand}")
            print(f"可用子命令: {', '.join(COMMANDS[command].keys())}")
    else:
        print(f"未知命令: {command}")
        print("可用命令: dag, task, file, preference, log")


if __name__ == "__main__":
    cli_main()
