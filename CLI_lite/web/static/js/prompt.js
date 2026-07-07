// Prompt 编辑器 - 系统提示词管理

// 加载当前提示词
async function loadPrompt() {
    const editor = document.getElementById('promptEditor');
    const status = document.getElementById('promptStatus');
    if (!editor) return;

    try {
        const resp = await fetch('/api/prompt');
        const data = await resp.json();
        if (data.error) {
            status.textContent = '加载失败: ' + data.error;
            status.className = 'prompt-status error';
            return;
        }
        editor.value = data.content || '';
        status.textContent = data.exists ? '已加载' : '文件不存在，已显示空白';
        status.className = 'prompt-status success';
        setTimeout(() => { status.textContent = ''; }, 2000);
    } catch (err) {
        status.textContent = '加载失败: ' + err.message;
        status.className = 'prompt-status error';
    }
}

// 保存提示词
async function savePrompt() {
    const editor = document.getElementById('promptEditor');
    const status = document.getElementById('promptStatus');
    if (!editor) return;

    const content = editor.value;
    if (!content.trim()) {
        status.textContent = '内容不能为空';
        status.className = 'prompt-status error';
        return;
    }

    try {
        status.textContent = '保存中...';
        status.className = 'prompt-status';
        const resp = await fetch('/api/prompt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content })
        });
        const data = await resp.json();
        if (data.error) {
            status.textContent = '保存失败: ' + data.error;
            status.className = 'prompt-status error';
            return;
        }
        status.textContent = '已保存（旧版本已自动备份）';
        status.className = 'prompt-status success';
        setTimeout(() => { status.textContent = ''; }, 3000);
    } catch (err) {
        status.textContent = '保存失败: ' + err.message;
        status.className = 'prompt-status error';
    }
}

// 恢复初始化默认模板
async function resetPrompt() {
    const editor = document.getElementById('promptEditor');
    const status = document.getElementById('promptStatus');
    if (!editor) return;

    if (!confirm('确定要恢复为系统默认模板吗？\n当前版本会自动备份，可从 config/backup 目录找回。')) {
        return;
    }

    try {
        status.textContent = '恢复中...';
        status.className = 'prompt-status';
        const resp = await fetch('/api/prompt/reset', { method: 'POST' });
        const data = await resp.json();
        if (data.error) {
            status.textContent = '恢复失败: ' + data.error;
            status.className = 'prompt-status error';
            return;
        }
        editor.value = data.content || '';
        status.textContent = '已恢复为初始化模板';
        status.className = 'prompt-status success';
        setTimeout(() => { status.textContent = ''; }, 3000);
    } catch (err) {
        status.textContent = '恢复失败: ' + err.message;
        status.className = 'prompt-status error';
    }
}

// 页面加载时自动加载（如果当前在 prompt tab）
document.addEventListener('DOMContentLoaded', () => {
    const url = window.location.pathname;
    if (url === '/prompt') {
        loadPrompt();
    }
});
