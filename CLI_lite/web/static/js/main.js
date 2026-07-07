// 青稞 Chat Main JavaScript - 单页应用 + DAG授权 + 终端输出

let sessionId = null;
let isSending = false;
let pendingFiles = [];  // 待发送文件列表
let currentPollStop = null;  // 轮询模式停止函数
let workspacePath = '';  // 工作区文件夹路径

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    sessionId = localStorage.getItem('cli_lite_session') || `session_${Math.random().toString(36).substr(2, 8)}`;
    localStorage.setItem('cli_lite_session', sessionId);

    // Enter to send
    document.getElementById('chatInput').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // 文件拖拽支持
    const chatContainer = document.querySelector('.chat-container');
    if (chatContainer) {
        chatContainer.addEventListener('dragover', handleDragOver);
        chatContainer.addEventListener('dragleave', handleDragLeave);
        chatContainer.addEventListener('drop', handleDrop);
    }

    // 工作区标签点击移除
    const workspaceTag = document.getElementById('workspaceTag');
    if (workspaceTag) {
        workspaceTag.addEventListener('click', clearWorkspace);
    }

    // TAB switching (SPA with URL updates)
    initTabSwitching();

    // Handle initial URL
    const initialTab = getTabFromUrl();
    if (initialTab !== 'chat') {
        switchTab(initialTab, false);
    }

    // Load initial data for other tabs
    loadTabData('dag');
    loadTabData('logs');
    loadTabData('preferences');
    // config tab is handled by config.js DOMContentLoaded
});

// ==================== TAB Switching ====================

function getTabFromUrl() {
    const path = window.location.pathname;
    if (path === '/dag') return 'dag';
    if (path === '/logs') return 'logs';
    if (path === '/preferences') return 'preferences';
    if (path === '/config') return 'config';
    if (path === '/prompt') return 'prompt';
    return 'chat';
}

function updateUrl(tabName) {
    const url = tabName === 'chat' ? '/' : `/${tabName}`;
    window.history.pushState({ tab: tabName }, '', url);
}

function initTabSwitching() {
    const tabs = document.querySelectorAll('.nav-tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', (e) => {
            e.preventDefault();
            const targetTab = tab.getAttribute('data-tab');
            switchTab(targetTab, true);
        });
    });

    // Handle browser back/forward
    window.addEventListener('popstate', (e) => {
        const tabName = e.state?.tab || getTabFromUrl();
        switchTab(tabName, false);
    });
}

function switchTab(tabName, updateHistory = true) {
    // Update nav tabs
    document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.nav-tab[data-tab="${tabName}"]`).classList.add('active');

    // Update tab content
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.getElementById(`tab-${tabName}`).classList.add('active');

    // Update URL
    if (updateHistory) {
        updateUrl(tabName);
    }

    // Refresh data when switching to certain tabs
    if (tabName === 'dag') loadTabData('dag');
    if (tabName === 'logs') loadTabData('logs');
    if (tabName === 'preferences') loadTabData('preferences');
    if (tabName === 'prompt') loadPrompt();
    // config tab is handled by config.js
}

// ==================== Tab Data Loading ====================

async function loadTabData(tabName) {
    try {
        switch (tabName) {
            case 'dag':
                await loadDagData();
                break;
            case 'logs':
                await loadLogsData();
                break;
            case 'preferences':
                await loadPreferencesData();
                break;
        }
    } catch (err) {
        console.error(`Failed to load ${tabName} data:`, err);
    }
}

async function loadDagData() {
    const container = document.getElementById('dagContent');
    try {
        const response = await fetch('/api/dag/list');
        const dags = await response.json();

        if (!dags || dags.length === 0) {
            container.innerHTML = '<p class="empty-state">暂无DAG任务</p>';
            return;
        }

        let html = '<div class="dag-list">';
        dags.forEach(dag => {
            const status = dag.status || 'pending';
            html += `
                <div class="dag-item" data-dag-id="${dag.id}">
                    <div class="dag-item-header">
                        <span class="dag-item-name">${dag.name || dag.id}</span>
                        <span class="dag-item-status ${status}">${status}</span>
                    </div>
                    <div class="dag-item-meta">${dag.description || ''}</div>
                    <div class="dag-item-actions">
                        <button class="dag-item-btn" onclick="viewDagDetail('${dag.id}')">查看详情</button>
                        ${status === 'pending' ? `<button class="dag-item-btn primary" onclick="runDag('${dag.id}')">执行</button>` : ''}
                        ${status === 'failed' ? `<button class="dag-item-btn primary" onclick="retryDag('${dag.id}')">重试</button>` : ''}
                    </div>
                </div>
            `;
        });
        html += '</div>';
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = `<p class="empty-state">加载DAG列表失败: ${err.message}</p>`;
    }
}

async function loadLogsData() {
    const container = document.getElementById('logsContent');
    try {
        const response = await fetch('/api/logs');
        const logs = await response.json();

        if (!logs || logs.length === 0) {
            container.innerHTML = '<p class="empty-state">暂无日志</p>';
            return;
        }

        let html = '<div class="log-layout"><div class="log-sidebar">';
        logs.forEach(log => {
            const dateStr = log.date || log;
            const entries = log.entries || 0;
            html += `
                <div class="log-item" onclick="loadLogDetail('${dateStr}', this)">
                    <div class="log-item-date">${dateStr}</div>
                    <div class="log-item-entries">${entries} 条记录</div>
                </div>
            `;
        });
        html += '</div><div class="log-detail" id="logDetail"><p class="empty-state">点击左侧日期查看日志详情</p></div></div>';
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = `<p class="empty-state">加载日志失败: ${err.message}</p>`;
    }
}

async function loadLogDetail(date, element) {
    // Update active state
    document.querySelectorAll('.log-item').forEach(el => el.classList.remove('active'));
    if (element) element.classList.add('active');

    const detailDiv = document.getElementById('logDetail');
    if (!detailDiv) return;

    detailDiv.innerHTML = '<p>加载中...</p>';
    try {
        const response = await fetch(`/api/logs/${date}`);
        const data = await response.json();
        if (data.error) {
            detailDiv.innerHTML = `<p class="empty-state">${data.error}</p>`;
        } else {
            detailDiv.innerHTML = `<pre class="log-content">${data.content || '暂无内容'}</pre>`;
        }
    } catch (err) {
        detailDiv.innerHTML = `<p class="empty-state">加载失败: ${err.message}</p>`;
    }
}

async function loadPreferencesData() {
    const container = document.getElementById('preferencesContent');
    try {
        const response = await fetch('/api/preferences');
        const prefs = await response.json();

        if (!prefs || prefs.length === 0) {
            container.innerHTML = '<p class="empty-state">暂无偏好设置</p>';
            return;
        }

        let html = '<div class="pref-list">';
        prefs.forEach(pref => {
            html += `
                <div class="dag-item">
                    <div class="dag-item-header">
                        <span class="dag-item-name">${pref.level1 || ''} > ${pref.level2 || ''}</span>
                    </div>
                    <div class="dag-item-meta">${pref.level3 || ''}</div>
                </div>
            `;
        });
        html += '</div>';
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = `<p class="empty-state">加载偏好失败: ${err.message}</p>`;
    }
}

async function viewDagDetail(dagId) {
    const container = document.getElementById('dagContent');
    try {
        const response = await fetch(`/api/dag/${dagId}`);
        const dag = await response.json();
        if (dag.error) {
            container.innerHTML = `<p class="empty-state">${dag.error}</p>`;
            return;
        }

        const nodes = dag.nodes || [];
        const statusColors = {
            completed: '#4caf50',
            failed: '#f44336',
            running: '#2196f3',
            pending: '#9e9e9e',
            cancelled: '#bdbdbd',
            aborted: '#bdbdbd',
        };

        let html = `
            <div class="dag-detail-view">
                <div class="dag-detail-header">
                    <button class="dag-back-btn" onclick="loadDagData()">← 返回列表</button>
                    <h3>${dag.name || dag.id}</h3>
                    <span class="dag-status-badge ${dag.status}">${dag.status}</span>
                </div>
                <div class="dag-wbs">
        `;

        nodes.forEach((node, idx) => {
            const color = statusColors[node.status] || '#9e9e9e';
            const statusIcons = {
                completed: '✅', failed: '❌', running: '⏳',
                pending: '⏸️', cancelled: '🚫', aborted: '⏹️'
            };
            const statusIcon = statusIcons[node.status] || '⏸️';
            html += `
                <div class="wbs-node">
                    <div class="wbs-connector" style="border-left-color: ${color}"></div>
                    <div class="wbs-card" style="border-left: 4px solid ${color}">
                        <div class="wbs-card-header">
                            <span class="wbs-index">${idx + 1}</span>
                            <span class="wbs-name">${node.name || node.id}</span>
                            <span class="wbs-status">${statusIcon} ${node.status}</span>
                        </div>
                        ${node.command ? `<div class="wbs-command"><code>${node.command}</code></div>` : ''}
                        ${node.result ? `<div class="wbs-result">${node.result}</div>` : ''}
                    </div>
                </div>
            `;
        });

        html += '</div></div>';
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = `<p class="empty-state">加载DAG详情失败: ${err.message}</p>`;
    }
}

async function runDag(dagId) {
    try {
        const response = await fetch(`/api/dag/${dagId}/run`, { method: 'POST' });
        const result = await response.json();

        // Switch to chat and show results
        switchTab('chat');
        const messagesDiv = document.getElementById('chatMessages');
        const resultDiv = document.createElement('div');
        resultDiv.className = 'message assistant';

        let nodesHtml = '';
        if (result.results) {
            result.results.forEach(node => {
                const status = node.status || 'completed';
                nodesHtml += `
                    <div class="dag-node-detail ${status}">
                        <div class="dag-node-header">
                            <span class="dag-node-name">${node.name}</span>
                            <span class="dag-node-status ${status}">${status}</span>
                        </div>
                        ${node.result ? `
                            <div class="dag-node-output-label">输出</div>
                            <div class="dag-node-output">${escapeHtml(node.result)}</div>
                        ` : ''}
                    </div>
                `;
            });
        }

        resultDiv.innerHTML = `
            <div class="message-content" style="max-width:90%;width:90%;">
                <strong>DAG执行结果</strong>
                <div class="dag-execution-details">${nodesHtml}</div>
            </div>
        `;
        messagesDiv.appendChild(resultDiv);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;

        // Refresh DAG list
        loadDagData();
    } catch (err) {
        console.error('Failed to run DAG:', err);
    }
}

async function retryDag(dagId) {
    try {
        const response = await fetch(`/api/dag/${dagId}/retry`, { method: 'POST' });
        const result = await response.json();
        loadDagData();
    } catch (err) {
        console.error('Failed to retry DAG:', err);
    }
}

// ==================== Chat & DAG Authorization ====================

// 文件选择处理
async function handleFileSelect(e) {
    const files = Array.from(e.target.files);
    await uploadFiles(files);
    // 清空input以允许重复选择同一文件
    e.target.value = '';
}

// 选择工作区文件夹（前端prompt输入路径，后端验证）
async function selectWorkspaceFolder() {
    const path = prompt('请输入工作区文件夹的完整路径：', '');
    if (!path || !path.trim()) return;

    try {
        const response = await fetch('/api/select-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: path.trim() })
        });
        const data = await response.json();
        if (data.status === 'ok' && data.path) {
            workspacePath = data.path;
            const tag = document.getElementById('workspaceTag');
            if (tag) {
                tag.textContent = `工作区: ${workspacePath}`;
                tag.classList.remove('hidden');
            }
        } else if (data.status === 'error') {
            alert(data.message || '路径不存在，请检查后重新输入');
        }
    } catch (e) {
        console.error('选择文件夹失败:', e);
    }
}

// 清除工作区
function clearWorkspace() {
    workspacePath = '';
    const tag = document.getElementById('workspaceTag');
    if (tag) {
        tag.textContent = '';
        tag.classList.add('hidden');
    }
}

// 拖拽处理
function handleDragOver(e) {
    e.preventDefault();
    e.stopPropagation();
    document.querySelector('.chat-container').classList.add('drag-over');
}

function handleDragLeave(e) {
    e.preventDefault();
    e.stopPropagation();
    document.querySelector('.chat-container').classList.remove('drag-over');
}

async function handleDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    document.querySelector('.chat-container').classList.remove('drag-over');
    
    const files = Array.from(e.dataTransfer.files);
    await uploadFiles(files);
}

// 上传文件到服务器并解析内容
async function uploadFiles(files) {
    for (const file of files) {
        try {
            const formData = new FormData();
            formData.append('file', file);

            const response = await fetch('/api/file/upload', {
                method: 'POST',
                body: formData
            });

            if (response.ok) {
                const result = await response.json();
                pendingFiles.push({
                    name: result.name,
                    path: result.path,
                    size: result.size,
                    extension: result.extension,
                    file_text: result.file_text,
                    content_preview: result.content_preview,
                    uploaded: true
                });
                updateFilePreview();
            } else {
                const err = await response.json();
                alert(`文件上传失败: ${err.error || '未知错误'}`);
            }
        } catch (err) {
            console.error('文件上传失败:', err);
            alert(`文件上传失败: ${file.name}`);
        }
    }
}

// 更新文件预览区域
function updateFilePreview() {
    let previewArea = document.querySelector('.file-preview-area');
    
    if (pendingFiles.length === 0) {
        if (previewArea) previewArea.remove();
        return;
    }
    
    if (!previewArea) {
        previewArea = document.createElement('div');
        previewArea.className = 'file-preview-area';
        document.querySelector('.chat-input').before(previewArea);
    }
    
    let html = '<div class="file-preview-list">';
    pendingFiles.forEach((file, index) => {
        const sizeStr = formatFileSize(file.size);
        html += `
            <div class="file-preview-item">
                <div class="file-icon">${getFileIcon(file.extension)}</div>
                <div class="file-info">
                    <div class="file-name">${escapeHtml(file.name)}</div>
                    <div class="file-size">${sizeStr}</div>
                </div>
                <button class="file-remove-btn" onclick="removeFile(${index})" title="移除">×</button>
            </div>
        `;
    });
    html += '</div>';
    previewArea.innerHTML = html;
}

// 移除文件（同时删除服务器上的文件）
async function removeFile(index) {
    const file = pendingFiles[index];
    if (file && file.uploaded && file.path) {
        try {
            await fetch('/api/file/remove', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: file.path })
            });
        } catch (err) {
            console.error('删除服务器文件失败:', err);
        }
    }
    pendingFiles.splice(index, 1);
    updateFilePreview();
}

// 格式化文件大小
function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// 获取文件图标
function getFileIcon(ext) {
    const icons = {
        '.pdf': '📄',
        '.doc': '📄', '.docx': '📄',
        '.xls': '📊', '.xlsx': '📊',
        '.ppt': '📊', '.pptx': '📊',
        '.txt': '📝',
        '.jpg': '🖼️', '.jpeg': '🖼️', '.png': '🖼️', '.gif': '🖼️',
        '.mp3': '🎵', '.wav': '🎵',
        '.mp4': '🎬', '.avi': '🎬',
        '.zip': '🗜️', '.rar': '🗜️',
        '.py': '🐍', '.js': '📜', '.html': '🌐', '.css': '🎨'
    };
    return icons[ext] || '📎';
}

async function sendMessage() {
    const input = document.getElementById('chatInput');
    const sendBtn = document.getElementById('sendBtn');
    const message = input.value.trim();
    const files = [...pendingFiles];  // 复制待发送文件列表

    if ((!message && files.length === 0) || isSending) return;

    isSending = true;
    input.value = '';
    input.disabled = true;  // ② DAG运行中禁用输入框
    pendingFiles = [];  // 清空待发送文件
    updateFilePreview();  // 移除预览区域

    // 显示停止任务按钮
    const stopAllBtn = document.getElementById('stopAllBtn');
    if (stopAllBtn) stopAllBtn.classList.remove('hidden');

    // 切换为终止按钮
    sendBtn.textContent = '终止';
    sendBtn.classList.add('stop-mode');
    sendBtn.disabled = false;
    sendBtn.onclick = abortDag;

    // 轮询模式：设置停止函数
    let stopPolling = false;
    currentPollStop = () => { stopPolling = true; };

    // 如果有工作区，附加到消息中
    let finalMessage = message;
    if (workspacePath) {
        finalMessage = message + `\n本次的工作区（有且仅有工作区的文件作为本次任务的素材）是：${workspacePath}`;
    }

    // 添加用户消息（包含文件卡片）
    addUserMessage(message, files);

    // Add loading indicator
    const loadingDiv = addMessage('', 'assistant');
    loadingDiv.innerHTML = '<div class="message-content"><div class="agentic-loading">思考中...</div></div>';

    let finalResponse = '';
    let agenticStepCount = 0;
    let progressContainer = null;
    let dagAuthDiv = null;

    try {
        // 1. 启动异步任务（POST /api/chat/start，返回JSON，非SSE长连接）
        const startResp = await fetch('/api/chat/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                input: finalMessage,
                session_id: sessionId,
                files: files
            })
        });

        if (!startResp.ok) {
            const errData = await startResp.json();
            throw new Error(errData.error || '启动任务失败');
        }

        // 2. 短轮询获取事件（每500ms请求一次，彻底避免长连接超时问题）
        let eventIndex = 0;
        let taskDone = false;
        const POLL_INTERVAL = 500;

        while (!stopPolling && !taskDone) {
            await new Promise(r => setTimeout(r, POLL_INTERVAL));
            if (stopPolling) break;

            try {
                const pollResp = await fetch(`/api/chat/poll/${sessionId}?since=${eventIndex}`);
                if (!pollResp.ok) continue;

                const pollData = await pollResp.json();

                // 处理每个事件（逻辑与原SSE完全一致）
                for (const data of pollData.events) {
                    if (data.type === 'analysis') {
                        if (data.action === 'need_dag' && data.dag_suggested) {
                            progressContainer = createProgressContainer();
                            updateLoadingDiv(loadingDiv, '');
                        }
                    }
                    else if (data.type === 'dag_auth_request') {
                        if (dagAuthDiv) dagAuthDiv.remove();
                        dagAuthDiv = createDagAuthRequest(data, loadingDiv);
                    }
                    else if (data.type === 'dag_auth_result') {
                        if (dagAuthDiv) {
                            updateDagAuthResult(dagAuthDiv, data);
                        }
                    }
                    else if (data.type === 'dag_planning') {
                        if (!progressContainer) {
                            progressContainer = createProgressContainer();
                            updateLoadingDiv(loadingDiv, '');
                        }
                        const todoList = progressContainer.querySelector('.dag-todo-list');
                        if (todoList) {
                            todoList.innerHTML = `<div class="dag-todo-item dag-todo-running">
                                <span class="dag-todo-text">${data.content || '正在规划任务...'}</span>
                            </div>`;
                        }
                    }
                    else if (data.type === 'dag_plan') {
                        if (!progressContainer) {
                            progressContainer = createProgressContainer();
                            updateLoadingDiv(loadingDiv, '');
                        }
                        renderDagPlan(progressContainer, data.steps, data.planned_at, data.plan_version);
                    }
                    else if (data.type === 'dag_replan') {
                        if (progressContainer) {
                            renderDagReplan(progressContainer, data.steps, data.planned_at, data.plan_version, data.reason);
                        }
                    }
                    else if (data.type === 'dag_node_start') {
                        if (progressContainer) {
                            addDagNodeStart(progressContainer, data);
                        }
                    }
                    else if (data.type === 'dag_node_output') {
                        if (progressContainer) {
                            addDagNodeOutput(progressContainer, data);
                        }
                    }
                    else if (data.type === 'dag_node_complete') {
                        if (progressContainer) {
                            addDagNodeComplete(progressContainer, data);
                        }
                        if (data.name === '回复用户' || data.command === 'reply_to_user') {
                            const result = data.result || '';
                            // 前端拦截：过滤畸形JSON内容
                            finalResponse = isMalformedJsonContent(result) ? '' : result;
                        }
                    }
                    else if (data.type === 'dag_evaluating') {
                        if (progressContainer) {
                            updateDagStatusText(progressContainer, data.message || '正在评估节点质量...');
                        }
                    }
                    else if (data.type === 'dag_replanning') {
                        if (progressContainer) {
                            updateDagStatusText(progressContainer, data.message || '正在规划后续步骤...');
                        }
                    }
                    else if (data.type === 'dag_node_stuck') {
                        if (progressContainer) {
                            addDagNodeStuck(progressContainer, data);
                        }
                    }
                    else if (data.type === 'dag_ask_user') {
                        if (progressContainer) {
                            addDagAskUser(progressContainer, data);
                        }
                    }
                    else if (data.type === 'step') {
                        agenticStepCount++;
                        if (progressContainer) {
                            addProgressStep(progressContainer, data);
                        }
                    }
                    else if (data.type === 'response' || data.type === 'done') {
                        if (data.response) {
                            finalResponse = data.response;
                        }
                        sessionId = data.session_id || sessionId;
                        localStorage.setItem('cli_lite_session', sessionId);
                        // 更新DAG卡片标题为"已完成"
                        if (progressContainer) {
                            markDagCompleted(progressContainer);
                        }
                    }
                    else if (data.type === 'aborted') {
                        if (progressContainer) {
                            markPendingNodesCancelled(progressContainer);
                        }
                        if (data.response) {
                            finalResponse = data.response;
                        }
                        sessionId = data.session_id || sessionId;
                        localStorage.setItem('cli_lite_session', sessionId);
                    }
                    else if (data.type === 'error') {
                        finalResponse = data.message || '执行出现异常，请重试';
                    }
                }

                eventIndex = pollData.total;

                // 检查任务是否完成
                if (['done', 'error', 'aborted'].includes(pollData.status)) {
                    taskDone = true;
                }
            } catch (pollErr) {
                // 轮询请求失败，将在下一个间隔重试（短请求失败概率极低）
                console.warn('轮询请求失败，稍后重试:', pollErr);
            }
        }

        // Remove loading indicator
        loadingDiv.remove();

        // 处理结果
        if (stopPolling && !taskDone) {
            // 用户主动终止
            if (finalResponse) {
                addMessage(finalResponse + '\n\n（DAG已被用户终止）', 'assistant');
            } else {
                addMessage('DAG已被用户终止。', 'assistant');
            }
        } else {
            // 正常完成（包括后端主动abort）
            if (finalResponse) {
                addMessage(finalResponse, 'assistant');
            }
        }

        // Remove progress container if empty
        if (progressContainer && agenticStepCount === 0 && !dagAuthDiv) {
            progressContainer.remove();
        }

        // Refresh DAG list
        loadDagData();

    } catch (error) {
        loadingDiv.remove();
        addMessage(`错误: ${error.message}`, 'assistant');
    } finally {
        isSending = false;
        currentPollStop = null;
        // 重新启用输入框
        const chatInput = document.getElementById('chatInput');
        if (chatInput) chatInput.disabled = false;
        // 隐藏停止任务按钮
        const stopAllBtn = document.getElementById('stopAllBtn');
        if (stopAllBtn) stopAllBtn.classList.add('hidden');
        // 恢复为发送按钮
        const sendBtn = document.getElementById('sendBtn');
        sendBtn.textContent = '发送';
        sendBtn.classList.remove('stop-mode');
        sendBtn.onclick = sendMessage;
        sendBtn.disabled = false;
    }
}

// ==================== DAG Authorization UI ====================

// 终止DAG执行（紧急避险）
function abortDag() {
    // 自动生成一条用户输入记录到聊天记录中
    addMessage('停止前面的任务', 'user');

    const stopAllBtn = document.getElementById('stopAllBtn');
    if (stopAllBtn) {
        stopAllBtn.disabled = true;
        stopAllBtn.querySelector('span').textContent = '正在停止...';
    }

    // 停止前端轮询循环
    if (currentPollStop) {
        currentPollStop();
    }

    // 通知后端设置终止标志
    fetch('/api/chat/abort', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId })
    }).catch(err => console.error('Abort request failed:', err));
}

function createDagAuthRequest(data, loadingDiv) {
    const messagesDiv = document.getElementById('chatMessages');
    const authDiv = document.createElement('div');
    authDiv.className = 'message assistant';

    const tasks = data.tasks || [];
    const reason = data.reason || '';

    let tasksHtml = tasks.map((t, i) => `<div class="dag-auth-detail-item">${i + 1}. ${t}</div>`).join('');

    authDiv.innerHTML = `
        <div class="message-content" style="max-width:90%;width:90%;padding:0;background:transparent;">
            <div class="dag-auth-request">
                <div class="dag-auth-header">
                    <span class="dag-auth-icon">&#9888;</span>
                    <span>需要授权执行DAG任务</span>
                </div>
                <div class="dag-auth-details">
                    <div class="dag-auth-detail-item">
                        <span class="dag-auth-detail-label">原因:</span>
                        <span>${escapeHtml(reason)}</span>
                    </div>
                    <div class="dag-auth-detail-item">
                        <span class="dag-auth-detail-label">任务列表:</span>
                    </div>
                    ${tasksHtml}
                </div>
                <div class="dag-auth-actions">
                    <button class="dag-auth-btn approve" onclick="approveDagAuth('${data.dag_id || ''}', this)">批准执行</button>
                    <button class="dag-auth-btn reject" onclick="rejectDagAuth('${data.dag_id || ''}', this)">拒绝</button>
                </div>
            </div>
        </div>
    `;

    messagesDiv.appendChild(authDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    // Remove loading
    if (loadingDiv) loadingDiv.remove();

    return authDiv;
}

function updateDagAuthResult(authDiv, data) {
    const authRequest = authDiv.querySelector('.dag-auth-request');
    if (authRequest) {
        const actionsDiv = authRequest.querySelector('.dag-auth-actions');
        if (actionsDiv) {
            if (data.approved) {
                actionsDiv.innerHTML = '<span style="color:#4caf50;font-weight:500;">已批准执行</span>';
            } else {
                actionsDiv.innerHTML = '<span style="color:#ff9800;font-weight:500;">已拒绝</span>';
            }
        }
    }
}

async function approveDagAuth(dagId, btn) {
    // Disable buttons
    const actionsDiv = btn.parentElement;
    actionsDiv.innerHTML = '<span style="color:#4caf50;font-weight:500;">已批准，执行中...</span>';

    try {
        const response = await fetch('/api/dag/authorize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dag_id: dagId, approved: true })
        });
        const result = await response.json();

        // Show execution results in chat
        if (result.node_results) {
            showDagExecutionResults(result);
        }
    } catch (err) {
        addMessage(`DAG执行失败: ${err.message}`, 'assistant');
    }

    loadDagData();
}

async function rejectDagAuth(dagId, btn) {
    const actionsDiv = btn.parentElement;
    actionsDiv.innerHTML = '<span style="color:#ff9800;font-weight:500;">已拒绝</span>';

    try {
        await fetch('/api/dag/authorize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dag_id: dagId, approved: false })
        });
    } catch (err) {
        console.error('Failed to reject DAG:', err);
    }
}

function showDagExecutionResults(result) {
    const messagesDiv = document.getElementById('chatMessages');
    const resultDiv = document.createElement('div');
    resultDiv.className = 'message assistant';

    const nodeResults = result.node_results || [];
    let nodesHtml = '';

    nodeResults.forEach((node, idx) => {
        const status = node.status || 'completed';
        nodesHtml += `
            <div class="dag-node-detail ${status}">
                <div class="dag-node-header">
                    <span class="dag-node-name">${idx + 1}. ${escapeHtml(node.name || '')}</span>
                    <span class="dag-node-status ${status}">${status}</span>
                </div>
                ${node.command ? `<div class="dag-node-command">PS> ${escapeHtml(node.command)}</div>` : ''}
                ${node.result ? `
                    <div class="dag-node-output-label">终端输出</div>
                    <div class="dag-node-output">${escapeHtml(node.result)}</div>
                ` : ''}
                ${node.duration ? `<div style="font-size:11px;color:#999;margin-top:4px;">耗时: ${node.duration}s</div>` : ''}
            </div>
        `;
    });

    resultDiv.innerHTML = `
        <div class="message-content" style="max-width:90%;width:90%;">
            <strong>DAG执行结果</strong>
            <div class="dag-execution-details">${nodesHtml}</div>
        </div>
    `;

    messagesDiv.appendChild(resultDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// ==================== Progress & Terminal Output ====================

function createProgressContainer() {
    const messagesDiv = document.getElementById('chatMessages');
    const container = document.createElement('div');
    container.className = 'message assistant';
    container.innerHTML = `
        <div class="message-content" style="max-width:90%;width:90%;">
            <div class="agentic-progress">
                <div class="agentic-header">
                    <span class="agentic-icon">&#9881;</span>
                    <span>DAG任务执行中...</span>
                </div>
                <div class="dag-todo-list"></div>
            </div>
        </div>
    `;
    messagesDiv.appendChild(container);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
    return container;
}

function addProgressStep(container, step) {
    // 保留兼容，但不再单独显示 step
}

// 渲染完整的 DAG 任务计划（WBS 树形结构）
function renderDagPlan(container, steps, plannedAt, planVersion) {
    const todoList = container.querySelector('.dag-todo-list');
    if (!todoList || !steps || steps.length === 0) return;

    // 清空现有内容
    todoList.innerHTML = '';
    
    // 添加 WBS 树形容器
    todoList.className = 'dag-todo-list wbs-tree';

    // 添加规划时间头部
    if (plannedAt) {
        const planHeader = document.createElement('div');
        planHeader.className = 'dag-plan-header';
        const versionText = planVersion && planVersion > 1 ? ` (v${planVersion})` : '';
        planHeader.innerHTML = `<span class="dag-plan-time">规划时间: ${plannedAt}${versionText}</span>`;
        todoList.appendChild(planHeader);
    }

    // 按轮次分组（level=1 的是新一轮）
    let currentRound = 0;
    
    // 渲染所有步骤，根据 level 设置缩进
    steps.forEach(step => {
        const level = step.level || 1;
        const indent = (level - 1) * 20; // 每层缩进 20px
        
        // 如果是 level=1，开始新一轮
        if (level === 1) {
            currentRound++;
            const roundHeader = document.createElement('div');
            roundHeader.className = 'wbs-round-header';
            roundHeader.textContent = `轮次 ${currentRound}`;
            todoList.appendChild(roundHeader);
        }
        
        const nodeDiv = document.createElement('div');
        nodeDiv.className = 'dag-todo-item dag-todo-pending wbs-tree-node';
        nodeDiv.id = `dag-todo-${step.index}`;
        nodeDiv.dataset.level = level;  // 设置 data-level 属性
        nodeDiv.style.marginLeft = `${indent}px`;
        
        // 根据层级显示不同的图标
        const levelIcon = level === 1 ? '📋' : level === 2 ? '📄' : '•';
        
        nodeDiv.innerHTML = `
            <span class="dag-todo-index">${step.index}.</span>
            <span class="wbs-icon">${levelIcon}</span>
            <span class="dag-todo-text">${escapeHtml(step.name)}</span>
            <span class="dag-todo-status">&#9203; 等待执行</span>
        `;
        todoList.appendChild(nodeDiv);
    });

    const messagesDiv = document.getElementById('chatMessages');
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// DAG 重规划：替换原卡片内容，保留已完成节点，将旧待执行节点标记为已取消，追加新规划节点
function renderDagReplan(container, newSteps, plannedAt, planVersion, reason) {
    const todoList = container.querySelector('.dag-todo-list');
    if (!todoList || !newSteps || newSteps.length === 0) return;

    // 将所有旧的"等待执行"节点标记为"已取消"
    todoList.querySelectorAll('.dag-todo-pending').forEach(node => {
        node.className = 'dag-todo-item dag-todo-cancelled wbs-tree-node';
        const statusSpan = node.querySelector('.dag-todo-status');
        if (statusSpan) {
            statusSpan.innerHTML = '&#128686; 已取消';
        }
    });

    // 添加重规划分隔线和提示
    const replanDivider = document.createElement('div');
    replanDivider.className = 'dag-replan-divider';
    replanDivider.innerHTML = `
        <div class="dag-replan-label">
            <span class="dag-replan-icon">&#8635;</span>
            <span>${escapeHtml(reason || '任务重新规划')}</span>
        </div>
        ${plannedAt ? `<div class="dag-plan-time">重新规划时间: ${plannedAt} (v${planVersion || ''})</div>` : ''}
    `;
    todoList.appendChild(replanDivider);

    // 追加新规划的步骤（不清空旧的，保留已完成节点的历史）
    let currentRound = 0;
    newSteps.forEach(step => {
        const level = step.level || 1;
        const indent = (level - 1) * 20;
        
        if (level === 1) {
            currentRound++;
            const roundHeader = document.createElement('div');
            roundHeader.className = 'wbs-round-header';
            roundHeader.textContent = `轮次 ${currentRound}`;
            todoList.appendChild(roundHeader);
        }
        
        const nodeDiv = document.createElement('div');
        nodeDiv.className = 'dag-todo-item dag-todo-pending wbs-tree-node';
        nodeDiv.id = `dag-todo-${step.index}`;
        nodeDiv.dataset.level = level;
        nodeDiv.style.marginLeft = `${indent}px`;
        
        const levelIcon = level === 1 ? '📋' : level === 2 ? '📄' : '•';
        
        nodeDiv.innerHTML = `
            <span class="dag-todo-index">${step.index}.</span>
            <span class="wbs-icon">${levelIcon}</span>
            <span class="dag-todo-text">${escapeHtml(step.name)}</span>
            <span class="dag-todo-status">&#9203; 等待执行</span>
        `;
        todoList.appendChild(nodeDiv);
    });

    // 更新头部状态为"重新规划中"
    const header = container.querySelector('.agentic-header span:last-child');
    if (header) {
        header.textContent = `DAG任务执行中 (v${planVersion || ''})...`;
    }

    const messagesDiv = document.getElementById('chatMessages');
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function addDagNodeStart(container, data) {
    const todoList = container.querySelector('.dag-todo-list');
    if (!todoList) return;

    const index = data.index || 1;
    const name = data.name || '';
    const startedAt = data.started_at || '';

    // 检查节点是否已存在（通过 dag_plan 渲染的）
    const existingNode = todoList.querySelector(`#dag-todo-${index}`);
    
    if (existingNode) {
        // 更新已存在的节点状态为执行中，保留 wbs-tree-node 类
        existingNode.className = 'dag-todo-item dag-todo-running wbs-tree-node';
        const statusSpan = existingNode.querySelector('.dag-todo-status');
        if (statusSpan) {
            statusSpan.innerHTML = '&#9203; 执行中';
        }
        // 更新开始时间
        const timeSpan = existingNode.querySelector('.dag-node-time');
        if (timeSpan && startedAt) {
            timeSpan.textContent = `开始: ${startedAt}`;
        }
    } else {
        // 节点不存在，创建新节点（兼容旧逻辑）
        const nodeDiv = document.createElement('div');
        nodeDiv.className = 'dag-todo-item dag-todo-running';
        nodeDiv.id = `dag-todo-${index}`;
        nodeDiv.innerHTML = `
            <span class="dag-todo-index">${index}.</span>
            <span class="dag-todo-text">${escapeHtml(name)}</span>
            <span class="dag-todo-status">&#9203; 执行中</span>
            ${startedAt ? `<span class="dag-node-time">开始: ${startedAt}</span>` : ''}
        `;
        todoList.appendChild(nodeDiv);
    }

    const messagesDiv = document.getElementById('chatMessages');
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function addDagNodeOutput(container, data) {
    // 输出不再 inline 展示，只在 complete 时展示简要结果
}

// 将所有待执行节点标记为"已取消"（DAG终止时调用）
function markPendingNodesCancelled(container) {
    const todoList = container.querySelector('.dag-todo-list');
    if (!todoList) return;

    todoList.querySelectorAll('.dag-todo-pending, .dag-todo-running').forEach(node => {
        // 不处理已经有完成/失败/取消状态的节点
        if (node.classList.contains('dag-todo-done') || 
            node.classList.contains('dag-todo-failed') || 
            node.classList.contains('dag-todo-cancelled')) return;
        
        node.className = 'dag-todo-item dag-todo-cancelled wbs-tree-node';
        const statusSpan = node.querySelector('.dag-todo-status');
        if (statusSpan) {
            statusSpan.innerHTML = '&#128686; 已取消';
        }
    });

    // 更新头部状态
    const header = container.querySelector('.agentic-header span:last-child');
    if (header) {
        header.textContent = 'DAG任务已终止';
    }
}

// 标记DAG任务已完成（正常结束时调用）
function markDagCompleted(container) {
    if (!container) return;
    const header = container.querySelector('.agentic-header span:last-child');
    if (header) {
        header.textContent = 'DAG任务已完成';
    }
    // 将图标从齿轮改为完成
    const icon = container.querySelector('.agentic-icon');
    if (icon) {
        icon.innerHTML = '&#9989;';
    }
}

// 更新DAG卡片状态文本（自审/重规划等中间状态）
function updateDagStatusText(container, message) {
    if (!container) return;
    const header = container.querySelector('.agentic-header span:last-child');
    if (header) {
        header.textContent = message;
    }
}

// 拦截疑似截断JSON的回复内容
function isMalformedJsonContent(content) {
    if (!content || typeof content !== 'string') return false;
    const trimmed = content.trim();
    if (trimmed.length < 5) return false;
    // 以 { 或 [ 开头，且不像正常markdown或自然语言
    if (trimmed[0] === '{' || trimmed[0] === '[') {
        try {
            JSON.parse(trimmed);
            return false; // 合法JSON不拦截
        } catch (e) {
            // 非法JSON，判定为截断/畸形
            return true;
        }
    }
    return false;
}

function addDagNodeComplete(container, data) {
    const index = data.index || 1;
    const nodeDiv = container.querySelector(`#dag-todo-${index}`);
    if (!nodeDiv) return;

    const completedAt = data.completed_at || '';
    const status = data.status || 'completed';

    // 完整的状态映射
    let cssStatus, statusText;
    switch (status) {
        case 'failed':
            cssStatus = 'failed';
            statusText = '&#10060; 失败';
            break;
        case 'cancelled':
        case 'aborted':
            cssStatus = 'cancelled';
            statusText = '&#128686; 已取消';
            break;
        case 'stuck':
            cssStatus = 'stuck';
            statusText = '&#128308; 卡点';
            break;
        case 'completed':
        default:
            cssStatus = 'done';
            statusText = '&#9989; 已完成';
            break;
    }

    // 更新为对应状态样式，保留 wbs-tree-node 类
    nodeDiv.className = `dag-todo-item dag-todo-${cssStatus} wbs-tree-node`;

    const statusSpan = nodeDiv.querySelector('.dag-todo-status');
    if (statusSpan) {
        statusSpan.innerHTML = statusText;
    }

    // 更新完成时间
    const timeSpan = nodeDiv.querySelector('.dag-node-time');
    if (timeSpan && completedAt) {
        timeSpan.textContent = `完成: ${completedAt}`;
    } else if (completedAt) {
        const timeDiv = document.createElement('span');
        timeDiv.className = 'dag-node-time';
        timeDiv.textContent = `完成: ${completedAt}`;
        nodeDiv.appendChild(timeDiv);
    }

    const messagesDiv = document.getElementById('chatMessages');
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function addDagNodeStuck(container, data) {
    const index = data.index || 0;
    const nodeDiv = container.querySelector(`#dag-todo-${index}`);
    if (!nodeDiv) return;

    // 更新为卡点状态
    nodeDiv.className = 'dag-todo-item dag-todo-stuck wbs-tree-node';

    const statusSpan = nodeDiv.querySelector('.dag-todo-status');
    if (statusSpan) {
        statusSpan.innerHTML = '&#128308; 卡点';
    }

    // 添加卡点原因
    const reason = data.reason || '检测到卡点';
    const action = data.action || '';
    const stuckInfo = document.createElement('div');
    stuckInfo.className = 'dag-stuck-info';
    stuckInfo.innerHTML = `<span class="stuck-icon">&#9888;</span> <strong>卡点:</strong> ${reason}` +
        (action ? `<br><span class="stuck-action"><strong>建议:</strong> ${action}</span>` : '');
    nodeDiv.appendChild(stuckInfo);

    const messagesDiv = document.getElementById('chatMessages');
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function addDagAskUser(container, data) {
    const index = data.index || 0;
    const question = data.question || '';
    const context = data.context || '';
    const nodeDiv = container.querySelector(`#dag-todo-${index}`);
    if (!nodeDiv) return;

    // 更新节点状态为等待用户
    nodeDiv.className = 'dag-todo-item dag-todo-ask-user wbs-tree-node';
    const statusSpan = nodeDiv.querySelector('.dag-todo-status');
    if (statusSpan) {
        statusSpan.innerHTML = '&#128172; 等待回复';
    }

    // 在聊天区添加问题卡片
    const messagesDiv = document.getElementById('chatMessages');
    const askDiv = document.createElement('div');
    askDiv.className = 'message assistant';
    askDiv.id = `dag-ask-user-${index}`;

    let contextHtml = context ? `<div class="dag-ask-context">${escapeHtml(context)}</div>` : '';

    askDiv.innerHTML = `
        <div class="message-content" style="max-width:90%;width:90%;padding:0;background:transparent;">
            <div class="dag-ask-user-card">
                <div class="dag-ask-header">
                    <span class="dag-ask-icon">&#128172;</span>
                    <span>需要您的回复</span>
                </div>
                <div class="dag-ask-question">${escapeHtml(question)}</div>
                ${contextHtml}
                <div class="dag-ask-input-area">
                    <input type="text" class="dag-ask-input" id="dagAskInput-${index}"
                           placeholder="输入您的回复..." />
                    <button class="dag-ask-submit" onclick="submitDagAskUser(${index})">发送</button>
                </div>
            </div>
        </div>
    `;
    messagesDiv.appendChild(askDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    // 聚焦输入框
    setTimeout(() => {
        const inputEl = document.getElementById(`dagAskInput-${index}`);
        if (inputEl) {
            inputEl.focus();
            inputEl.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    submitDagAskUser(index);
                }
            });
        }
    }, 100);
}

function submitDagAskUser(index) {
    const inputEl = document.getElementById(`dagAskInput-${index}`);
    if (!inputEl) return;

    const answer = inputEl.value.trim();
    if (!answer) return;

    // 禁用输入区域
    inputEl.disabled = true;
    const submitBtn = inputEl.parentElement.querySelector('.dag-ask-submit');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = '已发送';
    }

    // 发送回复到后端
    fetch('/api/chat/respond', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            answer: answer,
            session_id: sessionId
        })
    }).then(resp => {
        if (!resp.ok) {
            console.error('Failed to submit ask_user response');
        }
    }).catch(err => {
        console.error('Ask user response error:', err);
    });

    // 显示用户回复
    const askDiv = document.getElementById(`dag-ask-user-${index}`);
    if (askDiv) {
        const inputArea = askDiv.querySelector('.dag-ask-input-area');
        if (inputArea) {
            inputArea.innerHTML = `<div class="dag-ask-answered">已回复: ${escapeHtml(answer)}</div>`;
        }
    }
}

// ==================== Utility Functions ====================

function updateLoadingDiv(div, text) {
    div.innerHTML = `<div class="message-content">${text}</div>`;
}

function addMessage(content, role) {
    // 拦截疑似截断JSON的畸形内容，替换为友好提示
    if (role === 'assistant' && isMalformedJsonContent(content)) {
        content = '(模型返回了格式异常的内容，已自动过滤)';
    }
    const messagesDiv = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    // Render markdown-like content for assistant messages
    if (role === 'assistant' && content) {
        contentDiv.innerHTML = renderMarkdown(content);
    } else {
        contentDiv.textContent = content;
    }

    messageDiv.appendChild(contentDiv);
    messagesDiv.appendChild(messageDiv);

    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    return messageDiv;
}

// 添加用户消息（支持文件卡片）
function addUserMessage(content, files = []) {
    const messagesDiv = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message user';

    let html = '';
    
    // 如果有文件，先显示文件卡片
    if (files.length > 0) {
        html += '<div class="file-cards">';
        files.forEach(file => {
            const sizeStr = formatFileSize(file.size);
            html += `
                <div class="file-card">
                    <div class="file-card-icon">${getFileIcon(file.extension)}</div>
                    <div class="file-card-info">
                        <div class="file-card-name">${escapeHtml(file.name)}</div>
                        <div class="file-card-size">${sizeStr}</div>
                    </div>
                </div>
            `;
        });
        html += '</div>';
    }
    
    // 如果有文本内容，显示文本
    if (content) {
        html += `<div class="message-text">${escapeHtml(content)}</div>`;
    }
    
    messageDiv.innerHTML = `<div class="message-content">${html}</div>`;
    messagesDiv.appendChild(messageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
    
    return messageDiv;
}

function renderMarkdown(text) {
    // Comprehensive markdown rendering
    let html = escapeHtml(text);

    // Code blocks (``` ... ```) — protect from other replacements
    const codeBlocks = [];
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (match, lang, code) => {
        const placeholder = `__CODEBLOCK_${codeBlocks.length}__`;
        codeBlocks.push(`<pre class="md-code-block"><code>${code}</code></pre>`);
        return placeholder;
    });

    // Inline code — protect from other replacements
    const inlineCodes = [];
    html = html.replace(/`([^`]+)`/g, (match, code) => {
        const placeholder = `__INLINECODE_${inlineCodes.length}__`;
        inlineCodes.push(`<code class="md-inline-code">${code}</code>`);
        return placeholder;
    });

    // Tables — process before line breaks
    html = html.replace(/(?:^|\n)((?:\|[^\n]+\|\n)+)/g, (match, tableBlock) => {
        const rows = tableBlock.trim().split('\n').filter(r => r.trim());
        if (rows.length < 2) return match;

        // Check if second row is separator (---|---|---)
        const isSeparator = /^\|[\s\-:|]+\|$/.test(rows[1].trim());
        if (!isSeparator) return match;

        let tableHtml = '<table class="md-table">';
        // Header row
        const headerCells = rows[0].split('|').filter((_, i, arr) => i > 0 && i < arr.length - 1);
        tableHtml += '<thead><tr>';
        headerCells.forEach(cell => {
            tableHtml += `<th>${cell.trim()}</th>`;
        });
        tableHtml += '</tr></thead>';

        // Body rows
        tableHtml += '<tbody>';
        for (let i = 2; i < rows.length; i++) {
            const cells = rows[i].split('|').filter((_, i, arr) => i > 0 && i < arr.length - 1);
            tableHtml += '<tr>';
            cells.forEach(cell => {
                tableHtml += `<td>${cell.trim()}</td>`;
            });
            tableHtml += '</tr>';
        }
        tableHtml += '</tbody></table>';
        return '\n' + tableHtml + '\n';
    });

    // Headers
    html = html.replace(/^######\s+(.+)$/gm, '<h6 class="md-h6">$1</h6>');
    html = html.replace(/^#####\s+(.+)$/gm, '<h5 class="md-h5">$1</h5>');
    html = html.replace(/^####\s+(.+)$/gm, '<h4 class="md-h4">$1</h4>');
    html = html.replace(/^###\s+(.+)$/gm, '<h3 class="md-h3">$1</h3>');
    html = html.replace(/^##\s+(.+)$/gm, '<h2 class="md-h2">$1</h2>');
    html = html.replace(/^#\s+(.+)$/gm, '<h1 class="md-h1">$1</h1>');

    // Horizontal rule
    html = html.replace(/^---+$/gm, '<hr class="md-hr">');

    // Blockquote
    html = html.replace(/^&gt;\s+(.+)$/gm, '<blockquote class="md-blockquote">$1</blockquote>');

    // Unordered list items
    html = html.replace(/^[\s]*[-*+]\s+(.+)$/gm, '<li class="md-li">$1</li>');

    // Ordered list items
    html = html.replace(/^[\s]*\d+\.\s+(.+)$/gm, '<li class="md-oli">$1</li>');

    // Wrap consecutive <li class="md-li"> in <ul>
    html = html.replace(/((?:<li class="md-li">.*<\/li>\n?)+)/g, '<ul class="md-ul">$1</ul>');

    // Wrap consecutive <li class="md-oli"> in <ol>
    html = html.replace(/((?:<li class="md-oli">.*<\/li>\n?)+)/g, '<ol class="md-ol">$1</ol>');

    // Strikethrough
    html = html.replace(/~~(.+?)~~/g, '<del>$1</del>');

    // Italic
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" class="md-link">$1</a>');

    // Images
    html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" class="md-img">');

    // Line breaks (preserve double newline as paragraph break)
    html = html.replace(/\n\n/g, '</p><p class="md-p">');
    html = html.replace(/\n/g, '<br>');

    // Restore code blocks and inline codes
    codeBlocks.forEach((block, i) => {
        html = html.replace(`__CODEBLOCK_${i}__`, block);
    });
    inlineCodes.forEach((code, i) => {
        html = html.replace(`__INLINECODE_${i}__`, code);
    });

    return html;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
