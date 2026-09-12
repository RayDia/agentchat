import React, { useState, useEffect } from 'react';
import { api } from '../utils/api';

const InviteModal = ({ channel, onClose }) => {
  const [inviteLink, setInviteLink] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    loadInviteLink();
  }, [channel?.id]);

  const loadInviteLink = async () => {
    if (!channel?.id) return;
    try {
      setLoading(true);
      const data = await api.generateInviteLink(channel.id);
      setInviteLink(data.invite_link || '');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const copyInviteLink = async () => {
    try {
      await navigator.clipboard.writeText(inviteLink);
      alert('链接已复制到剪贴板！');
    } catch (err) {
      // Fallback
      const input = document.createElement('input');
      input.value = inviteLink;
      document.body.appendChild(input);
      input.select();
      document.execCommand('copy');
      document.body.removeChild(input);
      alert('链接已复制到剪贴板！');
    }
  };

  const regenerateLink = async () => {
    try {
      setLoading(true);
      const data = await api.generateInviteLink(channel.id);
      setInviteLink(data.invite_link || '');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3><i className="fas fa-link"></i> 邀请成员</h3>
          <button className="modal-close" onClick={onClose}>
            <i className="fas fa-times"></i>
          </button>
        </div>
        
        <div className="modal-body">
          <p className="invite-desc">分享以下链接邀请他人加入频道: {channel?.name}</p>
          
          {error && <div className="error-message">{error}</div>}
          
          <div className="invite-link-box">
            <input
              type="text"
              className="invite-link-input"
              value={inviteLink}
              readOnly
            />
            <button className="btn btn-primary" onClick={copyInviteLink}>
              <i className="fas fa-copy"></i> 复制链接
            </button>
          </div>
          
          <div className="invite-info">
            <p><i className="fas fa-info-circle"></i> 任何人持有此链接都可以加入该频道</p>
            <p><i className="fas fa-clock"></i> 链接有效期：永久</p>
          </div>
        </div>
        
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose}>关闭</button>
          <button className="btn btn-primary" onClick={regenerateLink} disabled={loading}>
            <i className="fas fa-sync"></i> 重新生成
          </button>
        </div>
      </div>
    </div>
  );
};

export default InviteModal;
