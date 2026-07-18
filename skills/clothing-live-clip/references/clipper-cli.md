# clothing-live-clipper CLI

## Paths

- Repo: `C:\Users\MR\AppData\grok\clothing-live-clipper`
- Module: `python -m clipper` with `PYTHONPATH=src`

## Setup (once)

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run (plan + render)

```bat
cd /d C:\Users\MR\AppData\grok\clothing-live-clipper
set PYTHONPATH=src
.venv\Scripts\python -m clipper run --video PATH\TO\video.mp4 --transcript PATH\TO\transcript_for_clipper.json --out PATH\TO\output\job_id
```

## Plan only

```bat
.venv\Scripts\python -m clipper run --transcript PATH\TO\transcript_for_clipper.json --out PATH\TO\output\job_id --no-render
```

## Required precondition

`transcript_for_clipper.json` must already have host filter + hard excludes applied.
Never pass raw livestream transcript with size/sentiment/chitchat lines.

## Outputs to read

- plan.json
- review.md
- final.mp4 (if render)
- clips.json

## ffmpeg

If missing, clipper skips render; treat as incomplete when user asked for mp4.
