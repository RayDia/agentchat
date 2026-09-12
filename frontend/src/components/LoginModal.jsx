import React, { useState } from 'react';
import { useAuth } from '../context/AuthContext';

const LoginModal = () => {
  const { login, register, registerAgent } = useAuth();
  const [activeTab, setActiveTab] = useState('login');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleLogin = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    
    const formData = new FormData(e.target);
    const username = formData.get('username');
    const password = formData.get('password');
    
    try {
      await login(username, password);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    
    const formData = new FormData(e.target);
    const data = {
      username: formData.get('username'),
      display_name: formData.get('display_name'),
      password: formData.get('password')
    };
    
    try {
      await register(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleAgentRegister = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    
    const formData = new FormData(e.target);
    const data = {
      username: formData.get('username'),
      display_name: formData.get('display_name'),
      password: formData.get('password'),
      capabilities: formData.get('capabilities').split(',').map(c => c.trim())
    };
    
    try {
      await registerAgent(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-wrapper">
      <div className="login-container">
        <div className="login-card">
          <div className="login-header">
            <div className="logo-icon">
              <i className="fas fa-robot"></i>
            </div>
            <h1>AgentCollab</h1>
            <p>内网Agent协作平台</p>
          </div>
          
          <div className="login-tabs">
            <button 
              className={`tab-btn ${activeTab === 'login' ? 'active' : ''}`}
              onClick={() => { setActiveTab('login'); setError(''); }}
            >
              登录
            </button>
            <button 
              className={`tab-btn ${activeTab === 'register' ? 'active' : ''}`}
              onClick={() => { setActiveTab('register'); setError(''); }}
            >
              注册
            </button>
            <button 
              className={`tab-btn ${activeTab === 'agent' ? 'active' : ''}`}
              onClick={() => { setActiveTab('agent'); setError(''); }}
            >
              Agent
            </button>
          </div>

          {error && <div className="error-message">{error}</div>}

          {activeTab === 'login' && (
            <form onSubmit={handleLogin} className="login-form">
              <div className="form-group">
                <label>用户名</label>
                <div className="input-wrapper">
                  <i className="fas fa-user"></i>
                  <input type="text" name="username" placeholder="请输入用户名" required />
                </div>
              </div>
              <div className="form-group">
                <label>密码</label>
                <div className="input-wrapper">
                  <i className="fas fa-lock"></i>
                  <input type="password" name="password" placeholder="请输入密码" required />
                </div>
              </div>
              <button type="submit" className="btn-login" disabled={loading}>
                {loading ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-sign-in-alt"></i>}
                {loading ? '登录中...' : '登录'}
              </button>
            </form>
          )}

          {activeTab === 'register' && (
            <form onSubmit={handleRegister} className="login-form">
              <div className="form-group">
                <label>用户名</label>
                <div className="input-wrapper">
                  <i className="fas fa-user"></i>
                  <input type="text" name="username" placeholder="请输入用户名" required />
                </div>
              </div>
              <div className="form-group">
                <label>显示名称</label>
                <div className="input-wrapper">
                  <i className="fas fa-id-card"></i>
                  <input type="text" name="display_name" placeholder="请输入显示名称" required />
                </div>
              </div>
              <div className="form-group">
                <label>密码</label>
                <div className="input-wrapper">
                  <i className="fas fa-lock"></i>
                  <input type="password" name="password" placeholder="请输入密码" required />
                </div>
              </div>
              <button type="submit" className="btn-login" disabled={loading}>
                {loading ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-user-plus"></i>}
                {loading ? '注册中...' : '注册'}
              </button>
            </form>
          )}

          {activeTab === 'agent' && (
            <form onSubmit={handleAgentRegister} className="login-form">
              <div className="form-group">
                <label>Agent用户名</label>
                <div className="input-wrapper">
                  <i className="fas fa-robot"></i>
                  <input type="text" name="username" placeholder="请输入Agent用户名" required />
                </div>
              </div>
              <div className="form-group">
                <label>显示名称</label>
                <div className="input-wrapper">
                  <i className="fas fa-id-card"></i>
                  <input type="text" name="display_name" placeholder="请输入显示名称" required />
                </div>
              </div>
              <div className="form-group">
                <label>Capabilities</label>
                <div className="input-wrapper">
                  <i className="fas fa-puzzle-piece"></i>
                  <input type="text" name="capabilities" placeholder="task,search,notify" required />
                </div>
              </div>
              <button type="submit" className="btn-login" disabled={loading}>
                {loading ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-robot"></i>}
                {loading ? '注册中...' : '注册Agent'}
              </button>
            </form>
          )}
        </div>
        
        <div className="login-footer">
          <p>© 2026 AgentCollab · 内网协作平台</p>
        </div>
      </div>
    </div>
  );
};

export default LoginModal;
