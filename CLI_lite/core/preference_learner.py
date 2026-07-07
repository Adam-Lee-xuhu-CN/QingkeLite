"""偏好学习器 - 每10轮对话提取偏好并更新Sys Prompt"""
import json
import os
import time
import logging
from datetime import datetime

logger = logging.getLogger('preference_learner')


class PreferenceLearner:
    """用户偏好学习器，每10轮对话触发偏好学习"""

    def __init__(self, config: dict, llm_gateway, logger, context_mgr=None, skill_manager=None):
        self.llm = llm_gateway
        self.logger = logger
        self.context_mgr = context_mgr  # 保存 context_mgr 引用
        self.skill_manager = skill_manager  # 技能管理器（可选）
        self.pref_file = config.get("preference_file", "data/preferences/current_prefs.json")
        self.sys_prompt_file = config.get("sys_prompt_file", "config/sys_prompt.md")
        self.history_dir = config.get("preference_history_dir", "data/preferences/preference_history")
        # backup_dir 基于 sys_prompt_file 所在目录，确保与配置文件同位置
        self.backup_dir = os.path.join(os.path.dirname(self.sys_prompt_file), "backup")
        self.round_count = 0
        self.current_prefs = []
        self.confidence_threshold = config.get("confidence_threshold", 0.7)

        os.makedirs(self.history_dir, exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)
        self._load_preferences()

    def record_turn(self, user_input: str, ai_output: str, dag_id: str = None):
        """记录一轮对话"""
        self.round_count += 1
        self._save_turn(user_input, ai_output, dag_id)

        # 每10轮触发偏好学习
        if self.round_count % 10 == 0:
            self._trigger_preference_learning()

    def _save_turn(self, user_input: str, ai_output: str, dag_id: str = None):
        """保存单轮对话到临时文件"""
        turn_file = os.path.join(self.history_dir, f"turns_{self.round_count}.json")
        turn_data = {
            "turn": self.round_count,
            "user_input": user_input,
            "ai_output": ai_output,
            "dag_id": dag_id,
            "timestamp": time.time()
        }
        with open(turn_file, 'w', encoding='utf-8') as f:
            json.dump(turn_data, f, ensure_ascii=False, indent=2)

    def _trigger_preference_learning(self):
        """触发偏好学习流程"""
        # 1. 收集最近10轮对话
        turns = self._get_recent_turns(10)
        if len(turns) < 10:
            return

        # 2. LLM提取偏好
        new_prefs = self.llm.extract_preferences(turns)

        # 3. 置信度过滤
        filtered_prefs = [p for p in new_prefs if p.get("confidence", 0) >= self.confidence_threshold]

        # 4. 对比新旧偏好
        comparison = self.llm.compare_preferences(filtered_prefs, self.current_prefs)

        if comparison.get("need_update", False):
            # 5. 通知用户审核
            self._notify_user_review(comparison.get("updates", []))

            # 6. 执行更新
            self._update_preferences(comparison.get("updates", []))
            self._update_sys_prompt(comparison.get("updates", []))
            self._reload_sys_prompt()

            # 7. 记录日志
            self.logger.log_preference_update(comparison.get("updates", []))

        # 8. 技能学习：检测典型任务并生成技能
        if self.skill_manager:
            try:
                turns_for_skill = self._get_recent_turns(5)
                if len(turns_for_skill) >= 4:
                    skill_generated = self.skill_manager.detect_and_summarize(turns_for_skill)
                    if skill_generated:
                        logger.info("自我学习：检测到典型任务，已生成新技能")
            except Exception as e:
                logger.error(f"技能学习异常: {e}")

    def _notify_user_review(self, updates: list):
        """通知用户审核偏好更新（通过WebSocket/SSE推送）"""
        # 在Web端通过SSE接收更新确认
        pass

    def _get_recent_turns(self, count: int) -> list[dict]:
        """获取最近N轮对话"""
        turns = []
        for i in range(self.round_count, 0, -1):
            turn_file = os.path.join(self.history_dir, f"turns_{i}.json")
            if os.path.exists(turn_file):
                with open(turn_file, 'r', encoding='utf-8') as f:
                    turn_data = json.load(f)
                    turns.append({
                        "role": "user",
                        "content": turn_data.get("user_input", "")
                    })
                    turns.append({
                        "role": "assistant",
                        "content": turn_data.get("ai_output", "")
                    })
            if len(turns) >= count * 2:
                break

        # 限制总字数 <= 4000
        total_text = "".join([t["content"] for t in turns])
        if len(total_text) > 4000:
            # 倒序截断，保留最新的
            trimmed_turns = []
            total_len = 0
            for t in reversed(turns):
                if total_len + len(t["content"]) <= 4000:
                    trimmed_turns.insert(0, t)
                    total_len += len(t["content"])
                else:
                    remaining = 4000 - total_len
                    if remaining > 0:
                        t["content"] = t["content"][-remaining:]
                        trimmed_turns.insert(0, t)
                    break
            turns = trimmed_turns

        return turns

    def _load_preferences(self):
        """加载当前偏好"""
        if os.path.exists(self.pref_file):
            try:
                with open(self.pref_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.current_prefs = data.get("preferences", [])
                    self.round_count = data.get("round_count", 0)
            except (json.JSONDecodeError, IOError) as e:
                # 文件损坏或读取失败时使用默认值
                self.current_prefs = []
                self.round_count = 0
        else:
            self.current_prefs = []

    def _update_preferences(self, updates: list):
        """执行偏好更新"""
        for update in updates:
            p = update.get("preference", {})
            if update["type"] == "new":
                self.current_prefs.append({
                    "level1": p.get("level1", ""),
                    "level2": p.get("level2", ""),
                    "level3": p.get("level3", ""),
                    "confidence": p.get("confidence", 0),
                    "source_dag_ids": p.get("source_dag_ids", []),
                    "created_at": time.time(),
                    "updated_at": time.time()
                })
            elif update["type"] == "modified":
                old = update.get("old", {})
                for i, pref in enumerate(self.current_prefs):
                    if (pref.get("level1") == old.get("level1") and
                            pref.get("level2") == old.get("level2")):
                        self.current_prefs[i]["level3"] = update["new"].get("level3", "")
                        self.current_prefs[i]["updated_at"] = time.time()
                        break

        # 保存到文件
        data = {
            "version": len(self.current_prefs),
            "updated_at": time.time(),
            "preferences": self.current_prefs
        }
        with open(self.pref_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 保存变更历史
        history_file = os.path.join(
            self.history_dir,
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump({
                "timestamp": time.time(),
                "turn_count": self.round_count,
                "updates": updates,
                "sys_prompt_updated": True
            }, f, ensure_ascii=False, indent=2)

    def _update_sys_prompt(self, updates: list):
        """更新 sys_prompt.md 文件中的偏好区域"""
        if not os.path.exists(self.sys_prompt_file):
            return

        # 备份原文件
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = os.path.join(self.backup_dir, f"sys_prompt_{timestamp}.md")
        with open(self.sys_prompt_file, 'r', encoding='utf-8') as src:
            content = src.read()
        with open(backup_file, 'w', encoding='utf-8') as dst:
            dst.write(content)

        # 构建新的偏好内容
        pref_lines = []
        for pref in self.current_prefs:
            pref_lines.append(f"- {pref['level1']} > {pref['level2']} > {pref['level3']}")
        new_pref_content = "\n".join(pref_lines) if pref_lines else "- 暂无偏好"

        # 替换标记之间的内容
        lines = content.split("\n")
        new_lines = []
        in_pref_section = False
        replaced = False

        for line in lines:
            if "[preferences_start]" in line:
                new_lines.append(line)
                new_lines.append(new_pref_content)
                in_pref_section = True
                replaced = True
            elif "[preferences_end]" in line:
                new_lines.append(line)
                in_pref_section = False
            elif not in_pref_section:
                new_lines.append(line)

        if not replaced:
            # 如果没有找到标记，追加到文件末尾
            new_lines.append("\n[preferences_start]")
            new_lines.append(new_pref_content)
            new_lines.append("[preferences_end]")

        with open(self.sys_prompt_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(new_lines))

        # 校验写入后的提示词完整性，如有问题自动修复
        try:
            from core.config_guard import ConfigGuard
            guard = ConfigGuard("", os.path.dirname(os.path.dirname(self.sys_prompt_file)))
            validated = guard.load_and_validate_prompt(self.sys_prompt_file)
            if validated != "\n".join(new_lines):
                logger.warning("偏好更新后提示词校验不通过，已自动修复")
        except Exception as e:
            logger.error(f"偏好更新后提示词校验失败: {e}")

    def _reload_sys_prompt(self):
        """重新加载 sys_prompt.md 到内存"""
        if self.context_mgr:
            self.context_mgr.reload_system_prompt()

    def get_preferences(self) -> list[dict]:
        """获取当前偏好"""
        return self.current_prefs

    def get_history(self) -> list[dict]:
        """获取偏好变更历史"""
        histories = []
        if os.path.exists(self.history_dir):
            for filename in os.listdir(self.history_dir):
                if filename.endswith('.json') and filename.startswith('2'):
                    file_path = os.path.join(self.history_dir, filename)
                    with open(file_path, 'r', encoding='utf-8') as f:
                        histories.append(json.load(f))
        return sorted(histories, key=lambda x: x.get("timestamp", 0), reverse=True)

    def rollback(self, timestamp: str) -> bool:
        """回滚偏好到指定版本"""
        backup_file = os.path.join(self.backup_dir, f"sys_prompt_{timestamp}.md")
        if not os.path.exists(backup_file):
            return False

        with open(backup_file, 'r', encoding='utf-8') as f:
            content = f.read()
        with open(self.sys_prompt_file, 'w', encoding='utf-8') as f:
            f.write(content)

        self._reload_sys_prompt()
        return True
