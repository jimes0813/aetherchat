# AetherChat Voice UX (ChatGPT-parity)

## What users get

1. **Composer hold-to-talk** — press and hold the mic button to dictate into the input box. Browser SpeechRecognition may show live partials; final text comes from `/voice/stt`.
2. **Reply speak** — each assistant message has a **朗读** button; composer speaker toggles **auto-speak** and can stop playback.
3. **Live voice mode** — AudioLines opens the orb overlay (continuous conversation or push-to-talk).
4. **Interrupt** — speak (barge-in), spacebar, or orb tap while the assistant is talking.
5. **Clear states** — idle / listening / processing(transcribing) / thinking / speaking / error, with actionable mic and engine hints.

## Engines

| Path | STT | TTS |
|------|-----|-----|
| Local (preferred in `auto`) | whisper.cpp | Piper / CosyVoice |
| Network soft-fallback | OpenAI `whisper-1` or 智谱 `glm-asr-2512` | OpenAI `tts-1` or 智谱 `glm-tts` |

Vault auto-select mirrors embeddings: OpenAI key first, then GLM (`MODEL_KEY_GLM`).

Env knobs:

- `AETHERCHAT_VOICE_STT_PROVIDER=auto|local|network`
- `AETHERCHAT_VOICE_TTS_PROVIDER=auto|local|network`
- `AETHERCHAT_VOICE_BASE_URL` / `AETHERCHAT_VOICE_API_KEY`
- `AETHERCHAT_VOICE_STT_MODEL` / `AETHERCHAT_VOICE_TTS_MODEL` / `AETHERCHAT_VOICE_TTS_VOICE`

## Known hardware caveat

Machines with **only HDMI/speaker sinks and no PipeWire Sources** cannot capture audio. Connect a USB or Bluetooth mic. TTS/朗读 still works without a microphone.
