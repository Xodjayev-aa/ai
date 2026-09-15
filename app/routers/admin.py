import os
from fastapi import APIRouter, Depends, HTTPException
from app.deps import get_current_user

router = APIRouter(prefix="/api/admin", tags=["admin"])

@router.get("/info")
def admin_info(user=Depends(get_current_user)):
    owner_email = os.getenv("OWNER_EMAIL", "xoja10122012@gmail.com")
    if user["email"] != owner_email and not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access denied")
    return {"owner": owner_email, "status": "authorized"}
