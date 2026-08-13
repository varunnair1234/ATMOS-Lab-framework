"""Offline sanity checks for framework.insights — no real network calls."""

from unittest.mock import patch

import pandas as pd

import framework.insights as insights


def _sample_df(n=10):
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-08-12", periods=n, freq="s"),
        "temperature": [20.0 + i * 0.1 for i in range(n)],
        "humidity": [50.0 - i * 0.1 for i in range(n)],
        "pressure": [1000.0 - i * 0.05 for i in range(n)],
        "altitude": [100.0 + i * 2 for i in range(n)],
        "wind_speed": [3.0 + i * 0.05 for i in range(n)],
    })


def test_no_token_returns_clear_error_without_calling_huggingface():
    insights._cache.update(text=None, generated_at=None, expires=0.0)
    with patch.object(insights, "HF_TOKEN", None), \
         patch.object(insights, "_call_huggingface") as mock_call:
        result = insights.get_insights(_sample_df())

    mock_call.assert_not_called()
    assert result["insights"] is None
    assert "HF_TOKEN" in result["error"]


def test_empty_dataframe_returns_no_data_yet():
    insights._cache.update(text=None, generated_at=None, expires=0.0)
    with patch.object(insights, "HF_TOKEN", "fake-token"):
        result = insights.get_insights(pd.DataFrame())

    assert result["insights"] is None
    assert result["error"] == "No data yet"


def test_successful_call_is_cached_within_ttl():
    insights._cache.update(text=None, generated_at=None, expires=0.0)
    with patch.object(insights, "HF_TOKEN", "fake-token"), \
         patch.object(insights, "_call_huggingface", return_value="- Temp falling steadily") as mock_call:
        first = insights.get_insights(_sample_df())
        second = insights.get_insights(_sample_df())

    assert first["insights"] == "- Temp falling steadily"
    assert second["insights"] == "- Temp falling steadily"
    mock_call.assert_called_once()  # second call served from cache, not a new HF request


def test_model_not_supported_error_gets_a_readable_hint():
    insights._cache.update(text=None, generated_at=None, expires=0.0)
    with patch.object(insights, "HF_TOKEN", "fake-token"), \
         patch.object(insights, "_call_huggingface",
                      side_effect=RuntimeError("model_not_supported: nope")):
        result = insights.get_insights(_sample_df())

    assert result["insights"] is None
    assert "HF_INSIGHTS_MODEL" in result["error"]


if __name__ == "__main__":
    test_no_token_returns_clear_error_without_calling_huggingface()
    test_empty_dataframe_returns_no_data_yet()
    test_successful_call_is_cached_within_ttl()
    test_model_not_supported_error_gets_a_readable_hint()
    print("All tests passed.")
