"""
Agent Collaboration Platform - Client Example
"""
import requests
import json
import websocket
import threading
import time


class AgentCollabClient:
    """Client for Agent Collaboration Platform"""
    
    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = base_url
        self.token = None
        self.user = None
        self.ws = None
        self.ws_callbacks = {}
    
    def register(self, username: str, password: str, email: str = None):
        """Register a new user"""
        response = requests.post(f"{self.base_url}/api/auth/register", json={
            "username": username,
            "password": password,
            "email": email
        })
        response.raise_for_status()
        data = response.json()
        self.token = data["access_token"]
        self.user = data["user"]
        return data
    
    def login(self, username: str, password: str):
        """Login"""
        response = requests.post(f"{self.base_url}/api/auth/login", data={
            "username": username,
            "password": password
        })
        response.raise_for_status()
        data = response.json()
        self.token = data["access_token"]
        self.user = data["user"]
        return data
    
    def register_agent(self, username: str, password: str, display_name: str, capabilities: list):
        """Register an agent"""
        response = requests.post(f"{self.base_url}/api/auth/register-agent", json={
            "username": username,
            "password": password,
            "display_name": display_name,
            "capabilities": capabilities
        })
        response.raise_for_status()
        data = response.json()
        self.token = data["access_token"]
        self.user = data["user"]
        return data
    
    def _get_headers(self):
        """Get authorization headers"""
        return {"Authorization": f"Bearer {self.token}"}
    
    def create_channel(self, name: str, channel_type: str = "public"):
        """Create a channel"""
        response = requests.post(
            f"{self.base_url}/api/channels/",
            json={"name": name, "channel_type": channel_type},
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()
    
    def list_channels(self):
        """List channels"""
        response = requests.get(
            f"{self.base_url}/api/channels/",
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()
    
    def join_channel(self, channel_id: int):
        """Join a channel"""
        response = requests.post(
            f"{self.base_url}/api/channels/{channel_id}/join",
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()
    
    def send_message(self, channel_id: int, content: str):
        """Send a message"""
        response = requests.post(
            f"{self.base_url}/api/messages/",
            json={"channel_id": channel_id, "content": content},
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()
    
    def get_messages(self, channel_id: int, limit: int = 50):
        """Get messages from a channel"""
        response = requests.get(
            f"{self.base_url}/api/messages/channel/{channel_id}?page_size={limit}",
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()
    
    def create_task(self, title: str, description: str = None, assignee_id: int = None):
        """Create a task"""
        data = {"title": title}
        if description:
            data["description"] = description
        if assignee_id:
            data["assignee_id"] = assignee_id
        
        response = requests.post(
            f"{self.base_url}/api/tasks/",
            json=data,
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()
    
    def list_agents(self):
        """List available agents"""
        response = requests.get(
            f"{self.base_url}/api/users/agents",
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()
    
    def connect_websocket(self, on_message=None):
        """Connect via WebSocket"""
        def on_open(ws):
            print("WebSocket connected")
        
        def on_message(ws, message):
            data = json.loads(message)
            msg_type = data.get("type")
            
            if msg_type == "message":
                print(f"New message: {data['data']['content']}")
            elif msg_type == "agent_task":
                print(f"Agent task received: {data['data']}")
            elif msg_type == "agent_response":
                print(f"Agent response: {data['data']}")
            
            if on_message:
                on_message(data)
        
        def on_error(ws, error):
            print(f"WebSocket error: {error}")
        
        def on_close(ws, close_status_code, close_msg):
            print("WebSocket closed")
        
        ws_url = f"ws://localhost:8000/ws?token={self.token}"
        self.ws = websocket.WebSocketApp(
            ws_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        
        # Run in background thread
        thread = threading.Thread(target=self.ws.run_forever)
        thread.daemon = True
        thread.start()
        
        return self.ws
    
    def send_ws_message(self, message: dict):
        """Send WebSocket message"""
        if self.ws:
            self.ws.send(json.dumps(message))
    
    def send_chat_message(self, channel_id: int, content: str):
        """Send chat message via WebSocket"""
        self.send_ws_message({
            "type": "message",
            "data": {
                "channel_id": channel_id,
                "content": content
            }
        })
    
    def send_agent_task(self, target_agent_id: int, task_type: str, payload: dict):
        """Send agent task via WebSocket"""
        self.send_ws_message({
            "type": "agent_task",
            "data": {
                "target_agent_id": target_agent_id,
                "task_type": task_type,
                "payload": payload
            }
        })
    
    def disconnect_websocket(self):
        """Disconnect WebSocket"""
        if self.ws:
            self.ws.close()


def main():
    """Example usage"""
    client = AgentCollabClient()
    
    # 1. Register a user
    print("=== Registering User ===")
    try:
        user_data = client.register("testuser", "password123", "test@example.com")
        print(f"User registered: {user_data['user']['username']}")
    except requests.exceptions.HTTPError as e:
        if "already registered" in str(e):
            # Login instead
            print("User exists, logging in...")
            user_data = client.login("testuser", "password123")
            print(f"Logged in as: {user_data['user']['username']}")
        else:
            raise
    
    # 2. Register an agent
    print("\n=== Registering Agent ===")
    agent_client = AgentCollabClient()
    try:
        agent_data = agent_client.register_agent(
            "code-reviewer",
            "agent_password",
            "Code Reviewer Agent",
            ["code_review", "security_scan", "best_practices"]
        )
        print(f"Agent registered: {agent_data['user']['username']}")
    except requests.exceptions.HTTPError as e:
        if "already registered" in str(e):
            agent_client.login("code-reviewer", "agent_password")
        else:
            raise
    
    # 3. Create a channel
    print("\n=== Creating Channel ===")
    channel = client.create_channel("general", "public")
    print(f"Channel created: {channel['name']} (ID: {channel['id']})")
    
    # 4. Connect WebSocket
    print("\n=== Connecting WebSocket ===")
    def on_message(data):
        print(f"Received: {data}")
    
    client.connect_websocket(on_message)
    time.sleep(1)  # Wait for connection
    
    # 5. Send a message
    print("\n=== Sending Message ===")
    client.send_chat_message(channel['id'], "Hello from Python client!")
    
    # 6. List agents
    print("\n=== Listing Agents ===")
    agents = client.list_agents()
    print(f"Available agents: {len(agents.get('items', []))}")
    
    # 7. Create a task
    print("\n=== Creating Task ===")
    task = client.create_task(
        "Review code for security vulnerabilities",
        "Please review the authentication module for potential security issues",
        assignee_id=agent_data['user']['id']
    )
    print(f"Task created: {task['title']} (ID: {task['id']})")
    
    # Keep running
    print("\n=== Running (Press Ctrl+C to stop) ===")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        client.disconnect_websocket()


if __name__ == "__main__":
    main()
