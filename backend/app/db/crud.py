# app/db/crud.py
import logging
from typing import Dict, Union, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy import update, select # <--- Import update and select
from sqlalchemy.sql import func      # <--- Import func
from app.db.models import Course

logger = logging.getLogger(__name__)

async def create_course(
    session: AsyncSession,
    video_id: str,
    transcript_text: str,
    structured_content: Dict[str, Any],
    course_content: Dict[str, Any], # This dict should have S3 URLs
    quiz_content: Dict[str, Any],
    retention_plan: Dict[str, Any],
    status: str = "completed"
) -> Union[Course, None]:
    """
    Creates a new course record in the database.

    Args:
        session: The AsyncSession for database interaction.
        video_id: The unique YouTube video ID.
        transcript_text: The extracted transcript.
        structured_content: The structured content JSON.
        course_content: The course content JSON (with S3 URLs for frames).
        quiz_content: The quiz content JSON.
        retention_plan: The retention plan JSON.
        status: The status of the course generation.

    Returns:
        The created Course object or None if an error occurred.
    """
    new_course = Course(
        video_id=video_id,
        transcript_text=transcript_text,
        structured_content=structured_content,
        course_content=course_content,
        quiz_content=quiz_content,
        retention_plan=retention_plan,
        status=status,
        # created_at is handled by server_default
    )
    try:
        session.add(new_course)
        await session.commit()
        await session.refresh(new_course)
        logger.info("Successfully created course record for video_id: %s", video_id)
        return new_course
    except IntegrityError:
        await session.rollback()
        logger.warning("Database integrity error: Course with video_id %s already exists. Fetching existing.", video_id)
        # Fetch the existing one instead of returning None
        try:
            result = await session.execute(select(Course).where(Course.video_id == video_id))
            existing_course = result.scalars().first()
            if existing_course:
                 logger.info("Fetched existing course record for video_id: %s", video_id)
                 return existing_course
            else:
                 # This case should be rare if IntegrityError was raised, but handle it.
                 logger.error("IntegrityError for video_id %s, but could not fetch existing record.", video_id)
                 return None
        except Exception as fetch_exc:
             await session.rollback() # Rollback potential changes from failed fetch attempt
             logger.exception("Error fetching existing course record after IntegrityError for video_id %s: %s", video_id, fetch_exc)
             return None

    except Exception as e:
        await session.rollback()
        logger.exception("Error creating or fetching course record for video_id %s: %s", video_id, e)
        return None

async def update_course_status(session: AsyncSession, video_id: str, status: str) -> bool:
    """Updates the status of a course record."""
    try:
        stmt = (
            update(Course)                   # Use the imported update
            .where(Course.video_id == video_id)
            .values(status=status, updated_at=func.now()) # Use the imported func
            # Ensure returning something to check if update happened, if dialect supports it
            # .returning(Course.id) # Optional: Check if supported by asyncpg/PostgreSQL
        )
        result = await session.execute(stmt)
        await session.commit()

        if result.rowcount == 0:
            logger.warning("Attempted to update status for non-existent video_id: %s", video_id)
            return False

        logger.info("Updated status to '%s' for video_id: %s", status, video_id)
        return True
    except Exception as e:
        await session.rollback()
        logger.exception("Error updating course status for video_id %s: %s", video_id, e)
        return False

# You might also want a function to get a course by video_id
async def get_course_by_video_id(session: AsyncSession, video_id: str) -> Union[Course, None]:
    """Fetches a course record by its video_id."""
    try:
        result = await session.execute(select(Course).where(Course.video_id == video_id))
        course = result.scalars().first()
        if course:
            logger.debug("Found course for video_id: %s", video_id)
        else:
            logger.debug("No course found for video_id: %s", video_id)
        return course
    except Exception as e:
        logger.exception("Error fetching course by video_id %s: %s", video_id, e)
        return None