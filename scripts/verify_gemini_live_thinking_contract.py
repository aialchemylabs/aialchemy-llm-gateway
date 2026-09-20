#!/usr/bin/env python3
"""Verify serialized Gemini Live setup with the installed patched adapter."""

import json

from litellm.llms.gemini.realtime.transformation import GeminiRealtimeConfig


def verify() -> None:
    config = GeminiRealtimeConfig()
    models = (
        "gemini-3.8-live-extended-thinking",
        "gemini-3.8-live",
        "gemini-3.1-flash-live-preview",
        "gemini-3.8-live-extended-thinking-other",
    )
    for model in models:
        serialized_setups = {
            "initial": config.session_configuration_request(model),
            "deferred": config._handle_session_update(
                {"type": "session.update", "session": {"modalities": ["audio"]}},
                model,
                None,
            )[0],
        }
        for path, serialized in serialized_setups.items():
            setup = json.loads(serialized)["setup"]
            generation = setup["generationConfig"]
            assert setup["model"] == f"models/{model}", (model, path, setup)
            assert generation["responseModalities"] == ["AUDIO"], (model, path, generation)
            if model == "gemini-3.8-live-extended-thinking":
                assert generation["thinkingConfig"] == {"thinkingLevel": "MEDIUM"}, (path, generation)
            else:
                assert "thinkingConfig" not in generation, (model, path, generation)

    explicit = {"generationConfig": {"thinkingConfig": {"thinkingLevel": "HIGH"}}}
    finalized = config._finalize_gemini_live_setup(models[0], explicit)
    assert finalized["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "HIGH"
    print("Gemini Live thinking contract passed: initial/deferred setup and exact model isolation")


if __name__ == "__main__":
    verify()
