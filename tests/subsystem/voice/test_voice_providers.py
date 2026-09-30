from __future__ import annotations

from row_bot.voice.local_provider import LocalFunASRProvider


class FakeFunASRModel:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def generate(self, **kwargs):
        self.inputs.append(kwargs["input"])
        return [{"text": ("<|en|><|NEUTRAL|><|Speech|><|woitn|>sensevoice text")}]


def test_local_funasr_provider_transcribes_raw_pcm_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "row_bot.voice.local_provider.sensevoice_platform_support", lambda: (True, "")
    )
    monkeypatch.setattr(
        "row_bot.voice.local_provider.missing_funasr_packages", lambda: ()
    )
    model_path = tmp_path / "snapshot"
    model_path.mkdir()
    (model_path / "config.yaml").write_text("model: SenseVoiceSmall\n")
    (model_path / "model.pt").write_bytes(b"weights")
    model = FakeFunASRModel()
    provider = LocalFunASRProvider(
        model_path=model_path,
        cache_dir=tmp_path,
        model_factory=lambda **kwargs: model,
        postprocessor=lambda _text: "sensevoice text",
    )

    text = provider.transcribe_bytes(b"\x00\x00\x01\x00")

    assert text == "sensevoice text"
    assert model.inputs
