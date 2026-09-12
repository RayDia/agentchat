import React, { createContext, useState, useEffect, useContext } from 'react';
import { api } from '../utils/api';

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('token');
    const savedUser = localStorage.getItem('user');
    
    console.log('[Auth] 检查登录状态, token:', !!token, 'savedUser:', !!savedUser);
    
    if (token && savedUser && savedUser !== 'null') {
      try {
        const parsedUser = JSON.parse(savedUser);
        console.log('[Auth] 恢复用户:', parsedUser?.username);
        setUser(parsedUser);
      } catch (e) {
        console.error('[Auth] 解析用户数据失败:', e);
        localStorage.removeItem('token');
        localStorage.removeItem('user');
      }
    }
    setLoading(false);
  }, []);

  const login = async (username, password) => {
    const data = await api.login(username, password);
    localStorage.setItem('token', data.access_token);
    localStorage.setItem('user', JSON.stringify(data.user));
    setUser(data.user);
    return data;
  };

  const register = async (data) => {
    const result = await api.register(data);
    localStorage.setItem('token', result.access_token);
    localStorage.setItem('user', JSON.stringify(result.user));
    setUser(result.user);
    return result;
  };

  const registerAgent = async (data) => {
    const result = await api.registerAgent(data);
    localStorage.setItem('token', result.access_token);
    localStorage.setItem('user', JSON.stringify(result.user));
    setUser(result.user);
    return result;
  };

  const logout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    localStorage.removeItem('currentChannel');
    setUser(null);
    window.location.reload();
  };

  const value = {
    user,
    isAuthenticated: !!user,
    loading,
    login,
    register,
    registerAgent,
    logout
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
};
