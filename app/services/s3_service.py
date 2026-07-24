import os
import boto3
from uuid import uuid4

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

def generate_presigned_url(s3_key, expires=3600):
    return s3.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': BUCKET,
            'Key': s3_key
        },
        ExpiresIn=expires
    )