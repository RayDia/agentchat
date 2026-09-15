import React, { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { api } from '../utils/api';

const Sidebar = ({ 
  currentChannel, 
  onSelectChannel,
  openInviteCodeModal,
  onCreateChannel
}) => {
  const { user } = useAuth();
  const [channels, setChannels] = useState([]);
  const [discoverChannels, setDiscoverChannels] = useState([]);
  const [activeTab, setActiveTab] = useState('joined');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  useEffect(() => {
    loadChannels();
  }, []);

  const loadChannels = async () => {
    try {
      const [joined, discover] = await Promise.all([
        api.getChannels(),
        api.getDiscoverChannels()
      ]);
      setChannels(joined.items || []);
      setDiscoverChannels(discover.items || []);
      setLoadError(null);
    } catch (err) {
      console.error('Failed to load channels:', err);
      // 401 会由 api.js 触发全局登出，这里只提示其他失败原因，
      // 避免频道列表静默为空、用户不知道发生了什么
      if (err?.status !== 401) {
        setLoadError(err?.message || '加载频道失败');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleJoinChannel = async (channelId) => {
    try {
      await api.joinChannel(channelId);
      await loadChannels();
      onSelectChannel(channels.find(c => c.id === channelId) || discoverChannels.find(c => c.id === channelId));
    } catch (err) {
      alert(`加入频道失败: ${err.message}`);
    }
  };

  const handleLeaveChannel = async (channelId) => {
    if (!confirm('确定要离开这个频道吗？')) return;
    try {
      await api.leaveChannel(channelId);
      setChannels(prev => prev.filter(c => c.id !== channelId));
    } catch (err) {
      alert(`离开频道失败: ${err.message}`);
    }
  };

  const tabs = [
    { id: 'joined', label: '已加入', count: channels.length },
    { id: 'discover', label: '发现', count: discoverChannels.length }
  ];

  const renderChannel = (channel) => (
    <li
      key={channel.id}
      className={`channel-item ${currentChannel?.id === channel.id ? 'active' : ''} ${channel.channel_type === 'private' ? 'private' : ''}`}
      onClick={() => onSelectChannel(channel)}
    >
      <span className="channel-icon">{channel.channel_type === 'private' ? '🔒' : '#'}</span>
      <span className="channel-name">{channel.name}</span>
      {channel.channel_type === 'private' && <span className="private-tag">私有</span>}
      {user?.is_admin && (
        <button 
          className="leave-btn" 
          onClick={(e) => { e.stopPropagation(); handleLeaveChannel(channel.id); }}
          title="离开频道"
        >
          <i className="fas fa-sign-out-alt"></i>
        </button>
      )}
    </li>
  );

  return (
    <div className="sidebar">
      <div className="sidebar-section">
        <div className="section-header">
          <div className="section-header-left">
            <i className="fas fa-hashtag"></i>
            <span>频道</span>
          </div>
          <div className="section-header-actions">
            <button 
              className="icon-btn invite-code-btn" 
              onClick={openInviteCodeModal} 
              title="通过邀请码加入"
            >
              <i className="fas fa-key"></i>
            </button>
            <button 
              className="icon-btn create-channel-btn" 
              onClick={onCreateChannel} 
              title="创建新频道"
            >
              <i className="fas fa-plus"></i>
            </button>
          </div>
        </div>
        
        <div className="tab-nav">
          {tabs.map(tab => (
            <button
              key={tab.id}
              className={`tab-btn ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
              {tab.count > 0 && <span className="badge">{tab.count}</span>}
            </button>
          ))}
        </div>

        {loading ? (
          <div className="loading-spinner"><i className="fas fa-spinner fa-spin"></i></div>
        ) : loadError ? (
          <div className="empty-state sidebar-error">
            <p>{loadError}</p>
            <button className="retry-btn" onClick={loadChannels}>重试</button>
          </div>
        ) : activeTab === 'joined' ? (
          <ul className="channel-list">
            {channels.map(renderChannel)}
          </ul>
        ) : (
          <ul className="channel-list">
            {discoverChannels.length === 0 ? (
              <li className="empty-state">暂无可加入的公开频道</li>
            ) : (
              discoverChannels.map(channel => (
                <li key={channel.id} className="channel-item discover">
                  <span className="channel-icon">#</span>
                  <span className="channel-name">{channel.name}</span>
                  <button 
                    className="join-btn"
                    onClick={() => handleJoinChannel(channel.id)}
                  >
                    <i className="fas fa-sign-in-alt"></i> 加入
                  </button>
                </li>
              ))
            )}
          </ul>
        )}
      </div>
    </div>
  );
};

export default Sidebar;
