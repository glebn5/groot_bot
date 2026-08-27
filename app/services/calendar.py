import os
import logging
from datetime import datetime, timedelta
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build
from app.config import settings

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']


class CalendarService:
    def __init__(self):
        self.service = None
        self._init_service()

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
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'UTC',
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


calendar_service = CalendarService()
