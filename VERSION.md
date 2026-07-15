# Qingke · Lite Version History / 青稞·lite 版本说明

> **English** | [中文](#中文版本历史)

> **Note**: Qingke Lite (Desktop PyQt5) and CLI_lite (Web Flask) share the same core engine code, only the TUI interaction method differs.
> - **CLI_lite**: Flask Web service + browser access at `http://localhost:5000`
> - **Qingke Lite**: PyQt5 + QWebEngineView embedded web interface, packaged as standalone exe
> Both share identical core logic (`CLI_lite/core/`, `CLI_lite/web/`), with synchronized version numbers.

## Current Version

**v1.9.7** (2026-07-15)

---

## Version History

### v1.9.7 (2026-07-15)

**DAG Node Quality Assessment & Error Handling (Major Optimization):**
- **Quality Assessment Routing**: Errors no longer trigger replanning directly; instead, they go through quality assessment decision (retry/replan/skip)
- **New `_evaluate_error_action` Method**: Evaluates node error types and determines optimal handling
  - Timeout errors with retry < 2: automatic retry
  - Retry count limit reached: forced replanning
  - LLM evaluates three operations: retry, replan, skip
- **Enhanced Node Self-Review**: `_evaluate_node` return format adds `action` field
  - pass=True → action="continue"
  - pass=False → choose retry/replan/skip based on evaluation
- **Error Leakage Prevention**: All error context messages prefixed with "[System Internal - Error Auto-Handled]"
  - System prompt adds Rule 11: prohibit LLM from reporting internal error handling to users
  - Error messages explicitly marked "for internal decision only, do not report to user"
- **Non-run_command Tool Timeout Protection**: Default 120s timeout prevents tools from hanging DAG
- **Deep Code Self-Inspection Fixes** (issues found via reverse thinking):
  - Node failure direct replan → route to quality assessment
  - Reply processing exception direct replan → route to quality assessment
  - Parallel group execution failure direct replan → route to quality assessment
  - Stuck detection direct replan → route to quality assessment
  - Intermediate reply message leakage → add "do not report to user" instruction
- **Node Retry Counter**: `_node_retry_counts` tracks retry count per node
- Files: `agentic_loop.py`

### v1.9.6 (2026-07-15)

**DAG Node Error Capture & Auto-Replanning (Major New):**
- **Full-chain Exception Capture**: Added try-except error capture in 5 key DAG execution stages
  - `_parse_response`: LLM response parsing exception capture (JSON parsing failure, format errors, etc.)
  - `_execute_tool_with_stuck_detection`: Tool execution global exception capture (tool internal crash, timeout, etc.)
  - `_execute_parallel_batch`: Parallel batch exception capture (any parallel node failure triggers overall replanning)
  - `answer` type reply handling: answer generation exception capture
  - Result processing section: result extraction, filtering, output, self-review logic exception capture
- **Node Failure Marking**: After capturing exception, yield `dag_node_complete` with `status: "failed"` for real-time frontend display
- **Auto-Replanning Mechanism**:
  - Auto-call `_try_replan()` to generate new execution plan after node failure
  - Error info injected into LLM context via messages list to assist subsequent decisions
  - Consecutive failure tracking (`_consecutive_failures`), inject warning when same tool fails consecutively above threshold
- **Total Replanning Limit**:
  - Maximum 10 replans (`_max_total_replan_failures=10`), prevent infinite replanning loop
  - After reaching limit, yield failed node event and task exits cleanly
- **Parallel Group Error Handling**: When any node in parallel batch fails, mark entire group as failed and trigger replanning
- Files: `agentic_loop.py`

### v1.9.5 (2026-07-14)

**并行DAG执行（重大新增）:**
- **并行组标记**：LLM规划时可标记 `parallel_group`（如 "A"、"B"），互不依赖的步骤自动归为同组
- **ThreadPoolExecutor 并行执行**：同组节点通过线程池并发执行（最多5线程），显著提升多文件操作效率
- **并行事件流**：新增 `dag_node_parallel_start`/`dag_node_parallel_end` 事件，前端实时显示并行执行状态
- **前端并行展示**：并行节点在DAG卡片中以flex布局并排显示，蓝色虚线边框标识，并行组标签醒目提示
- **初始规划+重规划均支持**：`_generate_plan` 和 `_try_replan` 的prompt均要求LLM识别可并行步骤

**二进制内容过滤（Bug修复）:**
- **源头检测**：`ToolReadFile.execute` 在读取前8KB检测13种二进制签名（PK/ZIP/DOCX/XLSX/PDF/PNG/JPEG等）+ 通用null字节/非打印字符比率检测，直接拒绝二进制文件
- **兜底过滤**：`AgenticLoop._sanitize_output` 在工具结果输出点二次过滤，防止二进制数据泄漏到前端聊天栏

**重规划反思机制（优化）:**
- **反思prompt**：重规划时要求LLM先分析失败根因（reflection），再生成新计划，避免重复犯错
- **reflection字段**：`dag_replan` 事件新增 `reflection` 字段，前端以黄色提示条展示反思内容
- **JSON格式扩展**：重规划返回格式新增 `"reflection"` 字段

**上下文扩展到60K token（重大优化）:**
- **总上下文预算**：`max_tokens` 从8000扩展到24000（3倍），支持更长的对话历史和更复杂的任务
- **LLM输出token**：`max_tokens`（输出）从4000扩展到12000，支持更详细的回复
- **工具结果截断**：从800字符扩展到2400字符，减少信息丢失
- **DAG上下文压缩阈值**：10000/5000 → 30000/15000字符
- **DAG执行历史压缩**：10000/150 → 30000/450字符
- **预防性压缩触发**：80000 → 240000字符（约60K token）
- **滑动窗口**：保留最近4条 → 12条DAG消息
- 涉及文件：`context_manager.py`、`settings.yaml`、`config_guard.py`、`llm_gateway.py`、`agentic_loop.py`

**上下文安全机制（重大新增）:**
- **三级压缩重试**：LLM调用失败时逐级压缩上下文后重试（最多3轮）
  - Level 1 (mild)：DAG历史截断到450字符，保留最近12条DAG消息，超30K压缩旧消息
  - Level 2 (moderate)：DAG历史截断到250字符，保留最近6条，始终压缩旧消息
  - Level 3 (aggressive)：DAG历史截断到120字符，保留最近2条，裁剪旧用户消息
- **PayloadTooLargeError 识别**：新增异常类型，识别HTTP 400/413/429中payload过大场景（关键词匹配：request too large、reduce the length、token limit等）
- **通用异常兜底**：非网络、非上下文的其他LLM异常，自动尝试level 1压缩重试1次
- **渐进式压缩**：`_compress_for_retry(messages, level=N)` 参数化压缩等级，精准控制压缩力度
- 涉及文件：`llm_gateway.py`（新增PayloadTooLargeError）、`agentic_loop.py`（多级重试循环）

### v1.9.4 (2026-07-13)

**内置浏览器能力（重大新增）:**
- **TUI内置Chromium浏览器**：基于QWebEngineView（Chromium内核），在用户界面中直接显示浏览器窗口，用户可实时看到浏览过程
- **网络请求全量捕获**：
  - `QWebEngineUrlRequestInterceptor` 拦截所有网络请求（URL、method、resourceType）
  - JS注入 monkey-patch fetch/XHR 捕获响应信息（状态码、content-type、耗时）
  - 网络日志自动记录，最多保留5000条，支持时间戳过滤和关键词搜索
- **独立Profile隔离**：内置浏览器使用独立QWebEngineProfile，不与主窗口共享Cookie/缓存
- **7个内置浏览器工具**：
  - `builtin_browser_open`：打开内置浏览器窗口（支持指定初始URL）
  - `builtin_browser_navigate`：导航到指定URL
  - `builtin_browser_get_content`：获取页面HTML内容（支持max_length截断）
  - `builtin_browser_screenshot`：截图并保存为PNG文件
  - `builtin_browser_get_network`：获取捕获的网络请求日志（支持since/limit/keyword过滤）
  - `builtin_browser_execute_js`：执行JavaScript代码并返回结果
  - `builtin_browser_close`：关闭内置浏览器释放资源
- **跨线程安全通信**：工具线程（Flask）通过queue.Queue + threading.Lock与Qt主线程的BrowserWindow通信
- **Chrome 114 User-Agent伪装**：提升网站兼容性
- **System Prompt更新**：新增内置浏览器能力章节，说明工具用法和典型工作流

**代码质量优化（自检修复）:**
- 修复PowerShell命令注入风险（base64编码传递中文到剪贴板）
- 修复浏览器泄漏（close清理 + 重启前强制关闭）
- 修复CSS选择器兼容性（`:contains()` → XPath）
- 修复grep行数计数（`len(matches)` → `len(output.strip().splitlines())`）
- 修复裸except异常吞没（添加日志记录）
- 修复参数名覆盖内置函数（`input` → `user_input`）
- 修复Tesseract未安装时的友好提示
- 修复DAG执行竞态条件（状态检查加锁）
- 修复上下文压缩阈值逻辑（`>= 10000` 替代 `> 10000`）
- 修复LLM调用异常捕获粒度（细分LLMAPIError和通用Exception）
- 修复任务存储内存泄漏（TTL自动清理已完成任务30分钟）
- 修复配置热重载竞态条件（双重检查锁定模式）
- 内置浏览器结果存储TTL自动清理（防止超时命令内存泄漏）
- 内置浏览器Profile/WebView显式释放（deleteLater）

**打包与界面优化:**
- 修复打包后 `ModuleNotFoundError: core.engine`（PyInstaller PYZ 遗漏 core.engine 模块，改为 datas 文件兜底）
- TUI界面所有窗口标题增加版本号显示（主窗口、配置窗口、内置浏览器窗口）
- build.spec 将 core/dag/dispatcher/web 源码目录加入 datas，确保所有模块可从文件系统导入
- 新增 `runtime_hook.py`：PyInstaller运行时钩子，在任何代码执行前设置CLI_lite路径
- app.py frozen模式下使用 `sys._MEIPASS` 设置路径

**智能分析能力（新增）:**
- 新增「智能分析与主动建议」系统提示词章节：多角度分析（横向扩展、纵向深入、风险提示、最佳实践）
- 放宽工作原则中过于严格的限制：允许任务完成后附带关联建议和风险提示
- agentic_loop format_prompt 同步更新：新增多角度思考规则
- 前台Agent问候回复增强：更自然的语气、更完整的能力展示

### v1.12.0 (2026-07-13)

**历史对话分页加载（新增）:**
- 启动时自动加载最近3轮历史对话到聊天栏
- 滚动到顶部时自动加载更早的历史（每次3轮）
- 倒序分页：最新消息优先显示，向上滚动查看更早对话
- 加载状态提示：加载中显示"正在加载历史对话..."，无更多历史时显示"已加载全部历史对话"

**聊天栏重置功能（新增）:**
- 新增"清空聊天"按钮（橙色，位于文件上传区域右侧）
- 点击后清空聊天栏所有消息，重置为初始欢迎状态
- 仅清空前端显示，不删除session文件和归档的chat_history MD文件
- 同步重置历史分页状态，重新加载时从第1页开始

**重启机制修复（关键修复）:**
- 新增 `restart_app` 工具：AI可通过工具调用触发应用重启
- 新增 `/api/system/restart` 和 `/api/system/restart-status` API端点
- TUI轮询机制：每2秒检测重启信号，检测到后自动重载WebEngineView页面
- 重启流程：AI调用restart_app → POST /api/system/restart → TUI轮询检测 → 清理缓存 → 重载页面
- 重启标记防抖：执行重启后3秒内不重复触发

**黑屏防护增强（优化）:**
- 服务断开后自动重连：连续3次健康检查失败（90秒）后自动尝试重载页面
- 服务恢复检测：服务重新可用时自动隐藏"服务已断开"提示
- 保留原有防护：禁用GPU加速、30秒定期健康检查、渲染崩溃自动恢复、5分钟缓存清理

**后端新增:**
- `context_manager.get_history_page()` 方法：分页读取历史对话（倒序分页）
- `/api/chat/history/<session_id>` API：分页获取历史对话
- `/api/chat/clear-display` API：清空聊天栏显示（仅前端状态）
- `/api/system/restart` API：请求重启应用
- `/api/system/restart-status` API：检查重启信号状态

**前端新增:**
- `loadChatHistory(page)` 函数：分页加载历史对话
- `handleChatScroll(e)` 函数：滚动到顶部时触发加载更多（500ms节流）
- `clearChatDisplay()` 函数：清空聊天栏并重置状态
- `historyPage`、`hasMoreHistory`、`isLoadingHistory` 全局变量

### v1.9.3 (2026-07-10)

**迭代机制重构（重大改进）:**
- **取消迭代硬限制**：移除原来 `max_iterations × 3 = 45` 次的硬限制，改为无上限执行
- **迭代检查点机制**：达到 120 次迭代后，通过 DAG 交互节点（`dag_ask_user`）询问用户三个选项：
  - "继续执行"：重置计数器，继续任务
  - "换一个方向"：触发重规划，用新的策略继续
  - "终止任务"：立即结束
- **超时兜底**：用户 10 分钟内未回复则默认继续执行
- **检查点可重复触发**：每次选择"继续执行"后重置计数，120 轮后再次触发

**DAG 交互能力增强（重大改进）:**
- **ask_user 工具支持三种交互类型**（通过 `interaction_type` 参数指定）：
  - `input`（默认）：文本输入框 — 用于密码、API密钥等凭证信息
  - `confirm`：按钮选择 — 用于让用户选择方向或确认操作，支持自定义选项（`options`）和按钮样式（`style: primary/warning/danger`）
  - `authorize`：授权审批 — 用于敏感操作前征求用户批准/拒绝
- **前端动态渲染**：根据 `interaction_type` 动态渲染不同的 UI（文本框、彩色按钮、审批按钮）
- **按钮选择响应**：用户点击按钮后自动禁用所有按钮、高亮选中项、显示已选择结果
- **System Prompt 更新**：新增 ask_user 使用场景规则和三种交互类型的格式说明

**桌面自动化能力（重大新增）:**
- **13个桌面操作工具**：支持操控本地桌面应用（Office、记事本、系统设置等）
  - `desktop_open_app`：打开应用程序（路径、命令均可）
  - `desktop_list_windows`：列出所有可见窗口（标题、位置、大小）
  - `desktop_focus_window`：聚焦指定窗口（标题模糊匹配或hwnd句柄）
  - `desktop_close_window`：关闭指定窗口
  - `desktop_screenshot`：截取屏幕截图（全屏/指定窗口/指定区域，返回文件路径）
  - `desktop_click`：在指定坐标点击鼠标（支持左键/右键/双击）
  - `desktop_type_text`：输入文本（自动处理中文剪贴板粘贴）
  - `desktop_press_key`：按键或快捷键（如 ctrl+c, alt+f4, enter）
  - `desktop_scroll`：鼠标滚轮滚动
  - `desktop_drag`：鼠标拖拽（从A点到B点，用于滑块等）
  - `desktop_find_text`：OCR查找屏幕上的文字位置（需pytesseract）
  - `desktop_find_image`：在屏幕上查找指定图像的位置（支持OpenCV或pyautogui）
  - `desktop_get_cursor_pos`：获取当前鼠标坐标
- **窗口管理**：基于Windows API（ctypes），零额外依赖，支持列出/聚焦/关闭窗口
- **界面识别策略**：三级优先级 — VL模型分析截图（最准确）→ OCR文字定位 → 图像模板匹配
- **中文输入支持**：自动检测中文字符，通过剪贴板粘贴方式输入
- **安全机制**：pyautogui FAILSAFE（鼠标移到左上角触发中断）
- **依赖**：pyautogui（核心）、opencv-python（图像匹配，可选）、pytesseract（OCR，可选）
- **System Prompt 更新**：新增桌面自动化能力说明和典型工作流程

**浏览器自动化工具（重大新增）:**
- **12个浏览器相关工具**：支持访问JavaScript渲染的网页、需要登录的网站、以及处理各种验证码
  - `browser_open`：打开浏览器实例（默认使用Edge，Windows系统自带）
  - `browser_navigate`：导航到指定URL
  - `browser_login`：自动填写用户名密码并登录（支持自动检测输入框）
  - `browser_get_content`：获取页面内容（纯文本或HTML）
  - `browser_click`：点击页面元素
  - `browser_type`：在输入框中输入文本
  - `browser_close`：关闭浏览器释放资源
  - `browser_screenshot`：截图（支持全页截图和指定元素截图，用于验证码识别）
  - `browser_wait`：等待指定时间或等待元素出现
  - `browser_execute_script`：执行JavaScript代码
  - `browser_drag`：模拟拖拽操作（用于滑块验证码）
  - `browser_find_elements`：查找页面元素（用于分析页面结构）
  - 基于Selenium实现，支持Edge/Chrome/Firefox
  - 解决了requests无法处理JavaScript渲染页面的问题

- **验证码处理能力**：配合VL API可自动识别和处理多种验证码
  - 图片验证码：截图→VL识别→输入结果
  - 滑块验证码：截图分析→计算距离→拖拽
  - 点击验证码：截图→VL识别坐标→模拟点击
  - 系统提示词已添加详细的验证码处理指引

**页面崩溃恢复（关键修复）:**
- **页面崩溃后DAG状态丢失修复**：QWebEngineView渲染进程崩溃后页面重新加载，JavaScript状态被重置，导致DAG卡片丢失、聊天栏解锁
  - 新增 `/api/chat/status/<session_id>` API端点，用于检查指定session是否有正在运行的任务
  - 页面加载时自动检查并恢复DAG状态：锁定聊天栏、恢复DAG卡片显示、继续轮询获取新事件
  - 影响范围：所有长时间运行的DAG任务（页面崩溃恢复场景）

**答复准确性修复（关键修复）:**
- **答复内容与问题不对应修复**：需求分析结果未被加入到执行对话中，导致LLM在执行节点时可能不知道完整的需求上下文，最终答复偏离用户原始问题
  - 修复：将需求分析结果作为系统消息加入执行对话，并明确提示"始终围绕用户的原始需求展开"
  - 系统提示词增加工作原则："始终围绕用户的原始问题"
  - 影响范围：所有使用需求理解阶段的任务

**DAG规划质量优化（重大改进）:**
- **需求理解阶段**：新增 `_understand_requirements()` 方法，在规划前先分析任务需求（核心目标、关键约束、所需资源、潜在风险、成功标准），为LLM提供更全面的规划上下文
- **规划步骤数增加**：初始规划从 3-7 步提升到 5-12 步，步骤描述从 20 字放宽到 30 字，减少"计划覆盖不足→频繁重规划"的问题
- **重规划完整上下文**：`_try_replan()` 提供最近 5 个节点的执行日志（节点名称、状态、结果预览），让 LLM 基于实际执行情况生成更精准的后续计划
- **重规划步骤数下限**：强制要求至少生成 3 个步骤，最多重试 3 次，避免"只生成 1-2 个步骤→执行完立即再重规划"的循环

### v1.8.3 (2026-07-10)

**Bug修复（关键）:**
- **重规划后DAG莫名关闭修复**：`_try_replan()`中LLM返回的重规划步骤经常自带`index:1`，原代码使用`if "index" not in step:`条件判断导致index不被覆盖。重规划后的步骤index仍为[1,2,3]，而`dag_node_index`已递增到较大值，`dag_node_index >= max_planned_index`立即为True，误触发"所有规划步骤已执行完成"并终止DAG
  - 修复：将条件赋值改为强制覆盖`step["index"] = current_index + 1 + i`，确保重规划步骤index从当前节点号之后递增
  - 影响范围：所有涉及重规划的任务（节点失败、质量不达标、路径切换等场景）

### v1.8.2 (2026-07-09)

**DAG规划卡片修复:**
- **规划失败兜底**：`_generate_plan()`调用LLM失败时自动重试1次，仍失败则返回兜底最小计划（分析任务需求→执行任务→回复用户结果），确保前端始终有规划卡片展示
- **前端容错**：`addDagNodeStart()`在`dag_plan`事件缺失时自动补充WBS树形结构，节点逐个动态创建而非空白等待
- **聊天栏输出修复（继承自v1.8.1）**：`task_complete`工具和replan失败路径的`dag_node_complete`事件补充`name`/`command`字段，确保`final_response`正确提取
- **api.py兜底**：`last_node_result`记录最后一个节点结果，当`reply_to_user`和`task_complete`均未触发时兜底输出

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
