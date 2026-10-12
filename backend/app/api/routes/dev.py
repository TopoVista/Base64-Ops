from fastapi import APIRouter, Depends

from app.api.deps import get_current_user_id
from app.schemas.replay import RunReplayResponse
from app.services.session_service import get_run_replay_by_id

router = APIRouter(prefix="/dev", tags=["dev"])


@router.get("/runs/{run_id}", response_model=RunReplayResponse, response_model_exclude_none=True)
async def get_dev_run_inspector(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
) -> RunReplayResponse:
    return await get_run_replay_by_id(user_id, run_id)
