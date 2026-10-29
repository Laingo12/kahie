import os
import base64
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from google import genai
from google.genai import types
from . import config

router = APIRouter()


class SpeakRequest(BaseModel):
    text: str


@router.post("/api/speak")
def speak(req: SpeakRequest):
    if not config.GEMINI_API_KEY:
        raise HTTPException(400, "GEMINI_API_KEY tsy napetraka ao amin'ny .env")

    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)

        # Nampiasa ny model TTS manokana an'i Google
        response = client.models.generate_content(
            model='gemini-2.5-flash-preview-tts',
            contents=req.text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name='Aoede',  # Safidio: Aoede, Puck, Charon, Kore, Fenrir
                        )
                    )
                )
            )
        )

        audio_data = response.candidates[0].content.parts[0].inline_data.data
        return {"audio": base64.b64encode(audio_data).decode("utf-8")}

    except Exception as e:
        raise HTTPException(500, f"Fahadisoana TTS: {str(e)}")