// 青稞 - 配置管理页面脚本

// 页面加载时获取配置
document.addEventListener('DOMContentLoaded', function() {
    loadConfig();
    loadSystemInfo();
});

// 加载配置
async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        if (response.ok) {
            const config = await response.json();
            applyConfig(config);
        }
    } catch (error) {
        console.error('Failed to load config:', error);
    }
}

// 应用配置到表单
function applyConfig(config) {
    // 辅助函数：安全设置元素值
    function setVal(id, value) {
        const el = document.getElementById(id);
        if (el) el.value = value;
    }

    // LLM 配置
    if (config.llm) {
        setVal('llmProvider', config.llm.provider || 'dify');

        if (config.llm.dify) {
            const difyCfg = config.llm.dify;
            setVal('difyApiUrl', difyCfg.api_url || '');
            setVal('difyApiKey', difyCfg.api_key || '');
        }

        if (config.llm.openai) {
            const openaiCfg = config.llm.openai;
            setVal('openaiApiUrl', openaiCfg.api_url || '');
            setVal('openaiApiKey', openaiCfg.api_key || '');
            setVal('openaiModel', openaiCfg.model || '');
        }

        // 超时时间（dify 或 openai 共用）
        const timeout = (config.llm.openai && config.llm.openai.timeout)
            || (config.llm.dify && config.llm.dify.timeout) || 60;
        setVal('llmTimeout', timeout);
    }

    // 上下文配置
    if (config.context) {
        setVal('historyRounds', config.context.history_rounds || 3);
        setVal('maxSnippetLength', config.context.max_snippet_length || 2000);
        setVal('maxTokens', config.context.max_tokens || 8000);
    }

    // 偏好学习配置（后端键名是 preference）
    if (config.preference) {
        setVal('learningInterval', config.preference.learning_interval || 10);
        setVal('confidenceThreshold', config.preference.confidence_threshold || 0.7);
    }

    // CLI 配置
    if (config.cli) {
        setVal('shellType', config.cli.shell || 'powershell');
        setVal('cliTimeout', config.cli.timeout || 30);
    }

    // 服务配置（flask.port）
    if (config.flask) {
        setVal('serverPort', config.flask.port || 2253);
    }

    // 调度中心配置（后端键名是 dispatcher）
    if (config.dispatcher) {
        setVal('pollInterval', config.dispatcher.poll_interval || 1.0);
        setVal('maxConcurrent', config.dispatcher.max_concurrent || 5);
        setVal('maxRetries', config.dispatcher.max_retries || 2);
    }
}

// 保存配置
async function saveConfig() {
    const provider = document.getElementById('llmProvider').value || 'dify';
    const timeout = parseInt(document.getElementById('llmTimeout').value) || 60;

    const config = {
        llm: {
            provider: provider,
            dify: {
                api_url: document.getElementById('difyApiUrl').value,
                api_key: document.getElementById('difyApiKey').value,
                timeout: timeout
            },
            openai: {
                api_url: document.getElementById('openaiApiUrl').value,
                api_key: document.getElementById('openaiApiKey').value,
                model: document.getElementById('openaiModel').value,
                timeout: timeout
            }
        },
        flask: {
            port: parseInt(document.getElementById('serverPort').value) || 2253
        },
        context: {
            history_rounds: parseInt(document.getElementById('historyRounds').value) || 3,
            max_snippet_length: parseInt(document.getElementById('maxSnippetLength').value) || 2000,
            max_tokens: parseInt(document.getElementById('maxTokens').value) || 8000
        },
        preference: {
            learning_interval: parseInt(document.getElementById('learningInterval').value) || 10,
            confidence_threshold: parseFloat(document.getElementById('confidenceThreshold').value) || 0.7
        },
        cli: {
            shell: document.getElementById('shellType').value || 'powershell',
            timeout: parseInt(document.getElementById('cliTimeout').value) || 30
        },
        dispatcher: {
            poll_interval: parseFloat(document.getElementById('pollInterval').value) || 1.0,
            max_concurrent: parseInt(document.getElementById('maxConcurrent').value) || 5,
            max_retries: parseInt(document.getElementById('maxRetries').value) || 2
        }
    };

    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        if (response.ok) {
            showNotification('配置已保存', 'success');
        } else {
            const error = await response.json();
            showNotification('保存失败: ' + (error.message || '未知错误'), 'error');
        }
    } catch (error) {
        showNotification('保存失败: ' + error.message, 'error');
    }
}

// 重置为默认配置
async function resetConfig() {
    if (!confirm('确定要恢复默认配置吗？')) return;

    try {
        const response = await fetch('/api/config/reset', { method: 'POST' });
        if (response.ok) {
            const config = await response.json();
            applyConfig(config);
            showNotification('已恢复默认配置', 'success');
        }
    } catch (error) {
        showNotification('重置失败: ' + error.message, 'error');
    }
}

// 加载系统信息
async function loadSystemInfo() {
    try {
        const response = await fetch('/api/config/system');
        if (response.ok) {
            const info = await response.json();
            document.getElementById('appVersion').textContent = info.appVersion || '1.0.0';
            document.getElementById('pythonVersion').textContent = info.pythonVersion || '-';
            document.getElementById('flaskVersion').textContent = info.flaskVersion || '-';
        }
    } catch (error) {
        console.error('Failed to load system info:', error);
    }
}

// 显示通知
function showNotification(message, type) {
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    document.body.appendChild(notification);

    setTimeout(() => {
        notification.remove();
    }, 3000);
}
