"""File uploads — hardened: auth required, allowlist, size cap, random names.

Note: on Vercel the filesystem is ephemeral, so uploads only live for the
function instance. Useful for transient processing; persistent storage
(e.g. Turso blobs or object storage) can be added later.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.deps import get_current_user

router = APIRouter(prefix="/api/files", tags=["files"])

ALLOWED_EXTENSIONS = (".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".webp",
                      ".gif", ".csv", ".json", ".docx", ".pptx", ".webm",
                      ".mp3", ".m4a", ".wav", ".ogg", ".opus")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("/upload")
async def upload_file(file: UploadFile, user=Depends(get_current_user)):
    name = (file.filename or "")
    if not name.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=400,
                            detail=f"File type not allowed (allowed: "
                                   f"{', '.join(ALLOWED_EXTENSIONS)})")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")

    # Random server-side name — never trust client filenames for paths.
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    safe_name = f"{uuid.uuid4().hex}{ext}"
    path = f"/tmp/uploads/{safe_name}"
    try:
        import os

        os.makedirs("/tmp/uploads", exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    except OSError:
        raise HTTPException(status_code=503,
                            detail="Upload storage unavailable in this environment.")
    return {"filename": name, "stored_as": safe_name,
            "size": len(data), "status": "uploaded"}
