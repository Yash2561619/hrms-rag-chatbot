"""S3 Service for HR Assistant.

Handles uploading/downloading documents and syncing the FAISS index from AWS
S3. Location: app/services/s3_service.py
"""

import logging
import os
import shutil  # Added missing import
import tempfile
import zipfile
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Initialize boto3 S3 client
s3 = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION"),
)

BUCKET = os.getenv("S3_BUCKET_NAME")


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


def generate_presigned_url(s3_key: str, expires: int = 3600) -> str:
  """Generate pre-signed URL for viewing/downloading files securely."""
  try:
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": s3_key},
        ExpiresIn=expires,
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


def sync_faiss_from_s3() -> bool:
  """Downloads faiss_index.zip from S3 and extracts it into the 'faiss_index' directory."""
  zip_filename = "faiss_index.zip"
  target_dir = "faiss_index"

  try:
    logger.info(
        f"FETCHING_PREBUILT_FAISS | Downloading {zip_filename} from S3..."
    )
    s3.download_file(BUCKET, zip_filename, zip_filename)

    if os.path.exists(zip_filename):
      os.makedirs(target_dir, exist_ok=True)

      # Extract zip contents into target_dir
      with zipfile.ZipFile(zip_filename, "r") as zip_ref:
        zip_ref.extractall(target_dir)

      # Handle nested folder structure (e.g. faiss_index/faiss_index/index.faiss)
      nested_path = os.path.join(target_dir, "faiss_index")
      if os.path.exists(nested_path) and os.path.exists(
          os.path.join(nested_path, "index.faiss")
      ):
        for item in os.listdir(nested_path):
          shutil.move(
              os.path.join(nested_path, item), os.path.join(target_dir, item)
          )
        shutil.rmtree(nested_path)

      # Clean up the downloaded zip file
      if os.path.exists(zip_filename):
        os.remove(zip_filename)

      logger.info(
          "SYNC_FAISS_SUCCESS ✅ | FAISS vector index loaded successfully!"
      )
      return True

  except ClientError as e:
    if e.response.get("Error", {}).get("Code") == "404":
      logger.warning(
          "PREBUILT_FAISS_NOT_FOUND | faiss_index.zip does not exist in S3"
          " bucket."
      )
    else:
      logger.error(f"SYNC_FAISS_ERROR | error={e}")
  except Exception as e:
    logger.error(f"SYNC_FAISS_UNEXPECTED_ERROR | error={e}")

  return False