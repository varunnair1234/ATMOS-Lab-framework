import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from .. import state
from ..utils import fig_to_png_bytes
from framework.stats import StatsPlot

router = APIRouter()


def _current_stats_plot() -> StatsPlot:
    df = state.snapshot()
    if df.empty:
        raise HTTPException(503, "No data yet")
    return StatsPlot(df)


@router.get("/summary")
def summary():
    sp = _current_stats_plot()
    table = sp.summary(print_table=False)
    return json.loads(table.to_json(orient="index"))


@router.get("/histograms")
def histograms():
    sp = _current_stats_plot()
    return Response(content=fig_to_png_bytes(sp.histograms()), media_type="image/png")


@router.get("/correlation")
def correlation():
    sp = _current_stats_plot()
    return Response(content=fig_to_png_bytes(sp.correlation_heatmap()), media_type="image/png")


@router.get("/wind-rose")
def wind_rose():
    sp = _current_stats_plot()
    try:
        fig = sp.wind_rose()
    except ValueError as e:
        raise HTTPException(400, str(e))
    return Response(content=fig_to_png_bytes(fig), media_type="image/png")


@router.get("/vertical-profile")
def vertical_profile(variable: str = Query("temperature")):
    sp = _current_stats_plot()
    try:
        fig = sp.vertical_profile(variable=variable)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return Response(content=fig_to_png_bytes(fig), media_type="image/png")
