import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))

try:
    import fastapi
    import uvicorn
    print("fastapi", fastapi.__version__)
    print("uvicorn", uvicorn.__version__)
except Exception as e:
    print("IMPORT_DEPS_FAIL", type(e).__name__, e)
    raise

from clipper.web import app, STATIC_DIR, JOBS_DIR

print("app", type(app).__name__)
print("static_dir", STATIC_DIR, "exists", STATIC_DIR.exists())
print("static_index", (STATIC_DIR / "index.html").exists())
print("jobs_dir", JOBS_DIR)
print("OK")
