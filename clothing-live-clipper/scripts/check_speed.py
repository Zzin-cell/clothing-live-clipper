from clipper.config import Settings
s = Settings.from_env()
print("playback_speed", s.playback_speed)
print("source_select_duration_s", s.source_select_duration_s)
