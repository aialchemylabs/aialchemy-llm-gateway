# AI Alchemy Must-Have Hermes Guardrail Requirements

**Status:** Approved scope for a future implementation  
**Date:** 2026-08-17  
**Policy:** `aialchemy-global-baseline-v1`  
**Repositories:** `aialchemy-llm-gateway`, `core-infra`, and Hermes  

## 1. Purpose and authority

This document defines one narrow guardrail flow:

```text
Trusted user or JARVIS
        |
        v
     Hermes API
        |
        v
     LiteLLM
        |
        +-- Presidio masks provider-bound PII
        +-- Prompt Guard 2 checks only untrusted web-tool results
        |
        v
 Trusted provider
```

Creating this file does not mean the flow is implemented or tested. It does not authorize code changes, dependency installation, deployment, credential changes, service recreation, or traffic migration. Those are separate steps requiring explicit approval.

The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 2. Fixed scope and trust model

For this phase:

- authenticated AI Alchemy users are trusted;
- Hermes, JARVIS, and the configured provider LLM are trusted;
- content returned by remote web-search, web-extraction, and browser tools is untrusted;
- Presidio protects PII sent to the provider and PII in final user-visible model text; and
- Llama Prompt Guard 2 protects only the untrusted web-result boundary.

Prompt Guard MUST NOT scan:

- the original trusted user prompt;
- the trusted provider's function-call request;
- the trusted provider's final answer; or
- internal non-web tool results in this phase.

Prompt Guard is not tool authorization. Hermes MUST continue to validate tool names, arguments, user authority, and consequential actions in deterministic code.

## 3. Required topology

The target topology MUST be:

```text
JARVIS or trusted client -> Hermes API -> LiteLLM -> trusted provider
```

Requirements:

1. Hermes MUST use a scoped LiteLLM virtual key and an exact provider route.
2. Hermes MUST NOT use the LiteLLM master key.
3. Every initial provider request, tool continuation, retry, and provider-facing follow-up from Hermes MUST pass through LiteLLM.
4. After activation, Hermes MUST NOT retain a direct-provider or unguarded fallback.
5. Hermes workflows MUST NOT permanently use `JARVIS -> LiteLLM -> Hermes -> the same LiteLLM -> provider`.
6. Direct JARVIS model traffic that does not use Hermes MAY continue as `JARVIS -> LiteLLM -> provider`.

## 4. Required flow

### 4.1 Initial request

```text
Trusted user -> Hermes -> LiteLLM
    -> Presidio input masking
    -> web-result Prompt Guard no-op
    -> trusted provider
```

- `aialchemy-pii-input-v1` MUST mask configured PII before the provider call.
- Only the transformed request may reach the provider.
- The Prompt Guard step MUST perform no classification when no untrusted web-tool result is present.

### 4.2 Provider tool call

```text
Trusted provider -> LiteLLM -> Hermes
    -> validate tool name and arguments
    -> execute allowlisted tool
```

- LiteLLM MUST preserve the Responses API function-call structure and return it to Hermes.
- Prompt Guard MUST NOT inspect the provider's tool-call request.
- Hermes MUST validate the exact tool name and arguments against a server-controlled allowlist and schema.
- Unknown tools, invalid arguments, insufficient user authority, and unconfirmed consequential actions MUST be rejected before execution.
- A model or tool result MUST NOT add permissions, change the trusted user identity, or authorize its own action.

### 4.3 Web-tool result and provider continuation

```text
Hermes executes an allowlisted web tool
    -> untrusted web result
    -> Hermes sends function_call + function_call_output to LiteLLM
    -> Presidio masks PII in function_call_output.output
    -> Llama Prompt Guard 2 checks the masked result
        -> safe: send the transformed continuation to the provider
        -> malicious or guard error: block before the provider
```

Requirements:

1. Hermes MUST preserve the function-call ID and tool name as trusted structured provenance outside the tool-returned text.
2. Every web-tool result MUST return through LiteLLM before provider continuation.
3. The LiteLLM guard MUST map `function_call_output.call_id` to the corresponding `function_call.name`.
4. Prompt Guard MUST run only when the mapped name is in a version-controlled web-tool allowlist.
5. The allowlist MUST cover every deployed Hermes tool that returns remote web content, including the deployed equivalents of `web_search`, `web_extract`, and content-returning browser tools.
6. Tool renames and new web tools MUST update the allowlist and tests before activation.
7. Presidio MUST transform `function_call_output.output` before Prompt Guard runs.
8. Prompt Guard and the provider MUST receive only the transformed result.
9. LiteLLM MUST write the transformed output back into the structured Responses continuation.
10. Multiple and out-of-order calls MUST be associated by call ID, not list position.
11. If any inspected part is malicious, the entire provider continuation MUST be blocked.
12. A missing association, malformed result, over-limit result, Prompt Guard timeout, model failure, or invalid classifier response MUST fail closed.

The implementation MUST inspect the raw Responses request. It MUST NOT assume that LiteLLM's generic text extraction includes `function_call_output.output`.

### 4.4 Final answer

```text
Trusted provider -> LiteLLM
    -> Presidio final-output control
    -> Hermes -> trusted user
```

- Prompt Guard MUST NOT inspect a final provider answer.
- `aialchemy-pii-output-v1` MUST inspect final user-visible text before release.
- Intermediate function-call objects MUST remain structurally valid and MUST NOT be treated as final user-visible text.
- Streaming MUST release no uninspected final text. The protected route MUST buffer and safely re-emit the inspected response, or reject client streaming with a stable 4xx until equivalent streaming behavior is proven. Rejection MUST NOT be implemented by silently coercing the request to non-streaming or by fake-streaming an already-complete response. This restriction governs what Hermes requests and what LiteLLM releases to Hermes; a provider that uses SSE internally on the LiteLLM-to-provider leg is unaffected.
- An output-guard failure MUST release no partial provider text.

## 5. Global LiteLLM policy

LiteLLM MUST own one organization-controlled policy named `aialchemy-global-baseline-v1`. It MUST apply automatically to every Hermes request.

The required logical order is:

```text
Provider-bound content
    1. aialchemy-pii-input-v1
    2. aialchemy-web-tool-result-v1
         - no web function_call_output: no-op
         - web function_call_output: Prompt Guard 2 on Presidio output
    3. trusted provider

Final user-visible content
    4. aialchemy-pii-output-v1
    5. Hermes and trusted user
```

Requirements:

- transformed data from Presidio MUST be the only data passed to Prompt Guard and the provider;
- mandatory failures and technical errors MUST block;
- parallel execution is prohibited where it could expose the pre-Presidio value;
- clients MUST NOT disable, replace, reorder, or weaken the policy; and
- production policy configuration MUST be reviewed and version controlled, not an unexported UI-only edit.

## 6. Presidio requirements

Presidio MUST be self-hosted on a private service network. It MUST use non-reversible typed replacement such as `<EMAIL_ADDRESS>` or `<PERSON_1>`.

The first entity manifest MUST mask at least:

| Coverage | Required entities |
|---|---|
| Australia | `AU_TFN`, `AU_MEDICARE`, and Australian passport numbers through a tested built-in or version-controlled custom recognizer |
| India | `IN_AADHAAR`, `IN_PAN`, `IN_PASSPORT`, `IN_VOTER`, and `IN_VEHICLE_REGISTRATION` |
| General identity and contact | `PERSON`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, and `LOCATION` |
| General financial | `CREDIT_CARD`, `IBAN_CODE`, and supported personal bank-account identifiers used by enabled workflows |

Australian ABNs and ACNs and Indian GSTINs are excluded from automatic masking in this first policy because they are generally business identifiers. This is an implementation policy, not a claim that they can never identify an individual. A later change requires an explicit action and tests.

Additional requirements:

- recognizer versions, actions, and thresholds MUST be fixed in configuration and tested;
- Hindi free-text PII support MUST NOT be claimed merely because structured Indian identifiers work in English text;
- original-to-placeholder maps MUST NOT be persisted or exposed;
- masked PII MUST NOT be restored into the response;
- PII, prompts, tool results, and reversible mappings MUST NOT appear in logs, traces, metrics, or spend logs; and
- analyzer or anonymizer failure MUST fail closed at the applicable boundary.

## 7. Prompt Guard requirements

The first implementation MUST use `meta-llama/Llama-Prompt-Guard-2-86M`.

Requirements:

1. The exact model revision and artifacts MUST be pinned and integrity checked.
2. License obligations MUST be recorded before publication or deployment.
3. Prompt Guard MUST be integrated only at LiteLLM; Hermes and JARVIS MUST NOT have separate adapters.
4. The first implementation SHOULD load the model through the custom LiteLLM guardrail so no standalone Prompt Guard service or repository is required.
5. If in-process loading cannot meet the approved memory, startup, latency, or availability envelope, implementation MUST stop for approval before adding a sidecar or separate service.
6. A web result longer than the supported model context MUST be divided into bounded, overlapping chunks. Truncation is prohibited.
7. Every chunk MUST be classified, and one malicious chunk MUST block the whole continuation.
8. Threshold, chunk size, overlap, maximum result size, maximum chunk count, and timeout MUST be finite and version controlled.
9. Classifier input content MUST NOT be logged.

PIGuard, `gpt-oss-safeguard`, and other moderation models are outside this first runtime path.

## 8. Repository responsibilities

`aialchemy-llm-gateway` owns:

- the Responses-aware custom guard that inspects and rewrites `function_call_output.output`;
- the narrow, fail-closed LiteLLM compatibility patch that guarantees the selected guardrail is invoked for Responses continuations carrying only `function_call` / `function_call_output` items, which the pinned release otherwise skips;
- the protected-route streaming rejection guard;
- Prompt Guard model integration and pinned dependencies if implementation is approved; and
- image-level compatibility and regression tests, which MUST be executed in the image build rather than only imported.

`core-infra` owns:

- the global policy configuration and attachment;
- private Presidio services and recognizer configuration, including a version-controlled custom Australian passport recognizer — Presidio ships no built-in `AU_PASSPORT` recognizer, so listing the entity name alone detects nothing;
- proof that every required country-specific recognizer (Australian and Indian) is explicitly enabled in the deployed analyzer;
- the web-tool-name allowlist, health checks, limits, scoped key, and exact route; and
- deployment, rollback, and end-to-end evidence.

Hermes owns:

- using LiteLLM as its only provider path after activation;
- preserving Responses call IDs and function-call semantics;
- deterministic tool authorization and execution; and
- returning every web result through LiteLLM before provider continuation.

JARVIS and other trusted clients own calling Hermes directly for Hermes workflows and MUST NOT bypass a blocked or unavailable guardrail.

## 9. Acceptance tests

Tests MUST use synthetic data and the real Responses structures used by Hermes. HTTP 200, health, model visibility, or a guardrail listing is not acceptance evidence.

The minimum release suite MUST prove:

1. A trusted request reaches Hermes, LiteLLM, and the intended provider exactly once each.
2. Hermes has no working direct-provider or unguarded continuation fallback.
3. Australian, Indian, and general PII is masked in a mock-provider capture.
4. Prompt Guard is not invoked for a trusted user prompt, provider tool call, final provider answer, or non-web internal tool result.
5. Valid provider function calls preserve call ID, name, arguments, and continuation behavior.
6. Hermes rejects unknown tools, invalid arguments, and unauthorized or unconfirmed actions.
7. Every configured web tool invokes Prompt Guard on every required result chunk.
8. Presidio masks web-result PII before Prompt Guard and before the provider.
9. Benign web content continues through the provider and produces a semantically correct final answer.
10. Malicious web content causes zero provider continuation calls.
11. Multiple and out-of-order tool results map to the correct tool by call ID.
12. Presidio and Prompt Guard outages, timeouts, malformed responses, and size-limit failures fail closed without fallback.
13. Final response PII is transformed before the user receives it.
14. Output-guard failure releases no partial text.
15. Non-streaming Responses remain structurally valid and semantically usable, and a protected-route request with `stream: true` is rejected with a stable 4xx before provider dispatch — never silently coerced to non-streaming and never fake-streamed.
16. No synthetic original, prompt, web result, or reversible mapping appears in logs or telemetry.
17. Client-supplied guardrail fields cannot disable or weaken `aialchemy-global-baseline-v1`.

The final gate MUST run two native Hermes sessions:

- a benign web-tool session that completes successfully; and
- a controlled indirect-injection session that is blocked before provider continuation.

## 10. Explicitly out of scope

This phase does not include:

- prompt-injection scanning of trusted user prompts;
- Prompt Guard scanning of provider tool calls or final answers;
- generic jailbreak, harmful-content, NSFW, competitor, or brand filtering;
- secret scanning beyond the configured Presidio entities;
- LiteLLM MCP brokerage or MCP-wide guardrails;
- prompt-injection protection for internal non-web tools;
- RAG, vector-store, memory-ingestion, email, file, or document guardrails;
- a multi-model guard pipeline;
- a standalone Prompt Guard service unless separately approved after measurement;
- reversible PII rehydration; or
- legal certification or a claim of complete Australian or Indian privacy-law compliance.

## 11. Definition of done

This work is complete only when every requirement and acceptance test above passes on the pinned production candidate, the benign and malicious native Hermes sessions produce the expected semantic outcomes, and deployment and rollback evidence is reviewed before production activation.

Implementation MUST stop for owner direction if the pinned LiteLLM build cannot safely rewrite Responses `function_call_output.output`, the ordered transformed-data flow cannot be proven, Hermes cannot remove direct-provider fallback, Prompt Guard cannot meet the approved in-process envelope, or any mandatory guard can fail open.

## 12. Primary references

- [LiteLLM guardrail policies](https://docs.litellm.ai/docs/proxy/guardrails/guardrail_policies)
- [LiteLLM policy flow builder](https://docs.litellm.ai/docs/proxy/guardrails/policy_flow_builder)
- [LiteLLM custom guardrails](https://docs.litellm.ai/docs/proxy/guardrails/custom_guardrail)
- [LiteLLM Presidio PII masking](https://docs.litellm.ai/docs/proxy/guardrails/pii_masking_v2)
- [Meta Llama Prompt Guard 2 86M model card](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M)
- [Microsoft Presidio supported entities](https://microsoft.github.io/presidio/supported_entities/)


## More References: 
**Prompt-Guard-86M** is an encoder-only sequence classification model based on `mDeBERTa-v3`. Because it is a classifier rather than a text generator, Ollama doesn't support it.

However, because it only has 86 million parameters, it is incredibly lightweight and will run blazingly fast on this machine using standard Python.

Here is exactly how you can run it locally and hook it up to LiteLLM.

---

### Step 1: Set up a Python Environment

You will need the Hugging Face `transformers` and `torch` libraries to run the model locally, plus `fastapi` to serve it to LiteLLM. Open your terminal and run:

```bash
pip install transformers torch litellm fastapi uvicorn httpx

```

### Step 2: Serve Prompt-Guard as a Local API

The cleanest way to integrate this with LiteLLM is to spin up a tiny local API server that checks prompts for injections or jailbreaks.

Save this code as `guard_server.py`:

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import pipeline

app = FastAPI()

# Load the Prompt Guard model locally via Hugging Face
print("Loading Prompt-Guard-86M...")
classifier = pipeline("text-classification", model="meta-llama/Prompt-Guard-86M")
print("Model loaded!")

class GuardRequest(BaseModel):
    text: str

@app.post("/check")
def check_prompt(req: GuardRequest):
    # The model returns labels: BENIGN, INJECTION, or JAILBREAK
    result = classifier(req.text)[0]
    
    if result["label"] in ["INJECTION", "JAILBREAK"]:
        return {"action": "BLOCK", "reason": result["label"], "score": result["score"]}
    
    return {"action": "ALLOW", "reason": "BENIGN"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

```

Run the server in your terminal:

```bash
python guard_server.py

```

### Step 3: Connect LiteLLM to your Guard Server

LiteLLM allows you to write custom guardrail classes that intercept traffic. You can create a script that pauses the incoming prompt, sends it to your local `guard_server.py`, and blocks the request if it's flagged as malicious.

Create a file called `custom_guard.py` in the same directory where you run LiteLLM:

```python
import httpx
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.utils import GenericGuardrailAPIInputs
from typing import Literal

class MyPromptGuard(CustomGuardrail):
    async def apply_guardrail(
        self, 
        inputs: GenericGuardrailAPIInputs, 
        request_data: dict, 
        input_type: Literal["request", "response"], 
        logging_obj=None
    ) -> GenericGuardrailAPIInputs:
        
        # We only want to check the incoming user requests
        if input_type == "request":
            async with httpx.AsyncClient() as client:
                for text in inputs.get("texts", []):
                    # Send the prompt to your local FastAPI server
                    response = await client.post("http://localhost:8000/check", json={"text": text})
                    result = response.json()
                    
                    if result.get("action") == "BLOCK":
                        raise Exception(f"Blocked by Prompt-Guard! Reason: {result.get('reason')}")
        
        return inputs

```

### Step 4: Add it to your LiteLLM Config

Finally, configure LiteLLM to use your new custom guardrail, and point your generative model to your local Ollama instance.

Create a `config.yaml`:

```yaml
model_list:
  - model_name: my-local-model
    litellm_params:
      # Replace this with whatever model you are running in Ollama
      model: ollama/llama3 
      api_base: http://localhost:11434

litellm_settings:
  custom_guardrail: custom_guard.MyPromptGuard

guardrails:
  - guardrail_name: my_prompt_guard
    litellm_params:
      guardrail: custom_guardrail
      mode: pre_call
      default_on: true

```

Now, when you start the LiteLLM proxy (`litellm --config config.yaml`), every incoming prompt will first be scanned by your local Prompt-Guard-86M instance. If a user attempts a prompt injection or a jailbreak, LiteLLM will intercept and block it before it ever reaches the generative LLM running inside your Ollama setup.