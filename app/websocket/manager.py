"""
WebSocket Connection Manager
"""
from fastapi import WebSocket
from typing import Dict, List, Set
from ..models import User
import json


class ConnectionManager:
    """Manages WebSocket connections for real-time communication"""
    
    def __init__(self):
        # user_id -> list of WebSocket connections
        self.active_connections: Dict[int, List[WebSocket]] = {}
        # channel_id -> set of user_ids
        self.channel_members: Dict[int, Set[int]] = {}
        # user_id -> user info
        self.user_info: Dict[int, dict] = {}
        # agent_id -> agent capabilities
        self.agent_capabilities: Dict[int, List[str]] = {}
    
    async def connect(self, websocket: WebSocket, user: User):
        """Accept and store a new WebSocket connection"""
        await websocket.accept()
        
        user_id = user.id
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)
        
        # Store user info
        self.user_info[user_id] = {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "is_agent": user.is_agent,
            "avatar_url": user.avatar_url
        }
        
        # Store agent capabilities
        if user.is_agent and user.agent_capabilities:
            self.agent_capabilities[user_id] = json.loads(user.agent_capabilities)
        
        # Broadcast presence update
        await self.broadcast_presence(user_id, "online")
    
    def disconnect(self, websocket: WebSocket, user_id: int):
        """Remove a WebSocket connection"""
        if user_id in self.active_connections:
            self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
                # Broadcast offline status
                return True  # Last connection closed
        return False
    
    async def broadcast_presence(self, user_id: int, status: str):
        """Broadcast user presence status"""
        message = {
            "type": "presence",
            "data": {
                "user_id": user_id,
                "status": status,
                "user_info": self.user_info.get(user_id, {})
            }
        }
        await self.broadcast_to_all(message)
    
    async def send_to_user(self, user_id: int, message: dict):
        """Send message to a specific user"""
        if user_id in self.active_connections:
            for connection in self.active_connections[user_id]:
                try:
                    await connection.send_json(message)
                except:
                    pass
    
    async def send_to_channel(self, channel_id: int, message: dict, exclude_user: int = None):
        """Send message to all members of a channel"""
        if channel_id in self.channel_members:
            for user_id in self.channel_members[channel_id]:
                if user_id != exclude_user:
                    await self.send_to_user(user_id, message)
    
    async def broadcast_to_all(self, message: dict):
        """Broadcast message to all connected users"""
        for user_id, connections in self.active_connections.items():
            for connection in connections:
                try:
                    await connection.send_json(message)
                except:
                    pass
    
    async def send_agent_task(self, source_agent_id: int, target_agent_id: int, task_data: dict):
        """Send task request to an agent"""
        message = {
            "type": "agent_task",
            "data": {
                "source_agent_id": source_agent_id,
                "task_data": task_data
            }
        }
        await self.send_to_user(target_agent_id, message)
    
    async def send_agent_response(self, source_agent_id: int, target_agent_id: int, response_data: dict):
        """Send task response from an agent"""
        message = {
            "type": "agent_response",
            "data": {
                "source_agent_id": source_agent_id,
                "response_data": response_data
            }
        }
        await self.send_to_user(target_agent_id, message)
    
    def join_channel(self, channel_id: int, user_id: int):
        """Add user to channel members"""
        if channel_id not in self.channel_members:
            self.channel_members[channel_id] = set()
        self.channel_members[channel_id].add(user_id)
    
    def leave_channel(self, channel_id: int, user_id: int):
        """Remove user from channel members"""
        if channel_id in self.channel_members:
            self.channel_members[channel_id].discard(user_id)
    
    def get_online_users(self) -> List[int]:
        """Get list of online user IDs"""
        return list(self.active_connections.keys())
    
    def get_user_info(self, user_id: int) -> dict:
        """Get user info"""
        return self.user_info.get(user_id)
    
    def is_agent_online(self, agent_id: int) -> bool:
        """Check if an agent is online"""
        return agent_id in self.active_connections
    
    def get_online_agents(self) -> List[dict]:
        """Get all online agents with their capabilities"""
        agents = []
        for user_id in self.active_connections:
            if user_id in self.agent_capabilities:
                agents.append({
                    "user_id": user_id,
                    **self.user_info.get(user_id, {}),
                    "capabilities": self.agent_capabilities[user_id]
                })
        return agents


# Global connection manager instance
manager = ConnectionManager()
