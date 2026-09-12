import React, { useState } from 'react';
import { api } from '../utils/api';

const InviteCodeModal = ({ onClose, onJoinSuccess }) => {
  const [code, setCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!code.trim()) return;
    
    try {
      setLoading(true);
      setError('');
      const data = await api.acceptInvite(code.trim());
      onJoinSuccess(data.channel);
      onClose();
    } catch (err) {
      setError(err.message || '邀请码无效或已过期');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3><i className="fas fa-key"></i> 通过邀请码加入</h3>
          <button className="modal-close" onClick={onClose}>
            <i className="fas fa-times"></i>
          </button>
        </div>
        
        <div className="modal-body">
          <p className="invite-desc">输入邀请码加入未加入的频道</p>
          
          {error && <div className="error-message">{error}</div>}
          
          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <input
                type="text"
                className="invite-code-input"
                placeholder="输入邀请码"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                maxLength="8"
              />
            </div>
            
            <div className="invite-info">
              <p><i className="fas fa-info-circle"></i> 任何人持有有效邀请码都可以加入对应频道</p>
              <p><i className="fas fa-link"></i> 邀请码格式：8位 alphanumeric 码</p>
            </div>
          </form>
        </div>
        
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose}>取消</button>
          <button className="btn btn-primary" onClick={handleSubmit} disabled={loading}>
            {loading ? '加入中...' : '加入频道'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default InviteCodeModal;
