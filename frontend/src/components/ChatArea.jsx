import React, { useState, useEffect, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import { api } from '../utils/api';
import { useWebSocket } from '../context/WebSocketContext';
import MentionPicker from './MentionPicker';

const ChatArea = ({ 
  channel, 
  onLeaveChannel, 
  openInviteModal, 
  openMembersModal,
  onOpenUserMenu 
}) => {
  const { user } = useAuth();
  const { connected, sendMessage, messages, joinChannel, queuedMentions } = useWebSocket();
  const [messagesList, setMessagesList] = useState([]);
  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [oldestId, setOldestId] = useState(null);
  const [showMentionPicker, setShowMentionPicker] = useState(false);
  const [mentionFilter, setMentionFilter] = useState('');
  const [mentionCursor, setMentionCursor] = useState(-1);
  const messagesEndRef = useRef(null);
  const messagesContainerRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (channel?.id) {
      loadMessages();
      if (connected) {
        joinChannel(channel.id);
      }
    }
  }, [channel?.id, connected]);

  useEffect(() => {
    scrollToBottom();
  }, [messagesList, messages]);

  // 滚动加载更多消息
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;

    const handleScroll = () => {
      // 当滚动到顶部附近时加载更多
      if (container.scrollTop < 50 && hasMore && !loadingMore) {
        loadMoreMessages();
      }
    };

    container.addEventListener('scroll', handleScroll);
    return () => container.removeEventListener('scroll', handleScroll);
  }, [hasMore, loadingMore, messagesList.length]);

  // 同步WebSocket消息
  useEffect(() => {
    if (channel?.id && messages[channel.id]) {
      setMessagesList(prev => {
        const wsMessages = messages[channel.id] || [];
        // 合并历史消息和WebSocket消息，去重
        const existingIds = new Set(prev.map(m => m.id));
        const newMessages = wsMessages.filter(m => !existingIds.has(m.id));
        // 移除本地临时消息（通过时间匹配）
        const recentLocalId = prev.find(m => m.is_local)?.id;
        const updatedList = prev.filter(m => !m.is_local);
        // 按时间排序
        return [...updatedList, ...newMessages].sort((a, b) => {
          return new Date(a.created_at) - new Date(b.created_at);
        });
      });
    }
  }, [messages, channel?.id]);

  const loadMessages = async (reset = true) => {
    if (!channel?.id) {
      return;
    }
    try {
      if (reset) {
        setLoading(true);
        setOldestId(null);
        setHasMore(true);
      } else {
        setLoadingMore(true);
      }
      
      const data = await api.getMessages(channel.id, { page_size: 50, ...(oldestId && !reset ? { before: oldestId } : {}) });
      
      // 确保消息按时间升序排列（从旧到新）
      const newMessages = (data.items || []).sort((a, b) => {
        return new Date(a.created_at) - new Date(b.created_at);
      });
      
      if (reset) {
        setMessagesList(newMessages);
        setOldestId(newMessages.length > 0 ? newMessages[0].id : null);
        setHasMore(data.total > 50); // 如果总数大于50，还有更多
      } else {
        setMessagesList(prev => [...newMessages, ...prev]);
        setOldestId(newMessages.length > 0 ? newMessages[0].id : oldestId);
        setHasMore((data.total || 0) > (messagesList.length + newMessages.length));
      }
    } catch (err) {
      console.error('Failed to load messages:', err);
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  };

  const loadMoreMessages = async () => {
    if (!hasMore || loadingMore) return;
    await loadMessages(false);
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const handleInput = (e) => {
    const value = e.target.value;
    setInputValue(value);
    
    // Check for @ mention
    const cursorPos = e.target.selectionStart;
    const textBeforeCursor = value.substring(0, cursorPos);
    const atMatch = textBeforeCursor.match(/@([a-zA-Z0-9_-]*)$/);
    
    if (atMatch) {
      const filter = atMatch[1];
      setMentionFilter(filter);
      setMentionCursor(cursorPos - atMatch[0].length);
      setShowMentionPicker(true);
    } else {
      setShowMentionPicker(false);
      setMentionFilter('');
    }
  };

  const handleKeyDown = (e) => {
    if (showMentionPicker && (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter')) {
      // Let MentionPicker handle keyboard navigation
      return;
    }
  };

  const handleMentionSelect = (member) => {
    const prefix = '@';
    const mentionText = `${prefix}${member.username} `;
    const newValue = inputValue.substring(0, mentionCursor) + mentionText + inputValue.substring(mentionCursor + mentionFilter.length + 1);
    setInputValue(newValue);
    setShowMentionPicker(false);
    setMentionFilter('');
    
    // Focus input and set cursor position
    setTimeout(() => {
      if (inputRef.current) {
        const cursorPos = mentionCursor + mentionText.length;
        inputRef.current.focus();
        inputRef.current.setSelectionRange(cursorPos, cursorPos);
      }
    }, 0);
  };

  const handleSend = async (e) => {
    e.preventDefault();
    if (!inputValue.trim() || !channel?.id) return;

    const localMessage = {
      id: 'local-' + Date.now(),
      channel_id: channel.id,
      content: inputValue.trim(),
      sender: {
        id: user.id,
        username: user.username,
        display_name: user.display_name,
        is_agent: user.is_agent
      },
      created_at: new Date().toISOString(),
      is_local: true
    };

    setMessagesList(prev => [...prev, localMessage]);
    setInputValue('');

    const wsSent = sendMessage(channel.id, inputValue.trim());
    if (!wsSent) {
      try {
        await api.sendMessage(channel.id, inputValue.trim());
      } catch (err) {
        console.error('Failed to send message:', err);
        setMessagesList(prev => prev.filter(m => m.id !== localMessage.id));
      }
    }
  };

  const formatMessageContent = (content) => {
    // Parse @mentions in message content
    if (!content) return null;
    
    const parts = [];
    const regex = /@([a-zA-Z0-9_-]+)/g;
    let lastIndex = 0;
    let match;
    
    while ((match = regex.exec(content)) !== null) {
      // Add text before mention
      if (match.index > lastIndex) {
        parts.push(content.substring(lastIndex, match.index));
      }
      
      // Add mention component
      parts.push(
        <span key={match.index} className="mention-tag">
          @{match[1]}
        </span>
      );
      
      lastIndex = regex.lastIndex;
    }
    
    // Add remaining text
    if (lastIndex < content.length) {
      parts.push(content.substring(lastIndex));
    }
    
    return parts.length > 0 ? parts : content;
  };

  const formatTime = (timestamp) => {
    // 明确指定时间为UTC，转换为本地时区
    const date = new Date(timestamp + 'Z'); // 添加Z表示UTC时间
    const options = { 
      hour: '2-digit', 
      minute: '2-digit',
      hour12: false
    };
    return date.toLocaleTimeString('zh-CN', options);
  };

  if (!channel) {
    return (
      <div className="empty-chat">
        <div className="empty-chat-content">
          <i className="fas fa-hashtag"></i>
          <h2>欢迎使用 AgentCollab</h2>
          <p>请从左侧选择一个频道开始聊天</p>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-area">
      <div className="channel-header">
        <div className="channel-info">
          <span className="channel-hash">#</span>
          <span className="channel-name">{channel.name}</span>
          {channel.channel_type === 'private' && (
            <span className="private-badge">
              <i className="fas fa-lock"></i> 私有
            </span>
          )}
        </div>
        <div className="channel-actions">
          <button 
            className="channel-action-btn" 
            onClick={openMembersModal} 
            title="查看成员"
          >
            <i className="fas fa-users"></i>
            <span>成员</span>
          </button>
          <button 
            className="channel-action-btn" 
            onClick={openInviteModal} 
            title="邀请成员"
          >
            <i className="fas fa-user-plus"></i>
            <span>邀请</span>
          </button>
        </div>
      </div>

      <div className="messages-container" ref={messagesContainerRef}>
        {loading ? (
          <div className="loading-spinner"><i className="fas fa-spinner fa-spin"></i></div>
        ) : messagesList.length === 0 ? (
          <div className="empty-messages">
            <p>暂无消息</p>
            <p>成为第一个发消息的人！</p>
          </div>
        ) : (
          <>
            {loadingMore && (
              <div className="loading-more">
                <i className="fas fa-spinner fa-spin"></i> 加载中...
              </div>
            )}
            {messagesList.map((msg, index) => {
              const isOwnMessage = msg.sender?.id === user?.id || msg.is_local;
              // 被 @ 的 agent 离线时，消息会排队等待其上线处理，
              // 服务端通过 mention_queued 帧告知发送者
              const queued = queuedMentions?.[msg.id];
              return (
                <div
                  key={msg.id || index}
                  className={`message ${isOwnMessage ? 'own-message' : ''} ${msg.sender?.is_agent ? 'agent-message' : ''}`}
                >
                  <div className="message-avatar">
                    <div className="avatar-circle">
                      {msg.sender?.display_name?.[0]?.toUpperCase() || '?'}
                    </div>
                  </div>
                  <div className="message-content">
                    <div className="message-header">
                      <span className="message-author">
                        {msg.sender?.display_name || msg.sender?.username || '未知'}
                      </span>
                      {msg.sender?.is_agent && <span className="agent-tag">AGENT</span>}
                      {queued && isOwnMessage && (
                        <span
                          className="mention-queued-tag"
                          title="被 @ 的 agent 当前离线，消息已排队，待其上线后自动处理"
                        >
                          <i className="fas fa-clock"></i> 待 agent 上线
                        </span>
                      )}
                      <span className="message-time">{formatTime(msg.created_at)}</span>
                    </div>
                    <div className="message-text">{formatMessageContent(msg.content)}</div>
                  </div>
                </div>
              );
            })}
          </>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="message-input-area">
        <form onSubmit={handleSend} className="message-form">
          <div className="input-wrapper">
            <i className="fas fa-at mention-icon"></i>
            <input
              ref={inputRef}
              type="text"
              className="message-input"
              placeholder="输入消息... 使用 @ 提及成员"
              value={inputValue}
              onChange={handleInput}
              onKeyDown={handleKeyDown}
            />
          </div>
          <button type="submit" className="btn-send" disabled={!inputValue.trim()}>
            <i className="fas fa-paper-plane"></i>
          </button>
        </form>
        {showMentionPicker && channel && (
          <MentionPicker
            channel={channel}
            onSelect={handleMentionSelect}
            onClose={() => setShowMentionPicker(false)}
            inputRef={inputRef}
          />
        )}
      </div>
    </div>
  );
};

export default ChatArea;
