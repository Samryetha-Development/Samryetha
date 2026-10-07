"""/api/search — 镜像 backend/src/search/routes.ts。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import search as search_service
from ..core.deps import CurrentUser, DbConn, get_current_user
from ..search.models import SearchOptions, SearchResultResponse

router = APIRouter()


@router.get("/api/search", response_model=SearchResultResponse)
def search(
    conn: DbConn,
    viewer: CurrentUser | None = Depends(get_current_user),
    q: str = Query(min_length=1, max_length=100),
    board: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=20, ge=1, le=50),
) -> SearchResultResponse:
    result = search_service.search_discussions(
        conn,
        viewer,
        SearchOptions(query=q, board_slug=board, limit=limit),
    )
    return SearchResultResponse.model_validate(result)
