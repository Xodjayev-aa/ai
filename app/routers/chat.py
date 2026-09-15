import asyncio
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from app.deps import get_current_user

router = APIRouter(prefix="/api/chat", tags=["chat"])

class ChatPayload(BaseModel):
    conversation_id: str
    message: str

@router.post("/stream")
async def stream_chat(data: ChatPayload, user=Depends(get_current_user)):
    async def sse_generator():
        text = f"Aether response to: {data.message}"
        for token in text.split():
            yield f"data: {token} \n\n"
            await asyncio.sleep(0.08)
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_generator(), media_type="text/event-stream")
