import os
import socket
import logging
from datetime import datetime, timedelta
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from app.config import settings
from app.utils.timezone import get_tz

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']

# Protect against hanging sockets or broken pipe drops
socket.setdefaulttimeout(15.0)


class CalendarService:
    def __init__(self):
        self.service = None
        self._init_service()

    def is_configured(self) -> bool:
        return self.service is not None

    def _init_service(self):
        sa_file = settings.GOOGLE_SERVICE_ACCOUNT_FILE
        if os.path.exists(sa_file):
            try:
                credentials = service_account.Credentials.from_service_account_file(
                    sa_file, scopes=SCOPES
                )
                self.service = build('calendar', 'v3', credentials=credentials)
                logger.info("Google Calendar Service successfully initialized.")
            except Exception as e:
                logger.error(f"Failed to initialize Google Calendar Service: {e}", exc_info=True)
        else:
            logger.warning(f"Google Service Account file not found at '{sa_file}'. Calendar integration disabled.")

    async def find_duplicate_event(
        self,
        title: str,
        start_time: datetime
    ) -> Optional[dict]:
        """
        Checks if an event with a matching or very similar title already exists on that date.
        Prevents duplicate creation.
        """
        if not self.service:
            return None

        clean_title = title.strip().lower()
        target_date = start_time.date()
        try:
            events = await self.get_events_for_date(target_date)
            for ev in events:
                ev_summary = (ev.get("summary") or "").strip().lower()
                if not ev_summary:
                    continue
                if clean_title == ev_summary or clean_title in ev_summary or ev_summary in clean_title:
                    return ev
        except Exception as e:
            logger.warning(f"Error checking for duplicate calendar event: {e}")
        return None

    async def create_event(
        self,
        title: str,
        start_time: datetime,
        end_time: Optional[datetime] = None,
        description: Optional[str] = None
    ) -> Optional[dict]:
        """
        Creates an event in Google Calendar with idempotency and duplicate checking.
        """
        if not self.service:
            logger.warning("Google Calendar service not initialized. Skipping event creation.")
            return None

        # 1. Deduplication check
        existing = await self.find_duplicate_event(title, start_time)
        if existing:
            logger.info(f"Duplicate calendar event detected for '{title}' on {start_time.date()}. Returning existing event ID={existing.get('id')}.")
            return existing

        if not end_time:
            end_time = start_time + timedelta(hours=1)

        event_body = {
            'summary': title,
            'description': description or '',
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': settings.TIMEZONE,
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': settings.TIMEZONE,
            },
        }

        # 2. Resilient execute with reconnection retry
        for attempt in range(2):
            try:
                logger.info(f"Creating Google Calendar event (attempt {attempt + 1}): '{title}' at {start_time}")
                event = self.service.events().insert(
                    calendarId=settings.GOOGLE_CALENDAR_ID,
                    body=event_body
                ).execute()
                logger.info(f"Google Calendar event created successfully: {event.get('htmlLink')}")
                return event
            except (BrokenPipeError, ConnectionResetError, socket.timeout) as net_err:
                logger.warning(f"Network error on Google Calendar (attempt {attempt + 1}): {net_err}. Reconnecting...")
                self._init_service()
                if attempt == 1:
                    raise RuntimeError(f"Failed to connect to Google Calendar: {net_err}")
            except Exception as e:
                logger.error(f"Error creating Google Calendar event: {e}", exc_info=True)
                raise RuntimeError(f"Failed to create Google Calendar event: {str(e)}")


    async def get_events_for_date(self, target_date) -> list:
        """
        Retrieves list of events from Google Calendar for specific target_date.
        """
        return await self.get_events_for_date_range(target_date, target_date)

    async def get_events_for_date_range(self, start_date, end_date) -> list:
        """
        Retrieves list of events from Google Calendar between start_date and end_date.
        """
        if not self.service:
            return []
        try:
            tz = get_tz()
            start_of_day = datetime.combine(start_date, datetime.min.time(), tzinfo=tz).isoformat()
            end_of_day = datetime.combine(end_date, datetime.max.time(), tzinfo=tz).isoformat()
            events_result = self.service.events().list(
                calendarId=settings.GOOGLE_CALENDAR_ID,
                timeMin=start_of_day,
                timeMax=end_of_day,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            return events_result.get('items', [])
        except Exception as e:
            logger.error(f"Error fetching Google Calendar events for range {start_date} to {end_date}: {e}")
            return []

    async def search_events(self, query: str) -> list:
        """
        Searches Google Calendar events matching query string.
        """
        if not self.service:
            return []
        try:
            events_result = self.service.events().list(
                calendarId=settings.GOOGLE_CALENDAR_ID,
                q=query,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            return events_result.get('items', [])
        except Exception as e:
            logger.error(f"Error searching Google Calendar events for '{query}': {e}")
            return []

    async def get_event_by_id(self, event_id: str) -> Optional[dict]:
        """
        Retrieves a single Google Calendar event by ID.
        """
        if not self.service:
            return None
        try:
            return self.service.events().get(
                calendarId=settings.GOOGLE_CALENDAR_ID,
                eventId=event_id
            ).execute()
        except Exception as e:
            logger.error(f"Error retrieving Google Calendar event '{event_id}': {e}")
            return None

    async def update_event(
        self,
        event_id: str,
        title: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        description: Optional[str] = None
    ) -> Optional[dict]:
        """
        Updates an existing Google Calendar event.
        """
        if not self.service:
            logger.warning("Google Calendar service not initialized. Cannot update event.")
            return None

        try:
            event = self.service.events().get(
                calendarId=settings.GOOGLE_CALENDAR_ID,
                eventId=event_id
            ).execute()

            if title:
                event['summary'] = title
            if description is not None:
                event['description'] = description

            if start_time:
                if not end_time:
                    end_time = start_time + timedelta(hours=1)
                event['start'] = {
                    'dateTime': start_time.isoformat(),
                    'timeZone': settings.TIMEZONE,
                }
                event['end'] = {
                    'dateTime': end_time.isoformat(),
                    'timeZone': settings.TIMEZONE,
                }
            elif end_time:
                event['end'] = {
                    'dateTime': end_time.isoformat(),
                    'timeZone': settings.TIMEZONE,
                }

            updated_event = self.service.events().update(
                calendarId=settings.GOOGLE_CALENDAR_ID,
                eventId=event_id,
                body=event
            ).execute()
            logger.info(f"Google Calendar event {event_id} updated successfully.")
            return updated_event
        except Exception as e:
            logger.error(f"Error updating Google Calendar event '{event_id}': {e}", exc_info=True)
            raise RuntimeError(f"Failed to update Google Calendar event: {str(e)}")

    async def delete_event(self, event_id: str) -> bool:
        """
        Deletes a Google Calendar event by ID.
        """
        if not self.service:
            logger.warning("Google Calendar service not initialized. Cannot delete event.")
            return False

        try:
            self.service.events().delete(
                calendarId=settings.GOOGLE_CALENDAR_ID,
                eventId=event_id
            ).execute()
            logger.info(f"Google Calendar event {event_id} deleted successfully.")
            return True
        except Exception as e:
            logger.error(f"Error deleting Google Calendar event '{event_id}': {e}", exc_info=True)
            return False


calendar_service = CalendarService()
