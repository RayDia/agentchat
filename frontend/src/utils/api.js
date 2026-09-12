// API Client for AgentCollab
const API_BASE = '';

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
      throw new Error(error.detail || 'Request failed');
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
