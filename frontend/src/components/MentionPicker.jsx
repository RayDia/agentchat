import React, { useState, useEffect, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import { api } from '../utils/api';

const MentionPicker = ({ channel, onSelect, onClose, inputRef }) => {
  const { user } = useAuth();
  const [members, setMembers] = useState([]);
  const [filter, setFilter] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const pickerRef = useRef(null);

  useEffect(() => {
    loadMembers();
  }, [channel]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [filter]);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target)) {
        onClose();
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onClose]);

  const loadMembers = async () => {
    try {
      const data = await api.getChannelMembers(channel.id);
      setMembers(data.members || []);
    } catch (err) {
      console.error('Failed to load members:', err);
    }
  };

  const filteredMembers = members.filter(m => 
    m.username.toLowerCase().includes(filter.toLowerCase()) ||
    m.display_name?.toLowerCase().includes(filter.toLowerCase())
  );

  const handleSelect = (member) => {
    onSelect(member);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex(prev => Math.min(prev + 1, filteredMembers.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex(prev => Math.max(prev - 1, 0));
    } else if (e.key === 'Enter' && filteredMembers[selectedIndex]) {
      e.preventDefault();
      handleSelect(filteredMembers[selectedIndex]);
    } else if (e.key === 'Escape') {
      onClose();
    }
  };

  if (filteredMembers.length === 0) {
    return null;
  }

  return (
    <div className="mention-picker" ref={pickerRef} onKeyDown={handleKeyDown}>
      <div className="mention-picker-header">
        <i className="fas fa-at"></i>
        <span>@{filter || '成员'}</span>
      </div>
      <div className="mention-picker-list">
        {filteredMembers.map((member, index) => (
          <div
            key={member.id}
            className={`mention-item ${index === selectedIndex ? 'active' : ''}`}
            onClick={() => handleSelect(member)}
          >
            <div className="mention-avatar">
              <div className="avatar-circle">
                {member.display_name?.[0]?.toUpperCase() || member.username?.[0]?.toUpperCase() || '?'}
              </div>
              {member.is_agent && <span className="mention-agent-badge">AI</span>}
            </div>
            <div className="mention-info">
              <span className="mention-name">{member.display_name || member.username}</span>
              <span className="mention-username">@{member.username}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default MentionPicker;
