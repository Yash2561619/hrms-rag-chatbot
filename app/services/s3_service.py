"""S3 Service for HR Assistant.

Handles uploading, downloading, and presigned URL generation for documents,
policies, and videos stored in AWS S3.
Location: app/services/s3_service.py
"""

import logging
import os
import tempfile
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Initialize boto3 S3 client
s3 = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION", "ap-south-1"),
)

BUCKET = os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_BUCKET_NAME")


def upload_salary_to_s3(file_obj, filename: str) -> str:
    """Upload salary slip PDF to S3 under salary_slips/ directory."""
    key = f"salary_slips/{uuid4()}_{filename}"
    s3.upload_fileobj(
        file_obj, BUCKET, key, ExtraArgs={"ContentType": "application/pdf"}
    )
    return key


def upload_video_to_s3(file_obj, filename: str) -> str:
    """Upload training video MP4 to S3 under training_videos/ directory."""
    key = f"training_videos/{uuid4()}_{filename}"
    s3.upload_fileobj(
        file_obj, BUCKET, key, ExtraArgs={"ContentType": "video/mp4"}
    )
    return key


def upload_policy_to_s3(file_obj, filename: str) -> str:
    """Upload policy PDF to S3 with stream reset."""
    s3_key = f"policies/{filename}"

    if hasattr(file_obj, "seek"):
        file_obj.seek(0)

    s3.upload_fileobj(
        file_obj, BUCKET, s3_key, ExtraArgs={"ContentType": "application/pdf"}
    )
    logger.info(f"POLICY_UPLOADED_S3 | key={s3_key}")
    return s3_key


def generate_presigned_url(s3_key: str, expiration: int = 3600) -> str:
    """Generate pre-signed URL for viewing/downloading files securely."""
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": s3_key},
            ExpiresIn=expiration,
        )
    except ClientError as e:
        logger.error(f"PRESIGNED_URL_ERROR | key={s3_key} | error={e}")
        return ""


generate_download_url = generate_presigned_url


def delete_file_from_s3(s3_key: str) -> bool:
    """Delete file from S3."""
    try:
        s3.delete_object(Bucket=BUCKET, Key=s3_key)
        logger.info(f"FILE_DELETED_S3 | key={s3_key}")
        return True
    except ClientError as e:
        logger.error(f"DELETE_S3_ERROR | key={s3_key} | error={e}")
        return False


def download_policy_temp(s3_key: str) -> str:
    """Download S3 policy file to local temporary folder."""
    filename = os.path.basename(s3_key)
    temp_path = os.path.join(tempfile.gettempdir(), filename)

    try:
        s3.download_file(BUCKET, s3_key, temp_path)
        logger.info(
            f"POLICY_DOWNLOADED_TEMP | key={s3_key} | temp_path={temp_path}"
        )
        return temp_path
    except ClientError as e:
        logger.error(f"DOWNLOAD_TEMP_ERROR | key={s3_key} | error={e}")
        raise e