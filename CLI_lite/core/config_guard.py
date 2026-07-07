"""配置防护模块 - 校验/备份/恢复，防止手动改配置导致系统崩溃"""
import os
import yaml
import json
import shutil
import logging
from datetime import datetime
from copy import deepcopy

logger = logging.getLogger('config_guard')

# ============================================================
# settings.yaml 默认 Schema（所有字段的默认值 + 类型）
# ============================================================
_DEFAULT_CONFIG = {
    "llm": {
        "mode": "real",
        "provider": "dify",
        "dify": {
            "api_url": "",
            "api_key": "",
            "timeout": 60
        },
        "openai": {
            "api_url": "",
            "api_key": "",
            "model": "",
            "timeout": 60
        }
    },
    "context": {
        "system_prompt_file": "config/sys_prompt.md",
        "history_rounds": 3,
        "keyword_dict_file": "data/dictionary/keywords.json",
        "max_snippet_length": 2000,
        "max_tokens": 8000,
        "max_retrieved_results": 5,
        "session_dir": "data/sessions"
    },
    "preference": {
        "learning_interval": 10,
        "preference_file": "data/preferences/current_prefs.json",
        "preference_history_dir": "data/preferences/preference_history",
        "sys_prompt_file": "config/sys_prompt.md",
        "confidence_threshold": 0.7
    },
    "flask": {
        "host": "0.0.0.0",
        "port": 5000,
        "debug": True
    },
    "logging": {
        "log_dir": "data/logs",
        "level": "DEBUG",
        "format": "markdown"
    },
    "cli": {
        "shell": "powershell",
        "timeout": 30,
        "dag_dir": "dag/dags",
        "session_dir": "data/sessions"
    },
    "dispatcher": {
        "poll_interval": 1.0,
        "max_concurrent": 5,
        "max_retries": 2,
        "experts": {}
    },
    "agentic_loop": {
        "enabled": True,
        "max_iterations": 15
    }
}

# 类型约束：字段名 -> 期望类型
_TYPE_RULES = {
    "llm.mode": str,
    "llm.provider": str,
    "llm.dify.api_url": str,
    "llm.dify.api_key": str,
    "llm.dify.timeout": (int, float),
    "llm.openai.api_url": str,
    "llm.openai.api_key": str,
    "llm.openai.model": str,
    "llm.openai.timeout": (int, float),
    "context.system_prompt_file": str,
    "context.history_rounds": int,
    "context.max_snippet_length": int,
    "context.max_tokens": int,
    "context.max_retrieved_results": int,
    "context.session_dir": str,
    "preference.learning_interval": int,
    "preference.confidence_threshold": (int, float),
    "flask.host": str,
    "flask.port": int,
    "flask.debug": bool,
    "logging.log_dir": str,
    "logging.level": str,
    "cli.shell": str,
    "cli.timeout": int,
    "cli.dag_dir": str,
    "dispatcher.poll_interval": (int, float),
    "dispatcher.max_concurrent": int,
    "dispatcher.max_retries": int,
    "agentic_loop.enabled": bool,
    "agentic_loop.max_iterations": int,
}

# sys_prompt.md 关键段落标记（缺失任一则认为损坏）
_PROMPT_REQUIRED_MARKERS = [
    "# System Prompt",
    "[preferences_start]",
    "[preferences_end]",
]

# sys_prompt.md 默认模板（首次生成或损坏恢复用）
_DEFAULT_PROMPT_TEMPLATE = """# System Prompt

## 角色
你是青稞，一个智能AI助手。

## 名字
- 我的名字是青稞
- 在服务过程中，根据对话场景和用户需求，自然地判断是否需要表达自己的名字
- 当用户主动询问名字、初次见面打招呼、或需要自我介绍时，应主动告知"我是青稞"
- 在日常对话和任务执行中，无需刻意提及名字，保持自然流畅的交流即可

## 系统环境
- 操作系统：Windows
- 默认终端：PowerShell
- 文件编码：UTF-8

## 核心原则
1. **记住用户信息**：用户说过的个人信息要记住并在对话中使用
2. **结合上下文**：回答问题时要参考历史对话
3. **主动学习**：从对话中提取用户的偏好和习惯

## 技能调用规则
1. 根据用户输入识别触发词，匹配对应技能
2. **所有操作均通过 DAG 编排执行**，不允许跳过 DAG 直接回复
3. 构建 DAG 后，向用户说明将要执行的操作
4. 每个节点执行后汇报结果，最终汇总返回给用户

## 用户喜好（动态更新）
<!-- 以下为自动更新的偏好区域，请勿手动修改 -->
[preferences_start]
- 暂无偏好
[preferences_end]
<!-- 偏好区域结束 -->

## 执行规则
1. 简单问题直接回答
2. 复杂任务构建 DAG 执行
3. 回答问题时先查看历史对话
4. 执行前确认用户意图
5. 执行过程记录日志
"""


class ConfigGuard:
    """配置防护器：校验、备份、恢复"""

    def __init__(self, config_path: str, project_root: str):
        self.config_path = config_path
        self.project_root = project_root
        self.backup_dir = os.path.join(project_root, "config", "backup")
        os.makedirs(self.backup_dir, exist_ok=True)

    # ============================================================
    # settings.yaml 校验与修复
    # ============================================================

    def load_and_validate_config(self) -> dict:
        """加载 settings.yaml 并校验，返回合并后的完整配置。
        如果文件不存在或损坏，自动从备份恢复或使用默认值。
        """
        raw = self._try_load_yaml()

        if raw is None:
            # 加载失败，尝试从备份恢复
            restored = self._restore_config_from_backup()
            if restored:
                raw = restored
                logger.warning("配置文件损坏，已从备份恢复")
            else:
                raw = {}
                logger.warning("配置文件不存在且无备份，使用默认配置")

        # 深度合并：用户配置覆盖默认值（保证所有字段都有值）
        merged = self._deep_merge(deepcopy(_DEFAULT_CONFIG), raw)

        # 类型校验与修正
        fixes = self._validate_and_fix_types(merged)
        if fixes:
            logger.warning(f"配置类型修正: {fixes}")

        return merged

    def save_config_with_backup(self, config: dict):
        """保存配置前先备份旧版本，然后写入新配置"""
        # 1. 备份当前配置
        self._backup_file(self.config_path, "settings")

        # 2. 校验待写入的配置
        merged = self._deep_merge(deepcopy(_DEFAULT_CONFIG), config)
        self._validate_and_fix_types(merged)

        # 3. 写入
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, 'w', encoding='utf-8') as f:
            yaml.dump(merged, f, allow_unicode=True, default_flow_style=False)

        logger.info("配置已保存（含自动备份）")

    def validate_partial_update(self, update_data: dict) -> dict:
        """校验前端提交的部分配置更新，返回修正后的数据。
        过滤掉非法字段和类型不匹配的值。
        """
        cleaned = {}
        for key, value in update_data.items():
            if key not in _DEFAULT_CONFIG:
                logger.warning(f"忽略未知配置项: {key}")
                continue
            if isinstance(value, dict) and isinstance(_DEFAULT_CONFIG.get(key), dict):
                cleaned[key] = self._validate_subsection(key, value)
            else:
                cleaned[key] = value
        return cleaned

    def _validate_subsection(self, section_key: str, data: dict) -> dict:
        """校验配置子项，修复类型错误的字段"""
        defaults = _DEFAULT_CONFIG.get(section_key, {})
        cleaned = {}
        for k, v in data.items():
            full_key = f"{section_key}.{k}"
            expected_type = _TYPE_RULES.get(full_key)
            if expected_type and not isinstance(v, expected_type):
                default_val = defaults.get(k)
                logger.warning(f"配置类型错误: {full_key} 期望 {expected_type}, "
                               f"实际 {type(v).__name__}, 使用默认值 {default_val}")
                cleaned[k] = default_val if default_val is not None else v
            else:
                cleaned[k] = v
        return cleaned

    def _try_load_yaml(self) -> dict | None:
        """尝试加载 YAML 配置文件"""
        if not os.path.exists(self.config_path):
            return None
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                logger.error("配置文件不是有效的字典格式")
                return None
            return data
        except yaml.YAMLError as e:
            logger.error(f"YAML 解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"读取配置文件失败: {e}")
            return None

    def _deep_merge(self, base: dict, override: dict) -> dict:
        """深度合并：override 的值覆盖 base，但保留 base 中缺失的键"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                base[key] = self._deep_merge(base[key], value)
            else:
                base[key] = value
        return base

    def _validate_and_fix_types(self, config: dict) -> list:
        """校验类型并修正，返回修正列表"""
        fixes = []
        for path, expected_type in _TYPE_RULES.items():
            keys = path.split(".")
            obj = config
            for k in keys[:-1]:
                obj = obj.get(k, {})
            final_key = keys[-1]
            value = obj.get(final_key)
            if value is not None and not isinstance(value, expected_type):
                # 从默认值恢复
                default_obj = _DEFAULT_CONFIG
                for k in keys[:-1]:
                    default_obj = default_obj.get(k, {})
                default_val = default_obj.get(final_key)
                fixes.append(f"{path}: {type(value).__name__} -> {default_val}")
                obj[final_key] = default_val
        return fixes

    # ============================================================
    # sys_prompt.md 校验与恢复
    # ============================================================

    def load_and_validate_prompt(self, prompt_path: str) -> str:
        """加载系统提示词并校验完整性。
        文件缺失时自动恢复；标记缺失时仅在内存中补充，不覆盖用户文件。
        """
        content = self._try_read_file(prompt_path)

        if content is None:
            # 文件不存在，尝试从备份恢复
            restored = self._restore_prompt_from_backup(prompt_path)
            if restored:
                logger.warning("系统提示词文件不存在，已从备份恢复")
                return restored
            else:
                logger.warning("系统提示词不存在且无备份，生成默认模板")
                self._write_prompt(prompt_path, _DEFAULT_PROMPT_TEMPLATE)
                return _DEFAULT_PROMPT_TEMPLATE

        # 校验关键段落 — 缺失时仅在内存中补充，不写回文件（尊重用户手动修改）
        missing_markers = [m for m in _PROMPT_REQUIRED_MARKERS if m not in content]
        if missing_markers:
            logger.info(f"系统提示词缺少标记 {missing_markers}，在内存中自动补充（不覆盖文件）")
            content = self._fix_prompt_content(content, missing_markers)

        return content

    def validate_prompt_content(self, content: str) -> bool:
        """校验提示词内容是否完整"""
        return all(m in content for m in _PROMPT_REQUIRED_MARKERS)

    def _fix_prompt_content(self, content: str, missing: list) -> str:
        """修复缺失段落的提示词"""
        # 缺少标题
        if "# System Prompt" in missing and not content.strip().startswith("#"):
            content = "# System Prompt\n\n" + content

        # 缺少偏好标记
        if "[preferences_start]" in missing or "[preferences_end]" in missing:
            # 移除可能存在的不完整偏好段
            for marker in ["[preferences_start]", "[preferences_end]"]:
                content = content.replace(marker, "")

            # 在文件末尾追加完整偏好段
            content = content.rstrip() + "\n\n## 用户喜好（动态更新）\n"
            content += "<!-- 以下为自动更新的偏好区域，请勿手动修改 -->\n"
            content += "[preferences_start]\n- 暂无偏好\n[preferences_end]\n"
            content += "<!-- 偏好区域结束 -->\n"

        return content

    def _try_read_file(self, path: str) -> str | None:
        """尝试读取文件内容"""
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            return None

    def _write_prompt(self, path: str, content: str):
        """写入提示词文件"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

    # ============================================================
    # 备份与恢复
    # ============================================================

    def _backup_file(self, file_path: str, label: str):
        """备份文件到 backup 目录"""
        if not os.path.exists(file_path):
            return
        try:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            ext = os.path.splitext(file_path)[1]
            backup_name = f"{label}_{ts}{ext}"
            backup_path = os.path.join(self.backup_dir, backup_name)
            shutil.copy2(file_path, backup_path)
            logger.info(f"已备份: {backup_path}")

            # 清理旧备份（保留最近 20 个同类型）
            self._cleanup_backups(label, ext, keep=20)
        except Exception as e:
            logger.error(f"备份失败: {e}")

    def _restore_config_from_backup(self) -> dict | None:
        """从备份恢复最新的 settings.yaml"""
        backups = sorted(
            [f for f in os.listdir(self.backup_dir) if f.startswith("settings_") and f.endswith(".yaml")],
            reverse=True
        )
        for backup_name in backups:
            backup_path = os.path.join(self.backup_dir, backup_name)
            try:
                with open(backup_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    # 恢复到原位
                    with open(self.config_path, 'w', encoding='utf-8') as f:
                        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
                    logger.info(f"从备份恢复配置: {backup_name}")
                    return data
            except Exception:
                continue
        return None

    def _restore_prompt_from_backup(self, prompt_path: str) -> str | None:
        """从备份恢复最新的 sys_prompt.md"""
        backups = sorted(
            [f for f in os.listdir(self.backup_dir) if f.startswith("sys_prompt_") and f.endswith(".md")],
            reverse=True
        )
        for backup_name in backups:
            backup_path = os.path.join(self.backup_dir, backup_name)
            try:
                with open(backup_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                if content.strip():
                    os.makedirs(os.path.dirname(prompt_path), exist_ok=True)
                    with open(prompt_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    logger.info(f"从备份恢复系统提示词: {backup_name}")
                    return content
            except Exception:
                continue
        return None

    def _cleanup_backups(self, label: str, ext: str, keep: int = 20):
        """清理旧备份，只保留最近 N 个"""
        try:
            files = [f for f in os.listdir(self.backup_dir) if f.startswith(f"{label}_") and f.endswith(ext)]
            files.sort(reverse=True)
            for old_file in files[keep:]:
                os.remove(os.path.join(self.backup_dir, old_file))
        except Exception:
            pass
