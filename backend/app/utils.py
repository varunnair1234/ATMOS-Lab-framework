import io
import json

import matplotlib.pyplot as plt


def fig_to_png_bytes(fig, dpi: int = 150) -> bytes:
    """Render a matplotlib Figure to PNG bytes without touching disk."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def fig_to_json(fig) -> dict:
    """Serialize a Plotly Figure to a JSON-safe dict (numpy-clean)."""
    return json.loads(fig.to_json())
