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
    is_schedule_query: bool = Field(default=False, description="Whether user is asking to view plans/schedule/reminders for a specific date or date range")
    query_date: Optional[date] = Field(None, description="Target start date if user is asking for plans/schedule (YYYY-MM-DD)")
    query_end_date: Optional[date] = Field(None, description="Target end date if user is asking for plans/schedule for a range/month (YYYY-MM-DD)")
    is_note_save: bool = Field(default=False, description="Whether user wants to remember or save a quick note")
    note_content: Optional[str] = Field(None, description="Extracted note text to be saved")
    is_note_query: bool = Field(default=False, description="Whether user is asking to view their saved notes")
    is_task_add: bool = Field(default=False, description="Whether user wants to add a daily task for a specific date")
    task_text: Optional[str] = Field(None, description="Task text to add")
    task_date: Optional[date] = Field(None, description="Target date for the task (YYYY-MM-DD)")
    is_task_move: bool = Field(default=False, description="Whether user wants to reschedule/move a task to another date")
    move_task_query: Optional[str] = Field(None, description="Keywords of the task to be moved")
    move_from_date: Optional[date] = Field(None, description="Original date of the task if specified (YYYY-MM-DD)")
    move_to_date: Optional[date] = Field(None, description="New target date to move the task to (YYYY-MM-DD)")
    is_task_clear: bool = Field(default=False, description="Whether user wants to delete/clear all tasks (or tasks for a specific date)")
    clear_date: Optional[date] = Field(None, description="Specific date to clear tasks for, or null for all tasks")
    is_task_delete_single: bool = Field(default=False, description="Whether user wants to delete a specific single task")
    delete_task_query: Optional[str] = Field(None, description="Keywords of single task to delete")
    title: str = Field(..., description="Short title summary of the action/user request")
    description: Optional[str] = Field(None, description="Detailed description or context if available")
    event_start: Optional[datetime] = Field(None, description="Start date and time for Google Calendar event")
    event_end: Optional[datetime] = Field(None, description="End date and time for Google Calendar event")
    reminders: List[ReminderItem] = Field(default_factory=list, description="List of scheduled chat reminders")
    obsidian_entry: Optional[ObsidianEntry] = Field(None, description="Obsidian daily note task entry if applicable")
    confirmation_text: str = Field(..., description="Friendly user message confirming created events/tasks/reminders")
