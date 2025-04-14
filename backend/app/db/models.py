# app/db/models.py
import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

class Course(Base):
    """
    SQLAlchemy model for storing generated course data.
    """
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True, index=True)
    video_id = Column(String, unique=True, index=True, nullable=False)
    transcript_text = Column(Text, nullable=True)
    # Store structured data as JSON Binary for efficiency
    structured_content = Column(JSONB, nullable=True)
    course_content = Column(JSONB, nullable=True) # Will contain S3 URLs for frames
    quiz_content = Column(JSONB, nullable=True)
    retention_plan = Column(JSONB, nullable=True)
    status = Column(String, default="completed", nullable=False) # e.g., processing, completed, failed
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<Course(video_id='{self.video_id}', status='{self.status}')>"

# Optional: Add an async function to create tables (run once manually or via migration tool like Alembic)
# from app.db.database import engine
# async def create_tables():
#     async with engine.begin() as conn:
#         await conn.run_sync(Base.metadata.create_all)