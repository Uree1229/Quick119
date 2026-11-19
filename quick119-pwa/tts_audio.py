"""Korean speech audio for system messages, rendered on the PC so phones need no installed voice.

Windows SAPI renders offline by default. Explicitly selecting the Microsoft voice also
requires the application's external-API setting. If the provider is unavailable, use SAPI.
"""
import asyncio
from base64 import b64encode
from collections import OrderedDict
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import threading

from settings import external_api_enabled

MAX_CHARS = 400
# SAPI rate 1 measured about 1.11x the default pace: moderately fast but still clear.
RATE = 1
CACHE_BYTES = 8 * 1024 * 1024
NEURAL_VOICE = 'ko-KR-SunHiNeural'
NEURAL_RATE = '+15%'
NEURAL_TIMEOUT = 8

_SCRIPT = r'''
$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Speech
$speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voice = $speech.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'ko-KR' } | Select-Object -First 1
    if (-not $voice) { exit 3 }
    $speech.SelectVoice($voice.VoiceInfo.Name)
    $speech.Rate = [int]$env:QUICK119_TTS_RATE
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo 16000, ([System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen), ([System.Speech.AudioFormat.AudioChannel]::Mono)
    $speech.SetOutputToWaveFile($env:QUICK119_TTS_OUT, $format)
    $speech.Speak($env:QUICK119_TTS_TEXT)
} finally { $speech.Dispose() }
'''
_ENCODED = b64encode(_SCRIPT.encode('utf-16-le')).decode('ascii')
_lock = threading.Lock()
_cache: OrderedDict[tuple[bool, str], tuple[bytes, str]] = OrderedDict()


class SpeechUnavailable(RuntimeError):
    pass


class SpeechBusy(SpeechUnavailable):
    pass


def neural_enabled() -> bool:
    return os.getenv('QUICK119_TTS_VOICE', '').strip().lower() == 'neural' and external_api_enabled()


def normalize(text: str) -> str:
    return ' '.join(''.join(ch if ch.isprintable() else ' ' for ch in text).split())


def _powershell() -> Path:
    return Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'


def _render(text: str) -> bytes:
    if os.name != 'nt' or not _powershell().is_file():
        raise SpeechUnavailable('Windows speech is unavailable')
    with tempfile.TemporaryDirectory(prefix='quick119-tts-') as folder:
        out = Path(folder) / 'speech.wav'
        env = dict(os.environ, QUICK119_TTS_TEXT=text, QUICK119_TTS_OUT=str(out), QUICK119_TTS_RATE=str(RATE))
        try:
            result = subprocess.run(
                [str(_powershell()), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-EncodedCommand', _ENCODED],
                env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SpeechUnavailable('speech process failed') from error
        if result.returncode != 0 or not out.is_file() or out.stat().st_size <= 44:
            raise SpeechUnavailable('Korean voice is unavailable')
        return out.read_bytes()


def _render_neural(text: str) -> bytes | None:
    try:
        import edge_tts
    except ImportError:
        return None

    async def fetch():
        chunks = []
        async for chunk in edge_tts.Communicate(text, NEURAL_VOICE, rate=NEURAL_RATE).stream():
            if chunk['type'] == 'audio':
                chunks.append(chunk['data'])
        return b''.join(chunks)
    try:
        audio = asyncio.run(asyncio.wait_for(fetch(), NEURAL_TIMEOUT))
    except Exception:
        return None
    return audio or None


def synthesize(text: str) -> tuple[bytes, str]:
    """Return (audio bytes, media type); one render at a time keeps CPU and network use bounded."""
    if not isinstance(text, str):
        raise ValueError('text')
    text = normalize(text)
    if not text or len(text) > MAX_CHARS:
        raise ValueError('text')
    if not _lock.acquire(blocking=False):
        raise SpeechBusy('another request is rendering')
    try:
        neural = neural_enabled()
        key = (neural, hashlib.sha256(text.encode('utf-8')).hexdigest())
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
        if neural:
            audio = _render_neural(text)
            if audio is None:
                # Allow a later neural request to retry after a temporary outage.
                return _render(text), 'audio/wav'
            result = (audio, 'audio/mpeg')
        else:
            result = (_render(text), 'audio/wav')
        if len(result[0]) <= CACHE_BYTES:
            _cache[key] = result
            while sum(len(audio) for audio, _ in _cache.values()) > CACHE_BYTES:
                _cache.popitem(last=False)
        return result
    finally:
        _lock.release()
