# Local Whisper Models

This directory is **not committed to Git** (weights exceed GitHub’s 100MB file limit).

Default expected layout:

```
models/
├── whisper-tiny/
│   ├── model.bin
│   ├── config.json
│   ├── tokenizer.json
│   └── vocabulary.txt
└── whisper-small/
    ├── model.bin
    ├── config.json
    ├── tokenizer.json
    └── vocabulary.txt
```

## Download (Windows)

From repo root / project:

```bat
cd clothing-live-clipper
python scripts\download_whisper_small.py
```

Or point env to your local path:

```bat
set CLIPPER_LOCAL_WHISPER_MODEL=C:\Users\MR\AppData\grok\models\whisper-small
set CLIPPER_ASR_DEVICE=cuda
set CLIPPER_ASR_COMPUTE_TYPE=float16
```

## Sources

- faster-whisper tiny/small (CTranslate2 converted):
  - `Systran/faster-whisper-tiny`
  - `Systran/faster-whisper-small`

Mirror example:

- `https://hf-mirror.com/Systran/faster-whisper-small`
