"""
agentic.py: Agentic Workflow for Video-to-Course Conversion (using OpenTelemetry)

Implements a LangGraph state graph to automate conversion of video content into structured courses.
Takes video transcripts and extracted frames as input and orchestrates an agentic workflow to:
- Structure content into modules and sections.
- Select relevant frames as visual aids.
- Generate detailed course content (lessons).
- Design quizzes for assessment.
- Develop retention-focused learning strategies.
"""

import json
import logging
import os
import time
from typing import Dict, List, TypedDict, Any
from dotenv import load_dotenv
import google.api_core.exceptions
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import ChatPromptTemplate, PromptTemplate
from langchain.schema import HumanMessage, BaseMessage
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.api.utils import clean_json_string, encode_image
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.trace import Status, StatusCode


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("langgraph_agentic")
load_dotenv()

gemini_api_key = os.environ.get("GEMINI_API_KEY")
groq_api_key = os.environ.get("GROQ_API_KEY")

if not gemini_api_key:
    raise ValueError("GEMINI_API_KEY environment variable not set.")
if not groq_api_key:
    raise ValueError("GROQ_API_KEY environment variable not set.")

# Model names are configurable so a retired model (e.g. the original
# `gemini-1.5-flash`) can be swapped without a code change.
gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
groq_model = os.environ.get("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

resource = Resource(attributes={
    SERVICE_NAME: "video-to-course-agent"
})
provider = TracerProvider(resource=resource)
exporter = OTLPSpanExporter()
processor = BatchSpanProcessor(exporter)
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

#pylint: disable=line-too-long

class GraphState(TypedDict):
    """State representation for the content generation graph."""
    transcript: str
    frames: List[Dict[str, str]]
    structured_content: Dict
    course_content: Dict
    quiz_content: Dict
    retention_plan: Dict

gemini_llm = ChatGoogleGenerativeAI(
    model=gemini_model,
    temperature=0.7,
    google_api_key=gemini_api_key
)
logger.info("Initialized ChatGoogleGenerativeAI with model %s.", gemini_model)

groq_llm = ChatGroq(
    model=groq_model,
    temperature=0.7,
    api_key=groq_api_key
)
logger.info("Initialized Groq with %s.", groq_model)

memory = MemorySaver()

def log_retry_error(retry_state):
    """Log retry error."""
    logger.error("Retrying: %s", retry_state)
    current_span = trace.get_current_span()
    if current_span.is_recording():
        current_span.add_event("Retry Occurred", attributes={"retry_state": str(retry_state)})

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type(google.api_core.exceptions.ResourceExhausted),
    retry_error_callback=log_retry_error
)
def call_gemini_with_retry(
    chain: Any,
    input_data: Dict,
    span_name: str,
) -> BaseMessage:
    """Call Gemini with retries and create an OTel span for the call."""
    with tracer.start_as_current_span(span_name) as llm_span:
        llm_span.set_attribute("gen_ai.system", "google_ai_studio")
        llm_span.set_attribute("gen_ai.request.model", gemini_llm.model)
        llm_span.set_attribute("gen_ai.request.temperature", gemini_llm.temperature)
        prompt_str = str(input_data)
        llm_span.set_attribute("gen_ai.prompt", prompt_str[:1000] + "..." if len(prompt_str) > 1000 else prompt_str)

        try:
            start_time = time.time()
            result = chain.invoke(input_data)
            end_time = time.time()
            duration = end_time - start_time

            content = result.content
            llm_span.set_attribute("gen_ai.completion", content[:1000] + "..." if len(content) > 1000 else content)
            llm_span.set_attribute("gen_ai.response.model", gemini_llm.model)
            llm_span.set_attribute("gen_ai.usage.prompt_tokens", len(prompt_str))
            llm_span.set_attribute("gen_ai.usage.completion_tokens", len(content))
            llm_span.set_attribute("gen_ai.usage.total_tokens", len(prompt_str) + len(content))
            llm_span.set_attribute("gen_ai.response.duration", duration)
            # Add other metadata if available from the result object (e.g., finish reason)
            # if hasattr(result, 'response_metadata') and result.response_metadata:
            #    finish_reason = result.response_metadata.get('finish_reason')
            #    if finish_reason:
            #       llm_span.set_attribute("gen_ai.response.finish_reasons", [str(finish_reason)])
            llm_span.set_status(Status(StatusCode.OK))
            return result
        except Exception as e:
            logger.exception("Gemini call failed: %s", e)
            llm_span.record_exception(e)
            llm_span.set_status(Status(StatusCode.ERROR, description=f"Gemini call failed: {e}"))
            raise

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry_error_callback=log_retry_error
)
def call_groq_with_retry(
    chain: Any,
    input_data: Any,
    span_name: str
) -> BaseMessage:
    """Call Groq with retries and create an OTel span for the call."""
    with tracer.start_as_current_span(span_name) as llm_span:
        llm_span.set_attribute("gen_ai.system", "groq")
        llm_span.set_attribute("gen_ai.request.model", groq_llm.model_name)
        llm_span.set_attribute("gen_ai.request.temperature", groq_llm.temperature)

        prompt_str = str(input_data)
        llm_span.set_attribute("gen_ai.prompt", prompt_str[:1000] + "..." if len(prompt_str) > 1000 else prompt_str)

        try:
            start_time = time.time()
            result = chain.invoke(input_data)
            end_time = time.time()
            duration = end_time - start_time

            content = result.content
            llm_span.set_attribute("gen_ai.completion", content[:1000] + "..." if len(content) > 1000 else content)
            llm_span.set_attribute("gen_ai.response.model", groq_llm.model_name)
            llm_span.set_attribute("gen_ai.usage.prompt_tokens", len(prompt_str))
            llm_span.set_attribute("gen_ai.usage.completion_tokens", len(content))
            llm_span.set_attribute("gen_ai.usage.total_tokens", len(prompt_str) + len(content))
            llm_span.set_attribute("gen_ai.response.duration", duration)
            # Add other metadata like finish reason if available from result.response_metadata
            # if hasattr(result, 'response_metadata') and result.response_metadata:
            #     finish_reason = result.response_metadata.get('finish_reason')
            #     if finish_reason:
            #         llm_span.set_attribute("gen_ai.response.finish_reasons", [str(finish_reason)])

            llm_span.set_status(Status(StatusCode.OK))
            return result
        except Exception as e:
            logger.exception("Groq call failed: %s", e)
            llm_span.record_exception(e)
            llm_span.set_status(Status(StatusCode.ERROR, description=f"Groq call failed: {e}"))
            raise

def content_structurer(state: GraphState) -> GraphState:
    """Generate a blog-style course structure from the given transcript."""
    logger.info("Running Content Structurer Node")
    with tracer.start_as_current_span("Content Structurer") as span:
        span.add_event("content_structurer_start", attributes={"message": "Content Structurer Start"})
        span.set_attribute("input.transcript_length", len(state.get("transcript", "")))

        prompt_template = """
        Analyze the following video transcript and create a detailed, blog-style course structure.  
        The goal is to create a structure that is easy to understand and helps in selecting relevant images later.

        1. **Identify Main Modules:** Divide the video into distinct modules. Each module should represent a major topic or theme.
        -  Use **highly descriptive and specific module titles**.  Avoid generic titles like "Module 1" or "Introduction."  
        -  Example:  Instead of "Introduction", use "Famotidine:  Understanding the Basics and Mechanism of Action".

        2. **Structure within Modules (Sections):**  Within each module, identify logical sections.
        - Use **highly descriptive and specific section titles**.  Avoid generic titles like "Section 1" or "Overview."
        - Example: Instead of "Section 1", use "What is Famotidine and What Conditions Does it Treat?".
        - Provide accurate `start_ts` and `end_ts` (timestamps) for each section.

        3. **Extract Global Concepts:** Identify 5-10 key overarching concepts that are central to the entire video. These should be concise and informative.

        4. **Output Format:**  Output the structure as plain JSON, *without* any markdown formatting or code blocks. Follow the exact format below:

        ```json
        {{
            "modules": [
                {{
                    "module_title": "Descriptive Module Title Here",
                    "sections": [
                        {{
                            "section_title": "Descriptive Section Title Here",
                            "start_ts": 0.00,  
                            "end_ts": 10.50,
                            frames: [],//return it empty for now
                        }},
                        ... more sections ...
                    ]
                }},
                ... more modules ...
            ],
            "global_concepts": ["Concept 1", "Concept 2", ...]
        }}

        Transcript:
        {transcript}
        """
        prompt = PromptTemplate.from_template(prompt_template)
        chain = prompt | gemini_llm # pylint: disable=unsupported-binary-operation

        try:
            response = call_gemini_with_retry(
                chain,
                {"transcript": state["transcript"]},
                span_name="Content Structure Generation",
            )

            logger.info("Content Structurer output: %s", response)
            cleaned_content = clean_json_string(response.content)
            structured_content_json = json.loads(cleaned_content)
            state["structured_content"] = structured_content_json

            span.set_attribute("output.module_count", len(structured_content_json.get("modules", [])))
            span.set_attribute("output.global_concepts_count", len(structured_content_json.get("global_concepts", [])))
            span.add_event("content_structurer_success", attributes={"message": "Content Structurer Succeeded"})
            span.set_status(Status(StatusCode.OK))

        except (json.JSONDecodeError, ValueError, google.api_core.exceptions.GoogleAPIError) as e:
            logger.exception("Content Structurer error: %s", e)
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, description=f"Content Structurer error: {e}"))
            span.add_event("content_structurer_error",
                            attributes={"error.message": str(e)})
            state["structured_content"] = {}
    return state


def frame_selector(state: GraphState) -> GraphState:
    """Select frames from the video based on relevancy, avoiding repetition across the module."""
    logger.info("Running Frame Selector Node")
    with tracer.start_as_current_span("Frame Selector") as span:
        span.add_event("frame_selector_start", attributes={"message": "Frame Selector Start"})
        span.set_attribute("input.initial_frame_count", len(state.get("frames", [])))
        span.set_attribute("input.module_count", len(state.get("structured_content", {}).get("modules", [])))

        frames = state.get("frames", [])
        structured_content = state.get("structured_content", {"modules": []})
        course_content = state.get("course_content", {"modules": []})
        transcript = state.get("transcript", "")

        selected_frames_with_info = []
        logger.info("Initial frames to assess: %d", len(frames))

        if not structured_content or not structured_content.get("modules"):
            logger.warning("No structured content found. Skipping frame selection.")
            span.add_event("frame_selector_skipped", attributes={"reason": "No structured content"})
            span.set_status(Status(StatusCode.OK))
            return state

        total_frames_processed = 0
        total_frames_selected = 0

        for module_index, module in enumerate(structured_content["modules"]):
            module_start_ts_str = module.get("sections", [{}])[0].get("start_ts", "0")
            module_end_ts_str = module.get("sections", [{}])[-1].get("end_ts", "0")
            try:
                module_start_ts = int(float(module_start_ts_str)) if module_start_ts_str else 0
                module_end_ts = int(float(module_end_ts_str)) if module_end_ts_str else 0
                module_content = transcript[module_start_ts:module_end_ts]
            except (ValueError, TypeError) as ts_err:
                logger.error(f"Error processing timestamps for module {module_index}: {ts_err}. Skipping module.")
                span.add_event("timestamp_error", attributes={"module_index": module_index, "error": "%s" % ts_err})
                continue


            module_previous_captions = []
            for sec in module.get("sections", []):
                module_previous_captions.extend([f.get("caption", "") for f in sec.get("frames", []) if f.get("caption")])


            for section_index, section in enumerate(module.get("sections", [])):
                start_ts_str = section.get("start_ts", "0")
                end_ts_str = section.get("end_ts", "0")
                section_title = section.get("section_title", f"Untitled Section {section_index}")
                try:
                    start_ts = float(start_ts_str) if start_ts_str else 0.0
                    end_ts = float(end_ts_str) if end_ts_str else start_ts # Avoid negative range
                    section_content = transcript[int(start_ts):int(end_ts)]
                except (ValueError, TypeError) as ts_err:
                    logger.error(f"Error processing timestamps for section '{section_title}': {ts_err}. Skipping section.")
                    span.add_event("timestamp_error", attributes={"module_index": module_index, "section_index": section_index, "error": str(ts_err)})
                    continue

                section_frames_paths = []

                # --- Get corresponding generated content ---
                generated_section_content = ""
                if course_content and course_content.get("modules"):
                    try:
                        generated_section_content = course_content["modules"][module_index]["sections"][section_index]["content"]
                    except (KeyError, IndexError) as e:
                        logger.warning("Could not retrieve generated content for module %d, section %d: %s", module_index, section_index, e)
                        span.add_event("get_generated_content_warning", attributes={"module": module_index, "section": section_index, "error": str(e)})
                # --- End ---


                for frame in frames:
                    try:
                        frame_ts = float(frame.get("timestamp", -1)) # Use get with default
                        if start_ts <= frame_ts <= end_ts:
                            section_frames_paths.append(frame) # Store the whole frame dict
                    except (ValueError, TypeError) as ts_err:
                        logger.warning(f"Invalid timestamp for frame {frame.get('path')}: {ts_err}. Skipping frame.")
                        span.add_event("invalid_frame_timestamp", attributes={"frame_path": frame.get("path", "N/A"), "error": str(ts_err)})

                if "frames" not in section:
                    section["frames"] = []  # Initialize if missing

                if section_frames_paths:
                    logger.info("Processing %d potential frames for section: '%s'", len(section_frames_paths), section_title)
                    span.add_event("processing_section_frames", attributes={
                        "module": module_index, "section": section_index, "section_title": section_title, "frame_count": len(section_frames_paths)
                    })

                    for frame_index, frame_data in enumerate(section_frames_paths):
                        total_frames_processed += 1
                        frame_path = frame_data.get("path")
                        frame_timestamp = frame_data.get("timestamp")
                        if not frame_path or frame_timestamp is None:
                            logger.warning(f"Skipping frame with missing path or timestamp in section '{section_title}'. Data: {frame_data}")
                            span.add_event("skipping_incomplete_frame", attributes={"section_title": section_title, "frame_data": str(frame_data)})
                            continue

                        logger.info("Processing frame: %s (Index within section: %d) for section: '%s'", frame_path, frame_index, section_title)

                        image_path = os.path.abspath(frame_path)
                        base64_image = encode_image(image_path)
                        if not base64_image:
                            logger.warning("Skipping frame %s due to encoding failure.", frame_path)
                            span.add_event("frame_encoding_failure", attributes={"frame_path": frame_path})
                            continue

                        image_data_item = {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{base64_image}"},
                        }

                        user_prompt_text = (
                            "**You MUST respond *only* with valid JSON.  No other text is permitted.**\n\n"
                            "You are an expert image selector for educational content. Your task is to analyze an image and determine its "
                            "relevance and distinctiveness within the context of a specific section of a learning module.  "
                            "You will be provided with the module content, section content, generated section content, "
                            "and a list of captions from previously selected images for *this entire module*.\n\n"
                            f"**Module Content:**\n{module_content}\n\n"
                            f"**Section Content (Transcript):**\n{section_content}\n\n"
                            f"**Generated Section Content (Refined):**\n{generated_section_content}\n\n"
                            f"**Captions of Previously Selected Images (for this ENTIRE MODULE):**\n{module_previous_captions}\n\n"
                            "**Image Analysis Task:**\n\n"
                            "1. **Relevance:** Assess whether the image directly supports, illustrates, or clarifies the concepts "
                            "   discussed in the *section content* (both transcript and generated) and the overall *module content*.  "
                            "   Consider whether the image adds educational value to the section.\n\n"
                            "2. **Distinctiveness:**  Carefully compare the current image to the captions of previously selected images *for the entire module*.  "
                            "   Determine if the current image offers *new* visual information or a *different perspective* compared to "
                            "   *any* of the previously selected images *in this module*.  Avoid selecting images that are visually similar, redundant, "
                            "   or convey essentially the same information as a previously selected image *anywhere in the module*.\n\n"
                            "   *  Consider the visual elements, composition, and the overall message conveyed by the image.\n"
                            "   *  Even if an image is relevant, if it's too similar to a previous image *from any section in this module*, it should be rejected.\n\n"
                            "3. **Reasoning (Chain-of-Thought):**\n"
                            "    * Briefly explain *why* you are making your decision. Focus your reasoning on both RELEVANCE and DISTINCTIVENESS (or lack thereof), and refer to specific points within the content and captions (if rejecting).\n\n"
                            "**Output (JSON Format - Strictly Adhere):**\n\n"
                            "Respond with *only* one of the following JSON objects.  Do not include any other text.\n\n"
                            "*   **If the image is BOTH relevant AND distinct:**\n"
                            "    ```json\n"
                            "    {\"relevant\": true, \"info\": \"Your concise reasoning (1-2 sentences).\", \"caption\": \"A short, descriptive caption for the image (1-2 sentences).\"}\n"
                            "    ```\n\n"
                            "*   **If the image is NOT relevant OR NOT distinct:**\n"
                            "    ```json\n"
                            "    {\"relevant\": false, \"info\": \"Your concise reasoning (1-2 sentences).\"}\n"
                            "    ```\n\n"

                            "**Example Reasoning (Relevant):**\n"
                            "```json\n"
                            "{\"relevant\": true, \"info\": \"The image clearly depicts the experimental setup described in the section, providing a visual aid for understanding the procedure.\", \"caption\": \"Experimental setup showing the sensor and data acquisition system.\"}\n"
                            "```\n"

                            "**Example Reasoning (Not Relevant):**\n"
                            "```json\n"
                            "{\"relevant\": false, \"info\": \"The image shows a generic graph, but it doesn't directly relate to the specific data analysis techniques discussed in this section.\"}\n"
                            "```\n"

                            "**Example Reasoning (Not Distinct):**\n"
                            "```json\n"
                            "{\"relevant\": false, \"info\": \"This image is very similar to a previously selected image from Section 1 that also showed the overall system architecture. It doesn't add new visual information.\"}\n"  # Example now refers to another section
                            "```\n"
                        )

                        message_content = [
                            {"type": "text", "text": user_prompt_text},
                            image_data_item,
                        ]
                        message_to_llama = [HumanMessage(content=message_content)]
                        llm_span_name = f"Frame Relevance - M{module_index} S{section_index} F{frame_index}"

                        try:
                            # Nested call to Groq using helper
                            response = call_groq_with_retry(
                                groq_llm, # Use the LLM instance directly
                                message_to_llama,
                                span_name=llm_span_name,
                                # parent_span=span # Not needed
                            )
                            cleaned_response = clean_json_string(response.content)
                            logger.info("Frame processing response for %s: %s", frame_path, cleaned_response)
                            result = json.loads(cleaned_response)

                            if isinstance(result, dict) and result.get("relevant") is True:
                                if "info" in result and "caption" in result:
                                    new_frame_entry = {
                                        "frame_path": frame_path,
                                        "caption": result["caption"],
                                        "info": result["info"],
                                        "timestamp": float(frame_timestamp), # Ensure float
                                    }
                                    # Append to the section in structured_content FIRST
                                    # This is crucial because module_previous_captions relies on it
                                    state["structured_content"]["modules"][module_index]["sections"][section_index]["frames"].append(new_frame_entry)

                                    # Add to overall selected list (if needed, though maybe redundant now)
                                    selected_frames_with_info.append(frame_data) # Keep original frame data? Or the new entry? Let's use new.
                                    # selected_frames_with_info.append(new_frame_entry)

                                    # Add new caption to module-level list for subsequent checks in THIS module
                                    module_previous_captions.append(result["caption"])
                                    total_frames_selected += 1
                                    logger.info("Frame %s (%s) selected for section: %s", frame_path, frame_timestamp, section_title)
                                    span.add_event("frame_selected", attributes={
                                        "frame_path": frame_path, "section_title": section_title, "caption": result["caption"]
                                    })
                                else:
                                    logger.warning(f"Relevant frame missing info/caption in response for {frame_path}: {result}")
                                    span.add_event("frame_relevant_missing_data", attributes={"frame_path": frame_path, "response": str(result)})
                            else:
                                logger.info("Frame %s deemed not relevant/distinct for section '%s'. Reason: %s", frame_path, section_title, result.get("info", "N/A"))
                                span.add_event("frame_rejected", attributes={"frame_path": frame_path, "section_title": section_title, "reason": result.get("info", "N/A")})


                        except (json.JSONDecodeError, ValueError, KeyError) as e:
                            logger.exception("Frame processing JSON/Key error for %s: %s", frame_path, e)
                            span.add_event("frame_selection_error", attributes={"frame_path": frame_path, "error": str(e)})
                            span.record_exception(e) # Optionally record the exception
                        except Exception as e: # Catch potential Groq API errors if not caught by retry helper
                            logger.exception("Frame processing unexpected error for %s: %s", frame_path, e)
                            span.add_event("frame_selection_unexpected_error", attributes={"frame_path": frame_path, "error": str(e)})
                            span.record_exception(e)

                else: # if not section_frames_paths:
                    logger.info("No frames found in timestamp range for section: %s", section_title)
                    span.add_event("no_frames_in_range", attributes={"section_title": section_title})

        # Add selected frames to course_content AFTER processing all structured_content
        if course_content and course_content.get("modules"):
            for module_idx, module_data in enumerate(course_content["modules"]):
                for section_idx, section_data in enumerate(module_data.get("sections", [])):
                    try:
                        # Get the frames added during the processing above
                        structured_section = structured_content["modules"][module_idx]["sections"][section_idx]
                        # Ensure 'media' key exists in the target course_content section
                        if "media" not in state["course_content"]["modules"][module_idx]["sections"][section_idx]:
                            state["course_content"]["modules"][module_idx]["sections"][section_idx]["media"] = []

                        state["course_content"]["modules"][module_idx]["sections"][section_idx]["media"] = structured_section.get("frames", [])

                    except (KeyError, IndexError) as e:
                        logger.warning("Could not find matching structured/course section for final media update: Mod %d, Sec %d. Error: %s", module_idx, section_idx, e)
                        span.add_event("media_update_match_error", attributes={"module": module_idx, "section": section_idx, "error": str(e)})


        span.set_attribute("output.total_frames_processed", total_frames_processed)
        span.set_attribute("output.total_frames_selected", total_frames_selected)
        span.add_event("frame_selector_completed", attributes={"message": "Frame selection complete"})
        span.set_status(Status(StatusCode.OK))
        # Update state['frames'] ? Maybe it's better to let course_content hold the final selected media?
        # If state['frames'] is needed later, update it with 'selected_frames_with_info'
        state["frames"] = selected_frames_with_info # Re-evaluate if this overwrite is desired
    return state

def course_content_generator(state: GraphState) -> GraphState:
    """Generate course content based on structured content."""
    logger.info("Running Course Content Generator Node")
    with tracer.start_as_current_span("Course Content Generator") as span:
        span.add_event("course_content_generation_start", attributes={"message": "Course Content Generation Start"})
        structured_content = state.get("structured_content")
        transcript = state.get("transcript", "")

        if not structured_content or not structured_content.get("modules"):
            logger.warning("No structured content available. Skipping course content generation.")
            span.add_event("course_content_skipped", attributes={"reason": "No structured content"})
            state["course_content"] = {"modules": []}
            span.set_status(Status(StatusCode.OK)) # Successfully did nothing
            return state

        span.set_attribute("input.module_count", len(structured_content.get("modules", [])))

        system_message = """
        You are an Educational Content Designer. Create blog-style course content based on the provided structure, transcript.

        Output JSON in the specified format. Do NOT include markdown code blocks.
        """
        prompt_template_str = """
        {structured_content}
        Transcript: {transcript}

        For each section:
        1.  Write a 300-500 word explanation of the concept.
        2.  If frames are provided for the section, include a "media" field with the frame_path and caption *exactly* as provided.
        3. Include a "Key Insight" callout.
        4. Include timestamps (only where required) in the generated section content (format: [HH:MM:SS]).

        Output JSON:
        {{        
            "modules": [
                {{
                    "module_title": "...",
                    "sections": [
                        {{
                            "section_title": "...",
                            "content": "Markdown formatted content...",
                            "media": [{{ "frame_path": "...", "caption": "...", "info": "...", "timestamp": x.xx }}],
                        }},
                        ...
                    ]
                }},
                ...
            ]
        }}
        """

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_message),
            ("user", prompt_template_str)
        ])
        chain = prompt | gemini_llm # pylint: disable=unsupported-binary-operation

        try:
            course_content_response = call_gemini_with_retry(
                chain,
                {
                    "structured_content": json.dumps(structured_content, indent=2),
                    "transcript": transcript,
                },
                span_name="Course Content LLM Generation",
            )
            cleaned_content = clean_json_string(course_content_response.content)
            logger.info("Course Content Generation output: %s", cleaned_content)
            generated_data = json.loads(cleaned_content)
            state["course_content"] = generated_data

            span.add_event("course_content_generation_success", attributes={"message": "Course Content Generation Succeeded"})
            span.set_attribute("output.module_count", len(generated_data.get("modules", [])))
            span.set_status(Status(StatusCode.OK))

        except (json.JSONDecodeError, ValueError, google.api_core.exceptions.GoogleAPIError) as e:
            logger.exception("Course Content Generation error: %s", e)
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, description=f"Course Content Generation error: {e}"))
            span.add_event("course_content_error", attributes={"error.message": str(e)})
            state["course_content"] = {"modules": []}
    return state

def quiz_architect(state: GraphState) -> GraphState:
    """Generate quiz questions based on the structured content."""
    logger.info("Running Quiz Architect Node")
    with tracer.start_as_current_span("Quiz Architect") as span:
        span.add_event("quiz_architect_start", attributes={"message": "Quiz Architect Start"})
        structured_content = state.get("structured_content")

        if not structured_content or not structured_content.get("modules"):
            logger.warning("No structured content available. Skipping quiz generation.")
            span.add_event("quiz_architect_skipped", attributes={"reason": "No structured content"})
            state["quiz_content"] = {"quizzes": []}
            span.set_status(Status(StatusCode.OK))
            return state

        span.set_attribute("input.module_count", len(structured_content.get("modules", [])))

        prompt_template_str = """
        You are an Assessment Designer creating quizzes for a blog-style course. Design multiple-choice questions (MCQs) for each section based on the provided structured content.

        Quiz Requirements:

        Placement: Generate 1-2 relevant MCQs for *each section* within the modules.
        Question Details: For each MCQ, provide:
            - type: "multiple_choice"
            - text: The question text.
            - options: A list of 4 plausible answer strings (one correct, three distractors).
            - correct_answer: The exact string of the correct option.
            - rationale: A brief explanation (1-2 sentences) why the correct answer is right and the others are wrong.
            - review_timestamp: A relevant timestamp from the section's `start_ts` or `end_ts` (formatted as HH:MM:SS like [00:05:30]) to help learners find the answer in the video. Use the `start_ts` if possible.
            - difficulty: A numerical rating from 1 (easy) to 5 (hard).

        Output Format: Plain JSON. Do NOT include markdown code blocks. Ensure the JSON is valid.

        {{
        "quizzes": [
            {{
                "module_title": "...", // Match the module title from input
                "section_title": "...", // Match the section title from input
                "questions": [
                    {{
                        "type": "multiple_choice",
                        "text": "What is the primary mechanism of action for Famotidine?",
                        "options": ["Proton pump inhibitor", "H2 receptor antagonist", "Antacid", "Antibiotic"],
                        "correct_answer": "H2 receptor antagonist",
                        "rationale": "Famotidine selectively blocks H2 receptors on parietal cells, reducing gastric acid secretion. It's not a PPI, antacid, or antibiotic.",
                        "review_timestamp": "[00:01:15]", // Example timestamp from the section
                        "difficulty": 2
                    }},
                    // ... potentially another question for the same section ...
                ]
            }},
            // ... more sections/modules ...
        ]
        }}

        Structured Content:
        {structured_content_json}
        """

        prompt = PromptTemplate.from_template(prompt_template_str)
        chain = prompt | gemini_llm # pylint: disable=unsupported-binary-operation

        try:
            quiz_content_response = call_gemini_with_retry(
                chain,
                {"structured_content_json": json.dumps(structured_content, indent=2)}, # Pass as JSON string
                span_name="Quiz Generation",
                # parent_span=span
            )
            cleaned_content = clean_json_string(quiz_content_response.content)
            logger.info("Quiz Architect output received.")
            # logger.debug("Quiz Architect output: %s", cleaned_content)
            quiz_data = json.loads(cleaned_content)
            state["quiz_content"] = quiz_data
            span.add_event("quiz_architect_success", attributes={"message": "Quiz Architect Succeeded"})
            span.set_attribute("output.quiz_count", len(quiz_data.get("quizzes", [])))
            total_questions = sum(len(q.get("questions", [])) for q in quiz_data.get("quizzes", []))
            span.set_attribute("output.total_questions", total_questions)
            span.set_status(Status(StatusCode.OK))

        except (json.JSONDecodeError, ValueError, google.api_core.exceptions.GoogleAPIError) as e:
            logger.exception("Quiz Architect error: %s", e)
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, description=f"Quiz Architect error: {e}"))
            span.add_event("quiz_architect_error", attributes={"error.message": str(e)})
            state["quiz_content"] = {"quizzes": []}
    return state

def retention_designer(state: GraphState) -> GraphState:
    """Design a retention plan based on structured content."""
    logger.info("Running Retention Designer Node")
    with tracer.start_as_current_span("Retention Designer") as span:
        span.add_event("retention_designer_start", attributes={"message": "Retention Designer Start"})
        structured_content = state.get("structured_content")

        if not structured_content or not structured_content.get("modules"):
            logger.warning("No structured content available. Skipping retention design.")
            span.add_event("retention_designer_skipped", attributes={"reason": "No structured content"})
            state["retention_plan"] = {"retention_plan": {"module_retention": [], "overall_summary": ""}}
            span.set_status(Status(StatusCode.OK))
            return state

        span.set_attribute("input.module_count", len(structured_content.get("modules", [])))

        prompt_template_str = """
        You are a Learning Experience Designer focused on enhancing retention. Design a retention plan, focusing on text-based strategies, for a course based on the provided structured content.

        Retention Features (at the MODULE level):

        For *each module* in the structured content:
        - retention_tips: Generate 2-3 concise tips. Each tip MUST have a specific "type" from the allowed list and a "description" that clearly states *what* the learner should do or create, related to the module's content.
            - Allowed `type` values: analogy, real_world_example, table_creation, mnemonic_device, categorization, prioritization, role_playing, example, explanation, summary, comparison, question_generation.
            - The `description` should be an instruction for the *next* agent (Retention Tip Executor) to fulfill. Example (for table_creation): "Create a table comparing the side effects of Drug A and Drug B discussed in this module." Example (for analogy): "Develop an analogy to explain the concept of receptor binding from this module."
        - spaced_repetition_prompts: Generate 1-2 short questions or prompts (just the text) designed for later review of the module's key concepts.
        - scenario_examples: Generate 1-2 brief (1-3 sentence) scenarios illustrating the application of concepts from the module.

        Overall Retention Feature (at the end):
        - overall_summary: Generate a concise text-based summary of the *entire* course content based on the structured content. If appropriate, include comparison tables within the summary using Markdown.

        Output Format: Plain JSON. Do NOT include markdown code blocks. Ensure the JSON is valid.

        {{
            "retention_plan": {{
                "module_retention": [
                    {{
                        "module_title": "...", // Match module title from input
                        "retention_tips": [
                            {{
                                "type": "analogy",  // MUST be one of the allowed types
                                "description": "Develop an analogy comparing the H2 blocking mechanism to a gatekeeper for acid production." // Actionable instruction for the executor agent
                            }},
                            {{
                                "type": "table_creation",
                                "description": "Create a markdown table summarizing the common use cases and typical dosages for Famotidine mentioned in this module."
                            }}
                            // ... more tips if applicable (max 3) ...
                        ],
                        "spaced_repetition_prompts": [
                            "What are the main differences between Famotidine and PPIs?",
                            "Recall the key side effect mentioned for Famotidine."
                            // ... more prompts if applicable (max 2) ...
                        ],
                        "scenario_examples": [
                            "A patient complains of frequent heartburn after meals. Based on this module, when might Famotidine be considered?",
                            "Imagine explaining to a friend how Famotidine works using a simple non-medical analogy."
                            // ... more scenarios if applicable (max 2) ...
                        ]
                    }},
                    // ... more modules ...
                ],
                "overall_summary": "This course covered the fundamentals of Famotidine, including its mechanism as an H2 receptor antagonist, common uses for conditions like GERD and ulcers, typical dosages, and potential side effects. Key differences from other acid reducers like PPIs were highlighted. [Include Markdown table here if useful, e.g., comparing features]" // Overall summary text
            }}
        }}

        Structured Content:
        {structured_content_json}
"""
        prompt = PromptTemplate.from_template(prompt_template_str)
        chain = prompt | gemini_llm  # pylint: disable=unsupported-binary-operation

        try:
            retention_plan_response = call_gemini_with_retry(
                chain,
                {"structured_content_json": json.dumps(structured_content, indent=2)},
                span_name="Retention Plan Generation",
                # parent_span=span
            )
            cleaned_content = clean_json_string(retention_plan_response.content)
            logger.info("Retention Designer output received.")
            # logger.debug("Retention Designer output: %s", cleaned_content)
            retention_data = json.loads(cleaned_content)
            state["retention_plan"] = retention_data

            span.add_event("retention_design_success", attributes={"message": "Retention Designer Succeeded"})
            span.set_attribute("output.module_retention_count", len(retention_data.get("retention_plan", {}).get("module_retention", [])))
            span.set_attribute("output.has_summary", bool(retention_data.get("retention_plan", {}).get("overall_summary")))
            span.set_status(Status(StatusCode.OK))

        except (json.JSONDecodeError, ValueError, google.api_core.exceptions.GoogleAPIError) as e:
            logger.exception("Retention Designer error: %s", e)
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, description=f"Retention Designer error: {e}"))
            span.add_event("retention_design_error", attributes={"error.message": str(e)})
            state["retention_plan"] = {"retention_plan": {"module_retention": [], "overall_summary": ""}}
    return state

def retention_tip_executor(state: GraphState) -> GraphState:
    """Execute retention tips by generating additional content based on tip type."""
    logger.info("Running Retention Tip Executor Node")
    with tracer.start_as_current_span("Retention Tip Executor") as span:
        span.add_event("retention_tip_executor_start", attributes={"message": "Retention Tip Executor Start"})

        retention_plan_data = state.get("retention_plan")
        if not retention_plan_data or "retention_plan" not in retention_plan_data or not retention_plan_data["retention_plan"].get("module_retention"):
            logger.warning("No valid retention plan data or module retention found. Skipping Retention Tip Executor.")
            span.add_event("retention_plan_missing_or_invalid", attributes={"reason": "No valid retention plan data"})
            span.set_status(Status(StatusCode.OK))
            return state

        modules_retention = retention_plan_data["retention_plan"]["module_retention"]
        span.set_attribute("input.module_retention_count", len(modules_retention))
        total_tips_processed = 0
        total_tips_executed = 0
        total_tips_failed = 0

        # --- Define prompts OUTSIDE the loop ---
        tip_prompts = {
            "table_creation": PromptTemplate.from_template("Based on the module's content, fulfill this request: {instruction}. Respond with *only* the Markdown table. Do not add explanations before or after."),
            "real_world_example": PromptTemplate.from_template("Based on the module's content, provide a real-world example as described: {instruction}. Respond concisely in 2-3 sentences."),
            "analogy": PromptTemplate.from_template("Based on the module's content, expand on the following analogy: {instruction}. Explain the analogy clearly in 2-3 sentences."),
            "mnemonic_device": PromptTemplate.from_template("Explain this mnemonic device and how it relates to the module's content: {instruction}. Explain clearly in 2-3 sentences."),
            "categorization": PromptTemplate.from_template("Based on the module's content, provide an example of categorization as requested: {instruction}. Give a short example in 2-3 sentences or a brief list."),
            "prioritization": PromptTemplate.from_template("Based on the module's content, explain how to prioritize as described: {instruction}. Explain clearly in 2-3 sentences."),
            "role_playing": PromptTemplate.from_template("Based on the module's content, describe a brief role-playing scenario as requested: {instruction}. Describe the scenario (2-4 sentences)."),
            "example": PromptTemplate.from_template("Based on the module's content, provide an example as requested: {instruction}. Respond concisely in 2-3 sentences."),
            "explanation": PromptTemplate.from_template("Based on the module's content, provide an explanation as requested: {instruction}. Respond clearly in 2-3 sentences."),
            "summary": PromptTemplate.from_template("Based on the module's content, provide a summary as requested: {instruction}. Respond concisely in 2-3 sentences."),
            "comparison": PromptTemplate.from_template("Based on the module's content, provide a comparison as requested: {instruction}. Respond clearly in 2-3 sentences or a brief list/table if appropriate."),
            "question_generation": PromptTemplate.from_template("Based on the module's content, generate 1-2 questions related to: {instruction}. Provide *only* the questions, not the answers."),
        }
        # --- End prompt definitions ---


        for module_index, module_retention in enumerate(modules_retention):
            module_title = module_retention.get("module_title", f"Module {module_index}")
            for tip_index, retention_tip_dict in enumerate(module_retention.get("retention_tips", [])):
                total_tips_processed += 1
                prompt = None
                executed_content = "Not Executed" # Default
                tip_type = retention_tip_dict.get("type", "Unknown")
                instruction = retention_tip_dict.get("description", "")

                if not instruction:
                    logger.warning(f"Skipping tip {tip_index} in module '{module_title}' due to missing description.")
                    span.add_event("tip_skipped_no_description", attributes={"module": module_title, "tip_index": tip_index, "tip_type": tip_type})
                    retention_tip_dict["executed_content"] = "Skipped - No Description"
                    continue

                if tip_type in tip_prompts:
                    prompt = tip_prompts[tip_type]
                else:
                    logger.warning(f"Unknown retention tip type '{tip_type}' for instruction: {instruction}")
                    span.add_event(f"unknown_tip_type", attributes={"tip_type": tip_type, "module": module_title})
                    retention_tip_dict["executed_content"] = f"Skipped - Unknown Type: {tip_type}"
                    continue

                if prompt:
                    chain = prompt | gemini_llm # pylint: disable=unsupported-binary-operation
                    llm_span_name = f"Retention Tip Exec - {tip_type} - M{module_index} T{tip_index}"

                    try:
                        response = call_gemini_with_retry(
                            chain,
                            {"instruction": instruction},
                            span_name=llm_span_name,
                            # parent_span=span
                        )
                        executed_content = response.content
                        retention_tip_dict["executed_content"] = executed_content # Update the dict IN PLACE
                        total_tips_executed += 1
                        logger.info("Executed retention tip '%s' for module '%s'.", tip_type, module_title)
                        span.add_event("retention_tip_execution_success", attributes={"tip_type": tip_type, "module": module_title})
                    except (google.api_core.exceptions.GoogleAPIError, json.JSONDecodeError, ValueError) as e:
                        logger.exception("Retention tip execution error (%s) for module '%s': %s", tip_type, module_title, e)
                        executed_content = f"Error executing tip: {e}"
                        retention_tip_dict["executed_content"] = executed_content # Update dict in place with error
                        total_tips_failed += 1
                        span.add_event(f"retention_tip_error", attributes={"tip_type": tip_type, "module": module_title, "error": str(e)})
                        span.record_exception(e) # Record exception on the main node span


        span.set_attribute("output.total_tips_processed", total_tips_processed)
        span.set_attribute("output.total_tips_executed", total_tips_executed)
        span.set_attribute("output.total_tips_failed", total_tips_failed)
        span.set_status(Status(StatusCode.OK if total_tips_failed == 0 else StatusCode.ERROR if total_tips_processed > 0 else StatusCode.OK))
        span.add_event("retention_tip_executor_finished")
        # State is updated because we modified retention_tip_dict in place within retention_plan_data
        state["retention_plan"] = retention_plan_data # Assign back to state explicitly for clarity
    return state

graph = StateGraph(GraphState)
graph.add_node("content_structurer", content_structurer)
graph.add_node("course_content_generator", course_content_generator)
graph.add_node("frame_selector", frame_selector)
graph.add_node("quiz_architect", quiz_architect)
graph.add_node("retention_designer", retention_designer)
graph.add_node("retention_tip_executor", retention_tip_executor)
graph.add_edge("content_structurer", "course_content_generator")
graph.add_edge("course_content_generator", "frame_selector")
graph.add_edge("frame_selector", "quiz_architect")
graph.add_edge("quiz_architect", "retention_designer")
graph.add_edge("retention_designer", "retention_tip_executor")
graph.add_edge("retention_tip_executor", END)
graph.set_entry_point("content_structurer")

app = graph.compile(checkpointer=memory)
