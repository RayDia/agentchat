import React, { createContext, useContext, useEffect, useRef, useState } from 'react';
import { useAuth } from './AuthContext';

const WebSocketContext = createContext(null);

export const WebSocketProvider = ({ children }) => {
  const { user } = useAuth();
  const [messages, setMessages] = useState({});
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const reconnectTimeout = useRef(null);

  useEffect(() => {
    if (!user) {
      setConnected(false);
      return;
    }

    const connectWebSocket = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws?token=${localStorage.getItem('token')}`;
      
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('WebSocket connected');
        setConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          handleWsMessage(data);
        } catch (e) {
          console.error('Failed to parse WS message:', e);
        }
      };

      ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        setConnected(false);
      };

      ws.onclose = (event) => {
        setConnected(false);
        wsRef.current = null;

        // 4001 = 服务端认证失败（token 已过期/失效）。
        // 此时继续用同一个 token 重连只会无限失败，改为停止重连等待重新登录。
        if (event.code === 4001) {
          console.warn('[WS] 认证失败（token 可能已过期），停止重连');
          return;
        }

        console.log('WebSocket closed, reconnecting...');
        reconnectTimeout.current = setTimeout(connectWebSocket, 3000);
      };
    };

    connectWebSocket();

    return () => {
      if (reconnectTimeout.current) {
        clearTimeout(reconnectTimeout.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [user?.id]);

  const handleWsMessage = (data) => {
    console.log('[WS] 收到消息:', data);
    switch (data.type) {
      case 'new_message':
        // 旧格式兼容
        setMessages(prev => ({
          ...prev,
          [data.data.channel_id]: [...(prev[data.data.channel_id] || []), data.data.message]
        }));
        break;
      case 'message':
        // 新格式：直接包含消息数据
        if (data.data && data.data.channel_id) {
          console.log('[WS] 处理频道', data.data.channel_id, '的消息:', data.data.id);
          setMessages(prev => {
            const channelMessages = prev[data.data.channel_id] || [];
            // 检查是否已存在
            const exists = channelMessages.some(m => m.id === data.data.id);
            console.log('[WS] 消息已存在:', exists, '当前消息列表长度:', channelMessages.length);
            if (exists) return prev;
            const newMessages = [...channelMessages, data.data];
            console.log('[WS] 新消息列表长度:', newMessages.length);
            return {
              ...prev,
              [data.data.channel_id]: newMessages
            };
          });
        }
        break;
      case 'typing':
        // Handle typing indicator
        break;
      case 'presence':
        // Ignore presence messages
        break;
      default:
        console.log('Unknown WS message type:', data.type);
    }
  };

  const sendMessage = (channelId, content) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'message',
        data: { channel_id: channelId, content }
      }));
      return true;
    }
    return false;
  };

  const joinChannel = (channelId) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'join_channel',
        data: { channel_id: channelId }
      }));
    }
  };

  const value = {
    ws: wsRef.current,
    connected,
    messages,
    sendMessage,
    joinChannel
  };

  return (
    <WebSocketContext.Provider value={value}>
      {children}
    </WebSocketContext.Provider>
  );
};

export const useWebSocket = () => {
  const context = useContext(WebSocketContext);
  if (!context) throw new Error('useWebSocket must be used within WebSocketProvider');
  return context;
};
