from clipper.config import asr_status, public_config

a = asr_status()
c = public_config()
print("configured", a["asr_configured"])
print("base", a["asr_base_url"])
print("model", a["asr_model"])
print("hint", a.get("key_hint"))
print("note", a.get("asr_note"))
print("source", a.get("source"))
print("has_key", c.get("has_api_key"))
