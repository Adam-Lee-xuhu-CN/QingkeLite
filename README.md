# 青稞·lite

一个轻量级桌面AI助手，支持自然语言交互、自主任务执行、DAG任务编排。

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

1. 从 Releases 页面下载 `青稞lite.zip`
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
CLI_lite/
├── app.py                 # Flask 应用入口
├── config/
│   ├── settings.yaml      # 主配置文件
│   └── sys_prompt.md      # 系统提示词
├── core/
│   ├── engine.py          # 核心引擎（LLM分析+DAG调度+Agentic Loop）
│   ├── agentic_loop.py    # 自主任务执行引擎
│   ├── llm_gateway.py     # LLM服务网关（Dify/OpenAI）
│   ├── context_manager.py # 上下文管理器
│   ├── logger.py          # 日志系统
│   ├── tools.py           # 工具注册中心
│   └── agent/
│       └── front_desk_agent.py  # 前台Agent（意图识别）
├── web/
│   ├── templates/
│   │   └── index.html     # 主页面
│   ├── static/
│   │   ├── css/style.css  # 样式
│   │   └── js/
│   │       ├── main.js    # 主逻辑
│   │       └── config.js  # 配置页
│   └── routes/
│       └── api.py         # API路由
├── dag/                   # DAG调度模块
├── data/                  # 运行时数据（日志、会话等）
└── tests/                 # 测试
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

## License

MIT
