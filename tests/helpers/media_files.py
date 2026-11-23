"""Real, tiny media files written with PyAV, for tests that read what a video holds."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from PIL import Image


def video(path: Path, *, sound: bool | Literal["silence"], codec: str = "libx264",
          size: tuple[int, int] = (320, 180), colour: tuple[int, int, int] = (240, 240, 240), rate: int = 10,
          seconds: int = 3, rotation: int = 0) -> Path:
    """``seconds`` (three) at ``rate`` frames a second (ten), 320×180 unless another size is given, light grey
    unless another colour is; the right half turns red from the second second. ``sound``: a tone throughout,
    ``"silence"`` a sound track of nothing, False no sound track at all (a screen recording). ``rotation``: stored
    turned, as a phone does, with the display rotation (degrees counter-clockwise) that turns it back to ``size``
    upright; a key frame every second."""
    import av
    import numpy

    width, height = size
    plain = Image.new("RGB", (width, height), colour)
    red = plain.copy()
    red.paste((220, 20, 20), (width // 2, 0, width, height))
    stored = [picture.rotate(-rotation, expand=True) for picture in (plain, red)]
    with av.open(str(path), "w") as writer:
        stream = writer.add_stream(codec, rate=rate)
        stream.width, stream.height = stored[0].size
        stream.pix_fmt = "yuv420p"
        stream.codec_context.gop_size = rate
        if rotation:
            stream.set_display_rotation(rotation)
        audio = writer.add_stream("aac", rate=48000, layout="stereo") if sound else None
        for index in range(seconds * rate):
            frame = av.VideoFrame.from_image(stored[index >= rate])
            frame.pts = index
            for packet in stream.encode(frame):
                writer.mux(packet)
        for packet in stream.encode(None):
            writer.mux(packet)
        if audio is not None:
            loudness = 0.3 if sound is True else 0.0
            tone = (loudness * numpy.sin(numpy.arange(48000 * seconds) * 2 * numpy.pi * 440 / 48000)).astype("float32")
            for at in range(0, tone.size, 1024):
                piece = av.AudioFrame.from_ndarray(numpy.stack([tone[at:at + 1024]] * 2), format="fltp",
                                                   layout="stereo")
                piece.sample_rate, piece.pts = 48000, at
                for packet in audio.encode(piece):
                    writer.mux(packet)
            for packet in audio.encode(None):
                writer.mux(packet)
    return path
