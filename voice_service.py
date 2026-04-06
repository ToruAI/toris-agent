"""
TTS and STT provider abstraction.

Wraps ElevenLabs and OpenAI voice clients. Call reconfigure() when API keys change.
"""
import asyncio
import logging
from io import BytesIO

import config as _cfg
from elevenlabs.client import ElevenLabs
from openai import OpenAI as OpenAIClient

logger = logging.getLogger(__name__)

# Mutable client references — updated by reconfigure() when keys change
_elevenlabs = ElevenLabs(api_key=_cfg.ELEVENLABS_API_KEY) if _cfg.ELEVENLABS_API_KEY else None
_openai_client = OpenAIClient(api_key=_cfg.OPENAI_API_KEY) if _cfg.OPENAI_API_KEY else None

# Provider selection — mirrors config, updated by reconfigure()
_tts_provider = _cfg.TTS_PROVIDER
_stt_provider = _cfg.STT_PROVIDER


def reconfigure(
    elevenlabs_key: str = None,
    openai_key: str = None,
    tts_provider: str = None,
    stt_provider: str = None,
):
    """Hot-swap clients after API keys change (called by apply_saved_credentials and /elevenlabs_key, /openai_key)."""
    global _elevenlabs, _openai_client, _tts_provider, _stt_provider
    if elevenlabs_key is not None:
        _elevenlabs = ElevenLabs(api_key=elevenlabs_key)
    if openai_key is not None:
        _openai_client = OpenAIClient(api_key=openai_key)
    if tts_provider is not None:
        _tts_provider = tts_provider
    if stt_provider is not None:
        _stt_provider = stt_provider


async def _transcribe_elevenlabs(voice_bytes: bytes) -> str:
    """Transcribe voice using ElevenLabs Scribe."""
    try:
        transcription = await asyncio.to_thread(
            _elevenlabs.speech_to_text.convert,
            file=BytesIO(voice_bytes),
            model_id="scribe_v1",
            language_code=_cfg.STT_LANGUAGE or None,
        )
        return transcription.text
    except Exception as e:
        logger.error(f"ElevenLabs STT error: {e}")
        raise


async def _transcribe_openai(voice_bytes: bytes) -> str:
    """Transcribe voice using OpenAI Whisper."""
    try:
        lang = _cfg.STT_LANGUAGE or None
        kwargs = {
            "model": _cfg.OPENAI_STT_MODEL,
            "file": ("voice.ogg", BytesIO(voice_bytes), "audio/ogg"),
        }
        if lang:
            kwargs["language"] = lang
        result = await asyncio.to_thread(_openai_client.audio.transcriptions.create, **kwargs)
        return result.text
    except Exception as e:
        logger.error(f"OpenAI STT error: {e}")
        raise


async def transcribe_voice(voice_bytes: bytes) -> str:
    """Transcribe voice — routes to active STT provider."""
    try:
        if _stt_provider == "openai":
            return await _transcribe_openai(voice_bytes)
        if _stt_provider == "elevenlabs":
            return await _transcribe_elevenlabs(voice_bytes)
        return "[Transcription error: no STT provider configured]"
    except Exception as e:
        return f"[Transcription error: {e}]"


def is_valid_transcription(text: str) -> bool:
    """Return True if transcription is usable — not empty and not an error string."""
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith("[Transcription error")


async def _tts_elevenlabs(text: str, speed: float = None) -> BytesIO:
    """Convert text to speech using ElevenLabs Flash v2.5."""
    def _sync_tts():
        kwargs = dict(
            text=text,
            voice_id=_cfg.ELEVENLABS_VOICE_ID,
            model_id="eleven_flash_v2_5",
            output_format="mp3_44100_128",
        )
        if speed is not None:
            kwargs["voice_settings"] = {"speed": speed}
        audio = _elevenlabs.text_to_speech.convert(**kwargs)
        buf = BytesIO()
        for chunk in audio:
            if isinstance(chunk, bytes):
                buf.write(chunk)
        buf.seek(0)
        return buf
    return await asyncio.to_thread(_sync_tts)


async def _tts_openai(text: str, speed: float = None) -> BytesIO:
    """Convert text to speech using OpenAI TTS."""
    def _sync_tts():
        kwargs = dict(model=_cfg.OPENAI_TTS_MODEL, voice=_cfg.OPENAI_VOICE_ID, input=text)
        if _cfg.OPENAI_VOICE_INSTRUCTIONS:
            kwargs["instructions"] = _cfg.OPENAI_VOICE_INSTRUCTIONS
        if speed is not None:
            kwargs["speed"] = speed
        response = _openai_client.audio.speech.create(**kwargs)
        buf = BytesIO()
        for chunk in response.iter_bytes(chunk_size=4096):
            buf.write(chunk)
        buf.seek(0)
        return buf
    return await asyncio.to_thread(_sync_tts)


async def text_to_speech(text: str, speed: float = None) -> BytesIO:
    """Convert text to speech — routes to active TTS provider."""
    try:
        if _tts_provider == "openai":
            return await _tts_openai(text, speed)
        if _tts_provider == "elevenlabs":
            return await _tts_elevenlabs(text, speed)
        logger.debug("TTS skipped: no provider configured")
        return None
    except Exception as e:
        logger.error(f"TTS error: {e}")
        return None


def format_tts_fallback(response_text: str) -> str:
    """Format response as text when TTS fails silently — adds a notice."""
    return f"🔇 Voice generation failed — here's the text:\n\n{response_text}"
