import os
import logging
from datetime import datetime, timedelta
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from app.config import settings
from app.utils.timezone import get_tz

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']


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

    async def create_event(
        self,
        title: str,
        start_time: datetime,
        end_time: Optional[datetime] = None,
        description: Optional[str] = None
    ) -> Optional[dict]:
        """
        Creates an event in Google Calendar.
        """
        if not self.service:
            logger.warning("Google Calendar service not initialized. Skipping event creation.")
            return None

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

        try:
            logger.info(f"Creating Google Calendar event: '{title}' at {start_time}")
            event = self.service.events().insert(
                calendarId=settings.GOOGLE_CALENDAR_ID,
                body=event_body
            ).execute()
            logger.info(f"Google Calendar event created successfully: {event.get('htmlLink')}")
            return event
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


calendar_service = CalendarService()
