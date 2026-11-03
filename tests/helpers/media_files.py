"""Real, tiny media files written with PyAV, for tests that read what a video holds."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from PIL import Image


def video(path: Path, *, sound: bool | Literal["silence"], codec: str = "libx264") -> Path:
    """Three seconds at 10 frames a second; the right half turns red from the second second. ``sound``: a tone
    throughout, ``"silence"`` a sound track of nothing, False no sound track at all (a screen recording)."""
    import av
    import numpy

    with av.open(str(path), "w") as writer:
        stream = writer.add_stream(codec, rate=10)
        stream.width, stream.height, stream.pix_fmt = 320, 180, "yuv420p"
        audio = writer.add_stream("aac", rate=48000, layout="stereo") if sound else None
        for index in range(30):
            picture = Image.new("RGB", (320, 180), (240, 240, 240))
            if index >= 10:
                picture.paste((220, 20, 20), (160, 0, 320, 180))
            frame = av.VideoFrame.from_image(picture)
            frame.pts = index
            for packet in stream.encode(frame):
                writer.mux(packet)
        for packet in stream.encode(None):
            writer.mux(packet)
        if audio is not None:
            loudness = 0.3 if sound is True else 0.0
            tone = (loudness * numpy.sin(numpy.arange(48000 * 3) * 2 * numpy.pi * 440 / 48000)).astype("float32")
            for at in range(0, tone.size, 1024):
                piece = av.AudioFrame.from_ndarray(numpy.stack([tone[at:at + 1024]] * 2), format="fltp",
                                                   layout="stereo")
                piece.sample_rate, piece.pts = 48000, at
                for packet in audio.encode(piece):
                    writer.mux(packet)
            for packet in audio.encode(None):
                writer.mux(packet)
    return path
