# Qingke · Lite / 青稞·lite

> **English** | [中文](#中文)

A lightweight desktop AI assistant supporting natural language interaction, autonomous task execution, and DAG task orchestration.

## Features

- **Natural Language Interaction**: Describe tasks via chat interface, AI automatically analyzes and executes
- **Autonomous Task Execution Engine** (Agentic Loop): Automatically decomposes complex tasks into multiple steps, executes step by step with real-time feedback
- **DAG Task Orchestration**: Complex tasks automatically orchestrated as Directed Acyclic Graphs, supporting node-level status tracking and dynamic replanning
- **Tool System**: Built-in run_command, read_file, write_file, list_directory, glob and more
- **Real-time Streaming Output**: SSE (Server-Sent Events) based real-time task progress push
- **Data Persistence**: All logs, sessions, DAG records persisted in real-time, no data loss on abnormal exit
- **Standalone Desktop Application**: Packaged as single exe file, double-click to run

## Quick Start

### Option 1: Run EXE directly (Recommended)

1. Download `QingkeLite.zip` from [Releases](https://github.com/Adam-Lee-xuhu-CN/QingkeLite/releases)
2. Extract to any directory
3. Double-click `QingkeLite.exe` to run

### Option 2: Run from source

```bash
# Install dependencies
pip install -r CLI_lite/requirements.txt

# Start service
python CLI_lite/app.py
```

Visit `http://localhost:5000` in browser

### Option 3: Build from source

```bash
# Install build dependencies
pip install -r 青稞lite/requirements.txt

# Execute build
python 青稞lite/build.py
```

Build artifacts will be in `青稞lite/dist/` directory.

## Configuration

Edit `config/settings.yaml` to configure LLM service:

```yaml
llm:
  provider: openai    # dify | openai
  openai:
    api_key: "your-api-key"
    base_url: "https://api.openai.com/v1"
    model: "gpt-4o"
  dify:
    api_key: "your-dify-key"
    base_url: "https://api.dify.ai/v1"
```

## Project Structure

```
CLI_lite/
├── app.py                 # Flask application entry
├── config/
│   ├── settings.yaml      # Main configuration
│   └── sys_prompt.md      # System prompt
├── core/
│   ├── engine.py          # Core engine (LLM analysis + DAG scheduling + Agentic Loop)
│   ├── agentic_loop.py    # Autonomous task execution engine
│   ├── llm_gateway.py     # LLM service gateway (Dify/OpenAI)
│   ├── context_manager.py # Context manager
│   ├── logger.py          # Logging system
│   ├── tools.py           # Tool registry center
│   └── agent/
│       └── front_desk_agent.py  # Front desk agent (intent recognition)
├── web/
│   ├── templates/
│   │   └── index.html     # Main page
│   ├── static/
│   │   ├── css/style.css  # Styles
│   │   └── js/
│   │       ├── main.js    # Main logic
│   │       └── config.js  # Config page
│   └── routes/
│       └── api.py         # API routes
├── dag/                   # DAG scheduling module
├── data/                  # Runtime data (logs, sessions, etc.)
└── tests/                 # Tests
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| Desktop Shell | PyQt5 + QWebEngineView |
| Backend | Flask (Python) |
| Frontend | Native HTML/CSS/JavaScript |
| LLM Integration | Dify API / OpenAI Compatible API |
| Streaming Communication | SSE (Server-Sent Events) |
| Packaging | PyInstaller |

## Latest Version

**v1.9.7** (2026-07-15) - DAG Node Quality Assessment & Error Handling Optimization

See [VERSION.md](VERSION.md) for full changelog.

## License

MIT

---

# 中文

轻量级桌面AI助手，支持自然语言交互、自主任务执行、DAG任务编排。

## 功能特性

- **自然语言交互**：通过聊天界面描述任务，AI自动分析并执行
- **自主任务执行引擎**（Agentic Loop）：自动拆解复杂任务为多个步骤，逐步执行并实时反馈
- **DAG任务编排**：复杂任务自动编排为有向无环图，支持节点级状态追踪、动态重规划
- **工具系统**：内置 run_command、read_file、write_file、list_directory、glob 等工具
- **实时流式输出**：基于 SSE（Server-Sent Events）实时推送任务进度
- **数据持久化**：所有日志、会话、DAG记录实时落盘，程序异常退出不丢失
- **桌面独立应用**：打包为单个 exe 文件，双击即用

## 快速开始

### 方式一：直接运行 exe（推荐）

1. 从 [Releases](https://github.com/Adam-Lee-xuhu-CN/QingkeLite/releases) 页面下载 `QingkeLite.zip`
2. 解压到任意目录
3. 双击 `QingkeLite.exe` 运行

### 方式二：源码运行

```bash
# 安装依赖
pip install -r CLI_lite/requirements.txt

# 启动服务
python CLI_lite/app.py
```

浏览器访问 `http://localhost:5000`

### 方式三：源码打包

```bash
# 安装打包依赖
pip install -r 青稞lite/requirements.txt

# 执行打包
python 青稞lite/build.py
```

打包产物在 `青稞lite/dist/` 目录下。

## 配置说明

编辑 `config/settings.yaml` 配置 LLM 服务：

```yaml
llm:
  provider: openai    # dify | openai
  openai:
    api_key: "your-api-key"
    base_url: "https://api.openai.com/v1"
    model: "gpt-4o"
  dify:
    api_key: "your-dify-key"
    base_url: "https://api.dify.ai/v1"
```

## 项目结构

```
CLI_lite应用/
├── CLI_lite/              # 核心引擎（Web服务、AI对话、DAG调度）
│   ├── config/            # 配置文件
│   │   ├── settings.yaml  # 应用配置
│   │   └── sys_prompt.md  # 系统提示词
│   ├── core/              # 核心模块
│   ├── web/               # Web界面（Flask + HTML/JS/CSS）
│   ├── dag/               # DAG任务模块
│   ├── dispatcher/        # 命令执行模块
│   └── data/              # 运行时数据（会话、日志、记忆）
├── 青稞lite/              # PyQt5桌面版打包目录
│   ├── main.py            # 桌面应用入口
│   ├── build.py           # 打包脚本
│   ├── build.spec         # PyInstaller配置
│   ├── requirements.txt   # 桌面版依赖
│   └── dist/              # 打包产物（QingkeLite.exe）
└── VERSION.md             # 版本说明
```

## 技术栈

| 组件 | 技术 |
|------|------|
| 桌面壳 | PyQt5 + QWebEngineView |
| 后端 | Flask (Python) |
| 前端 | 原生 HTML/CSS/JavaScript |
| LLM接入 | Dify API / OpenAI 兼容 API |
| 流式通信 | SSE (Server-Sent Events) |
| 打包 | PyInstaller |

## 最新版本

**v1.9.7** (2026-07-15) - DAG节点质量评估与错误处理优化

详见 [VERSION.md](VERSION.md) 完整更新日志。

## 许可证

MIT