"""
LLM-generated actionable insights over the live telemetry buffer.

Uses the Hugging Face Inference API (via huggingface_hub's InferenceClient)
to turn the current summary statistics into a few plain-English callouts.
Results are cached for INSIGHTS_TTL_SECONDS since each call is a network
round-trip to an external model and the dashboard polls frequently.
"""

import os
import time
from typing import Optional

import pandas as pd

HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_TOKEN")
# zephyr-7b-beta (and most of the old single-host "serverless Inference API"
# models) are no longer served by any Inference Provider now that HF routes
# chat completions through the multi-provider router. Qwen2.5-7B-Instruct is
# ungated (Apache-2.0) and currently mirrored by ~10 providers (Groq,
# Novita, Cerebras, SambaNova, Together, Fireworks, ...), so "auto" provider
# selection reliably finds a live host. Override with HF_INSIGHTS_MODEL if
# you'd rather point at a specific model/provider.
HF_MODEL = os.environ.get("HF_INSIGHTS_MODEL", "Qwen/Qwen2.5-7B-Instruct")
HF_PROVIDER = os.environ.get("HF_INSIGHTS_PROVIDER", "auto")
INSIGHTS_TTL_SECONDS = float(os.environ.get("INSIGHTS_TTL_SECONDS", "20"))

_cache: dict = {"text": None, "generated_at": None, "expires": 0.0}


def _build_prompt(df: pd.DataFrame) -> str:
    recent = df.tail(60)
    stats = recent.describe().to_dict()

    lines = ["Recent iMet-X4 flight telemetry summary (last {} samples):".format(len(recent))]
    for col in ("temperature", "humidity", "pressure", "altitude", "wind_speed"):
        if col in stats:
            s = stats[col]
            lines.append(
                f"- {col}: mean={s['mean']:.2f}, min={s['min']:.2f}, "
                f"max={s['max']:.2f}, std={s['std']:.2f}"
            )

    lines.append(
        "\nAs a concise atmospheric-science assistant, give 3-4 short, "
        "actionable bullet points calling out notable trends, anomalies, "
        "or recommendations for the flight crew based on this data. "
        "Keep each bullet under 20 words. No preamble."
    )
    return "\n".join(lines)


def _call_huggingface(prompt: str) -> str:
    from huggingface_hub import InferenceClient

    client = InferenceClient(provider=HF_PROVIDER, api_key=HF_TOKEN)
    completion = client.chat_completion(
        model=HF_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.4,
    )
    return completion.choices[0].message.content.strip()


def get_insights(df: pd.DataFrame) -> dict:
    """Return cached or freshly-generated insights for the current buffer."""
    if not HF_TOKEN:
        return {
            "insights": None,
            "model": HF_MODEL,
            "generated_at": None,
            "error": "No Hugging Face token configured. Set HF_TOKEN to enable insights.",
        }

    if df.empty:
        return {"insights": None, "model": HF_MODEL, "generated_at": None, "error": "No data yet"}

    now = time.time()
    if _cache["text"] is not None and now < _cache["expires"]:
        return {
            "insights": _cache["text"],
            "model": HF_MODEL,
            "generated_at": _cache["generated_at"],
            "error": None,
        }

    try:
        text = _call_huggingface(_build_prompt(df))
    except Exception as e:  # noqa: BLE001 - surface any provider/network error to the UI
        msg = str(e)
        if "model_not_supported" in msg or "not supported by any provider" in msg:
            msg = (
                f"'{HF_MODEL}' isn't hosted by any enabled Inference Provider "
                f"right now. Set HF_INSIGHTS_MODEL to a model shown as "
                f"available at huggingface.co/models?inference_provider=all "
                f"(original error: {msg})"
            )
        return {"insights": None, "model": HF_MODEL, "generated_at": None, "error": msg}

    generated_at = pd.Timestamp.utcnow().isoformat()
    _cache.update(text=text, generated_at=generated_at, expires=now + INSIGHTS_TTL_SECONDS)
    return {"insights": text, "model": HF_MODEL, "generated_at": generated_at, "error": None}
