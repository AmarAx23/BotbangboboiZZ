"""Upload the original document image to Cloudflare R2 so it can be
re-attached later when the reminder fires (LINE deletes uploaded content
after a while, so we need our own copy with a public HTTPS URL)."""

import uuid

import boto3
from botocore.client import Config

from config import (
    R2_ACCOUNT_ID,
    R2_ACCESS_KEY_ID,
    R2_SECRET_ACCESS_KEY,
    R2_BUCKET_NAME,
    R2_PUBLIC_URL_BASE,
)


def _get_client():
    endpoint_url = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_image(image_bytes: bytes, content_type: str = "image/jpeg") -> str:
    key = f"reminders/{uuid.uuid4().hex}.jpg"
    return upload_file(image_bytes, key, content_type)


def upload_file(file_bytes: bytes, key: str, content_type: str) -> str:
    """Generic upload, e.g. for monthly report .xlsx files (key like
    "reports/2026-08.xlsx"). Returns the public URL."""
    client = _get_client()
    client.put_object(
        Bucket=R2_BUCKET_NAME,
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
    )
    return f"{R2_PUBLIC_URL_BASE.rstrip('/')}/{key}"


def latest_backup_key():
    """Returns the R2 key of the most recent nightly SQLite backup (see
    scheduler.backup_database, keys look like "backups/reminders-YYYY-MM-DD.db"
    - string-sortable since the date is zero-padded ISO), or None if there
    isn't one / the bucket isn't reachable. Used on startup so a host with
    an ephemeral disk (e.g. Render's free tier, which can wipe local files
    on redeploy) can recover the last known database automatically."""
    try:
        client = _get_client()
        resp = client.list_objects_v2(Bucket=R2_BUCKET_NAME, Prefix="backups/")
    except Exception as exc:
        print(f"[storage] failed to list backups: {exc}")
        return None

    keys = [obj["Key"] for obj in resp.get("Contents", [])]
    if not keys:
        return None
    return max(keys)  # ISO-dated filenames sort chronologically as strings


def download_file(key: str) -> bytes:
    """Returns the raw bytes for an R2 object, or None on failure."""
    try:
        client = _get_client()
        obj = client.get_object(Bucket=R2_BUCKET_NAME, Key=key)
        return obj["Body"].read()
    except Exception as exc:
        print(f"[storage] failed to download {key}: {exc}")
        return None
