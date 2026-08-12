from fastapi import APIRouter

from .. import state
from ..utils import fig_to_json
from framework.figures import build_figures, build_stat_values

router = APIRouter()


@router.get("/status")
def get_status():
    return state.status()


@router.get("/snapshot")
def get_snapshot():
    df = state.snapshot()
    if df.empty:
        return {
            "last_update": None,
            "stats": build_stat_values({}),
            "figures": None,
            "status": state.status(),
        }

    fig_th, fig_p, fig_prof, fig_wind = build_figures(df)
    last = df.iloc[-1].to_dict()

    return {
        "last_update": str(last.get("timestamp", "")),
        "stats": build_stat_values(last),
        "figures": {
            "temperature_humidity": fig_to_json(fig_th),
            "pressure": fig_to_json(fig_p),
            "profile": fig_to_json(fig_prof),
            "wind": fig_to_json(fig_wind),
        },
        "status": state.status(),
    }
