"""
Notification Service
处理@提及通知、系统通知等
"""
import re
from sqlalchemy.orm import Session
from ..models import User, Message, Notification, Channel
from ..websocket.manager import manager


class NotificationService:
    def __init__(self, db: Session):
        self.db = db
    
    def extract_mentions(self, content: str) -> list:
        """从消息中提取@提及的用户名
        
        只匹配 @ 英文字母数字下划线，避免误匹配中文词语
        """
        # 匹配 @username 格式 - 只匹配英文、数字和下划线
        pattern = r'@([a-zA-Z0-9_]+)'
        mentions = re.findall(pattern, content)
        # 过滤掉纯数字或过长的匹配
        return list(set([m for m in mentions if not m.isdigit() and len(m) <= 30]))
    
    async def process_mentions(self, message: Message, channel_id: int) -> list:
        """处理@提及，创建通知并推送
        
        Args:
            message: 消息对象
            channel_id: 频道ID
            
        Returns:
            创建的通知列表
        """
        import traceback
        mentions = self.extract_mentions(message.content)
        print(f"DEBUG notification: extract_mentions result: {mentions}")
        print(f"DEBUG notification: message.content: {message.content}")
        notifications = []
        
        for username in mentions:
            try:
                # 查找被提及的用户
                mentioned_user = self.db.query(User).filter(
                    User.username == username,
                    User.is_active == True
                ).first()
                print(f"DEBUG notification: looking for {username}, found: {mentioned_user}")
                
                if not mentioned_user or mentioned_user.id == message.sender_id:
                    print(f"DEBUG notification: skipping {username}")
                    continue  # 跳过不存在的用户或提及自己
                
                # 获取频道信息
                channel = self.db.query(Channel).filter(Channel.id == channel_id).first()
                print(f"DEBUG notification: channel: {channel}")
                
                # 创建通知
                notification = Notification(
                    user_id=mentioned_user.id,
                    type="mention",
                    title=f"在 #{channel.name if channel else 'channel'} 中被提及",
                    content=message.content[:200],
                    link=f"/channels/{channel_id}?message={message.id}"
                )
                self.db.add(notification)
                self.db.flush()
                
                notifications.append(notification)
                
                # 通过WebSocket推送通知
                print(f"DEBUG notification: sending WebSocket to user {mentioned_user.id}")
                await manager.send_to_user(mentioned_user.id, {
                    "type": "notification",
                    "data": {
                        "id": notification.id,
                        "type": "mention",
                        "title": notification.title,
                        "content": notification.content,
                        "link": notification.link,
                        "sender": {
                            "id": message.sender_id,
                            "username": message.sender.username if message.sender else None
                        },
                        "created_at": notification.created_at.isoformat() if notification.created_at else None
                    }
                })
                print("DEBUG notification: WebSocket sent successfully")
            except Exception as e:
                print(f"ERROR processing mention {username}: {e}")
                traceback.print_exc()
        
        self.db.commit()
        return notifications