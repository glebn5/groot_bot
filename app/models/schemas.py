from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class ReminderItem(BaseModel):
    trigger_at: datetime = Field(..., description="Timestamp when reminder notification should fire")
    message: str = Field(..., description="Reminder notification message text")


class ObsidianEntry(BaseModel):
    entry_date: date = Field(default_factory=date.today, alias="date", description="Target daily note date (YYYY-MM-DD)")
    target_section: str = Field(default="## Задачи на сегодня", description="Target section header in Markdown note")
    task_text: str = Field(..., description="Task string to append into the section (e.g. '15:30 Встреча с клиентом')")

    class Config:
        populate_by_name = True


class ParsedAction(BaseModel):
    is_actionable: bool = Field(..., description="Whether user request contains tasks, calendar events, or reminders")
    title: str = Field(..., description="Short title summary of the action/user request")
    description: Optional[str] = Field(None, description="Detailed description or context if available")
    event_start: Optional[datetime] = Field(None, description="Start date and time for Google Calendar event")
    event_end: Optional[datetime] = Field(None, description="End date and time for Google Calendar event")
    reminders: List[ReminderItem] = Field(default_factory=list, description="List of scheduled chat reminders")
    obsidian_entry: Optional[ObsidianEntry] = Field(None, description="Obsidian daily note task entry if applicable")
    confirmation_text: str = Field(..., description="Friendly user message confirming created events/tasks/reminders")
