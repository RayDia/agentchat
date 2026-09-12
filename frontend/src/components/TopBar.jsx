import React, { useState, useEffect, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import { api } from '../utils/api';

const TopBar = ({ onLogout }) => {
  const { user, logout } = useAuth();
  const [showUserMenu, setShowUserMenu] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setShowUserMenu(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleLogout = () => {
    setShowUserMenu(false);
    logout();
    if (onLogout) onLogout();
  };

  return (
    <div className="topbar">
      <div className="topbar-left">
        <div className="logo">
          <i className="fas fa-robot"></i>
          <span>AgentCollab</span>
        </div>
      </div>
      
      <div className="topbar-right">
        <div 
          className="topbar-user"
          ref={menuRef}
          onClick={() => setShowUserMenu(!showUserMenu)}
        >
          <div className="topbar-user-avatar">
            {user?.display_name?.[0]?.toUpperCase() || '?'}
          </div>
          <span className="topbar-username">
            {user?.display_name || user?.username}
          </span>
          <i className={`fas fa-chevron-down ${showUserMenu ? 'open' : ''}`}></i>
          
          {showUserMenu && (
            <div className="user-dropdown-menu">
              <div className="user-dropdown-header">
                <div className="user-dropdown-avatar">
                  {user?.display_name?.[0]?.toUpperCase() || '?'}
                </div>
                <div className="user-dropdown-info">
                  <div className="user-dropdown-name">
                    {user?.display_name || user?.username}
                  </div>
                  <div className="user-dropdown-email">
                    {user?.email || '无邮箱'}
                  </div>
                  {user?.is_agent && <span className="agent-badge">AGENT</span>}
                </div>
              </div>
              <div className="user-dropdown-divider"></div>
              <button 
                className="user-dropdown-item" 
                onClick={handleLogout}
              >
                <i className="fas fa-sign-out-alt"></i>
                <span>退出登录</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default TopBar;
