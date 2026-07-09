# QingkeLite - AI Agent Assistant

[English](#english) | [中文](#中文)

---

## English

**QingkeLite** is a local AI Agent application built on large language models, designed for **individual developers and small teams** who need an AI coding assistant that is **easier to deploy and use** than enterprise platforms like OpenClaw.

### Why QingkeLite?

Unlike OpenClaw which requires complex multi-service deployment (Docker, databases, message queues), QingkeLite runs as a **single executable** with zero configuration. Just download, double-click, and start working.

| Feature | OpenClaw | QingkeLite |
|---------|----------|------------|
| Deployment | Docker + multiple services | Single .exe, double-click to run |
| Dependencies | Redis, PostgreSQL, etc. | None (all bundled) |
| Target users | Enterprise teams | Individual developers |
| Setup time | 30+ minutes | < 1 minute |
| LLM support | Multiple providers | Multiple providers (OpenAI, DeepSeek, etc.) |
| Task planning | Agent-based | DAG-based automatic planning |

### Features

- **Intelligent Task Planning**: DAG-based task decomposition and execution
- **Code Generation & Execution**: Generate and run Python scripts with auto dependency installation
- **File Operations**: Read, write, format conversion, batch processing
- **Web Search**: Real-time internet information retrieval
- **Skill System**: Extensible skill framework with custom skill support
- **Local Execution**: All data stored locally, privacy-safe

### Tech Stack

- Python 3.10 + Flask (backend)
- HTML/CSS/JavaScript (frontend)
- PyQt5 + QWebEngineView (desktop)
- PyInstaller (packaging)

### Quick Start

```bash
# Install dependencies
pip install -r CLI_lite/requirements.txt

# Start the application
python main.py
```

Access http://localhost:5000 after startup.

### License

[CC BY-NC 4.0](LICENSE) (Attribution-NonCommercial)

**You may**: Copy, modify, and distribute this project
**You must**: Retain copyright notice and license, indicate changes
**You may NOT**: Use this project for commercial purposes

---

## 中文

**青稞Lite** 是一款基于大语言模型的本地 AI Agent 应用，专为**个人开发者和小团队**设计，相比 OpenClaw 等企业级平台，**部署更简单、使用更便捷**。

### 为什么选择青稞Lite？

与 OpenClaw 需要复杂的多服务部署（Docker、数据库、消息队列）不同，青稞Lite 以**单个可执行文件**运行，零配置，下载即用。

| 特性 | OpenClaw | 青稞Lite |
|------|----------|---------|
| 部署方式 | Docker + 多服务 | 单个 .exe，双击运行 |
| 依赖项 | Redis、PostgreSQL 等 | 无（全部内置） |
| 目标用户 | 企业团队 | 个人开发者 |
| 部署时间 | 30分钟以上 | 不到1分钟 |
| LLM 支持 | 多种提供商 | 多种提供商（OpenAI、DeepSeek 等） |
| 任务规划 | 基于 Agent | 基于 DAG 自动规划 |

### 功能特性

- **智能任务规划**：基于 DAG 的任务分解与执行，自动规划多步骤任务
- **代码生成与执行**：生成并运行 Python 脚本，自动处理依赖安装
- **文件操作**：文件读写、格式转换、批量处理
- **网页搜索**：实时搜索互联网信息
- **技能系统**：可扩展的技能框架，支持自定义技能
- **本地运行**：所有数据存储在本地，保护隐私安全

### 技术栈

- Python 3.10 + Flask（后端）
- HTML/CSS/JavaScript（前端）
- PyQt5 + QWebEngineView（桌面端）
- PyInstaller（打包）

### 快速开始

```bash
# 安装依赖
pip install -r CLI_lite/requirements.txt

# 启动服务
python main.py
```

启动后访问 http://localhost:5000 即可使用。

### 许可证

[CC BY-NC 4.0](LICENSE)（署名-非商业性使用）

**您可以**：复制、修改、分发本项目代码
**您必须**：保留版权声明和许可证，注明修改内容
**您不能**：将本项目用于商业用途
