# 青稞·Lite 项目结构说明

本文件供 LLM（Agent大脑）参考，了解应用的完整结构，以便进行自我优化和代码修改。

## 项目概述

青稞·Lite 是一个基于 Flask 的桌面 AI 助手应用，支持 Dify 和 OpenAI 兼容 API。通过 PyInstaller 打包为单文件 exe，内嵌 QWebEngineView 提供 Web 界面。

## 目录结构

```
CLI_lite/
├── app.py                    # Flask 应用入口
├── requirements.txt          # Python 依赖清单
├── PROJECT_STRUCTURE.md      # 本文件——项目结构说明（供LLM参考）
├── 功能代码映射.md            # 功能与代码文件的映射关系文档
├── 开发自测问题清单.md        # 开发过程中的问题记录
│
├── config/                   # 配置文件目录
│   ├── settings.yaml         # 核心配置（LLM提供商、端口、上下文参数）
│   ├── sys_prompt.md         # 系统提示词（Agent身份定义、行为规则）
│   ├── backup/               # 配置文件自动备份
│   └── skills/               # 技能定义文件（Markdown格式）
│       ├── code_gen.md       # 代码生成技能
│       ├── file_ops.md       # 文件操作技能
│       ├── project_init.md   # 项目初始化技能
│       └── test.md           # 测试技能
│
├── core/                     # 核心引擎模块
│   ├── engine.py             # 核心引擎——协调所有模块，处理用户请求
│   ├── llm_gateway.py        # LLM网关——封装 Dify/OpenAI API 调用
│   ├── context_manager.py    # 上下文管理器——构建对话上下文、管理提示词
│   ├── agentic_loop.py       # Agentic Loop——自主任务执行引擎
│   ├── tools.py              # 工具注册表——LLM可调用的工具定义
│   ├── config_guard.py       # 配置校验器——配置完整性检查、自动修复
│   ├── history_retriever.py  # 历史检索器——关键词+向量混合检索
│   ├── preference_learner.py # 偏好学习器——从对话中提取用户偏好
│   ├── logger.py             # 日志管理器——MD格式记录执行日志
│   └── agent/
│       └── front_desk_agent.py  # 前台接待Agent——分析用户意图
│
├── web/                      # Web 界面模块
│   ├── routes/
│   │   ├── api.py            # API路由——REST接口和SSE流式端点
│   │   ├── pages.py          # 页面路由——HTML页面路由
│   │   └── events.py         # 事件路由——SSE事件推送
│   ├── static/
│   │   ├── css/style.css     # 前端样式（UI布局、DAG卡片、动画）
│   │   └── js/
│   │       ├── main.js       # 前端主脚本（聊天、DAG渲染、SSE处理）
│   │       ├── config.js     # 前端配置管理
│   │       └── prompt.js     # 提示词编辑器脚本
│   └── templates/
│       └── index.html        # 前端HTML模板（页面结构、Tab导航）
│
├── dag/                      # DAG 任务管理模块
│   ├── dag_parser.py         # DAG解析器——解析DAG定义文件
│   ├── dag_scheduler.py      # DAG调度器——调度和执行DAG任务
│   ├── schemas.py            # DAG数据模型定义
│   └── dags/                 # DAG任务定义文件（JSON格式）
│
├── cli/                      # CLI 命令行模块
│   ├── cli_main.py           # CLI主入口
│   └── commands/             # CLI命令定义
│
├── dispatcher/               # 任务调度与执行模块
│   └── task_executor.py      # 任务执行器——执行Shell命令
│
├── data/                     # 数据存储目录（运行时生成）
│   ├── sessions/             # 会话记录（JSON格式的对话历史）
│   ├── logs/                 # 日志目录
│   │   ├── {date}.md         # 执行日志（MD格式）
│   │   ├── dag_dialogue_{date}.md  # DAG执行中LLM对话记录
│   │   └── chat_history_{date}.md  # 聊天内容记录（用户输入+Agent答复）
│   ├── preferences/          # 用户偏好存储
│   │   ├── current_prefs.json      # 当前偏好
│   │   └── preference_history/     # 偏好变更历史
│   ├── dictionary/           # 关键词词典
│   │   └── keywords.json     # 关键词+向量检索词典
│   └── uploads/              # 用户上传文件暂存
│
└── tests/                    # 测试文件
    ├── test_all.py           # 全量测试
    ├── test_history_retriever.py  # 历史检索测试
    └── test_integration.py   # 集成测试
```

## 核心模块关系

```
用户输入 → app.py → engine.py
                        ├── front_desk_agent.py (意图分析)
                        ├── agentic_loop.py (自主执行)
                        │   ├── llm_gateway.py (调用LLM)
                        │   └── tools.py (执行工具)
                        ├── context_manager.py (上下文管理)
                        │   ├── history_retriever.py (历史检索)
                        │   └── sys_prompt.md (系统提示词)
                        ├── dag_scheduler.py (DAG调度)
                        │   └── task_executor.py (命令执行)
                        └── preference_learner.py (偏好学习)
```

## 关键文件修改指南

### 修改 Agent 行为
- `config/sys_prompt.md` — 修改 Agent 的身份、规则、能力描述
- `core/agent/front_desk_agent.py` — 修改意图分析逻辑

### 修改 LLM 调用方式
- `core/llm_gateway.py` — 修改 API 调用逻辑、请求格式
- `config/settings.yaml` — 修改 LLM 提供商配置

### 修改工具能力
- `core/tools.py` — 添加/修改 LLM 可调用的工具

### 修改前端界面
- `web/templates/index.html` — 页面结构
- `web/static/css/style.css` — 样式
- `web/static/js/main.js` — 交互逻辑

### 修改 DAG 执行逻辑
- `core/agentic_loop.py` — Agentic Loop 执行逻辑
- `dag/dag_scheduler.py` — DAG 调度逻辑

### 修改日志和记录
- `core/logger.py` — 执行日志
- `core/context_manager.py` — 聊天历史记录
- `core/agentic_loop.py` — LLM 对话记录
