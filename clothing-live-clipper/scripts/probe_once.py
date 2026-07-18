from clipper.system_status import probe_whisper, probe_llm

print("whisper", probe_whisper())
print("llm", probe_llm())
