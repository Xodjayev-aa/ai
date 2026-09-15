from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from app.security import hash_password, verify_password, create_access_token
from app.database import create_user, get_user_by_email
from app.deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

class AuthPayload(BaseModel):
    email: EmailStr
    password: str

@router.post("/register")
def register(payload: AuthPayload):
    if get_user_by_email(payload.email):
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed = hash_password(payload.password)
    user_id = create_user(payload.email, hashed)
    token = create_access_token({"sub": payload.email, "id": user_id})
    return {"access_token": token, "token_type": "bearer"}

@router.post("/login")
def login(payload: AuthPayload):
    user = get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user["email"], "id": user["id"]})
    return {"access_token": token, "token_type": "bearer"}

@router.get("/me")
def me(user=Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"], "is_admin": bool(user["is_admin"])}
