import json, urllib.request
url = "https://hf-mirror.com/api/models/Systran/faster-whisper-tiny"
with urllib.request.urlopen(url, timeout=60) as r:
    d = json.load(r)
print([x.get("rfilename") for x in d.get("siblings", [])])
