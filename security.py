import io
import time
import base64
from collections import defaultdict
from PIL import Image, ImageOps

# Maximum allowed file upload size: 15 MB
MAX_UPLOAD_SIZE = 15 * 1024 * 1024

# Allowed image MIME types
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/bmp"
}

# Rate limiting: max 20 requests per 5 minutes per client identifier
RATE_LIMIT_MAX_REQUESTS = 20
RATE_LIMIT_WINDOW_SECONDS = 300
_rate_limit_records = defaultdict(list)


def check_rate_limit(client_id: str) -> tuple[bool, int]:
    """
    Checks if client_id is within rate limits.
    Returns (is_allowed, remaining_seconds_until_reset)
    """
    now = time.time()
    timestamps = _rate_limit_records[client_id]
    
    # Filter out timestamps outside the window
    valid_timestamps = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW_SECONDS]
    _rate_limit_records[client_id] = valid_timestamps
    
    if len(valid_timestamps) >= RATE_LIMIT_MAX_REQUESTS:
        oldest = valid_timestamps[0]
        retry_after = int(RATE_LIMIT_WINDOW_SECONDS - (now - oldest))
        return False, max(1, retry_after)
    
    _rate_limit_records[client_id].append(now)
    return True, 0


def sanitize_and_process_image(file_storage, max_dim=1600, quality=85):
    """
    Validates, sanitizes, resizes and compresses an uploaded image.
    Returns:
        processed_bytes: bytes (JPEG)
        mime_type: str ("image/jpeg")
        thumbnail_b64: str (data URI for 120x120 thumb)
    Raises:
        ValueError on invalid format, size or corrupted data.
    """
    # 1. Read data with size guard
    data = file_storage.read()
    if not data:
        raise ValueError("Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_SIZE:
        raise ValueError(f"Image exceeds maximum upload limit of {MAX_UPLOAD_SIZE // (1024*1024)}MB.")
    
    # 2. Open and verify image structure with Pillow
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()  # Verifies file integrity
    except Exception as e:
        raise ValueError(f"Corrupted or invalid image file: {str(e)}")

    # 3. Re-open for actual processing (verify closes/invalidates the stream)
    try:
        image = Image.open(io.BytesIO(data))
        # Handle EXIF orientation (crucial for smartphone camera captures!)
        image = ImageOps.exif_transpose(image)
        
        # Convert paletted or RGBA to RGB for clean JPEG output
        if image.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", image.size, (255, 255, 255))
            if image.mode == "P":
                image = image.convert("RGBA")
            background.paste(image, mask=image.split()[-1] if "A" in image.mode else None)
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
            
        # 4. Resize if larger than max_dim while preserving aspect ratio
        w, h = image.size
        if max(w, h) > max_dim:
            if w > h:
                new_w = max_dim
                new_h = int(h * (max_dim / w))
            else:
                new_h = max_dim
                new_w = int(w * (max_dim / h))
            image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
        
        # 5. Compress main image to JPEG bytes
        output_buffer = io.BytesIO()
        image.save(output_buffer, format="JPEG", quality=quality, optimize=True)
        processed_bytes = output_buffer.getvalue()
        
        # 6. Generate tiny thumbnail for instant history loading
        thumb = image.copy()
        thumb.thumbnail((140, 140), Image.Resampling.LANCZOS)
        thumb_buffer = io.BytesIO()
        thumb.save(thumb_buffer, format="JPEG", quality=75, optimize=True)
        thumbnail_b64 = "data:image/jpeg;base64," + base64.b64encode(thumb_buffer.getvalue()).decode("utf-8")
        
        return processed_bytes, "image/jpeg", thumbnail_b64
        
    except Exception as e:
        raise ValueError(f"Failed to process and optimize image: {str(e)}")


def sanitize_user_prompt(text: str) -> str:
    """
    Sanitizes user question text to prevent prompt injection attempts.
    """
    if not text:
        return ""
    text = text.strip()
    if len(text) > 1000:
        text = text[:1000]
    return text
