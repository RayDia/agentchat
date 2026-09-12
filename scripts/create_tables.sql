-- ============================================
-- Agent Collaboration Platform - TDSQL Schema
-- ============================================

-- Create database if not exists
CREATE DATABASE IF NOT EXISTS agent_collab 
CHARACTER SET utf8mb4 
COLLATE utf8mb4_unicode_ci;

USE agent_collab;

-- ============================================
-- Users Table
-- ============================================
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(255) UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    display_name VARCHAR(200),
    avatar_url VARCHAR(500),
    role ENUM('user', 'admin', 'agent') DEFAULT 'user',
    is_active BOOLEAN DEFAULT TRUE,
    is_agent BOOLEAN DEFAULT FALSE,
    agent_capabilities TEXT,
    status_emoji VARCHAR(10),
    status_text VARCHAR(100),
    status_expires_at TIMESTAMP NULL,
    last_seen_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_username (username),
    INDEX idx_email (email),
    INDEX idx_role (role),
    INDEX idx_is_agent (is_agent)
) ENGINE=InnoDB;

-- ============================================
-- Workspaces Table
-- ============================================
CREATE TABLE IF NOT EXISTS workspaces (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    owner_id INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_owner (owner_id)
) ENGINE=InnoDB;

-- ============================================
-- Channels Table
-- ============================================
CREATE TABLE IF NOT EXISTS channels (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    channel_type ENUM('public', 'private', 'dm', 'agent') DEFAULT 'public',
    workspace_id INT,
    created_by INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_workspace (workspace_id),
    INDEX idx_channel_type (channel_type)
) ENGINE=InnoDB;

-- ============================================
-- Channel Members Table
-- ============================================
CREATE TABLE IF NOT EXISTS channel_members (
    channel_id INT,
    user_id INT,
    role VARCHAR(50) DEFAULT 'member',
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_muted BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (channel_id, user_id),
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ============================================
-- Messages Table
-- ============================================
CREATE TABLE IF NOT EXISTS messages (
    id INT AUTO_INCREMENT PRIMARY KEY,
    channel_id INT NOT NULL,
    sender_id INT NOT NULL,
    content TEXT NOT NULL,
    message_type ENUM('text', 'file', 'agent_response', 'system', 'command') DEFAULT 'text',
    reply_to_id INT,
    thread_id INT,
    mentions TEXT,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE,
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (reply_to_id) REFERENCES messages(id) ON DELETE SET NULL,
    INDEX idx_channel (channel_id),
    INDEX idx_sender (sender_id),
    INDEX idx_thread (thread_id),
    INDEX idx_created_at (created_at),
    INDEX idx_channel_created (channel_id, created_at)
) ENGINE=InnoDB;

-- ============================================
-- Tasks Table
-- ============================================
CREATE TABLE IF NOT EXISTS tasks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    channel_id INT,
    creator_id INT NOT NULL,
    assignee_id INT,
    status ENUM('pending', 'in_progress', 'completed', 'failed') DEFAULT 'pending',
    priority INT DEFAULT 0,
    due_date TIMESTAMP NULL,
    result TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE SET NULL,
    FOREIGN KEY (creator_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (assignee_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_creator (creator_id),
    INDEX idx_assignee (assignee_id),
    INDEX idx_status (status),
    INDEX idx_channel (channel_id)
) ENGINE=InnoDB;

-- ============================================
-- Agent Tasks Table (for Agent-to-Agent collaboration)
-- ============================================
CREATE TABLE IF NOT EXISTS agent_tasks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    parent_task_id INT,
    source_agent_id INT NOT NULL,
    target_agent_id INT NOT NULL,
    task_type VARCHAR(100),
    input_data TEXT,
    output_data TEXT,
    status ENUM('pending', 'in_progress', 'completed', 'failed') DEFAULT 'pending',
    error_message TEXT,
    started_at TIMESTAMP NULL,
    completed_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_task_id) REFERENCES tasks(id) ON DELETE SET NULL,
    FOREIGN KEY (source_agent_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (target_agent_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_source_agent (source_agent_id),
    INDEX idx_target_agent (target_agent_id),
    INDEX idx_status (status),
    INDEX idx_task_type (task_type)
) ENGINE=InnoDB;

-- ============================================
-- Webhooks Table
-- ============================================
CREATE TABLE IF NOT EXISTS webhooks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    url VARCHAR(500) NOT NULL,
    secret VARCHAR(255),
    events TEXT,
    workspace_id INT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB;

-- ============================================
-- Activity Logs Table
-- ============================================
CREATE TABLE IF NOT EXISTS notifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    type VARCHAR(50) NOT NULL,
    title VARCHAR(200) NOT NULL,
    content TEXT,
    link VARCHAR(500),
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_user (user_id),
    INDEX idx_type (type),
    INDEX idx_is_read (is_read),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS activity_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(100),
    resource_id INT,
    details TEXT,
    ip_address VARCHAR(45),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_user (user_id),
    INDEX idx_action (action),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB;

-- ============================================
-- ACP CLI Bridge Tables (thread -> session persistence)
-- ============================================
CREATE TABLE IF NOT EXISTS acp_sessions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(36) NOT NULL UNIQUE,
    cli_type VARCHAR(50) NOT NULL,
    thread_id VARCHAR(100),
    channel_id INT,
    process_state VARCHAR(20) DEFAULT 'stopped',
    extra_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_session (session_id),
    INDEX idx_thread (thread_id),
    INDEX idx_channel (channel_id),
    INDEX idx_cli_type (cli_type)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS acp_messages (
    id INT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(36) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    content TEXT NOT NULL,
    extra_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_msg_session (session_id)
) ENGINE=InnoDB;

-- ============================================
-- Create default admin user
-- ============================================
-- Password: admin123 (bcrypt hash)
INSERT INTO users (username, email, hashed_password, display_name, role) 
VALUES ('admin', 'admin@agentcollab.local', '$2b$12$LJ3m4ys3Lz0ZQxQxQxQxQeQxQxQxQxQxQxQxQxQxQxQxQxQxQ', 'System Admin', 'admin')
ON DUPLICATE KEY UPDATE id=id;
