import React, { useState, useEffect } from 'react';
import { AuthProvider, useAuth } from './context/AuthContext';
import { WebSocketProvider } from './context/WebSocketContext';
import LoginModal from './components/LoginModal';
import TopBar from './components/TopBar';
import Sidebar from './components/Sidebar';
import ChatArea from './components/ChatArea';
import InviteModal from './components/InviteModal';
import InviteCodeModal from './components/InviteCodeModal';
import MembersModal from './components/MembersModal';
import CreateChannelModal from './components/CreateChannelModal';
import './App.css';

const AppContent = () => {
  const { user, isAuthenticated, loading, logout } = useAuth();
  const [currentChannel, setCurrentChannel] = useState(null);
  const [showInviteModal, setShowInviteModal] = useState(false);
  const [showInviteCodeModal, setShowInviteCodeModal] = useState(false);
  const [showMembersModal, setShowMembersModal] = useState(false);
  const [showCreateChannelModal, setShowCreateChannelModal] = useState(false);

  useEffect(() => {
    const saved = localStorage.getItem('currentChannel');
    console.log('[App] 检查localStorage, currentChannel:', saved);
    if (saved && saved !== 'null') {
      try {
        const channel = JSON.parse(saved);
        setCurrentChannel(channel);
        console.log('[App] 从localStorage恢复频道:', channel?.id, channel?.name);
      } catch (e) {
        console.error('[App] 解析频道数据失败:', e);
        localStorage.removeItem('currentChannel');
      }
    }
  }, []);

  const handleSelectChannel = (channel) => {
    setCurrentChannel(channel);
    localStorage.setItem('currentChannel', JSON.stringify(channel));
  };

  const handleLogout = () => {
    logout(true);
  };

  // 会话校验中：不渲染主界面，避免 token 已过期却先闪出空白的频道列表
  if (loading) {
    return (
      <div className="app-loading">
        <div className="app-loading-spinner" />
        <p>正在恢复会话…</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginModal />;
  }

  return (
    <div className="app">
      <TopBar onLogout={handleLogout} />
      
      <div className="app-body">
        <Sidebar
          currentChannel={currentChannel}
          onSelectChannel={handleSelectChannel}
          openInviteCodeModal={() => setShowInviteCodeModal(true)}
          onCreateChannel={() => setShowCreateChannelModal(true)}
        />
        
        <main className="main-content">
          <ChatArea key={currentChannel?.id || "none"}
            channel={currentChannel}
            onLeaveChannel={() => {
              setCurrentChannel(null);
              localStorage.removeItem('currentChannel');
            }}
            openInviteModal={() => setShowInviteModal(true)}
            openMembersModal={() => setShowMembersModal(true)}
          />
        </main>
      </div>

      {showInviteModal && currentChannel && (
        <InviteModal
          channel={currentChannel}
          onClose={() => setShowInviteModal(false)}
        />
      )}

      {showInviteCodeModal && (
        <InviteCodeModal
          onClose={() => setShowInviteCodeModal(false)}
          onJoinSuccess={handleSelectChannel}
        />
      )}

      {showMembersModal && currentChannel && (
        <MembersModal
          channel={currentChannel}
          onClose={() => setShowMembersModal(false)}
        />
      )}

      {showCreateChannelModal && (
        <CreateChannelModal
          onClose={() => setShowCreateChannelModal(false)}
          onChannelCreated={(channel) => {
            handleSelectChannel(channel);
          }}
        />
      )}
    </div>
  );
};

function App() {
  return (
    <AuthProvider>
      <WebSocketProvider>
        <AppContent />
      </WebSocketProvider>
    </AuthProvider>
  );
}

export default App;
