from clipper.config import apply_config_update, resolve_llm_model, llm_status

cfg = apply_config_update(
    {
        "persist": True,
        "llm_model": "grok-4.5",
        "llm_plan": True,
        "llm_enabled": True,
    }
)
print("public_llm_model", cfg.get("llm_model"))
print("resolve", resolve_llm_model())
print("status", llm_status())
