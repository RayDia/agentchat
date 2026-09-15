import React, { createContext, useState, useEffect, useContext, useCallback } from 'react';
import { api, UNAUTHORIZED_EVENT } from '../utils/api';

const AuthContext = createContext(null);

function clearAuthStorage() {
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  localStorage.removeItem('currentChannel');
}

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const logout = useCallback((reload = false) => {
    clearAuthStorage();
    setUser(null);
    if (reload) {
      window.location.reload();
    }
  }, []);

  // 刷新页面时校验 token 是否仍然有效。
  // 之前只读 localStorage 就判定"已登录"，token 过期后主界面照常渲染，
  // 但所有接口 401，导致频道/消息全空且无任何提示，只能手动退出重登。
  useEffect(() => {
    let cancelled = false;

    const restoreSession = async () => {
      const token = localStorage.getItem('token');
      const savedUser = localStorage.getItem('user');

      if (!token || !savedUser || savedUser === 'null') {
        clearAuthStorage();
        if (!cancelled) setLoading(false);
        return;
      }

      // 先用本地缓存渲染，避免白屏；再向后端确认 token 是否仍有效
      try {
        setUser(JSON.parse(savedUser));
      } catch (e) {
        console.error('[Auth] 解析用户数据失败:', e);
        clearAuthStorage();
        if (!cancelled) setLoading(false);
        return;
      }

      try {
        const me = await api.getMe();
        if (cancelled) return;
        // 用服务端返回的最新资料覆盖本地缓存（改名/权限变更等能同步）
        setUser(me);
        localStorage.setItem('user', JSON.stringify(me));
      } catch (e) {
        if (cancelled) return;
        // 401 时 api.js 已清理存储并广播事件，这里兜底处理其他失败原因
        console.warn('[Auth] token 校验失败，需要重新登录:', e?.message);
        clearAuthStorage();
        setUser(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    restoreSession();
    return () => { cancelled = true; };
  }, []);

  // 任意接口返回 401（token 过期）时，立即回到登录页
  useEffect(() => {
    const onUnauthorized = () => {
      console.warn('[Auth] 收到未授权通知，已退出登录');
      setUser(null);
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
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
