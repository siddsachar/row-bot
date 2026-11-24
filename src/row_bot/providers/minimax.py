"""The MiniMax API region used by the configured account."""
from __future__ import annotations

import os

API_ORIGIN = "https://api.minimax.cn" if os.getenv("ROW_BOT_MINIMAX_CN") == "1" else "https://api.minimax.io"
ANTHROPIC_BASE_URL = f"{API_ORIGIN}/anthropic"

H3_IMAGE_MODELS = {
    "MiniMax-H3-Image": "MiniMax H3 Image",
    "MiniMax-H3-Image-Highspeed": "MiniMax H3 Image Highspeed",
}
