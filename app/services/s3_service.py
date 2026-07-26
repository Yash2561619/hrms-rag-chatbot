import os
import boto3
from uuid import uuid4
import tempfile
from botocore.exceptions import ClientError
import logging
logger = logging.getLogger(__name__)

s3 = boto3.client(
    's3',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    region_name=os.getenv('AWS_REGION')
)

BUCKET = os.getenv('S3_BUCKET_NAME')

def upload_salary_to_s3(file_obj, filename):
    key = f"salary_slips/{uuid4()}_{filename}"

    s3.upload_fileobj(
        file_obj,
        BUCKET,
        key,
        ExtraArgs={
            'ContentType': 'application/pdf'
        }
    )

    return key


def upload_video_to_s3(file_obj, filename):
    key = f"training_videos/{uuid4()}_{filename}"

    s3.upload_fileobj(
        file_obj,
        BUCKET,
        key,
        ExtraArgs={
            "ContentType": "video/mp4"
        }
    )

    return key
def generate_download_url(key, expires=3600):
    return s3.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': BUCKET,
            'Key': key
        },
        ExpiresIn=expires
    )

def upload_policy_to_s3(file_obj, filename: str) -> str:
    """Upload policy PDF to S3 with stream reset."""
    s3_key = f"policies/{filename}"

    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)

    s3.upload_fileobj(
        file_obj, BUCKET, s3_key, ExtraArgs={'ContentType': 'application/pdf'}
    )
    logger.info(f"POLICY_UPLOADED_S3 | key={s3_key}")
    return s3_key

def generate_presigned_url(s3_key: str, expires: int = 3600) -> str:
    """Generate pre-signed URL for viewing/downloading PDFs securely."""
    try:
        return s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': BUCKET, 'Key': s3_key},
            ExpiresIn=expires,
        )
    except ClientError as e:
        logger.error(
            f"PRESIGNED_URL_ERROR | key={s3_key} | error={e}"
        )
        return ""

    
generate_download_url = generate_presigned_url

def delete_file_from_s3(s3_key: str) -> bool:
    """Delete policy PDF from S3."""
    try:
        s3.delete_object(Bucket=BUCKET, Key=s3_key)
        logger.info(f"FILE_DELETED_S3 | key={s3_key}")
        return True
    except ClientError as e:
        logger.error(
            f"DELETE_S3_ERROR | key={s3_key} | error={e}"
        )
        return False


def download_policy_temp(s3_key: str) -> str:
    """Download S3 policy file to local temporary folder for OCR/RAG indexing."""
    filename = os.path.basename(s3_key)
    temp_path = os.path.join(tempfile.gettempdir(), filename)

    try:
        s3.download_file(BUCKET, s3_key, temp_path)
        logger.info(
            f"POLICY_DOWNLOADED_TEMP | key={s3_key} | temp_path={temp_path}"
        )
        return temp_path
    except ClientError as e:
        logger.error(
            f"DOWNLOAD_TEMP_ERROR | key={s3_key} | error={e}"
        )
        raise e

