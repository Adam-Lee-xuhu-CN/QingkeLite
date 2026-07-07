# 青稞·lite 版本说明

> **说明**：青稞lite（桌面版 PyQt5）和 CLI_lite（Web版 Flask）共享同一套核心引擎代码，仅TUI交互方式不同。
> - **CLI_lite**：Flask Web服务 + 浏览器访问 `http://localhost:5000`
> - **青稞lite**：PyQt5 + QWebEngineView 内嵌Web界面，打包为独立exe
> 两者的核心逻辑（`CLI_lite/core/`、`CLI_lite/web/`）完全一致，版本号同步更新。

## 当前版本

**v1.8.1** (2026-07-07)

---

## 版本历史

### v1.8.1 (2026-07-07)

**DAG体验优化:**
- **DAG中间状态显示**：节点完成后自审/重规划阶段，DAG卡片标题实时更新为"正在评估节点质量..."或"正在规划后续步骤..."，消除"等待执行"的空白感
  - 新增`dag_evaluating`事件：节点自审LLM调用前发送
  - 新增`dag_replanning`事件：重规划LLM调用前发送
  - 前端`updateDagStatusText()`函数动态更新DAG卡片标题

**聊天窗口内容拦截:**
- **截断JSON泄漏拦截**：LLM返回截断/畸形JSON（如`{"tool"`）时，前后端双重拦截，防止原始JSON溢出到聊天栏
  - 后端：`_extract_command_from_text()`检测以`{`或`[`开头的非合法JSON内容，替换为友好提示
  - 前端：`isMalformedJsonContent()`函数在`addMessage()`和`dag_node_complete`事件中双重过滤
  - 拦截内容记录到日志文件，不影响DAG执行流程

**DAG任务完成修复（继承自v1.8.0）:**
- 所有规划节点完成后`_try_replan()`失败时强制yield task_complete并return，防止无限循环

### v1.8.0 (2026-07-06)

**架构重构（关键）:**
- **彻底解决Network Error：用HTTP短轮询替代SSE长连接**
  - 根因：QWebEngineView内嵌Chromium（约87版本）对fetch + ReadableStream长连接存在不稳定行为，长时间运行的流式响应会被浏览器层中断，前端收到Network Error
  - 方案：废弃`/api/chat/stream` SSE端点，改为`POST /api/chat/start`启动后台任务 + `GET /api/chat/poll/<session_id>`短轮询获取事件（500ms间隔）
  - 后端：`_run_task_background()`后台线程执行agentic_loop，事件通过线程安全列表存储
  - 前端：`sendMessage()`改为POST启动+轮询GET，每个请求都是短生命周期，彻底避免长连接超时
  - `abortDag()`同步更新：调用`currentPollStop()`停止轮询 + POST `/api/chat/abort`通知后端

**新增API端点:**
- `POST /api/chat/start` — 启动异步对话任务，返回JSON `{"status": "started", "session_id": "..."}`
- `GET /api/chat/poll/<session_id>?since=N` — 轮询获取新事件，返回 `{"status": "...", "events": [...], "total": N}`
- `DELETE /api/chat/cleanup/<session_id>` — 清理已完成任务数据

**废弃端点:**
- `/api/chat/stream` (SSE) — 不再使用，已被轮询模式完全替代

### v1.7.3 (2026-07-06)

**Bug修复（关键）:**
- **彻底修复SSE Network Error**：v1.7.2的心跳机制未解决根本问题，本次从异常保护和响应头两方面彻底修复
  - 根因：`generate()`生成器函数中任何未捕获异常（如`front_desk.process()`失败、`save_conversation`出错、`json.dumps`序列化失败等）都会导致生成器崩溃，Flask直接关闭连接，前端收到Network Error
  - 方案1：整个`generate()`包裹在`try/except/finally`中，异常时发送error事件而非崩溃
  - 方案2：`finally`块中始终发送`[DONE]`结束标记，确保前端不会无限等待
  - 方案3：保存对话/DAG记录等非关键操作单独try/except，失败不影响SSE流
  - 方案4：添加SSE专用响应头（`Cache-Control: no-cache`、`X-Accel-Buffering: no`、`Connection: keep-alive`），防止缓冲导致连接异常
  - 心跳间隔从8秒缩短到5秒

### v1.7.2 (2026-07-06)

**Bug修复:**
- **SSE心跳保活机制**：使用线程+队列分离事件生产和SSE发送，主线程每8秒发送一次心跳包保持连接
  - 后台线程运行agentic_loop，事件通过Queue传递给主线程的SSE生成器
  - 前端忽略heartbeat类型事件，不展示给用户
- 修复v1.7.1中API超时和重试机制不足以解决Network Error的问题

### v1.7.1 (2026-07-06)

**Bug修复:**
- 修复LLM调用失败问题：`llm_gateway.py`缺少`logger`导入导致`name 'logger' is not defined`错误
- 修复Network Error问题：LLM API网络错误自动重试（最多3次，指数退避5秒→10秒→20秒）
- 超时时间从120秒增加到300秒，适应复杂任务（如教案生成）的长耗时需求
- 网络错误不再返回错误消息字符串，而是抛出LLMAPIError异常，防止agentic_loop误执行

**离线闭环安装（完善）:**
- 内置Python 3.12.4和Node.js 20.15.0离线安装包
- 用户无需自行下载安装包，开箱即用
- 压缩包解压后即可在无网络环境下完成环境安装

**打包信息:**
- QingkeLite.exe: 约115 MB
- 压缩包大小: 约168 MB（含离线安装包）
- 离线安装包: Python 3.12.4 (25.53MB) + Node.js 20.15.0 (25.28MB)

### v1.7.0 (2026-07-06)

**环境检测与自动安装（新增）:**
- 新增环境检测模块：启动时自动检测Python和Node.js是否安装，缺失组件自动安装
- 离线闭环安装：优先从本地`installers/`目录查找安装包，无需联网即可安装
- 安装进度UI：在PyQt窗口中显示每个组件的安装进度和日志
- 安装包状态显示：实时显示本地安装包可用状态（本地可用/需要下载）
- 安装向导：支持跳过安装、开始安装、启动青稞三个操作按钮

**Bug修复:**
- 修复PyQt GUI黑屏问题：禁用GPU加速 + 页面崩溃自动恢复 + 定期健康检查
- 修复思考内容（thinking content）对DAG执行的影响：`<think>`/`</think>`等标签在JSON解析前剥离
- 修复节点自审截断误判问题：检测到截断标记且内容>200字符时直接通过
- 修复Dify平台system prompt覆盖BUG：多条system消息合并而非覆盖

**优化:**
- System Prompt监控增强：记录所有system消息数量和总长度
- 节点自审评估输入从`[:1000]`放宽到`[:3000]`
- 大文件截断保护：read_file超500行截断、run_command超5000字符截断
- 连续失败阈值从2次提升到3次，给LLM更多恢复机会

**打包信息:**
- QingkeLite.exe: 约115 MB
- 打包工具: build.py（自动打包+复制运行时数据）
- 新增installers/目录支持离线安装包
- 环境检测与自动安装功能

### v1.6.0 (2026-07-02)

**大素材处理能力（新增）:**
- read_file大文件自动截断：超过500行时截断到200行，提示用Python脚本提取特征、分段读取或grep定位
- run_command输出超长截断：超过5000字符时截断，提示用脚本处理大输出
- system prompt新增大素材处理策略：4种方法（先探查再处理、Python脚本提取特征、分段读取、grep定位）+ 禁止行为清单
- 经2000行CSV测试验证，青稞自主采用Python脚本方案处理大文件，未直接读取全文

**Agent训练与优化:**
- 连续失败阈值从2次提升到3次，给LLM更多恢复机会，避免工具被过早禁用
- reply_to_user后自动取消剩余DAG节点并return，消除无效节点执行（节点数减少33%）
- 自动重规划前检查`_reply_sent`标志，避免回复用户后重复规划
- web_fetch超时从15秒增至20秒，减少网络超时失败
- Python脚本输出重定向改用绝对路径，空输出时自动fallback直接运行获取错误信息

**System Prompt优化:**
- 新增效率原则：控制节点数（3-7个），脚本一次性写好不要反复修改
- 新增失败恢复策略：工具失败时必须尝试替代方案（换命令、换工具），不得放弃告诉用户手动操作
- 新增禁止收集无关信息：不得执行whoami、磁盘空间检查等与任务无关的命令
- 新增reply_to_user后必须task_complete规则

**监控与可观测性:**
- 新增System Prompt注入监控日志：每次LLM调用时记录system prompt是否注入、长度和前100字符预览
- 验证确认：所有LLM调用均包含10344字符的system prompt

**训练验证结果:**

| 任务 | v1.5节点 | v1.6节点 | v1.5失败 | v1.6失败 | v1.5报告 | v1.6报告 |
|------|----------|----------|----------|----------|----------|----------|
| CSV数据分析 | 8 | 6 | 1 | 0 | 未生成 | 683B |
| PowerShell管理 | 8 | 7 | 1(放弃) | 1(自动恢复) | 未生成 | 2211B |
| 网络信息搜索 | 16 | 10 | 0 | 0 | 1809B | 2415B |
| 多文件关联分析 | 21 | 14 | 0 | 1(自动恢复) | 已生成 | 3566B |

**打包信息:**
- QingkeLite.exe: 约115 MB
- 打包工具: build.py（自动打包+复制运行时数据）

### v1.5.0 (2026-07-01)

**新增功能:**
- 添加工作区功能：新增"添加工作区"按钮，支持选择文件夹作为任务素材，消息自动附带工作区路径
- 超时分析节点：ask_user超时后自动创建分析节点，AI自主决策继续等待、跳过问题还是重新规划
- Python脚本输出重定向：Python脚本执行结果写入md文件（`data/logs/python_output_{时间戳}.md`），避免编码格式导致乱码
- system prompt日志路径：自动注入DAG执行日志和LLM调用日志的文件路径，方便需要时详细查询

**上下文管理优化:**
- 上下文彻底分离：用户聊天记录、DAG交互记录、原始system prompt三者独立，互不污染
- DAG上下文独立system message：DAG执行状态和历史不再追加到原始system prompt，而是作为独立的system message注入
- 压缩机制改为不丢弃：超过10000字符时压缩到5000字符左右（缩短每条记录而非丢弃旧记录）
- 压缩机制统一为10000→5000：所有DAG相关上下文（执行历史、交互消息）超过10000字符时才触发压缩，压缩到5000字符左右，未超阈值不压缩

**提示词优化:**
- 强制执行不建议：system prompt新增工作原则，禁止AI只给方案不执行，必须实际操作
- 自主决策机制：AI执行过程中所有决策自主完成，不再询问用户过程事项，ask_user仅限凭证信息（密码、API密钥等）
- 危险操作自主处理：AI自行评估风险+备份，不问用户

**Bug修复:**
- 修复answer类型响应取消剩余DAG节点的问题：改为与reply_to_user一致，回复后DAG继续执行
- 修复`_compress_for_retry`中`len(len(...))`双重嵌套bug

**打包信息:**
- QingkeLite.exe: 115.2 MB
- 打包工具: build.py（自动打包+复制运行时数据）
- 青稞lite和CLI_lite共享同一套核心引擎，仅TUI方式不同

### v1.3.0 (2026-06-29)

**新增功能:**
- DAG动态重规划：节点执行失败时自动触发重规划，生成新的DAG计划替换原卡片（最多3次）
- DAG节点时间戳：每个节点显示规划生成时间和实际完成时间
- DAG重规划指示器：橙色虚线分隔线+旋转图标，清晰标识重规划区域
- DAG节点失败样式：红色边框和文字，区分成功/失败状态

**优化:**
- `desktop` 文件夹重命名为 `青稞lite`，所有引用已更新
- 前端Markdown渲染全面增强（表格、标题、列表、代码块、引用、链接等）

---

### v1.2.0 (2026-06-29)

**新增功能:**
- 提示词编辑Tab：用户可在界面中直接编辑系统提示词，支持保存和恢复初始化模板
- 文件上传功能：支持拖拽/点击上传多文件，文件内容自动解析并随消息发送
- Markdown渲染增强：聊天窗口支持表格、标题、列表、代码块、引用、链接、删除线等完整Markdown语法
- 启动预检：自动检查配置文件、端口占用、依赖完整性

**Bug修复:**
- 修复OpenAI兼容API配置保存不完整问题（provider和openai字段丢失）
- 修复手动修改sys_prompt.md后重启不生效问题（引擎启动时不再无条件覆盖用户修改）
- 修复CLI命令执行时弹出PowerShell窗口问题
- 修复PyInstaller打包后模板404、stdout空值等打包相关问题
- 修复Flask reloader重复端口检查导致启动失败问题

**优化:**
- 文件夹整理：`desktop` 重命名为 `青稞lite`，消除冗余目录
- 错误提示细化：启动失败时分阶段捕获异常，提供具体错误信息

---

### v1.1.0 (2026-06-28)

**新增功能:**
- DAG任务授权：执行危险命令前需要用户确认
- DAG执行进度：实时显示任务执行状态（WBS树形结构）
- 终端输出展示：DAG节点执行结果在聊天窗口中展示
- 用户偏好学习：自动记录用户操作偏好
- 历史记忆检索：支持历史对话上下文关联

**优化:**
- 暗色主题UI优化
- SSE流式响应优化
- 会话管理改进

---

### v1.0.0 (2026-06-27)

**初始版本:**
- 基于Flask的Web聊天界面
- 支持Dify和OpenAI兼容API两种LLM提供商
- DAG任务调度与执行
- CLI命令执行（PowerShell）
- PyQt5桌面应用封装（QWebEngineView）
- 配置文件管理（settings.yaml + sys_prompt.md）

---

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
└── VERSION.md             # 本文件
```

## 运行方式

### Web版（CLI_lite）
```bash
cd CLI_lite
python app.py
# 访问 http://localhost:5000
```

### 桌面版（青稞lite）
```bash
# 开发模式
cd 青稞lite
python main.py

# 打包后
双击 青稞lite/dist/QingkeLite.exe
```

### 打包命令
```bash
python 青稞lite/build.py
```
