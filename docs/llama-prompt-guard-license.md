# Llama Prompt Guard 2 86M — License Obligations Record

**Date:** 2026-08-17  
**Model:** `meta-llama/Llama-Prompt-Guard-2-86M`  
**License:** Llama 4 Community License Agreement (effective April 5, 2025)  
**Implementation:** `guardrails/config.py` and `guardrails/prompt_guard_client.py`

## License Summary

Prompt Guard 2 86M is released under the **Llama 4 Community License Agreement**,
not the older Llama 2 Community License. The model page explicitly states
`License: llama4` and requires acceptance of the Llama 4 terms.

## Key Obligations

1. **Redistribution**: If we distribute the model weights or any product
   containing them, we MUST provide a copy of the Llama 4 License Agreement
   and prominently display "Built with Llama" on a related surface.

2. **Attribution**: Retain the attribution notice in a "Notice" file:
   > "Llama 4 is licensed under the Llama 4 Community License,
   > Copyright © Meta Platforms, Inc. All Rights Reserved."

3. **Naming**: If we use the model to create/improve an AI model that is
   distributed, include "Llama" at the beginning of the model name.

4. **Acceptable Use Policy**: Comply with Meta's Acceptable Use Policy at
   https://www.llama.com/llama4/use-policy

5. **Commercial threshold**: If our monthly active users exceed 700 million,
   a separate license from Meta is required.

## Our Use Case

- **In-process classification** within a private LiteLLM gateway container
- Model weights loaded at runtime (not baked into the published Docker image)
- No redistribution of weights to end users
- No public-facing model API
- Used solely for security/safety classification (injection detection)

## Compliance Status

| Obligation | Status | Notes |
|---|---|---|
| License file alongside weights | ⚠️ Required at deployment | Must include in model artifact directory |
| "Built with Llama" display | N/A | Not distributing a product containing the model |
| Acceptable Use Policy | ✅ Compliant | Security classification only |
| NOTICE file | ⚠️ Required at deployment | Add to model artifact directory |
| 700M MAU threshold | ✅ N/A | Private internal gateway |

## Model Pinning (§7.1)

The exact model revision and artifacts MUST be pinned and integrity-checked
at deployment time. `guardrails/config.py` pins
`meta-llama/Llama-Prompt-Guard-2-86M` to the immutable Hugging Face revision:

```
PROMPT_GUARD_MODEL_REVISION=a8ded8e697ce7c355e395a0df51f94adb4a2fd27
```

The runtime downloads the tokenizer snapshot at that revision, verifies the
resolved snapshot identity, loads it locally, requests the model at the same
revision, and rejects a model whose reported commit differs. Changing the
model revision is a reviewed source change rather than a runtime environment
override.

## If We Ship Weights in a Docker Image

If we ever bake the model weights into a public Docker image:
1. Include the full Llama 4 Community License Agreement
2. Include the attribution NOTICE file
3. Display "Built with Llama" in image documentation
4. Ensure the image README references the license
