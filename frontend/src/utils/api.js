// API Client for AgentCollab
const API_BASE = '';

// 未授权时广播的事件名：AuthContext 监听它来清理登录态并回到登录页
export const UNAUTHORIZED_EVENT = 'agentchat:unauthorized';

// 已认证接口收到 401 时，清理本地登录态并通知全局
function handleUnauthorized() {
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  localStorage.removeItem('currentChannel');
  window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
}

class ApiClient {
  constructor() {
    this.base = API_BASE;
  }

  async request(method, path, data = null) {
    const headers = {
      'Content-Type': 'application/json'
    };
    
    const token = localStorage.getItem('token');
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
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
      // token 失效/过期：清登录态并通知应用层回到登录页，避免页面静默空白
      if (response.status === 401 && token) {
        handleUnauthorized();
      }
      const err = new Error(error.detail || 'Request failed');
      err.status = response.status;
      throw err;
    }
    
    // 204 等无响应体的请求
    if (response.status === 204) {
      return null;
    }
    return response.json();
  }

  // Auth
  login = async (username, password) => {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ username, password })
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || 'Login failed');
    }
    return response.json();
  };

  register = (data) => this.request('POST', '/api/auth/register', data);
  registerAgent = (data) => this.request('POST', '/api/auth/register-agent', data);
  getMe = () => this.request('GET', '/api/auth/me');

  // Channels
  getChannels = () => this.request('GET', '/api/channels/');
  getDiscoverChannels = () => this.request('GET', '/api/channels/discover');
  createChannel = (data) => this.request('POST', '/api/channels/', data);
  joinChannel = (id) => this.request('POST', `/api/channels/${id}/join`);
  leaveChannel = (id) => this.request('POST', `/api/channels/${id}/leave`);
  inviteToChannel = (channelId, userId) => this.request('POST', `/api/channels/${channelId}/invite/${userId}`);
  generateInviteLink = (channelId) => this.request('POST', `/api/channels/${channelId}/invite-link`);
  acceptInvite = (code) => this.request('POST', `/api/channels/accept-invite/${code}`);

  // Messages
  getMessages = (channelId, params = {}) => {
    const queryParams = new URLSearchParams(params).toString();
    return this.request('GET', `/api/messages/channel/${channelId}${queryParams ? '?' + queryParams : ''}`);
  };
  sendMessage = (channelId, content) => this.request('POST', '/api/messages/', { channel_id: channelId, content });

  // Users & Agents
  getUsers = () => this.request('GET', '/api/users/');
  getAgents = () => this.request('GET', '/api/users/agents');
  getChannelMembers = (channelId) => this.request('GET', `/api/users/channel/${channelId}/members`);

  // Tasks
  createTask = (data) => this.request('POST', '/api/tasks/', data);
  getTasks = () => this.request('GET', '/api/tasks/');

  // Search
  searchMessages = (query) => this.request('GET', `/api/search/messages?query=${encodeURIComponent(query)}`);
}

export const api = new ApiClient();
