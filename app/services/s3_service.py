"""S3 Service for HR Assistant.

Handles uploading, downloading, and presigned URL generation for documents,
policies, and videos stored in AWS S3.
Location: app/services/s3_service.py
"""

import logging
import os
import tempfile
from typing import Optional
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_s3_client = None


def get_s3_client():
    """Lazily initializes and returns the boto3 S3 client."""
    global _s3_client
    if _s3_client is not None:
        return _s3_client

    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    aws_region = os.getenv("AWS_REGION", "ap-southeast-2")

    if aws_access_key and aws_secret_key:
        _s3_client = boto3.client(
            "s3",
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region,
        )
    else:
        logger.warning("AWS credentials missing from environment. Using default credential chain.")
        _s3_client = boto3.client("s3", region_name=aws_region)

    return _s3_client


def get_bucket_name() -> str:
    """Retrieves S3 bucket name from environment variables."""
    return os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_BUCKET_NAME") or ""


def upload_salary_to_s3(file_obj, filename: str) -> Optional[str]:
    """Upload salary slip PDF to S3 under salary_slips/ directory."""
    s3 = get_s3_client()
    bucket = get_bucket_name()
    if not bucket:
        logger.error("UPLOAD_SALARY_FAILED | S3_BUCKET_NAME not set")
        return None

    if hasattr(file_obj, "seek"):
        file_obj.seek(0)

    key = f"salary_slips/{uuid4()}_{filename}"
    try:
        s3.upload_fileobj(
            file_obj, bucket, key, ExtraArgs={"ContentType": "application/pdf"}
        )
        logger.info(f"SALARY_UPLOADED_S3 | key={key}")
        return key
    except ClientError as e:
        logger.error(f"SALARY_UPLOAD_ERROR | key={key} | error={e}")
        return None


def upload_video_to_s3(file_obj, filename: str) -> Optional[str]:
    """Upload training video MP4 to S3 under training_videos/ directory."""
    s3 = get_s3_client()
    bucket = get_bucket_name()
    if not bucket:
        logger.error("UPLOAD_VIDEO_FAILED | S3_BUCKET_NAME not set")
        return None

    if hasattr(file_obj, "seek"):
        file_obj.seek(0)

    key = f"training_videos/{uuid4()}_{filename}"
    try:
        s3.upload_fileobj(
            file_obj, bucket, key, ExtraArgs={"ContentType": "video/mp4"}
        )
        logger.info(f"VIDEO_UPLOADED_S3 | key={key}")
        return key
    except ClientError as e:
        logger.error(f"VIDEO_UPLOAD_ERROR | key={key} | error={e}")
        return None


def upload_policy_to_s3(file_obj, filename: str) -> Optional[str]:
    """Upload policy PDF to S3 with stream reset."""
    s3 = get_s3_client()
    bucket = get_bucket_name()
    if not bucket:
        logger.error("UPLOAD_POLICY_FAILED | S3_BUCKET_NAME not set")
        return None

    s3_key = f"policies/{filename}"

    if hasattr(file_obj, "seek"):
        file_obj.seek(0)

    try:
        s3.upload_fileobj(
            file_obj, bucket, s3_key, ExtraArgs={"ContentType": "application/pdf"}
        )
        logger.info(f"POLICY_UPLOADED_S3 | key={s3_key}")
        return s3_key
    except ClientError as e:
        logger.error(f"POLICY_UPLOAD_ERROR | key={s3_key} | error={e}")
        return None


def generate_presigned_url(s3_key: str, expiration: int = 3600) -> str:
    """Generate pre-signed URL for viewing/downloading files securely."""
    s3 = get_s3_client()
    bucket = get_bucket_name()
    if not bucket or not s3_key:
        return ""

    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": s3_key},
            ExpiresIn=expiration,
        )
    except ClientError as e:
        logger.error(f"PRESIGNED_URL_ERROR | key={s3_key} | error={e}")
        return ""


generate_download_url = generate_presigned_url


def delete_file_from_s3(s3_key: str) -> bool:
    """Delete file from S3."""
    s3 = get_s3_client()
    bucket = get_bucket_name()
    if not bucket or not s3_key:
        return False

    try:
        s3.delete_object(Bucket=bucket, Key=s3_key)
        logger.info(f"FILE_DELETED_S3 | key={s3_key}")
        return True
    except ClientError as e:
        logger.error(f"DELETE_S3_ERROR | key={s3_key} | error={e}")
        return False


def download_policy_temp(s3_key: str) -> str:
    """Download S3 policy file to local temporary folder."""
    s3 = get_s3_client()
    bucket = get_bucket_name()
    if not bucket:
        raise ValueError("S3_BUCKET_NAME not configured")

    filename = os.path.basename(s3_key)
    temp_path = os.path.join(tempfile.gettempdir(), filename)

    try:
        s3.download_file(bucket, s3_key, temp_path)
        logger.info(
            f"POLICY_DOWNLOADED_TEMP | key={s3_key} | temp_path={temp_path}"
        )
        return temp_path
    except ClientError as e:
        logger.error(f"DOWNLOAD_TEMP_ERROR | key={s3_key} | error={e}")
        raise e