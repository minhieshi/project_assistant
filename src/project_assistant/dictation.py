from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


MAX_AUDIO_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class DictationStatus:
    configured: bool
    backend: str = "whisper.cpp"
    binary: str | None = None
    model: str | None = None
    language: str = "en"
    detail: str | None = None

    def to_dict(self) -> dict:
        return {
            "configured": self.configured,
            "backend": self.backend,
            "binary": self.binary,
            "model": self.model,
            "language": self.language,
            "detail": self.detail,
        }


def _binary_path(raw: str) -> str | None:
    candidate = Path(raw).expanduser()
    if candidate.is_absolute() or "/" in raw:
        return str(candidate.resolve()) if candidate.exists() else None
    return shutil.which(raw)


def dictation_status() -> DictationStatus:
    binary_name = (os.getenv("PROJECT_ASSISTANT_WHISPER_BIN") or "whisper-cli").strip()
    model_raw = (os.getenv("PROJECT_ASSISTANT_WHISPER_MODEL") or "").strip()
    language = (os.getenv("PROJECT_ASSISTANT_WHISPER_LANGUAGE") or "en").strip() or "en"
    binary = _binary_path(binary_name)
    model = Path(model_raw).expanduser().resolve() if model_raw else None

    if not binary:
        return DictationStatus(False, binary=binary_name, model=str(model) if model else None, language=language, detail="whisper.cpp executable not found")
    if not model_raw:
        return DictationStatus(False, binary=binary, model=None, language=language, detail="PROJECT_ASSISTANT_WHISPER_MODEL is not set")
    if not model or not model.is_file():
        return DictationStatus(False, binary=binary, model=str(model), language=language, detail="Whisper model file does not exist")
    return DictationStatus(True, binary=binary, model=str(model), language=language)


def _validate_wav(audio: bytes) -> None:
    if not audio:
        raise ValueError("No audio was received")
    if len(audio) > MAX_AUDIO_BYTES:
        raise ValueError(f"Audio exceeds the {MAX_AUDIO_BYTES // (1024 * 1024)} MiB local dictation limit")
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise ValueError("Dictation expects a PCM WAV recording")


def transcribe_wav(audio: bytes) -> str:
    _validate_wav(audio)
    status = dictation_status()
    if not status.configured:
        raise RuntimeError(status.detail or "Local dictation is not configured")

    timeout = int(os.getenv("PROJECT_ASSISTANT_WHISPER_TIMEOUT_SECONDS", "180"))
    with tempfile.TemporaryDirectory(prefix="project-assistant-dictation-") as tmp:
        root = Path(tmp)
        wav_path = root / "input.wav"
        output_base = root / "transcript"
        wav_path.write_bytes(audio)

        command = [
            str(status.binary),
            "-m", str(status.model),
            "-f", str(wav_path),
            "-otxt",
            "-of", str(output_base),
            "-np",
            "-nt",
            "-l", status.language,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ},
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "whisper.cpp failed").strip()
            raise RuntimeError(f"Local Whisper transcription failed: {detail[:1200]}")

        transcript_path = output_base.with_suffix(".txt")
        if not transcript_path.exists():
            raise RuntimeError("whisper.cpp completed without creating a transcript")
        text = transcript_path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            raise RuntimeError("Local Whisper returned an empty transcript")
        return text
