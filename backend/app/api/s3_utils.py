# app/api/s3_utils.py
import logging
import os
import boto3
from typing import Union
from botocore.exceptions import ClientError, NoCredentialsError
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Configuration from environment variables
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1") # Default region if not set
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET_NAME]):
    logger.warning("AWS S3 credentials or bucket name not fully configured. S3 operations will fail.")
    s3_client = None
else:
    try:
        session = boto3.Session(
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION
        )
        s3_client = session.client('s3')
        # Test connection by listing buckets (optional, requires ListBuckets permission)
        # s3_client.list_buckets()
        logger.info("AWS S3 client initialized successfully for region %s and bucket %s", AWS_REGION, S3_BUCKET_NAME)
    except NoCredentialsError:
        logger.error("AWS credentials not found. Please configure AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.")
        s3_client = None
    except ClientError as e:
        logger.error("Failed to initialize AWS S3 client: %s", e)
        s3_client = None
    except Exception as e:
        logger.error("An unexpected error occurred during S3 client initialization: %s", e)
        s3_client = None


def upload_frame_to_s3(local_file_path: str, video_id: str, frame_filename: str) -> Union[str, None]:
    """
    Uploads a local frame file to the configured S3 bucket.

    Args:
        local_file_path: The path to the local frame image file.
        video_id: The YouTube video ID, used for structuring the S3 path.
        frame_filename: The base filename of the frame.

    Returns:
        The S3 URL of the uploaded file, or None if upload failed.
    """
    if not s3_client or not S3_BUCKET_NAME:
        logger.error("S3 client or bucket name not configured. Cannot upload.")
        return None

    s3_key = f"frames/{video_id}/{frame_filename}"
    s3_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"

    try:
        # Check if the file exists locally before uploading
        if not os.path.exists(local_file_path):
            logger.error("Local file not found, cannot upload to S3: %s", local_file_path)
            return None

        logger.info("Uploading %s to S3 bucket %s as %s", local_file_path, S3_BUCKET_NAME, s3_key)
        s3_client.upload_file(
            local_file_path,
            S3_BUCKET_NAME,
            s3_key,
            ExtraArgs={'ContentType': 'image/png'} # Adjust content type if needed (e.g., image/jpeg)

        )
        logger.info("Successfully uploaded to S3: %s", s3_url)
        return s3_url
    except ClientError as e:
        logger.exception("Failed to upload %s to S3: %s", local_file_path, e)
        return None
    except FileNotFoundError:
         logger.error("FileNotFoundError during S3 upload attempt (should have been caught earlier): %s", local_file_path)
         return None
    except Exception as e:
        logger.exception("An unexpected error occurred during S3 upload for %s: %s", local_file_path, e)
        return None