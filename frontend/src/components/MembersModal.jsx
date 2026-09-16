import React, { useState, useEffect } from 'react';
import { api } from '../utils/api';

const MembersModal = ({ channel, onClose }) => {
  const [members, setMembers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    if (channel?.id) {
      loadMembers();
    }
  }, [channel?.id]);

  const loadMembers = async () => {
    if (!channel?.id) return;
    try {
      setLoading(true);
      // 此前这里拉的是 /api/channels/{id}，但该接口并不返回 members 字段，
      // 所以成员列表恒为空、下面的在线绿点永不显示（死代码）。
      // 改用真正的成员接口，它现在会返回 agent 的 is_online 状态。
      const data = await api.getChannelMembers(channel.id);
      setMembers(data.members || []);
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
          <h3><i className="fas fa-users"></i> 频道成员</h3>
          <button className="modal-close" onClick={onClose}>
            <i className="fas fa-times"></i>
          </button>
        </div>
        
        <div className="modal-body">
          <p className="invite-desc">频道: {channel?.name}</p>
          
          {loading ? (
            <div className="loading-spinner"><i className="fas fa-spinner fa-spin"></i></div>
          ) : error ? (
            <div className="error-message">{error}</div>
          ) : members.length === 0 ? (
            <div className="empty-state">暂无成员</div>
          ) : (
            <div className="members-list">
              {members.map(member => (
                <div key={member.id} className="member-item">
                  <div className="member-avatar">
                    <div className="avatar-circle">
                      {member.display_name?.[0]?.toUpperCase() || '?'}
                    </div>
                  </div>
                  <div className="member-info">
                    <span className="member-name">{member.display_name || member.username}</span>
                    <span className="member-role">{member.role || 'member'}</span>
                  </div>
                  {member.is_online && <span className="online-indicator"></span>}
                </div>
              ))}
            </div>
          )}
        </div>
        
        <div className="modal-footer">
          <button className="btn btn-primary" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
};

export default MembersModal;
