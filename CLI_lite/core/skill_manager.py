"""技能管理器 - 检测典型任务、生成技能总结、管理技能文件"""
import json
import os
import re
import time
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger('skill_manager')


class SkillManager:
    """技能管理器：检测典型任务 → LLM总结技能 → 保存到skill文件夹"""

    def __init__(self, llm_gateway, skill_dir: str, logger_instance=None):
        self.llm = llm_gateway
        self.skill_dir = skill_dir
        self.logger = logger_instance
        self.catalog_file = os.path.join(skill_dir, "技能清单.md")
        os.makedirs(skill_dir, exist_ok=True)
        # 确保技能清单文件存在（首次运行或打包模式下自动创建）
        if not os.path.exists(self.catalog_file):
            self._update_catalog()

    def detect_and_summarize(self, turns: list) -> bool:
        """检测最近对话中是否存在典型任务，如果有则生成技能。

        Args:
            turns: 最近的对话轮次列表，格式 [{"role": "user"/"assistant", "content": "..."}]

        Returns:
            是否成功生成了新技能
        """
        if len(turns) < 4:
            return False

        # 1. 检测是否包含典型任务
        task_info = self._detect_typical_task(turns)
        if not task_info:
            return False

        task_name = task_info.get("name", "")
        task_desc = task_info.get("description", "")
        if not task_name or not task_desc:
            return False

        # 2. 检查是否已存在同名技能（避免重复生成）
        if self._skill_exists(task_name):
            logger.info(f"技能 [{task_name}] 已存在，跳过生成")
            return False

        # 3. 使用LLM生成技能总结
        skill_data = self._generate_skill_summary(task_name, task_desc, turns)
        if not skill_data:
            return False

        # 4. 保存技能文件
        self._save_skill(task_name, skill_data)

        # 5. 更新技能清单
        self._update_catalog()

        logger.info(f"新技能已生成: [{task_name}]")
        return True

    def _detect_typical_task(self, turns: list) -> Optional[dict]:
        """使用LLM检测对话中是否存在典型任务（可复用的固定流程）"""
        # 构建对话摘要
        conversation = []
        for msg in turns[-10:]:  # 最近10条消息
            role = "用户" if msg["role"] == "user" else "助手"
            content = msg["content"]
            if len(content) > 500:
                content = content[:500] + "..."
            conversation.append(f"[{role}] {content}")
        conversation_text = "\n".join(conversation)

        # 获取已有技能列表，避免重复
        existing_skills = self._get_existing_skill_names()
        existing_text = "、".join(existing_skills) if existing_skills else "暂无"

        prompt = f"""分析以下对话记录，判断其中是否包含**典型任务**。

典型任务的特征：
1. 任务有明确的目标和固定的操作流程
2. 步骤可以被总结为可复用的技能
3. 类似的任务在未来可能再次出现
4. 不是简单的问答或闲聊

已有技能（请勿重复）：{existing_text}

对话记录：
{conversation_text}

请严格按以下JSON格式返回（如果不存在典型任务，返回空对象）：
```json
{{"name": "技能名称（简短，10字以内）", "description": "技能描述（50字以内，说明这个技能做什么）", "is_typical": true}}
```
如果不存在典型任务：
```json
{{"is_typical": false}}
```"""

        try:
            response = self.llm.chat([{"role": "user", "content": prompt}])
            content = response.get("content", "")

            # 提取JSON
            json_str = self._extract_json(content)
            if json_str:
                data = json.loads(json_str)
                if data.get("is_typical", False):
                    return {
                        "name": data.get("name", ""),
                        "description": data.get("description", "")
                    }
        except Exception as e:
            logger.error(f"检测典型任务失败: {e}")

        return None

    def _generate_skill_summary(self, task_name: str, task_desc: str, turns: list) -> Optional[dict]:
        """使用LLM生成技能总结，包含详细执行步骤"""
        # 构建对话摘要（只取与任务相关的部分）
        conversation = []
        for msg in turns[-10:]:
            role = "用户" if msg["role"] == "user" else "助手"
            content = msg["content"]
            if len(content) > 800:
                content = content[:800] + "..."
            conversation.append(f"[{role}] {content}")
        conversation_text = "\n".join(conversation)

        prompt = f"""基于以下对话记录，为技能 [{task_name}] 生成详细的技能文档。

技能描述：{task_desc}

对话记录：
{conversation_text}

请生成技能文档，严格按以下JSON格式返回：
```json
{{
    "name": "{task_name}",
    "description": "技能的完整描述",
    "trigger_keywords": ["触发关键词1", "触发关键词2"],
    "steps": [
        {{"step": 1, "action": "步骤描述", "tool": "使用的工具（如有）", "note": "注意事项（如有）"}}
    ],
    "tools_used": ["工具1", "工具2"],
    "tips": ["经验提示1", "经验提示2"],
    "example": "一个典型的使用示例描述"
}}
```

要求：
1. steps 要具体、可执行，包含工具调用细节
2. trigger_keywords 用于未来匹配相似任务
3. tips 包含执行中的经验教训
4. 所有内容用中文"""

        try:
            response = self.llm.chat([{"role": "user", "content": prompt}])
            content = response.get("content", "")

            json_str = self._extract_json(content)
            if json_str:
                data = json.loads(json_str)
                return data
        except Exception as e:
            logger.error(f"生成技能总结失败: {e}")

        return None

    def _save_skill(self, skill_name: str, skill_data: dict):
        """保存技能到skill/{skill_name}/skill.md"""
        # 清理技能名称（去除特殊字符，用作文件夹名）
        safe_name = re.sub(r'[<>:"/\\|?*\s]', '_', skill_name).strip('_')
        if not safe_name:
            safe_name = f"skill_{int(time.time())}"

        skill_folder = os.path.join(self.skill_dir, safe_name)
        os.makedirs(skill_folder, exist_ok=True)

        # 生成技能文档
        md_lines = [
            f"# {skill_name}",
            "",
            f"> {skill_data.get('description', '')}",
            "",
            "## 触发条件",
            "",
        ]

        keywords = skill_data.get("trigger_keywords", [])
        if keywords:
            md_lines.append("当用户任务包含以下关键词时，可参考本技能：")
            md_lines.append("")
            for kw in keywords:
                md_lines.append(f"- {kw}")
        else:
            md_lines.append("- 通用任务")

        md_lines.extend(["", "## 执行步骤", ""])

        steps = skill_data.get("steps", [])
        for step in steps:
            step_num = step.get("step", "?")
            action = step.get("action", "")
            tool = step.get("tool", "")
            note = step.get("note", "")
            md_lines.append(f"### 步骤 {step_num}：{action}")
            if tool:
                md_lines.append(f"- 使用工具：`{tool}`")
            if note:
                md_lines.append(f"- 注意：{note}")
            md_lines.append("")

        tools_used = skill_data.get("tools_used", [])
        if tools_used:
            md_lines.extend(["## 涉及工具", ""])
            for t in tools_used:
                md_lines.append(f"- `{t}`")
            md_lines.append("")

        tips = skill_data.get("tips", [])
        if tips:
            md_lines.extend(["## 经验提示", ""])
            for tip in tips:
                md_lines.append(f"- {tip}")
            md_lines.append("")

        example = skill_data.get("example", "")
        if example:
            md_lines.extend(["## 使用示例", "", example, ""])

        md_lines.extend([
            "---",
            f"*生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
            ""
        ])

        skill_file = os.path.join(skill_folder, "skill.md")
        with open(skill_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(md_lines))

        # 同时保存JSON格式的技能数据（供程序读取）
        data_file = os.path.join(skill_folder, "skill.json")
        with open(data_file, 'w', encoding='utf-8') as f:
            json.dump(skill_data, f, ensure_ascii=False, indent=2)

        logger.info(f"技能文件已保存: {skill_file}")

    def _update_catalog(self):
        """更新技能清单.md，扫描所有技能子文件夹（支持skill.json和SKILL.md两种格式）"""
        skills = []
        if os.path.exists(self.skill_dir):
            for name in sorted(os.listdir(self.skill_dir)):
                skill_path = os.path.join(self.skill_dir, name)
                if not os.path.isdir(skill_path):
                    continue

                # 优先读取skill.json（自学习生成的技能）
                json_file = os.path.join(skill_path, "skill.json")
                if os.path.exists(json_file):
                    try:
                        with open(json_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        skills.append({
                            "name": data.get("name", name),
                            "folder": name,
                            "description": data.get("description", ""),
                            "keywords": data.get("trigger_keywords", []),
                            "tools": data.get("tools_used", []),
                        })
                        continue
                    except Exception:
                        pass

                # 读取SKILL.md（OpenClaw原生技能包格式，含YAML frontmatter）
                skill_md = os.path.join(skill_path, "SKILL.md")
                if os.path.exists(skill_md):
                    try:
                        with open(skill_md, 'r', encoding='utf-8') as f:
                            content = f.read()
                        # 提取YAML frontmatter中的name和description
                        fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
                        skill_name = name
                        skill_desc = ""
                        if fm_match:
                            for line in fm_match.group(1).split('\n'):
                                if line.startswith('name:'):
                                    skill_name = line.split(':', 1)[1].strip().strip('"\'')
                                elif line.startswith('description:'):
                                    skill_desc = line.split(':', 1)[1].strip().strip('"\'')
                        # 读取_meta.json获取版本等元数据（OpenClaw技能包标准格式）
                        meta_file = os.path.join(skill_path, "_meta.json")
                        meta = {}
                        if os.path.exists(meta_file):
                            try:
                                with open(meta_file, 'r', encoding='utf-8') as f:
                                    meta = json.load(f)
                            except Exception:
                                pass
                        skills.append({
                            "name": skill_name,
                            "folder": name,
                            "description": skill_desc,
                            "keywords": [],
                            "tools": [],
                            "version": meta.get("version", ""),
                            "slug": meta.get("slug", name),
                            "is_openclaw": True,
                        })
                        continue
                    except Exception:
                        pass

                # 兜底：目录存在但无已知格式
                skills.append({
                    "name": name,
                    "folder": name,
                    "description": "",
                    "keywords": [],
                    "tools": [],
                })

        # 分类：预设技能（SKILL.md / OpenClaw）和自学习技能（skill.json）
        preset_skills = []
        learned_skills = []
        for skill in skills:
            if skill.get("is_openclaw"):
                preset_skills.append(skill)
            elif os.path.exists(os.path.join(self.skill_dir, skill['folder'], "SKILL.md")) and \
                 not os.path.exists(os.path.join(self.skill_dir, skill['folder'], "skill.json")):
                preset_skills.append(skill)
            else:
                learned_skills.append(skill)

        # 生成技能清单文档
        lines = [
            "# 技能清单",
            "",
            "> 本文件由青稞自我学习系统自动维护。当检测到典型任务时，系统会通过DAG进行技能总结，并将结果更新到此文件和对应的技能子文件夹中。",
            "",
        ]

        # 预设技能
        lines.append("## 预设技能（内置）")
        lines.append("")
        if preset_skills:
            for i, skill in enumerate(preset_skills, 1):
                lines.append(f"### {i}. {skill['name']}")
                lines.append(f"- **文件夹**：`skill/{skill['folder']}/`")
                if skill.get('version'):
                    lines.append(f"- **版本**：{skill['version']}")
                if skill['description']:
                    lines.append(f"- **描述**：{skill['description']}")
                lines.append(f"- **详细文档**：`skill/{skill['folder']}/SKILL.md`")
                lines.append("")
        else:
            lines.append("暂无预设技能。")
            lines.append("")

        # 自学习技能
        lines.append("## 自学习技能（动态生成）")
        lines.append("")
        if learned_skills:
            for i, skill in enumerate(learned_skills, 1):
                lines.append(f"### {i}. {skill['name']}")
                lines.append(f"- **文件夹**：`skill/{skill['folder']}/`")
                if skill['description']:
                    lines.append(f"- **描述**：{skill['description']}")
                if skill['keywords']:
                    lines.append(f"- **触发关键词**：{', '.join(skill['keywords'])}")
                if skill['tools']:
                    lines.append(f"- **涉及工具**：{', '.join(skill['tools'])}")
                lines.append("")
        else:
            lines.append("暂无自学习技能。")
            lines.append("")

        lines.extend([
            "---",
            "",
            f"*最后更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
            ""
        ])

        with open(self.catalog_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

        logger.info(f"技能清单已更新，共 {len(skills)} 个技能")

    def _skill_exists(self, skill_name: str) -> bool:
        """检查同名技能是否已存在"""
        safe_name = re.sub(r'[<>:"/\\|?*\s]', '_', skill_name).strip('_')
        skill_folder = os.path.join(self.skill_dir, safe_name)
        if os.path.isdir(skill_folder):
            return True
        if os.path.exists(self.skill_dir):
            for name in os.listdir(self.skill_dir):
                folder = os.path.join(self.skill_dir, name)
                if not os.path.isdir(folder):
                    continue
                # 检查skill.json
                json_file = os.path.join(folder, "skill.json")
                if os.path.exists(json_file):
                    try:
                        with open(json_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        if data.get("name", "") == skill_name:
                            return True
                    except Exception:
                        pass
                # 检查SKILL.md
                skill_md = os.path.join(folder, "SKILL.md")
                if os.path.exists(skill_md):
                    try:
                        with open(skill_md, 'r', encoding='utf-8') as f:
                            content = f.read()
                        fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
                        if fm_match:
                            for line in fm_match.group(1).split('\n'):
                                if line.startswith('name:'):
                                    md_name = line.split(':', 1)[1].strip().strip('"\'')
                                    if md_name == skill_name:
                                        return True
                    except Exception:
                        pass
        return False

    def _get_existing_skill_names(self) -> list:
        """获取已有技能名称列表（支持skill.json和SKILL.md两种格式）"""
        names = []
        if os.path.exists(self.skill_dir):
            for name in os.listdir(self.skill_dir):
                folder = os.path.join(self.skill_dir, name)
                if not os.path.isdir(folder):
                    continue
                # 优先skill.json
                json_file = os.path.join(folder, "skill.json")
                if os.path.exists(json_file):
                    try:
                        with open(json_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        names.append(data.get("name", name))
                        continue
                    except Exception:
                        pass
                # 读取SKILL.md
                skill_md = os.path.join(folder, "SKILL.md")
                if os.path.exists(skill_md):
                    try:
                        with open(skill_md, 'r', encoding='utf-8') as f:
                            content = f.read()
                        fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
                        if fm_match:
                            for line in fm_match.group(1).split('\n'):
                                if line.startswith('name:'):
                                    names.append(line.split(':', 1)[1].strip().strip('"\''))
                                    break
                        else:
                            names.append(name)
                    except Exception:
                        pass
        return names

    def get_skill_list(self) -> list:
        """获取所有技能列表（供外部调用）"""
        return self._get_existing_skill_names()

    def _extract_json(self, text: str) -> Optional[str]:
        """从文本中提取JSON字符串"""
        if not text or not text.strip():
            return None
        text = text.strip()

        # 策略0：整段文本直接就是JSON
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

        # 策略1：提取代码块中的JSON
        for pattern in [r'```json\s*([\s\S]*?)\s*```', r'```\s*([\s\S]*?)\s*```']:
            match = re.search(pattern, text)
            if match:
                candidate = match.group(1).strip()
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    continue

        # 策略2：逐个 `{` 位置尝试解析
        start = 0
        while True:
            idx = text.find('{', start)
            if idx == -1:
                break
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
