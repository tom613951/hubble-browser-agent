// State Management
let socket = null;
let isRunning = false;

// DOM Elements
const connectionStatus = document.getElementById('connection-status');
const statusDot = connectionStatus.querySelector('.status-dot');
const statusText = connectionStatus.querySelector('.status-text');

const configForm = document.getElementById('config-form');
const apiProvider = document.getElementById('api-provider');
const apiKey = document.getElementById('api-key');
const apiKeyGroup = document.getElementById('api-key-group');
const modelName = document.getElementById('model-name');
const targetUrl = document.getElementById('target-url');
const maxSteps = document.getElementById('max-steps');
const headlessMode = document.getElementById('headless-mode');
const agentGoal = document.getElementById('agent-goal');

const btnRun = document.getElementById('btn-run');
const btnStop = document.getElementById('btn-stop');
const btnClearLogs = document.getElementById('btn-clear-logs');

const currentStepBadge = document.getElementById('current-step-badge');
const browserAddressText = document.getElementById('browser-address-text');
const liveScreenshot = document.getElementById('live-screenshot');
const screenLoader = document.getElementById('screen-loader');
const loaderText = document.getElementById('loader-text');
const agentThought = document.getElementById('agent-thought');

const terminalLogs = document.getElementById('terminal-logs');
const finalResult = document.getElementById('final-result');

// API Key input is always visible as all remaining providers are online models.

// Clear console logs
btnClearLogs.addEventListener('click', () => {
    terminalLogs.innerHTML = '<div class="log-line system-log">[SYSTEM] 控制台已清空。</div>';
});

// Helper: Add log to terminal
function addLog(message, type = 'system') {
    const line = document.createElement('div');
    line.className = `log-line ${type}-log`;
    
    // Formatting timestamp
    const now = new Date();
    const timeStr = now.toTimeString().split(' ')[0];
    
    line.textContent = `[${timeStr}] ${message}`;
    terminalLogs.appendChild(line);
    
    // Scroll to bottom
    terminalLogs.scrollTop = terminalLogs.scrollHeight;
}

// Helper: Safe Markdown-like renderer for thoughts and results
function renderText(text) {
    if (!text) return '';
    
    // Escape HTML to prevent XSS
    let escaped = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
        
    // Simple bold markdown
    escaped = escaped.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    escaped = escaped.replace(/\*(.*?)\*/g, '<em>$1</em>');
    
    // Simple inline code
    escaped = escaped.replace(/`(.*?)`/g, '<code style="background: rgba(255,255,255,0.08); padding: 2px 6px; border-radius: 4px; font-family: monospace;">$1</code>');
    
    // Simple bullet points
    if (escaped.includes('\n- ')) {
        const lines = escaped.split('\n');
        let inList = false;
        let result = '';
        for (let line of lines) {
            if (line.startsWith('- ')) {
                if (!inList) {
                    result += '<ul style="margin-left: 1.5rem; margin-bottom: 0.5rem;">';
                    inList = true;
                }
                result += `<li>${line.substring(2)}</li>`;
            } else {
                if (inList) {
                    result += '</ul>';
                    inList = false;
                }
                result += line + '<br>';
            }
        }
        if (inList) result += '</ul>';
        escaped = result;
    } else {
        escaped = escaped.replace(/\n/g, '<br>');
    }
    
    return escaped;
}

// Reset UI state to idle
function resetUI() {
    isRunning = false;
    btnRun.disabled = false;
    btnStop.disabled = true;
    screenLoader.style.display = 'none';
    currentStepBadge.textContent = 'IDLE';
    currentStepBadge.className = 'step-badge';
    
    statusDot.parentNode.className = 'status-disconnected';
    statusText.textContent = '未连接';
}

// Connect and run the agent
function runAgent(event) {
    event.preventDefault();
    
    if (isRunning) return;
    
    // Clean old state
    terminalLogs.innerHTML = '';
    addLog('[SYSTEM] 正在建立与 Hubble Backend 的连接...', 'system');
    finalResult.innerHTML = `
        <div class="result-placeholder">
            <div class="spinner" style="width:20px; height:20px; border-width:2px;"></div>
            <span>智能体正在执行任务，结果生成中...</span>
        </div>
    `;
    
    // Determine WebSocket URL
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host || 'localhost:8000';
    const wsUrl = `${protocol}//${host}/ws/run`;
    
    try {
        socket = new WebSocket(wsUrl);
    } catch (err) {
        addLog(`[SYSTEM] 连接失败: ${err.message}`, 'error');
        return;
    }
    
    isRunning = true;
    btnRun.disabled = true;
    btnStop.disabled = false;
    
    statusDot.parentNode.className = 'status-connected';
    statusText.textContent = '已连接';
    
    socket.onopen = () => {
        addLog('[SYSTEM] 连接已建立。正在初始化 Playwright 浏览器并发送任务配置...', 'system');
        
        statusDot.parentNode.className = 'status-running';
        statusText.textContent = '运行中';
        
        // Prepare config payload
        const payload = {
            provider: apiProvider.value,
            api_key: apiKey.value || null,
            model: modelName.value,
            url: targetUrl.value || null,
            goal: agentGoal.value,
            max_steps: parseInt(maxSteps.value),
            headless: headlessMode.checked
        };
        
        socket.send(JSON.stringify(payload));
        screenLoader.style.display = 'flex';
        loaderText.textContent = '正在启动浏览器窗口...';
    };
    
    socket.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        
        if (msg.type === 'log') {
            addLog(msg.message, msg.level);
            
            // Adjust loader text dynamically based on logs
            if (msg.level === 'agent' && msg.message.includes('开始分析')) {
                loaderText.textContent = '智能体思考中...';
            } else if (msg.level === 'action') {
                loaderText.textContent = `正在执行操作: ${msg.message}`;
            }
        } 
        
        else if (msg.type === 'state') {
            // Update browser info
            if (msg.url) {
                browserAddressText.textContent = msg.url;
            }
            if (msg.screenshot) {
                liveScreenshot.src = `data:image/png;base64,${msg.screenshot}`;
            }
            
            // Update steps
            if (msg.step && msg.max_steps) {
                currentStepBadge.textContent = `STEP ${msg.step}/${msg.max_steps}`;
                currentStepBadge.className = 'step-badge text-magenta';
            }
            
            // Update thoughts
            if (msg.thought) {
                agentThought.innerHTML = renderText(msg.thought);
            }
            
            // Toggle screen loader overlay
            if (msg.thinking) {
                screenLoader.style.display = 'flex';
                loaderText.textContent = '智能体正在规划下一步操作...';
            } else {
                screenLoader.style.display = 'none';
            }
        } 
        
        else if (msg.type === 'result') {
            addLog(`[SYSTEM] 任务成功完成！`, 'success');
            finalResult.innerHTML = `<div class="final-text-output">${renderText(msg.result)}</div>`;
            resetUI();
            socket.close();
        } 
        
        else if (msg.type === 'error') {
            addLog(`[ERROR] 智能体遇到严重错误: ${msg.message}`, 'error');
            finalResult.innerHTML = `
                <div class="result-placeholder" style="color: var(--color-red);">
                    <i class="fa-solid fa-triangle-exclamation"></i>
                    <span>任务异常中断：${msg.message}</span>
                </div>
            `;
            resetUI();
            socket.close();
        }
    };
    
    socket.onclose = (event) => {
        if (isRunning) {
            addLog('[SYSTEM] 连接被服务器关闭。', 'system');
            resetUI();
        }
    };
    
    socket.onerror = (err) => {
        addLog('[SYSTEM] WebSocket 发生错误。', 'error');
        resetUI();
    };
}

// Stop execution
function stopAgent() {
    if (!isRunning || !socket) return;
    
    addLog('[SYSTEM] 正在向智能体发送中止请求...', 'system');
    
    // We send a control message or just close connection
    try {
        socket.send(JSON.stringify({ control: 'stop' }));
    } catch(e) {}
    
    socket.close();
    resetUI();
    addLog('[SYSTEM] 任务已强行中止。', 'system');
}

// Attach Event Listeners
configForm.addEventListener('submit', runAgent);
btnStop.addEventListener('click', stopAgent);

// Log basic init status
addLog('[SYSTEM] Nifty-Hubble 前端控制台初始化成功。', 'system');
