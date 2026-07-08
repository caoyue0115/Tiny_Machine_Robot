# Tiny Machine Voice Pet

`Tiny Machine Voice Pet` / `小机仔` is an ESP32-S3 voice desk-pet backend base.

Phase 1 keeps the realtime Opus voice chain and routes ASR text through backend skills:

```text
小明同学 wake word -> Opus uplink -> ASR -> SkillRouter -> realtime TTS -> device playback
```

## Phase 1 Scope

- ESP32-S3 16MB Flash + 8MB PSRAM voice board.
- Guangzhou cloud entry: `tiny.praystack.top`.
- Realtime Opus endpoint: `/api/v5/realtime/opus-stream`.
- Default companion chat persona: `小机仔`.
- First stateful skill: idiom chain game.
- Coffee and Buddhism knowledge assets are retained for future RAG skills.
- OTA code retained but inactive.

## Local Cloud

```powershell
copy .env.example .env
docker compose up --build
```

## Coffee Index

```powershell
$env:PYTHONPATH='.'
python scripts/ingest_coffee.py
```

Coffee RAG is no longer the default realtime answer path in phase 1.

## Firmware

See `docs/firmware/windows-com6.md`.
