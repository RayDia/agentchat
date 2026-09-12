import React, { useState } from 'react';
import { api } from '../utils/api';

const CreateChannelModal = ({ onClose, onChannelCreated }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    channel_type: 'public'
  });

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const data = {
        name: formData.name.trim(),
        description: formData.description.trim(),
        channel_type: formData.channel_type
      };
      
      const result = await api.createChannel(data);
      onChannelCreated(result);
      onClose();
    } catch (err) {
      setError(err.message || '创建频道失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3><i className="fas fa-hashtag"></i> 创建新频道</h3>
          <button className="modal-close" onClick={onClose}>
            <i className="fas fa-times"></i>
          </button>
        </div>
        
        <form onSubmit={handleSubmit} className="modal-body">
          {error && <div className="error-message">{error}</div>}
          
          <div className="form-group">
            <label>频道名称 *</label>
            <input
              type="text"
              name="name"
              value={formData.name}
              onChange={handleChange}
              placeholder="请输入频道名称（如：general、tech）"
              required
              minLength={2}
              maxLength={100}
            />
          </div>
          
          <div className="form-group">
            <label>描述</label>
            <textarea
              name="description"
              value={formData.description}
              onChange={handleChange}
              placeholder="请输入频道描述（可选）"
              rows={3}
              maxLength={500}
            ></textarea>
          </div>
          
          <div className="form-group">
            <label>频道类型</label>
            <select name="channel_type" value={formData.channel_type} onChange={handleChange}>
              <option value="public">公开</option>
              <option value="private">私有</option>
            </select>
            <small className="form-help">
              公开频道可在发现页看到，私有频道仅邀请成员可加入
            </small>
          </div>
          
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-plus"></i>}
              {loading ? '创建中...' : '创建频道'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default CreateChannelModal;
