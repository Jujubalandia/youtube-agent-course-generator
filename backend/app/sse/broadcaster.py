"""
Server-Sent Events (SSE) broadcaster implementation.

Manages client subscriptions and message publishing using asyncio queues.
Tracks the status of background jobs associated with event IDs.
"""

import asyncio
import json
import logging
from collections import defaultdict
from typing import Dict, List, Any, DefaultDict, Optional

logger = logging.getLogger(__name__)

class MessageAnnouncer:
    """
    Simple broadcaster class using asyncio.Queue for SSE.

    Manages listeners (queues) for different event IDs (e.g., video_id)
    and tracks the last known status associated with each event ID.
    """

    def __init__(self):
        self.listeners: DefaultDict[str, List[asyncio.Queue]] = defaultdict(list)
        self.job_status: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, event_id: str) -> asyncio.Queue:
        """
        Subscribe a client to receive messages for a specific event_id.

        Args:
            event_id: The identifier for the event stream (e.g., job ID, video ID).

        Returns:
            An asyncio.Queue the client can listen on.
        """
        queue = asyncio.Queue()
        async with self._lock:
            self.listeners[event_id].append(queue)
            listener_count = len(self.listeners[event_id])
            logger.info("Client subscribed to SSE for %s. Listeners: %d", event_id, listener_count)

            current_status = self.job_status.get(event_id)
            if current_status:
                await queue.put(current_status)
        return queue

    async def unsubscribe(self, event_id: str, queue: asyncio.Queue):
        """
        Unsubscribe a client (queue) from an event_id.

        Args:
            event_id: The identifier for the event stream.
            queue: The specific queue to remove.
        """
        async with self._lock:
            try:
                self.listeners[event_id].remove(queue)
                listener_count = len(self.listeners.get(event_id, []))
                logger.info("Client unsubscribed from SSE for %s. Listeners: %d",
                            event_id, listener_count)
            except ValueError:
                logger.warning("Attempted to unsubscribe a queue not found for %s.", event_id)

    async def publish(self, event_id: str, message_data: Dict[str, Any]):
        """
        Publish a message (status update) to all listeners of an event_id.

        Args:
            event_id: The identifier for the event stream.
            message_data: The dictionary payload to send.
        """
        queues_to_notify: List[asyncio.Queue] = []
        async with self._lock:
            self.job_status[event_id] = message_data
            queues_to_notify = list(self.listeners.get(event_id, []))

        if queues_to_notify:
            logger.debug("Publishing SSE for %s to %d listeners: %s",
                        event_id, len(queues_to_notify), message_data)
        for queue in queues_to_notify:
            try:
                await queue.put(message_data)
            except asyncio.QueueFull:
                logger.warning("Queue full for event_id %s. Subscriber might be slow/disconnected.",
                               event_id)
            except asyncio.CancelledError:
                logger.warning("Queue task cancelled for event_id %s.", event_id)
            except RuntimeError as e:
                logger.error("Error putting message in queue for %s: %s: %s",
                            event_id, type(e).__name__, e, exc_info=False)

    async def get_job_status(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the last known status for a job/event_id.

        Args:
        event_id: The identifier for the job/event.

        Returns:
        The last status dictionary, or None if not found.
        """
        async with self._lock:
            return self.job_status.get(event_id)

    async def set_job_status(self, event_id: str, status: Dict[str, Any]):
        """
        Directly set the job status, useful for initial state.

        Args:
            event_id: The identifier for the job/event.
            status: The status dictionary to set.
        """
        async with self._lock:
            self.job_status[event_id] = status
            logger.info("Job status set for %s: %s", event_id, status)

    async def clear_job(self, event_id: str):
        """
        Remove all data (listeners and status) associated with an event_id.

        Args:
            event_id: The identifier for the job/event to clear.
        """
        async with self._lock:
            listeners = self.listeners.pop(event_id, [])
            self.job_status.pop(event_id, None)
            logger.info("Cleared job data for %s. Removed %d listeners.", event_id, len(listeners))

sse_broadcaster = MessageAnnouncer()

def format_sse(data: Dict[str, Any]) -> str:
    """
    Formats a dictionary into a Server-Sent Event message string.

    Args:
        data: The dictionary payload.

    Returns:
        A string formatted according to the SSE specification.
    """
    json_data = json.dumps(data)
    return f"data: {json_data}\n\n"
