"""
Module for generating a course from a YouTube video by extracting transcripts
and keyframes, then processing them with LangGraph agent.
"""
import os
import subprocess
import asyncio
import logging
import json
import re
import time
from typing import Any, Dict, List, Tuple, Set, Optional
import whisper
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled
from dotenv import load_dotenv
from app.api import frame_extraction, agentic
from app.api.s3_utils import upload_frame_to_s3
from app.db.database import async_session
from app.db.crud import create_course, get_course_by_video_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

router = APIRouter()
load_dotenv()

PROGRESS_QUEUES: Dict[str, asyncio.Queue] = {}
ACTIVE_TASKS: Dict[str, asyncio.Task] = {}


def _extract_video_id(url: str) -> Optional[str]:
    """Extract the 11-character YouTube video id from common URL shapes.

    Handles youtu.be/<id>, watch?v=<id> (with extra query params like ``?si=``),
    shorts, live, embed and /v/ links. Returns None when no id is found.
    """
    url = (url or "").strip()
    if not url:
        return None
    patterns = (
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{11})",
        # Generic `?v=` / `&v=` — covers youtube.com/watch?v=… (also mobile and
        # music subdomains) regardless of extra query params like `?si=…`.
        r"[?&]v=([A-Za-z0-9_-]{11})",
    )
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

class VideoRequest(BaseModel):
    """
    Request model for video URL.
    """
    videoUrl: str

class ProgressStatus(BaseModel):
    """Model for progress updates."""
    status: str
    detail: Optional[str] = None
    error: Optional[str] = None
    final_result: Optional[Dict[str, Any]] = None

async def send_progress(queue: asyncio.Queue, status: str,
                        detail: Optional[str] = None,
                        error: Optional[str] = None,
                        final_result: Optional[Dict[str, Any]] = None):
    """Puts a progress update onto the queue."""
    update = ProgressStatus(status=status, detail=detail, error=error, final_result=final_result)
    await queue.put(update)

async def _run_course_generation(video_id: str, video_url: str, queue: asyncio.Queue):
    """The actual course generation logic, run as a background task."""
    local_temp_files: Set[str] = set()
    final_result_data: Optional[Dict[str, Any]] = None

    try:
        await send_progress(queue, "Initializing", f"Starting process for video: {video_id}")
        start_time = time.time()
        stage_start_time = time.time()
        await send_progress(queue, "Transcript", "Fetching transcript...")
        def get_transcript_sync() -> Tuple[str, List[Dict[str, Any]]]:
            """Synchronous wrapper for transcript fetching."""
            nonlocal local_temp_files
            try:
                logger.info("Attempting to fetch transcript via YouTubeTranscriptApi...")
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                target_languages = ['en', 'en-US', 'en-GB']
                transcript_obj = None
                available_langs = [t.language for t in transcript_list]
                logger.info("Available transcript languages: %s" ,available_langs)
                for lang in target_languages:
                    try:
                        transcript_obj = transcript_list.find_generated_transcript([lang])
                        logger.info("Found generated transcript in '%s'.", lang)
                        break
                    except NoTranscriptFound:
                        continue

                if not transcript_obj:
                    try:
                        transcript_obj = transcript_list.find_generated_transcript(available_langs)
                        logger.info("Found generated transcript in '%s' (fallback).",
                                    transcript_obj.language)
                    except NoTranscriptFound:
                        logger.error("No generated transcripts found at all. Falling back to Whisper.")

                try:
                    transcript_data: List[Dict[str, Any]] = transcript_obj.fetch()
                except Exception as fetch_err:  # incl. xml ParseError on empty/blocked timedtext
                    logger.warning("Transcript fetch failed (%s). Falling back to Whisper.",
                                   type(fetch_err).__name__)
                    audio_file_path = os.path.join("/tmp", f"{video_id}.mp3")
                    local_temp_files.add(audio_file_path)
                    return _get_whisper_transcript(video_id, video_url)

                if not transcript_data:
                    logger.warning("Transcript fetch returned no segments. Falling back to Whisper.")
                    audio_file_path = os.path.join("/tmp", f"{video_id}.mp3")
                    local_temp_files.add(audio_file_path)
                    return _get_whisper_transcript(video_id, video_url)

                segments: List[Dict[str, Any]] = [
                    {"start": item["start"], "duration": item["duration"], "text": item["text"].strip()}
                    for item in transcript_data
                ]
                transcript_text: str = "\n".join([seg["text"] for seg in segments if seg["text"]])
                logger.info("Transcript extracted via YouTubeTranscriptApi.")
                return transcript_text, segments
            except (NoTranscriptFound, TranscriptsDisabled) as e:
                logger.warning("YouTubeTranscriptApi failed (%s). Falling back to Whisper.", type(e).__name__)
                audio_file_path = os.path.join("/tmp", f"{video_id}.mp3")
                local_temp_files.add(audio_file_path)
                return _get_whisper_transcript(video_id, video_url)
            except (KeyError, AttributeError, RuntimeError) as api_err:
                logger.exception("Unexpected error fetching transcript via YouTubeTranscriptApi: %s", api_err)
                if "video unavailable" in str(api_err).lower():
                    raise HTTPException(status_code=404, detail=f"Video unavailable: {api_err}") from api_err
                logger.warning("Unexpected API error (%s). Falling back to Whisper.", type(api_err).__name__)
                audio_file_path = os.path.join("/tmp", f"{video_id}.mp3")
                local_temp_files.add(audio_file_path)
                return _get_whisper_transcript(video_id, video_url)

        transcript_formatted, transcript_segments = await asyncio.to_thread(get_transcript_sync)
        stage_duration = time.time() - stage_start_time
        await send_progress(queue, "Transcript", f"Transcript obtained ({stage_duration:.1f}s).")
        if not transcript_formatted:
            raise ValueError("Failed to obtain transcript.")

        # --- 2. Get Frames (Local Extraction) ---
        stage_start_time = time.time()
        await send_progress(queue, "Frames", "Downloading video...")

        # --- Define get_frames_sync here ---
        def get_frames_sync() -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
            """Synchronous wrapper for frame extraction, returns frames and timing."""
            nonlocal local_temp_files
            timings = {}
            t_start = time.time()
            output_dir = os.path.normpath(os.path.join("frames", video_id))
            os.makedirs(output_dir, exist_ok=True)
            local_frame_paths = set()
            video_path = None
            try:
                # Download
                t_dl_start = time.time()
                logger.info("Downloading video...")
                _, video_path = frame_extraction.download_video(video_url)
                timings["download"] = time.time() - t_dl_start
                if not video_path or not os.path.exists(video_path):
                    raise ValueError(f"Video download failed: {video_path}")
                local_temp_files.add(video_path)
                logger.info(f"Video download complete ({timings['download']:.1f}s).")

                # Detect Scenes
                t_scene_start = time.time()
                logger.info("Detecting scenes...")
                scenes: List[Any] = frame_extraction.detect_scenes(video_path, threshold=30.0)
                timings["scene_detection"] = time.time() - t_scene_start
                if not scenes: logger.warning("No scenes detected."); return [], timings
                logger.info(f"Scene detection complete ({timings['scene_detection']:.1f}s).")

                # Extract Keyframes
                t_extract_start = time.time()
                logger.info("Extracting keyframes...")
                frames_data: List[Dict] = frame_extraction.extract_keyframes_with_timestamps(
                    video_path, scenes, output_dir=output_dir
                )
                timings["keyframe_extraction"] = time.time() - t_extract_start
                logger.info(f"Keyframe extraction complete ({timings['keyframe_extraction']:.1f}s).")


                # Validate paths
                existing_frames_data = []
                for f_data in frames_data:
                    f_path = f_data.get("path")
                    if f_path:
                        if os.path.isabs(f_path): f_path_rel = os.path.relpath(f_path, os.getcwd())
                        else: f_path_rel = os.path.normpath(f_path)
                        f_data["path"] = f_path_rel.replace("\\", "/")
                        if os.path.exists(f_path_rel):
                            existing_frames_data.append(f_data)
                            local_frame_paths.add(f_path_rel) # Add for potential cleanup later if needed, although cleanup focuses on local_temp_files
                        else:
                            logger.warning(f"Extracted frame path does not exist: {f_path_rel}")

                if not existing_frames_data:
                    logger.warning("No valid frames extracted after path check.")
                    return []

                # Load to FiftyOne & Select Unique
                t_unique_start = time.time()
                logger.info("Selecting unique frames using FiftyOne...")
                dataset_name = f"video-frames-{video_id}-{int(time.time())}"
                dataset = frame_extraction.load_frames_into_fiftyone(existing_frames_data, video_id=dataset_name)
                selected_frames: List[Dict[str, Any]] = frame_extraction.select_unique_frames(dataset, uniqueness_threshold=0.50)
                timings["uniqueness_selection"] = time.time() - t_unique_start
                logger.info(f"Uniqueness selection complete ({timings['uniqueness_selection']:.1f}s).")


                # Filter selected frames & add to cleanup
                final_selected_frames = []
                for frame in selected_frames:
                    frame_path = frame.get("path")
                    if frame_path and os.path.exists(frame_path):
                        local_temp_files.add(frame_path)
                        final_selected_frames.append(frame)
                    else: logger.warning(f"Selected unique frame path '{frame_path}' does not exist.")

                # Cleanup fiftyone dataset
                try:
                    import fiftyone as fo
                    if dataset and dataset.name in fo.list_datasets():
                        fo.delete_dataset(dataset.name)
                        logger.info(f"Deleted FiftyOne dataset: {dataset.name}")
                except Exception as fo_del_err:
                    logger.warning(f"Could not delete FiftyOne dataset {dataset.name if dataset else 'N/A'}: {fo_del_err}")

                logger.info(f"Selected {len(final_selected_frames)} unique frames locally.")
                timings["total_frame_sync"] = time.time() - t_start
                return final_selected_frames, timings
            except Exception as frame_err:
                logger.exception("Error during synchronous frame extraction/selection: %s", frame_err)
                raise ValueError(f"Frame extraction failed: {frame_err}") from frame_err

        # Call the sync wrapper in a thread
        local_frames, frame_timings = await asyncio.to_thread(get_frames_sync)

        # Send detailed progress based on timings collected
        await send_progress(queue, "Frames", f"Video download complete ({frame_timings.get('download', 0):.1f}s). Detecting scenes...")
        await send_progress(queue, "Frames", f"Scene detection complete ({frame_timings.get('scene_detection', 0):.1f}s). Extracting frames...")
        await send_progress(queue, "Frames", f"Keyframe extraction complete ({frame_timings.get('keyframe_extraction', 0):.1f}s). Selecting unique frames...")
        await send_progress(queue, "Frames", f"Uniqueness selection complete ({frame_timings.get('uniqueness_selection', 0):.1f}s). Found {len(local_frames)} unique frames.")

        stage_duration = time.time() - stage_start_time
        await send_progress(queue, "Frames", f"Frame processing finished ({stage_duration:.1f}s).")

        # --- 3. Align Frames ---
        stage_start_time = time.time()
        await send_progress(queue, "Alignment", "Aligning frames with transcript segments...")
        aligned_frames = await asyncio.to_thread(
            frame_extraction.align_frames_to_transcript,
            local_frames, transcript_segments
        )
        stage_duration = time.time() - stage_start_time
        await send_progress(queue, "Alignment", f"Frames aligned ({stage_duration:.1f}s).")

        # --- Save local transcript file (Quick operation, might not need progress) ---
        transcript_file_path = await asyncio.to_thread(save_transcript, transcript_formatted, video_id)
        if transcript_file_path:
            local_temp_files.add(transcript_file_path)

        # --- 4. Run LangGraph Agent ---
        stage_start_time = time.time()
        await send_progress(queue, "AI Processing", "AI agent analysing content...")
        inputs: Dict[str, Any] = {
            "transcript": transcript_formatted,
            "frames": aligned_frames
        }

        # Invoke the agent (this is the black box part for external progress)
        results: Dict[str, Any] = await agentic.app.ainvoke(
            inputs, {"configurable": {"thread_id": video_id}}
        )
        agent_duration = time.time() - stage_start_time
        await send_progress(queue, "AI Processing", f"AI analysis complete ({agent_duration:.1f}s). Processing results...")

        # --- 5. Process Agent Results ---
        # Break down post-processing steps
        post_agent_start_time = time.time()
        structured_content_dict = results.get("structured_content")
        course_content_dict = results.get("course_content")
        quiz_content_dict = results.get("quiz_content")
        retention_plan_dict = results.get("retention_plan")

        if not isinstance(structured_content_dict, dict) or \
           not isinstance(course_content_dict, dict) or \
           not isinstance(quiz_content_dict, dict) or \
           not isinstance(retention_plan_dict, dict):
            logger.error("Agent returned unexpected data structure.")
            raise ValueError("AI processing returned invalid data structure.")
        await send_progress(queue, "AI Processing", "Extracting generated course structure...")
        # Add slight delay if needed to ensure messages are spaced out
        await asyncio.sleep(2)
        await send_progress(queue, "AI Processing", "Generating lesson content...")
        await asyncio.sleep(2)
        await send_progress(queue, "AI Processing", "Generating quizzes...")
        await asyncio.sleep(2)
        await send_progress(queue, "AI Processing", "Generating retention plan...")
        post_agent_duration = time.time() - post_agent_start_time
        logger.info("Agent result processing took %.2fs", post_agent_duration)


        # --- 6. Upload *Agent-Selected* Frames to S3 ---
        stage_start_time = time.time()
        await send_progress(queue, "Cloud Storage", "Uploading relevant images...")
        updated_course_content, updated_structured_content = await process_and_upload_frames(
            course_content_dict,
            structured_content_dict,
            video_id
        )
        stage_duration = time.time() - stage_start_time
        await send_progress(queue, "Cloud Storage", f"Image upload complete ({stage_duration:.1f}s).")

        # --- 7. Save to Database ---
        stage_start_time = time.time()
        await send_progress(queue, "Database", "Saving final course...")
        # ... (keep database saving logic) ...
        async with async_session() as session:
             # create_course is async
            generated_course = await create_course(
                session=session,
                video_id=video_id,
                transcript_text=transcript_formatted,
                structured_content=updated_structured_content,
                course_content=updated_course_content,
                quiz_content=quiz_content_dict,
                retention_plan=retention_plan_dict,
                status="completed"
            )
        if not generated_course:
            async with async_session() as session_check:
                existing = await get_course_by_video_id(session_check, video_id)
                if existing:
                    logger.warning("Course creation failed, but course %s exists.", video_id)
                    final_result_data = {
                        "message": "Course already existed.",
                        "database_id": existing.id,
                        "course": {
                            "structured_content": existing.structured_content,
                            "course_content": existing.course_content,
                            "quiz_content": existing.quiz_content,
                            "retention_plan": existing.retention_plan,
                        }
                    }
                else:
                    raise ValueError("Failed to save course data to database.")
        else:
            final_result_data = {
            "message": "Course generated and saved successfully.",
            "database_id": generated_course.id,
            "course": {
                "structured_content": updated_structured_content,
                "course_content": updated_course_content,
                "quiz_content": quiz_content_dict,
                "retention_plan": retention_plan_dict,
            }
            }

        await send_progress(queue, "Database", f"Course saved successfully ({stage_duration:.1f}s).")

        # --- 8. Signal Completion ---
        total_duration = time.time() - start_time
        await send_progress(queue, "Completed", f"Course generation finished ({total_duration:.1f}s).", final_result=final_result_data)
        logger.info(f"Course generation process completed successfully for {video_id} in {total_duration:.1f}s.")

    except HTTPException as http_exc:
        logger.error("HTTP Exception during background processing for video_id %s: %s", video_id, http_exc.detail)
        await send_progress(queue, "Failed", f"Process failed: {http_exc.detail}", error=str(http_exc.detail))
        final_result_data = {"error": http_exc.detail}
    except ValueError as val_err:
        logger.error("Validation Error during background processing for video_id %s: %s", video_id, val_err)
        await send_progress(queue, "Failed", f"Process failed: {val_err}", error=str(val_err))
        final_result_data = {"error": str(val_err)}
    except Exception as e:
        logger.exception("Unhandled error during background course generation for video_id %s: %s", video_id, e)
        # Avoid sending raw exception details to the client for security
        error_msg = "An unexpected internal error occurred."
        await send_progress(queue, "Failed", error_msg, error=error_msg)
        final_result_data = {"error": error_msg}
    finally:
        # --- 9. Cleanup ---
        logger.info("Initiating cleanup of temporary local files for %s...", video_id)
        deleted_count = 0; failed_count = 0
        paths_to_delete = local_temp_files.copy()
        delete_tasks = []
        file_paths_in_tasks = [] # Keep track of paths corresponding to tasks
        for path in paths_to_delete:
            if path and os.path.isfile(path):
                delete_tasks.append(asyncio.to_thread(_safe_delete, path))
                file_paths_in_tasks.append(path)

        results = await asyncio.gather(*delete_tasks, return_exceptions=True)
        for i, path in enumerate(file_paths_in_tasks):
            if isinstance(results[i], Exception): failed_count += 1; logger.warning(f"Cleanup failed for {path}: {results[i]}")
            else: deleted_count += 1

        frame_video_dir = os.path.normpath(os.path.join("frames", video_id))
        try:
            if os.path.exists(frame_video_dir) and os.path.isdir(frame_video_dir):
                is_empty = not await asyncio.to_thread(os.listdir, frame_video_dir)
                if is_empty: await asyncio.to_thread(os.rmdir, frame_video_dir); logger.info(f"Removed empty dir: {frame_video_dir}"); deleted_count += 1
                else: logger.info(f"Frame dir {frame_video_dir} not empty, not removed.")
        except Exception as e: logger.warning(f"Could not check/remove frame dir {frame_video_dir}: {e}"); failed_count += 1
        logger.info(f"Local file cleanup attempt complete. \
                    Processed: {deleted_count},Failed/Skipped: {failed_count}")
        await queue.put(None)

        if video_id in ACTIVE_TASKS: 
            del ACTIVE_TASKS[video_id]
        logger.info("Removed task ref for completed/failed video_id: %s", video_id)

@router.post("/generate-course")
async def generate_course_request(request: VideoRequest) -> Dict[str, Any]:
    """
    Initiates the course generation process in the background
    and returns immediately.
    """
    video_id: str = ""
    try:
        if not request.videoUrl or ("youtube.com" not in request.videoUrl and "youtu.be" not in request.videoUrl):
            raise HTTPException(status_code=400, detail="Invalid YouTube URL provided.")
        video_id = _extract_video_id(request.videoUrl)
        if not video_id:
            raise HTTPException(status_code=400, detail="Could not extract video ID from URL.")
        logger.info("Received request for video_id: %s", video_id)

        # --- Check if already processing ---
        if video_id in ACTIVE_TASKS and not ACTIVE_TASKS[video_id].done():
            logger.warning("Course generation for video_id %s is already in progress.", video_id)
            raise HTTPException(status_code=409, detail="Course generation for this video is already in progress.")

        # --- Check if course already exists in DB ---
        async with async_session() as session:
            existing_course = await get_course_by_video_id(session, video_id)
            if existing_course:
                logger.info("Course for video_id %s already exists in DB. Returning existing data.", video_id)
                # Return existing data directly, no need to start process
                return {
                    "message": "Course already exists.",
                    "database_id": existing_course.id,
                    "video_id": video_id, # Include video_id for consistency
                    "course": { # Return the content directly for existing courses
                        "structured_content": existing_course.structured_content,
                        "course_content": existing_course.course_content,
                        "quiz_content": existing_course.quiz_content,
                        "retention_plan": existing_course.retention_plan,
                    }
                }

        # --- Start Background Processing ---
        logger.info("Starting background course generation for video_id: %s", video_id)
        queue = asyncio.Queue()
        PROGRESS_QUEUES[video_id] = queue

        # Create and store the background task
        task = asyncio.create_task(
            _run_course_generation(video_id, request.videoUrl, queue)
        )
        ACTIVE_TASKS[video_id] = task

        # Return immediately
        return {"message": "Course generation started.", "video_id": video_id}

    except HTTPException as e:
        logger.error("HTTP Exception during request for video_id %s: %s", video_id or 'N/A', e.detail)
        raise e
    except Exception as e:
        logger.exception("Error processing generate-course request for video_id %s: %s", video_id or 'N/A', e)
        raise HTTPException(status_code=500, detail=f"Failed to start course generation: {e}") from e


# --- New SSE Endpoint ---
@router.get("/progress/{video_id}")
async def stream_progress(request: Request, video_id: str):
    """
    Endpoint to stream progress updates for a given video_id using SSE.
    """
    logger.info("SSE connection requested for video_id: %s", video_id)

    # Check if the process was ever started (queue exists)
    if video_id not in PROGRESS_QUEUES:
        logger.warning("No progress queue found for video_id %s. Process might be finished or never started.", video_id)
        # Check DB again in case it finished *very* quickly
        async with async_session() as session:
            existing_course = await get_course_by_video_id(session, video_id)
            if existing_course:
                # If found, send a final 'completed' message and close
                async def finished_generator():
                    final_data = {
                           "message": "Course already existed or finished.",
                           "database_id": existing_course.id,
                           "course": {
                               "structured_content": existing_course.structured_content,
                               "course_content": existing_course.course_content,
                               "quiz_content": existing_course.quiz_content,
                               "retention_plan": existing_course.retention_plan,
                            }
                       }
                    update = ProgressStatus(status="Completed", detail="Course found in database.", final_result=final_data)
                    yield f"data: {update.model_dump_json()}\n\n" # Use model_dump_json for Pydantic v2
                    logger.info("Sent final existing course data via SSE for %s and closing stream.", video_id)
                return StreamingResponse(finished_generator(), media_type="text/event-stream")
            else:
                # If not in DB either, it's likely an invalid ID or hasn't started
                raise HTTPException(status_code=404, detail=f"Processing status not found for video ID: {video_id}")

    queue = PROGRESS_QUEUES[video_id]

    async def event_generator():
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info("SSE client disconnected for video_id: %s", video_id)
                    break

                try:
                    # Wait for a message from the background task
                    update: Optional[ProgressStatus] = await asyncio.wait_for(queue.get(), timeout=30) # Add timeout
                except asyncio.TimeoutError:
                    # No message received, send a keep-alive or just continue loop
                    # yield ":\n\n" # Optional keep-alive comment
                    continue

                if update is None:
                    # Sentinel value received, means processing is done (successfully or failed)
                    logger.info("End signal received from queue for video_id: %s. Closing SSE stream.", video_id)
                    # Optionally send a final confirmation message before closing
                    # yield f"data: {json.dumps({'status': 'Stream Closed'})}\n\n"
                    break
                else:
                    # Send the progress update to the client
                    yield f"data: {update.model_dump_json()}\n\n" # Use model_dump_json for Pydantic v2
                    # If the status is Completed or Failed, we can break after sending
                    if update.status in ["Completed", "Failed"]:
                        logger.info("Sent final status '%s' via SSE for %s. Closing stream.", update.status, video_id)
                        break # Close stream after sending final status

                # await asyncio.sleep(0.1) # Small sleep to prevent tight loop if needed
        except asyncio.CancelledError:
            logger.info("SSE stream cancelled for video_id: %s", video_id)
        except Exception as e:
            logger.exception("Error in SSE event generator for video_id %s: %s", video_id, e)
            # Attempt to send an error message to the client before closing
            try:
                error_update = ProgressStatus(status="Error", detail="SSE stream failed.", error=str(e))
                yield f"data: {error_update.model_dump_json()}\n\n"
            except Exception:
                pass # Ignore error during error reporting
        finally:
            logger.info("SSE stream closing for video_id: %s", video_id)
            # Clean up the queue *only if* it still exists (might be removed by background task already)
            if video_id in PROGRESS_QUEUES:
                # Ensure the queue is empty before deleting? Not strictly necessary.
                # while not queue.empty():
                #    await queue.get()
                del PROGRESS_QUEUES[video_id]
                logger.info("Removed progress queue from global dict for video_id: %s", video_id)


    return StreamingResponse(event_generator(), media_type="text/event-stream")

def save_transcript(transcript: str, video_id: str) -> str:
    """
    Save the transcript text to a file.

    Args:
        transcript: The transcript text.
        video_id: YouTube video identifier.

    Returns:
        The path to the saved transcript file.
    """
    transcript_folder: str = "transcripts"
    os.makedirs(transcript_folder, exist_ok=True)
    transcript_file: str = os.path.join(transcript_folder, f"{video_id}.txt")
    try:
        with open(transcript_file, "w", encoding="utf-8") as f:
            f.write(transcript)
        logger.info("Transcript saved to %s", transcript_file)
        return transcript_file
    except OSError as e:
        logger.error("Failed to save transcript file %s: %s", transcript_file, e)
        return ""


def _safe_delete(filepath: str):
    """Safely deletes a file if it exists."""
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
            logger.debug("Deleted local file: %s", filepath)
    except OSError as e:
        logger.warning("Failed to delete local file %s: %s", filepath, e)
    except Exception as e:
        logger.warning("Unexpected error deleting file %s: %s", filepath, e)


async def process_and_upload_frames(
    course_content: Dict[str, Any],
    structured_content: Dict[str, Any],
    video_id: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Finds local frame paths *present in the final content dicts*, uploads them to S3,
    and updates the dicts with S3 URLs.

    Returns:
        Tuple containing the updated course_content and structured_content dicts.
    """
    local_paths_to_upload: Set[str] = set()
    frame_path_map: Dict[str, str] = {} # Map local path to S3 URL

    content_dicts_to_scan = {"course": course_content, "structured": structured_content}
    logger.info("Scanning final agent output for local frame paths to upload...")

    # 1. Collect unique local frame paths *from the final agent output only*
    for content_key, content_dict in content_dicts_to_scan.items():
        if not isinstance(content_dict, dict) or "modules" not in content_dict:
            logger.debug(f"Skipping scan for {content_key}_content as it's not a valid dict or missing 'modules'.")
            continue # Skip if the structure isn't as expected

        for mod_idx, module in enumerate(content_dict.get("modules", [])):
             # Ensure module is a dict and has sections
            if not isinstance(module, dict) or "sections" not in module:
                logger.warning(f"Skipping module {mod_idx} in {content_key}_content: Invalid format.")
                continue

            for sec_idx, section in enumerate(module.get("sections", [])):
                # Ensure section is a dict
                if not isinstance(section, dict):
                    logger.warning(f"Skipping section {sec_idx} in module {mod_idx} ({content_key}_content): Invalid format.")
                    continue

                # Check both 'media' and 'frames' keys
                for frame_list_key in ["media", "frames"]:
                    frame_list = section.get(frame_list_key, [])
                    if not isinstance(frame_list, list):
                        logger.warning("Invalid format for '%s' in section %d," \
                        " module %d (%s_content). Expected list, got %s.", 
                        frame_list_key, sec_idx, mod_idx, content_key, type(frame_list))
                        continue

                    for frame_idx, frame in enumerate(frame_list):
                        # Ensure frame is a dict and has frame_path
                        if not isinstance(frame, dict) or "frame_path" not in frame:
                            logger.warning(f"Skipping frame {frame_idx} in section {sec_idx}, module {mod_idx} ({content_key}_content): Invalid format or missing 'frame_path'.")
                            continue

                        local_path = frame.get("frame_path")
                        # Check if it's a non-empty string and doesn't already look like a URL
                        if local_path and isinstance(local_path, str) and not local_path.startswith("http"):
                            if local_path not in local_paths_to_upload:
                                logger.debug(f"Found local frame path for upload in {content_key}_content - M{mod_idx}/S{sec_idx}/{frame_list_key}[{frame_idx}]: {local_path}")
                                local_paths_to_upload.add(local_path)
                        elif local_path and local_path.startswith("http"):
                            logger.debug(f"Skipping already existing URL in {content_key}_content - M{mod_idx}/S{sec_idx}/{frame_list_key}[{frame_idx}]: {local_path}")


    if not local_paths_to_upload:
        logger.info("No local frame paths found in the final agent output to upload.")
        # Return copies to ensure immutability if the original dicts are used elsewhere
        return json.loads(json.dumps(course_content)), json.loads(json.dumps(structured_content))

    logger.info(f"Found {len(local_paths_to_upload)} unique local frame paths in final content to upload to S3.")

    # 2. Upload each unique frame to S3
    upload_tasks = []
    local_path_list = list(local_paths_to_upload)
    for local_path in local_path_list:
        full_local_path = os.path.normpath(os.path.join(os.getcwd(), local_path))
        frame_filename = os.path.basename(local_path)
        if not os.path.exists(full_local_path):
            logger.error(f"Local file path specified in final content not found, cannot upload: {full_local_path}")
            frame_path_map[local_path] = local_path
            continue
        task = asyncio.to_thread(upload_frame_to_s3, full_local_path, video_id, frame_filename)
        upload_tasks.append((local_path, task))

    if not upload_tasks:
        logger.warning("No valid frames found to upload after checking local paths.")
        return json.loads(json.dumps(course_content)), json.loads(json.dumps(structured_content))

    results = await asyncio.gather(*(task for _, task in upload_tasks), return_exceptions=True)

    successful_uploads = 0
    failed_uploads = 0
    original_paths_in_tasks = [lp for lp, _ in upload_tasks]

    for i, local_path in enumerate(original_paths_in_tasks):
        result = results[i]
        if isinstance(result, Exception) or result is None:
            logger.error(f"Failed to upload frame {local_path}. Reason: {result}")
            frame_path_map[local_path] = local_path
            failed_uploads += 1
        else:
            frame_path_map[local_path] = result
            successful_uploads += 1

    logger.info(f"S3 Upload Results - Successful: {successful_uploads}, Failed: {failed_uploads}")

    # 3. Update the content dictionaries
    updated_course_content = json.loads(json.dumps(course_content))
    updated_structured_content = json.loads(json.dumps(structured_content))
    dicts_to_update = {"course": updated_course_content, "structured": updated_structured_content}

    for content_key, content_dict in dicts_to_update.items():
        if isinstance(content_dict, dict) and "modules" in content_dict:
            for module in content_dict.get("modules", []):
                if isinstance(module, dict) and "sections" in module:
                    for section in module.get("sections", []):
                        if isinstance(section, dict):
                            for frame_list_key in ["media", "frames"]:
                                updated_frames = []
                                original_frame_list = section.get(frame_list_key, [])
                                if not isinstance(original_frame_list, list):
                                    continue
                                for frame in original_frame_list:
                                    if not isinstance(frame, dict) or "frame_path" not in frame:
                                        updated_frames.append(frame)
                                        continue
                                    original_local_path = frame.get("frame_path")
                                    if original_local_path in frame_path_map:
                                        updated_frame = frame.copy()
                                        updated_frame["frame_path"] = frame_path_map[original_local_path]
                                        updated_frames.append(updated_frame)
                                    else:
                                        updated_frames.append(frame)
                                section[frame_list_key] = updated_frames

    logger.info("Finished processing and uploading frames selected by the agent.")
    return updated_course_content, updated_structured_content


def _get_whisper_transcript(video_id: str, video_url: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Downloads audio and transcribes using Whisper."""
    logger.info("Attempting Whisper fallback for video_id: %s", video_id)
    audio_file: str = os.path.join("/tmp", f"{video_id}.mp3")
    audio_dir = os.path.dirname(audio_file)
    os.makedirs(audio_dir, exist_ok=True)
    download_path_template = os.path.join(audio_dir, '%(id)s.%(ext)s')

    command: List[str] = [
        "yt-dlp",
        "-x", "--audio-format", "mp3",
        "-o", download_path_template,
        "--socket-timeout", "30", # Add timeout
        video_url
    ]
    process = None # Initialize process variable
    try:
        logger.info("Running yt-dlp command: %s", " ".join(command))
        process = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120) # Added timeout
        logger.info("yt-dlp stdout: %s", process.stdout)
        if process.stderr:
            logger.info("yt-dlp stderr: %s", process.stderr) # Log stderr even on success
    except subprocess.TimeoutExpired:
        logger.error("yt-dlp command timed out for video: %s", video_url)
        raise HTTPException(status_code=504, detail="Audio download timed out.") # Raise HTTP directly if needed by caller, else ValueError
    except subprocess.CalledProcessError as sub_err:
        error_message = f"yt-dlp failed: {sub_err.stderr or sub_err.stdout or sub_err}"
        logger.error(error_message)
        # Raise ValueError for internal handling, or HTTPException if needed by caller
        raise ValueError(f"Failed to download audio: {error_message}") from sub_err
    except Exception as dl_err:
        logger.exception("An unexpected error occurred during audio download: %s", dl_err)
        raise ValueError(f"Audio download failed: {dl_err}") from dl_err # Internal handling

    if not os.path.exists(audio_file):
        logger.error("Audio file %s not found after yt-dlp run.", audio_file)
        raise ValueError("Audio file download failed or file not found at expected path.") # Internal handling

    try:
        logger.info("Attempting transcription with Whisper model 'base'...")
        model = whisper.load_model("base")
        result: Dict[str, Any] = model.transcribe(audio_file, fp16=False)
        transcript_formatted = result.get("text", "").strip()
        logger.info("Transcript extracted via Whisper (first 200 chars): %s", transcript_formatted[:200])

        # Format segments like YouTube API for potential downstream consistency
        segments_whisper: List[Dict[str, Any]] = result.get("segments", [])
        segments_formatted: List[Dict[str, Any]] = [
            {
                "start": seg["start"],
                "duration": seg["end"] - seg["start"],
                "text": seg["text"].strip()
            }
            for seg in segments_whisper
        ]
        return transcript_formatted, segments_formatted
    except Exception as whisper_err:
        logger.exception("Whisper transcription failed: %s", whisper_err)
        raise ValueError(f"Whisper transcription failed: {whisper_err}") from whisper_err # Internal handling
    finally:
        _safe_delete(audio_file)
