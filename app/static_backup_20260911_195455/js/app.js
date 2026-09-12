/**
 * AgentCollab Frontend Application
 */

// ============ Global State ============
const state = {
    token: localStorage.getItem('token'),
    user: JSON.parse(localStorage.getItem('user') || 'null'),
    channels: [],           // 已加入的频道
    allChannels: [],        // 所有可见的频道（含公开频道）
    currentChannel: JSON.parse(localStorage.getItem('currentChannel') || 'null'),
    messages: [],
    agents: [],
    ws: null,
    typingTimeout: null,
    activeTab: 'joined'     // 'joined' | 'discover'
};

// ============ API Client ============
const api = {
    base: '',
    
    async request(method, path, data = null) {
        const headers = {
            'Content-Type': 'application/json'
        };
        
        if (state.token) {
            headers['Authorization'] = `Bearer ${state.token}`;
        }
        
        const config = {
            method,
            headers
        };
        
        if (data) {
            config.body = JSON.stringify(data);
        }
        
        const response = await fetch(`${this.base}${path}`, config);
        
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || 'Request failed');
        }
        
        return response.json();
    },
    
    // Auth
    login: async (username, password) => {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: new URLSearchParams({
                username: username,
                password: password
            })
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || 'Login failed');
        }
        return response.json();
    },
    register: (data) => api.request('POST', '/api/auth/register', data),
    registerAgent: (data) => api.request('POST', '/api/auth/register-agent', data),
    getMe: () => api.request('GET', '/api/auth/me'),
    
    // Channels
    getChannels: () => api.request('GET', '/api/channels/'),
    getDiscoverChannels: () => api.request('GET', '/api/channels/discover'),
    createChannel: (data) => api.request('POST', '/api/channels/', data),
    joinChannel: (id) => api.request('POST', `/api/channels/${id}/join`),
    leaveChannel: (id) => api.request('POST', `/api/channels/${id}/leave`),
    inviteToChannel: (channelId, userId) => api.request('POST', `/api/channels/${channelId}/invite/${userId}`),
    generateInviteLink: (channelId) => api.request('POST', `/api/channels/${channelId}/invite-link`),
    acceptInvite: (code) => api.request('POST', `/api/channels/accept-invite/${code}`),
    
    // Messages
    getMessages: (channelId) => api.request('GET', `/api/messages/channel/${channelId}`),
    sendMessage: (channelId, content) => api.request('POST', '/api/messages/', { channel_id: channelId, content }),
    
    // Users
    getUsers: () => api.request('GET', '/api/users/'),
    getAgents: () => api.request('GET', '/api/users/agents'),
    
    // Tasks
    createTask: (data) => api.request('POST', '/api/tasks/', data),
    getTasks: () => api.request('GET', '/api/tasks/'),
    
    // Search
    searchMessages: (query) => api.request('GET', `/api/search/messages?query=${encodeURIComponent(query)}`)
};

// ============ WebSocket ============
function connectWebSocket() {
    if (state.ws) {
        state.ws.close();
    }
    
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws?token=${state.token}`;
    
    state.ws = new WebSocket(wsUrl);
    
    state.ws.onopen = () => {
        console.log('WebSocket connected');
        // 加入当前频道
        if (state.currentChannel) {
            state.ws.send(JSON.stringify({
                type: 'join_channel',
                data: { channel_id: state.currentChannel.id }
            }));
        }
    };
    
    state.ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleWebSocketMessage(data);
    };
    
    state.ws.onclose = () => {
        console.log('WebSocket disconnected');
        setTimeout(connectWebSocket, 3000);
    };
    
    state.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
    };
}

function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'message':
            handleNewMessage(data.data);
            break;
        case 'typing':
            handleTypingIndicator(data.data);
            break;
        case 'presence':
            handlePresenceUpdate(data.data);
            break;
        case 'agent_task':
            handleAgentTask(data.data);
            break;
        case 'agent_response':
            handleAgentResponse(data.data);
            break;
        case 'notification':
            showNotification(data.data.title || '新通知', 'info');
            break;
    }
}

function handleNewMessage(message) {
    console.log('Received message via WebSocket:', message);
    if (state.currentChannel && message.channel_id === state.currentChannel.id) {
        const isOwnMessage = message.sender_id === state.user.id;
        if (isOwnMessage) {
            return;
        }
        
        // 检查是否已经存在相同ID的消息（避免重复）
        const exists = state.messages.some(m => m.id === message.id);
        if (exists) {
            console.log('Message already exists, skipping:', message.id);
            return;
        }
        
        state.messages.push(message);
        appendMessage(message);
        scrollToBottom();
    }
}

function handleTypingIndicator(data) {
    if (state.currentChannel && data.channel_id === state.currentChannel.id) {
        const indicator = document.getElementById('typing-indicator');
        if (indicator) {
            if (data.is_typing) {
                indicator.textContent = `${data.username} 正在输入...`;
                indicator.style.display = 'block';
            } else {
                indicator.style.display = 'none';
            }
        }
    }
}

function handlePresenceUpdate(data) {
    updateOnlineUsers();
}

function handleAgentTask(data) {
    showNotification(`Agent任务: ${data.task_type}`, 'info');
}

function handleAgentResponse(data) {
    showNotification('收到Agent响应', 'success');
}

// ============ UI Functions ============
function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.textContent = message;
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.remove();
    }, 3000);
}

function showModal(modalId) {
    document.getElementById(modalId).style.display = 'flex';
}

function closeModal(modalId) {
    document.getElementById(modalId).style.display = 'none';
}

function formatTime(dateString) {
    let date;
    if (dateString) {
        if (!dateString.includes('Z') && !dateString.includes('+') && dateString.length > 10) {
            date = new Date(dateString + 'Z');
        } else {
            date = new Date(dateString);
        }
    } else {
        date = new Date();
    }
    
    const now = new Date();
    const diff = now - date;
    
    if (diff < 86400000) {
        return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }
    return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
}

function scrollToBottom() {
    const container = document.getElementById('messages-container');
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

// ============ Auth Functions ============
function initAuth() {
    // Tab切换
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            const tab = btn.dataset.tab;
            document.getElementById('login-form').style.display = tab === 'login' ? 'flex' : 'none';
            document.getElementById('register-form').style.display = tab === 'register' ? 'flex' : 'none';
            document.getElementById('agent-register-form').style.display = tab === 'register-agent' ? 'flex' : 'none';
        });
    });
    
    // 登录
    document.getElementById('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
            const data = await api.login(
                document.getElementById('login-username').value,
                document.getElementById('login-password').value
            );
            handleAuthSuccess(data);
        } catch (error) {
            showNotification(error.message, 'error');
        }
    });
    
    // 注册
    document.getElementById('register-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
            const data = await api.register({
                username: document.getElementById('reg-username').value,
                display_name: document.getElementById('reg-displayname').value,
                password: document.getElementById('reg-password').value
            });
            handleAuthSuccess(data);
        } catch (error) {
            showNotification(error.message, 'error');
        }
    });
    
    // Agent注册
    document.getElementById('agent-register-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
            const capabilities = document.getElementById('agent-capabilities').value
                .split(',')
                .map(s => s.trim())
                .filter(s => s);
            
            const data = await api.registerAgent({
                username: document.getElementById('agent-username').value,
                display_name: document.getElementById('agent-displayname').value,
                password: document.getElementById('agent-password').value,
                capabilities: capabilities
            });
            handleAuthSuccess(data);
        } catch (error) {
            showNotification(error.message, 'error');
        }
    });
}

function handleAuthSuccess(data) {
    console.log('✅ 登录成功，保存token到localStorage');
    state.token = data.access_token;
    state.user = data.user;
    
    localStorage.setItem('token', data.access_token);
    localStorage.setItem('user', JSON.stringify(data.user));
    
    console.log('📝 Token已保存:', state.token ? '成功' : '失败');
    console.log('👤 User已保存:', state.user ? state.user.username : '失败');
    
    document.getElementById('auth-modal').style.display = 'none';
    document.getElementById('app').style.display = 'flex';
    
    initApp();
}

// ============ App Functions ============
function initApp() {
    document.getElementById('current-username').textContent = state.user.display_name || state.user.username;
    
    loadChannels();
    loadAgents();
    connectWebSocket();
    
    // 消息发送
    document.getElementById('message-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        await sendMessage();
    });
    
    // 输入状态
    document.getElementById('message-input').addEventListener('input', (e) => {
        if (state.currentChannel && state.ws) {
            state.ws.send(JSON.stringify({
                type: 'typing',
                data: {
                    channel_id: state.currentChannel.id,
                    is_typing: true
                }
            }));
            
            clearTimeout(state.typingTimeout);
            state.typingTimeout = setTimeout(() => {
                state.ws.send(JSON.stringify({
                    type: 'typing',
                    data: {
                        channel_id: state.currentChannel.id,
                        is_typing: false
                    }
                }));
            }, 2000);
        }
    });
    
    // 创建频道
    document.getElementById('add-channel-btn').addEventListener('click', () => {
        showModal('create-channel-modal');
    });
    
    document.getElementById('create-channel-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
            await api.createChannel({
                name: document.getElementById('new-channel-name').value,
                description: document.getElementById('new-channel-desc').value,
                channel_type: document.getElementById('new-channel-type').value
            });
            closeModal('create-channel-modal');
            await loadChannels();
            showNotification('频道创建成功', 'success');
        } catch (error) {
            showNotification(error.message, 'error');
        }
    });
    
    // 退出登录
    document.getElementById('logout-btn').addEventListener('click', () => {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        localStorage.removeItem('currentChannel');
        if (state.ws) {
            state.ws.close();
        }
        location.reload();
    });
    
    // 刷新Agent列表
    document.getElementById('refresh-agents-btn').addEventListener('click', loadAgents);
    
    // 关闭右侧面板
    document.getElementById('close-panel-btn').addEventListener('click', () => {
        document.getElementById('right-panel').style.display = 'none';
    });
    
    // 频道标签页切换
    document.querySelectorAll('.channel-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.channel-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            state.activeTab = tab.dataset.tab;
            renderChannels();
        });
    });
    
    // 邀请按钮
    document.getElementById('invite-btn').addEventListener('click', () => {
        if (!state.currentChannel) {
            showNotification('请先选择一个频道', 'error');
            return;
        }
        openInviteModal(state.currentChannel.id);
    });
}

// ============ Channel Functions ============
async function loadChannels() {
    try {
        // 加载已加入的频道
        const joinedData = await api.getChannels();
        state.channels = joinedData.items || [];
        
        // 加载可发现的公开频道
        try {
            const discoverData = await api.getDiscoverChannels();
            state.discoverChannels = discoverData.items || [];
        } catch (e) {
            state.discoverChannels = [];
        }
        
        renderChannels();
        
        // 恢复上次选择的频道或自动选择第一个
        if (state.currentChannel) {
            const savedChannel = [...state.channels, ...state.discoverChannels]
                .find(c => c.id === state.currentChannel.id);
            if (savedChannel) {
                await selectChannel(savedChannel.id);
                return;
            }
        }
        
        // 自动选择第一个已加入的频道
        if (state.channels.length > 0) {
            await selectChannel(state.channels[0].id);
        }
    } catch (error) {
        console.error('Failed to load channels:', error);
    }
}

function renderChannels() {
    const list = document.getElementById('channel-list');
    let channelsToShow = [];
    
    // 计算当前频道的成员列表
    const currentChannelId = state.currentChannel?.id
    const currentChannel = state.channels.find(c => c.id === currentChannelId);
    
    // 计算有多少个私有频道需要显示邀请码按钮
    const privateChannelCount = state.channels.filter(c => c.channel_type === 'private').length;
    
    if (state.activeTab === 'joined') {
        channelsToShow = state.channels;
    } else {
        channelsToShow = state.discoverChannels || [];
    }
    
    if (channelsToShow.length === 0) {
        const emptyMsg = state.activeTab === 'joined' 
            ? '还没有加入任何频道' 
            : '没有可用的公开频道';
        list.innerHTML = `<li style="padding: 20px; text-align: center; color: var(--text-secondary);">${emptyMsg}</li>`;
        return;
    }
    
    list.innerHTML = channelsToShow.map(channel => {
        const isCurrent = state.currentChannel?.id === channel.id;
        const isPrivate = channel.channel_type === 'private';
        const iconClass = isPrivate ? 'private-icon' : 'public-icon';
        const title = isPrivate ? '(私有频道，需邀请)' : '';
        
        return `
        <li class="${isCurrent ? 'active' : ''}"
            onclick="selectChannel(${channel.id})"
            title="${title}">
            <span class="channel-icon ${iconClass}">#</span>
            <span>${channel.name}</span>
            ${isPrivate ? '<span class="lock-icon"><i class="fas fa-lock"></i></span>' : ''}
            ${isPrivate ? '' : `<span class="member-count">${channel.member_count || 0} members</span>`}
        </li>
        `;
    }).join('');
}

async function selectChannel(channelId) {
    // 检查是否在已加入的频道中
    let channel = state.channels.find(c => c.id === channelId);
    let isDiscoverChannel = false;
    
    // 如果不在已加入频道中，检查是否在发现频道中
    if (!channel) {
        channel = (state.discoverChannels || []).find(c => c.id === channelId);
        isDiscoverChannel = true;
    }
    
    if (!channel) return;
    
    console.log('Selecting channel:', channelId, channel, 'isDiscover:', isDiscoverChannel);
    
    // 如果是发现频道，先加入
    if (isDiscoverChannel) {
        try {
            await api.joinChannel(channelId);
            showNotification(`已成功加入 #${channel.name}`, 'success');
            // 重新加载频道列表
            await loadChannels();
            // 清空发现列表中的该频道
            if (state.discoverChannels) {
                state.discoverChannels = state.discoverChannels.filter(c => c.id !== channelId);
            }
            // 获取更新后的频道信息
            channel = state.channels.find(c => c.id === channelId);
        } catch (error) {
            showNotification('加入频道失败: ' + error.message, 'error');
            return;
        }
    }
    
    // 离开旧频道
    if (state.currentChannel && state.ws) {
        state.ws.send(JSON.stringify({
            type: 'leave_channel',
            data: { channel_id: state.currentChannel.id }
        }));
    }
    
    state.currentChannel = channel;
    
    // 保存到localStorage
    localStorage.setItem('currentChannel', JSON.stringify(channel));
    
    // 更新UI
    const channelNameEl = document.getElementById('current-channel-name');
    const channelDescEl = document.getElementById('current-channel-desc');
    const inputAreaEl = document.getElementById('message-input-area');
    
    if (channelNameEl) channelNameEl.textContent = `# ${channel.name}`;
    if (channelDescEl) channelDescEl.textContent = channel.description || '';
    if (inputAreaEl) inputAreaEl.style.display = 'block';
    
    renderChannels();
    
    // 加入新频道
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({
            type: 'join_channel',
            data: { channel_id: channelId }
        }));
    }
    
    // 加载消息
    await loadMessages(channelId);
}

async function loadMessages(channelId) {
    try {
        console.log('Loading messages for channel:', channelId);
        const data = await api.getMessages(channelId);
        console.log('Messages loaded:', data);
        state.messages = data.items || [];
        renderMessages();
    } catch (error) {
        console.error('Failed to load messages:', error);
        showNotification('加载消息失败: ' + error.message, 'error');
    }
}

function renderMessages() {
    console.log('Rendering messages:', state.messages.length);
    const container = document.getElementById('messages-container');
    
    if (state.messages.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-comments"></i>
                <p>还没有消息，发送第一条吧！</p>
            </div>
        `;
        return;
    }
    
    // 反转消息数组，以便最新的消息显示在最下面
    const reversedMessages = [...state.messages].reverse();
    const html = reversedMessages.map(msg => createMessageHTML(msg)).join('');
    console.log('Generated HTML length:', html.length);
    container.innerHTML = html;
    scrollToBottom();
}

function createMessageHTML(msg) {
    const sender = msg.sender || { username: 'Unknown' };
    const isAgent = sender.is_agent;
    const initial = (sender.display_name || sender.username || '?')[0].toUpperCase();
    const isPinned = msg.is_pinned;
    const isOwnMessage = msg.sender_id === state.user?.id;
    
    return `
        <div class="message ${isPinned ? 'pinned' : ''}" data-id="${msg.id}" onmouseenter="showMessageActions(this)" onmouseleave="hideMessageActions(this)">
            <div class="message-avatar ${isAgent ? 'agent' : ''}">${initial}</div>
            <div class="message-content">
                <div class="message-header">
                    <span class="message-author ${isAgent ? 'agent' : ''}">${sender.display_name || sender.username}</span>
                    <span class="message-time">${formatTime(msg.created_at)}</span>
                    ${isPinned ? '<span class="pin-badge"><i class="fas fa-thumbtack"></i> 已固定</span>' : ''}
                </div>
                <div class="message-text">${escapeHtml(msg.content)}</div>
                <div class="message-actions" style="display: none;">
                    <button class="icon-btn" onclick="togglePinMessage(${msg.id}, ${isPinned})" title="${isPinned ? '取消固定' : '固定消息'}">
                        <i class="fas fa-thumbtack"></i>
                    </button>
                    ${isOwnMessage ? `<button class="icon-btn" onclick="editMessage(${msg.id})" title="编辑"><i class="fas fa-edit"></i></button>` : ''}
                </div>
            </div>
        </div>
    `;
}

function appendMessage(msg) {
    console.log('Appending message:', msg);
    const container = document.getElementById('messages-container');
    const emptyState = container.querySelector('.empty-state');
    if (emptyState) {
        emptyState.remove();
    }
    const html = createMessageHTML(msg);
    console.log('Message HTML:', html);
    container.insertAdjacentHTML('beforeend', html);
}

// ============ Message Actions ============
function showMessageActions(element) {
    const actions = element.querySelector('.message-actions');
    if (actions) actions.style.display = 'flex';
}

function hideMessageActions(element) {
    const actions = element.querySelector('.message-actions');
    if (actions) actions.style.display = 'none';
}

async function togglePinMessage(messageId, isPinned) {
    try {
        const endpoint = isPinned ? 'unpin' : 'pin';
        await api.request('POST', `/api/messages/${messageId}/${endpoint}`);
        showNotification(isPinned ? '已取消固定' : '已固定', 'success');
        await loadMessages(state.currentChannel.id);
    } catch (error) {
        showNotification('操作失败: ' + error.message, 'error');
    }
}

async function sendMessage() {
    const input = document.getElementById('message-input');
    const content = input.value.trim();
    
    if (!content || !state.currentChannel) return;
    
    const channelId = state.currentChannel.id;
    
    try {
        const localMessage = {
            id: Date.now(),
            channel_id: channelId,
            sender_id: state.user.id,
            sender: {
                id: state.user.id,
                username: state.user.username,
                display_name: state.user.display_name,
                is_agent: state.user.is_agent
            },
            content: content,
            message_type: 'text',
            created_at: new Date().toISOString(),
            is_deleted: false
        };
        state.messages.push(localMessage);
        appendMessage(localMessage);
        scrollToBottom();
        
        input.value = '';
        
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({
                type: 'message',
                data: {
                    channel_id: channelId,
                    content: content
                }
            }));
        } else {
            await api.sendMessage(channelId, content);
        }
    } catch (error) {
        showNotification('发送失败', 'error');
    }
}

// ============ Agent Functions ============
async function loadAgents() {
    try {
        const data = await api.getAgents();
        state.agents = data.items || [];
        renderAgents();
    } catch (error) {
        console.error('Failed to load agents:', error);
    }
}

function renderAgents() {
    const list = document.getElementById('agent-list');
    list.innerHTML = state.agents.map(agent => `
        <li onclick="showAgentPanel(${agent.id})">
            <span class="agent-dot"></span>
            <span>${agent.display_name || agent.username}</span>
        </li>
    `).join('');
}

function showAgentPanel(agentId) {
    const agent = state.agents.find(a => a.id === agentId);
    if (!agent) return;
    
    const panel = document.getElementById('right-panel');
    const title = document.getElementById('panel-title');
    const content = document.getElementById('panel-content');
    
    title.textContent = agent.display_name || agent.username;
    
    let capabilities = [];
    try {
        capabilities = JSON.parse(agent.agent_capabilities || '[]');
    } catch (e) {}
    
    content.innerHTML = `
        <div class="agent-detail">
            <div class="agent-avatar large">
                ${(agent.display_name || agent.username)[0].toUpperCase()}
            </div>
            <h4>${agent.display_name}</h4>
            <p class="agent-username">@${agent.username}</p>
            
            <div class="agent-section">
                <h5>能力</h5>
                <div class="capabilities">
                    ${capabilities.map(cap => `<span class="tag">${cap}</span>`).join('')}
                </div>
            </div>
            
            <div class="agent-section">
                <h5>发送任务</h5>
                <button class="btn btn-primary full-width" onclick="openAgentTaskModal(${agent.id})">
                    <i class="fas fa-paper-plane"></i> 发送任务
                </button>
            </div>
        </div>
    `;
    
    panel.style.display = 'block';
}

function openAgentTaskModal(agentId) {
    document.getElementById('task-target-agent').value = agentId;
    showModal('agent-task-modal');
}

// ============ User Functions ============
async function updateOnlineUsers() {
    try {
        const data = await api.getUsers();
        const users = data.items || [];
        
        const list = document.getElementById('online-users');
        list.innerHTML = users.map(user => `
            <li>
                <span class="online-dot"></span>
                <span>${user.display_name || user.username}</span>
            </li>
        `).join('');
    } catch (error) {
        console.error('Failed to load users:', error);
    }
}

// ============ Invite Functions ============
async function openInviteModal(channelId) {
    showModal('invite-modal');
    await loadInviteLink(channelId);
}

async function loadInviteLink(channelId) {
    try {
        const result = await api.generateInviteLink(channelId);
        const inviteLink = result.full_url || `${window.location.origin}${result.invite_link}`;
        document.getElementById('invite-link-input').value = inviteLink;
        state.currentInviteCode = result.invite_code;
    } catch (error) {
        showNotification('生成邀请链接失败: ' + error.message, 'error');
    }
}

async function copyInviteLink() {
    const input = document.getElementById('invite-link-input');
    const text = input.value;
    
    try {
        await navigator.clipboard.writeText(text);
        showNotification('邀请链接已复制到剪贴板!', 'success');
    } catch (err) {
        // 降级方案
        input.select();
        document.execCommand('copy');
        showNotification('邀请链接已复制!', 'success');
    }
}

async function regenerateInviteLink() {
    if (!state.currentChannel) return;
    
    try {
        await loadInviteLink(state.currentChannel.id);
        showNotification('邀请链接已重新生成!', 'success');
    } catch (error) {
        showNotification('重新生成失败: ' + error.message, 'error');
    }
}

async function generateInviteLink(channelId) {
    try {
        const result = await api.generateInviteLink(channelId);
        const inviteLink = `${window.location.origin}${result.invite_link}`;
        
        // 复制到剪贴板
        navigator.clipboard.writeText(inviteLink).then(() => {
            showNotification('邀请链接已复制到剪贴板!', 'success');
        }).catch(() => {
            // 复制失败，显示链接
            prompt('复制以下邀请链接:', inviteLink);
        });
        
        return result;
    } catch (error) {
        showNotification('生成邀请链接失败: ' + error.message, 'error');
    }
}

async function inviteUserToChannel(channelId, userId) {
    try {
        await api.inviteToChannel(channelId, userId);
        showNotification('邀请发送成功!', 'success');
    } catch (error) {
        showNotification('邀请失败: ' + error.message, 'error');
    }
}

// ============ Utility Functions ============
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}


// ============ Invite Link Handler ============
async function handleInviteOnLoad() {
    // Check if URL has invite code: /invite/{code}
    const path = window.location.pathname;
    const inviteMatch = path.match(/^\/invite\/([a-zA-Z0-9]+)$/);
    
    if (inviteMatch) {
        const inviteCode = inviteMatch[1];
        
        try {
            // 先获取邀请信息
            const inviteInfo = await api.request('GET', `/api/channels/invite/${inviteCode}`);
            
            // 显示邀请信息到登录表单
            const authModal = document.getElementById('auth-modal');
            const loginForm = document.getElementById('login-form');
            const signupForm = document.getElementById('signup-form');
            
            // 移除旧的邀请信息
            const oldInviteInfo = document.getElementById('invite-info-box');
            if (oldInviteInfo) oldInviteInfo.remove();
            
            // 创建邀请信息卡片
            const typeIcon = inviteInfo.channel_type === 'private' ? '🔒' : '🟢';
            const inviteInfoHTML = `
                <div id="invite-info-box" style="
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 20px;
                    border-radius: 12px;
                    margin-bottom: 20px;
                    text-align: center;
                ">
                    <div style="font-size: 40px; margin-bottom: 10px;">${typeIcon}</div>
                    <h3 style="margin: 0 0 10px 0;">邀请加入频道</h3>
                    <p style="margin: 0 0 5px 0; font-size: 18px; font-weight: bold;">#${inviteInfo.channel_name}</p>
                    <p style="margin: 0; opacity: 0.9;">${inviteInfo.description || 'Common workspace for your team'}</p>
                    <p style="margin: 10px 0 0 0; font-size: 14px; opacity: 0.8;">
                        成员数: ${inviteInfo.member_count} | 类型: ${inviteInfo.channel_type}
                    </p>
                    <p style="margin: 10px 0 0 0; font-size: 13px;">
                        登录后将自动加入此频道
                    </p>
                </div>
            `;
            
            // 在登录表单前插入邀请信息
            if (loginForm) {
                loginForm.insertAdjacentHTML('beforebegin', inviteInfoHTML);
            }
            
            // 显示登录框
            authModal.style.display = 'flex';
            
            // 如果已登录，自动加入
            if (state.token && state.user) {
                await joinChannelAutomatically(inviteCode, inviteInfo);
            }
            
        } catch (error) {
            console.error('获取邀请信息失败:', error);
            showNotification('邀请链接无效或已过期', 'error');
        }
    }
}

async function joinChannelAutomatically(inviteCode, inviteInfo) {
    try {
        await api.acceptInvite(inviteCode);
        showNotification('成功加入 #' + inviteInfo.channel_name + '!', 'success');
        
        // 关闭登录框，显示主界面
        document.getElementById('auth-modal').style.display = 'none';
        document.getElementById('app').style.display = 'flex';
        
        // 重新加载频道列表
        await loadChannels();
        
        // 跳转到邀请的频道
        setTimeout(() => {
            selectChannel(inviteInfo.channel_id);
        }, 500);
        
    } catch (error) {
        console.error('加入频道失败:', error);
        showNotification('加入频道失败: ' + error.message, 'error');
    }
}


// ============ Token Persistence ============
async function restoreSession() {
    console.log('🔄 尝试恢复会话...');
    
    // 如果已有token和user，验证是否有效
    if (state.token && state.user) {
        try {
            // 验证token是否有效
            const response = await fetch('/api/auth/me', {
                headers: {
                    'Authorization': `Bearer ${state.token}`
                }
            });
            
            if (response.ok) {
                const user = await response.json();
                console.log('✅ Token有效，用户:', user.username);
                state.user = user;
                localStorage.setItem('user', JSON.stringify(user));
                return true;
            } else {
                console.log('❌ Token已失效，清除本地存储');
                localStorage.removeItem('token');
                localStorage.removeItem('user');
                state.token = null;
                state.user = null;
                return false;
            }
        } catch (error) {
            console.error('验证token失败:', error);
            return false;
        }
    }
    return false;
}

// ============ Initialize ============
document.addEventListener('DOMContentLoaded', async () => {
    console.log('🚀 页面加载完成');
    console.log('📝 Token from localStorage:', state.token ? '存在' : '不存在');
    console.log('👤 User from localStorage:', state.user ? state.user.username : '不存在');
    
    initAuth();
    handleInviteOnLoad();
    
    // 尝试恢复会话
    const restored = await restoreSession();
    
    if (restored) {
        console.log('✅ 会话恢复成功');
        document.getElementById('auth-modal').style.display = 'none';
        document.getElementById('app').style.display = 'flex';
        initApp();
    } else if (state.token && state.user) {
        console.log('✅ 检测到已登录，自动恢复状态');
        document.getElementById('auth-modal').style.display = 'none';
        document.getElementById('app').style.display = 'flex';
        initApp();
    } else {
        console.log('ℹ️ 未检测到登录状态，显示登录框');
        document.getElementById('auth-modal').style.display = 'flex';
        document.getElementById('app').style.display = 'none';
    }
});

// 全局函数
window.selectChannel = selectChannel;
window.showAgentPanel = showAgentPanel;
window.openAgentTaskModal = openAgentTaskModal;
window.closeModal = closeModal;
window.showMessageActions = showMessageActions;
window.hideMessageActions = hideMessageActions;
window.togglePinMessage = togglePinMessage;
// ============ Open Invite by Code Modal ============
async function openInviteByCodeModal() {
    // 显示输入邀请码模态框
    // 预填入当前光位置，或者清空让用户手动输入
    document.getElementById('invite-code-input').value = '';
    showModal('invite-code-modal');
}

// ============ Open Members Modal ============
async function openMembersModal() {
    if (!state.currentChannel) {
        showNotification('请先选择一个频道', 'error');
        return;
    }
    
    try {
        // 获取频道详细信息包括成员
        const channel = state.channels.find(c => c.id === state.currentChannel.id);
        if (channel && channel.member_count > 0) {
            // 显示成员模态框
            showModal('members-modal');
            
            // 加载成员列表
            await loadMembersList();
        } else {
            showNotification('该频道暂无成员', 'info');
        }
    } catch (error) {
        console.error('获取成员列表失败:', error);
        showNotification('获取成员列表失败: ' + error.message, 'error');
    }
}



// ============ Submit Invite Code from Global ============
async function submitInviteCodeFromGlobal() {
    const inviteCode = document.getElementById('invite-code-input').value.trim();
    
    if (!inviteCode) {
        showNotification('请输入邀请码', 'error');
        return;
    }
    
    try {
        const result = await api.request('POST', `/api/channels/accept-invite/${inviteCode}`);
        showNotification('成功加入频道!', 'success');
        
        // 关闭模态框
        closeModal('invite-code-modal');
        
        // 重新加载频道列表
        await loadChannels();
        
        // 选中新加入的频道
        setTimeout(() => {
            const firstChannel = document.querySelector('.channel-item');
            if (firstChannel) {
                firstChannel.click();
            }
        }, 500);
        
    } catch (error) {
        showNotification('加入失败: ' + (error.message || '无效的邀请码'), 'error');
    }
}// 在全局函数中添加导出
window.openInviteModal = openInviteModal;

// ============ Load Members List ============
async function loadMembersList() {
    if (!state.currentChannel) return;
    
    try {
        const channel = state.channels.find(c => c.id === state.currentChannel.id);
        const membersList = document.getElementById('members-list');
        
        if (!channel || !channel.members) {
            membersList.innerHTML = '<p>No members found</p>';
            return;
        }
        
        // 显示成员头像和名字
        const members = channel.members.slice(0, 10); // 显示前10位成员
        let html = '';
        
        members.forEach(member => {
            const initial = (member.display_name || member.username || '?')[0].toUpperCase();
            html += `
                <div class="member-item">
                    <span class="member-avatar">${initial}</span>
                    <span class="member-name">${member.display_name || member.username}</span>
                </div>
            `;
        });
        
        if (channel.members.length > 10) {
            html += `<p><small>Showing 10 of ${channel.members.length} members</small></p>`;
        }
        
        membersList.innerHTML = html || '<p>No members found</p>';
        
    } catch (error) {
        console.error('加载成员列表失败:', error);
        document.getElementById('members-list').innerHTML = '<p>Failed to load members</p>';
    }
}// 在全局函数中添加导出
window.openInviteModal = openInviteModal;
window.openInviteByCodeModal = openInviteByCodeModal;
window.submitInviteCode = submitInviteCode;
window.submitInviteCodeFromGlobal = submitInviteCodeFromGlobal;
window.copyInviteLink = copyInviteLink;
window.regenerateInviteLink = regenerateInviteLink;
window.generateInviteLink = generateInviteLink;
window.inviteUserToChannel = inviteUserToChannel;
