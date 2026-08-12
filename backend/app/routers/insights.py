from fastapi import APIRouter

from .. import state
from ..insights import get_insights

router = APIRouter()


@router.get("/insights")
def insights():
    return get_insights(state.snapshot())
