from fastapi import APIRouter, Depends

from app.api.deps import AssistantDep, rate_limited
from app.api.schemas import ChatIn, ChatOut, ErrorOut
from app.domain.models import Channel

router = APIRouter(prefix="/api", tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatOut,
    dependencies=[Depends(rate_limited)],
    responses={429: {"model": ErrorOut}},
    summary="Send one patient message to the booking assistant",
)
def chat(body: ChatIn, assistant: AssistantDep) -> ChatOut:
    reply = assistant.reply(body.session_id, body.message, source=Channel.WEB)
    return ChatOut.model_validate(reply.model_dump())
