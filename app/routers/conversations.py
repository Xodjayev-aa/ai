import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.deps import get_current_user
from app.database import get_db_connection

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

class ConvCreate(BaseModel):
    title: str

@router.get("")
def list_conversations(user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM conversations WHERE user_id = ? ORDER BY created_at DESC", (user["id"],))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("")
def create_conversation(data: ConvCreate, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    conv_id = str(uuid.uuid4())
    cursor.execute("INSERT INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
                   (conv_id, user["id"], data.title))
    conn.commit()
    conn.close()
    return {"id": conv_id, "title": data.title}

@router.delete("/{conv_id}")
def delete_conversation(conv_id: str, user=Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user["id"]))
    conn.commit()
    conn.close()
    return {"status": "success"}
