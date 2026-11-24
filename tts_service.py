import io
import asyncio
import logging
from gtts import gTTS

logger = logging.getLogger(__name__)

EDGE_VOICE_MAP = {
    "en": "en-IN-NeerjaNeural",
    "kn": "kn-IN-GaganNeural",
    "te": "te-IN-MohanNeural",
    "hi": "hi-IN-SwaraNeural",
    "ta": "ta-IN-PallaviNeural"
}

GTTS_LANG_MAP = {
    "en": "en",
    "kn": "kn",
    "te": "te",
    "hi": "hi",
    "ta": "ta"
}


async def _generate_edge_tts(text: str, voice: str) -> bytes:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    audio_data = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.extend(chunk["data"])
    if not audio_data:
        raise ValueError("Edge TTS generated empty audio data.")
    return bytes(audio_data)


def generate_audio(text: str, language: str = "en") -> tuple[bytes, str]:
    """
    Generates MP3 audio for the given text and language.
    First tries Edge-TTS for high-fidelity neural speech.
    Falls back reliably to gTTS if Edge-TTS encounters network/socket issues.
    """
    lang = language.lower() if language else "en"
    if lang not in GTTS_LANG_MAP:
        lang = "en"

    clean_text = text.strip()
    # Safety limit to prevent audio generation DOS
    if len(clean_text) > 1200:
        clean_text = clean_text[:1200]

    # 1. Try edge-tts
    voice = EDGE_VOICE_MAP.get(lang, "en-IN-NeerjaNeural")
    try:
        # Run asyncio coroutine in synchronous context safely
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            audio_bytes = loop.run_until_complete(
                asyncio.wait_for(_generate_edge_tts(clean_text, voice), timeout=8.0)
            )
            if audio_bytes and len(audio_bytes) > 500:
                return audio_bytes, "audio/mpeg"
        finally:
            loop.close()
    except Exception as e:
        logger.info(f"Edge-TTS unavailable ({e}), falling back to gTTS for language: {lang}")

    # 2. Fallback to gTTS (robust HTTPS)
    try:
        gtts_lang = GTTS_LANG_MAP.get(lang, "en")
        tts = gTTS(text=clean_text, lang=gtts_lang)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        return fp.getvalue(), "audio/mpeg"
    except Exception as e:
        logger.error(f"gTTS audio generation error ({e})")
        raise RuntimeError(f"Unable to synthesize audio for language {lang}: {str(e)}")
