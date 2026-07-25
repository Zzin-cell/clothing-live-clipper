from clipper.user_llm import load_user_llm, save_user_llm, USER_CFG_PATH

d = load_user_llm()
k = str(d.get("api_key") or "").strip()
print("store", USER_CFG_PATH)
print("base", d.get("base_url"))
print("model", d.get("model"))
print("key_head", k[:12], "len", len(k))
if k.lower().startswith("http://") or k.lower().startswith("https://") or "://" in k:
    save_user_llm({"api_key": ""}, keep_old_key_if_blank=False)
    print("cleared_invalid_url_key")
else:
    print("key_looks_ok")
