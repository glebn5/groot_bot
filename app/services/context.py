import logging
from datetime import date, datetime, timedelta
from typing import Dict, Any, Optional, List
from app.utils.timezone import get_now
from app.config import settings

logger = logging.getLogger(__name__)


class UserContext:
    """
    Manages short-term conversational context and memory for each user:
    - Recent conversation messages (user, assistant, tool)
    - Last referenced entities (task, reminder, calendar_event, note, date)
    - Pending confirmation for destructive actions
    - Expiration / TTL management
    """
    def __init__(self):
        # Memory storage: user_id -> context dictionary
        self._storage: Dict[int, Dict[str, Any]] = {}

    def _get_user_record(self, user_id: int) -> Dict[str, Any]:
        now = get_now()
        record = self._storage.get(user_id)
        if not record:
            record = {
                "user_id": user_id,
                "last_date": None,
                "last_entities": {},  # type -> entity dict
                "messages": [],       # list of {"role": str, "content": str, "timestamp": datetime, ...}
                "pending_confirmation": None,
                "updated_at": now
            }
            self._storage[user_id] = record
        return record

    def set_last_date(self, user_id: int, target_date: date):
        record = self._get_user_record(user_id)
        record["last_date"] = target_date
        record["updated_at"] = get_now()
        logger.info(f"Updated context for user {user_id}: last_date={target_date}")

    def get_last_date(self, user_id: int, max_age_seconds: int = 1800) -> Optional[date]:
        record = self._storage.get(user_id)
        if not record:
            return None
        if get_now() - record["updated_at"] > timedelta(seconds=max_age_seconds):
            logger.info(f"Context for user {user_id} expired.")
            return None
        return record.get("last_date")

    def set_last_entity(self, user_id: int, entity_type: str, entity_data: Dict[str, Any]):
        """
        Stores the last referenced entity (e.g. 'task', 'reminder', 'calendar', 'note').
        """
        record = self._get_user_record(user_id)
        record["last_entities"][entity_type] = {
            **entity_data,
            "saved_at": get_now().isoformat()
        }
        record["updated_at"] = get_now()
        logger.info(f"Stored last_entity '{entity_type}' for user {user_id}: {entity_data.get('id', entity_data)}")

    def get_last_entity(self, user_id: int, entity_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        record = self._storage.get(user_id)
        if not record:
            return None
        if entity_type:
            return record.get("last_entities", {}).get(entity_type)
        # Return most recently updated entity if no type specified
        entities = record.get("last_entities", {})
        if not entities:
            return None
        return next(iter(entities.values()), None)

    def add_message(
        self,
        user_id: int,
        role: str,
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        tool_call_id: Optional[str] = None,
        name: Optional[str] = None
    ):
        """
        Appends a message to conversation history with sliding window limit.
        """
        record = self._get_user_record(user_id)
        now = get_now()
        
        msg_entry = {
            "role": role,
            "content": content or "",
            "timestamp": now
        }
        if tool_calls:
            msg_entry["tool_calls"] = tool_calls
        if tool_call_id:
            msg_entry["tool_call_id"] = tool_call_id
        if name:
            msg_entry["name"] = name

        record["messages"].append(msg_entry)
        record["updated_at"] = now

        # Keep only the last N messages
        max_messages = getattr(settings, "AGENT_HISTORY_LIMIT", 10) * 2  # exchanges
        if len(record["messages"]) > max_messages:
            record["messages"] = record["messages"][-max_messages:]

    def get_messages(self, user_id: int, limit: Optional[int] = None, max_age_seconds: int = 1800) -> List[Dict[str, Any]]:
        record = self._storage.get(user_id)
        if not record:
            return []
        now = get_now()
        if now - record["updated_at"] > timedelta(seconds=max_age_seconds):
            # Expired conversation
            record["messages"] = []
            return []

        msgs = record.get("messages", [])
        if limit and len(msgs) > limit:
            msgs = msgs[-limit:]
        return list(msgs)

    def set_pending_confirmation(self, user_id: int, action_data: Dict[str, Any]):
        """
        Stores an action requiring user confirmation before execution.
        """
        record = self._get_user_record(user_id)
        record["pending_confirmation"] = {
            **action_data,
            "created_at": get_now().isoformat()
        }
        record["updated_at"] = get_now()

    def get_pending_confirmation(self, user_id: int, max_age_seconds: int = 300) -> Optional[Dict[str, Any]]:
        record = self._storage.get(user_id)
        if not record:
            return None
        pending = record.get("pending_confirmation")
        if not pending:
            return None
        if get_now() - record["updated_at"] > timedelta(seconds=max_age_seconds):
            record["pending_confirmation"] = None
            return None
        return pending

    def clear_pending_confirmation(self, user_id: int):
        record = self._storage.get(user_id)
        if record:
            record["pending_confirmation"] = None

    def get_context_summary(self, user_id: int) -> str:
        """
        Generates a concise text summary of recent context for injection into LLM system prompt.
        """
        record = self._storage.get(user_id)
        if not record:
            return ""

        parts = []
        last_d = record.get("last_date")
        if last_d:
            parts.append(f"Последняя обсуждавшаяся дата: {last_d.strftime('%Y-%m-%d')}")

        last_ents = record.get("last_entities", {})
        if last_ents.get("task"):
            t = last_ents["task"]
            parts.append(f"Последняя задача: #{t.get('id')} «{t.get('text', '')}» ({t.get('date', '')})")
        if last_ents.get("reminder"):
            r = last_ents["reminder"]
            parts.append(f"Последнее напоминание: #{r.get('id')} «{r.get('text', r.get('message', ''))}» ({r.get('time', r.get('datetime', ''))})")
        if last_ents.get("calendar"):
            c = last_ents["calendar"]
            parts.append(f"Последнее событие календаря: «{c.get('title', '')}» ({c.get('start_time', '')})")
        if last_ents.get("note"):
            n = last_ents["note"]
            parts.append(f"Последняя заметка: #{n.get('id')} «{n.get('content', '')[:40]}»")

        if parts:
            return "Контекст недавнего диалога:\n- " + "\n- ".join(parts)
        return ""

    def clear_context(self, user_id: int):
        self._storage.pop(user_id, None)


context_service = UserContext()
