#!/usr/bin/env python3
"""Run a disposable HTTP-level WorkOS test against the built gateway image.

The harness generates a one-use RSA key and TLS certificate, serves JWKS plus
mock provider endpoints from the host, starts the real LiteLLM container, and
proves public liveness, fail-closed bearer admission, the shared model catalog,
and chat/embedding/rerank/OCR dispatch. No external credentials are required.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import http.server
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


MODEL_CATALOG = (
    "gemini/gemini-3.5-flash",
    "gemini/gemini-3.5-flash-lite",
    "gemini/gemini-3.6-flash",
    "gemini/gemini-embedding-2",
    "gemini/gemini-3.1-flash-image",
    "chatgpt/gpt-5.6-sol",
    "chatgpt/gpt-5.6-terra",
    "chatgpt/gpt-5.6-luna",
    "cohere/rerank-v4.0-fast",
    "qwen/qwen3-embedding-8b",
    "mistral/mistral-ocr-latest",
    "mistral/mistral-ocr-4",
)


def run(*args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        args,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def create_identity_material(directory: Path, issuer: str) -> tuple[dict[str, object], str]:
    key = directory / "jwt-key.pem"
    public_key = directory / "jwt-public.pem"
    certificate = directory / "host-ca.pem"
    run("openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(key))
    run("openssl", "pkey", "-in", str(key), "-pubout", "-out", str(public_key))
    run(
        "openssl",
        "req",
        "-x509",
        "-new",
        "-key",
        str(key),
        "-out",
        str(certificate),
        "-days",
        "1",
        "-subj",
        "/CN=host.docker.internal",
        "-addext",
        "subjectAltName=DNS:host.docker.internal",
    )

    public_text = run("openssl", "pkey", "-pubin", "-in", str(public_key), "-text", "-noout").stdout.decode()
    modulus_match = re.search(r"Modulus:\s*(.*?)\s*Exponent:", public_text, re.DOTALL)
    exponent_match = re.search(r"Exponent:\s*(\d+)", public_text)
    if not modulus_match or not exponent_match:
        raise RuntimeError("Could not parse generated RSA public key")
    modulus = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", modulus_match.group(1))).lstrip(b"\x00")
    exponent = int(exponent_match.group(1)).to_bytes(4, "big").lstrip(b"\x00")
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": "container-e2e",
                "use": "sig",
                "alg": "RS256",
                "n": b64url(modulus),
                "e": b64url(exponent),
            }
        ]
    }

    now = int(time.time())
    header = b64url(json.dumps({"alg": "RS256", "kid": "container-e2e", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64url(
        json.dumps(
            {
                "iss": issuer,
                "aud": "https://llmgateway.aialchemylabs.net",
                "org_id": "org_container_e2e",
                "sub": "user_container_e2e",
                "iat": now,
                "nbf": now - 5,
                "exp": now + 600,
            },
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = run("openssl", "dgst", "-sha256", "-sign", str(key), input_bytes=signing_input).stdout
    return jwks, f"{header}.{payload}.{b64url(signature)}"


class MockHandler(http.server.BaseHTTPRequestHandler):
    jwks: dict[str, object]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: int, value: object) -> None:
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/oauth2/jwks":
            self._json(200, self.jwks)
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/chat/completions"):
            self._json(
                200,
                {
                    "id": "chatcmpl-container-e2e",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": body.get("model", "mock-chat"),
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "container-e2e-chat-ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
            return
        if self.path.endswith("/embeddings"):
            self._json(
                200,
                {
                    "object": "list",
                    "model": body.get("model", "mock-embedding"),
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.25, 0.75]}],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
            )
            return
        if self.path.endswith("/rerank"):
            self._json(200, {"id": "rerank-container-e2e", "results": [{"index": 0, "relevance_score": 0.99}]})
            return
        if self.path.endswith("/ocr"):
            self._json(
                200,
                {
                    "pages": [{"index": 0, "markdown": "container-e2e-ocr-ok", "images": []}],
                    "model": "mistral-ocr-4",
                    "usage_info": {"pages_processed": 1, "doc_size_bytes": 10},
                },
            )
            return
        self._json(404, {"error": "not_found", "path": self.path})


def request(port: int, path: str, *, token: str | None = None, body: object | None = None) -> tuple[int, dict[str, object]]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    method = "POST" if body is not None else "GET"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        with contextlib.suppress(json.JSONDecodeError):
            return exc.code, json.loads(raw or b"{}")
        return exc.code, {"raw": raw.decode(errors="replace")}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="aialchemy-llm-gateway:workos-dev")
    args = parser.parse_args()
    if shutil.which("docker") is None or shutil.which("openssl") is None:
        raise RuntimeError("docker and openssl are required")

    gateway_port = free_port()
    mock_port = free_port()
    issuer = f"https://host.docker.internal:{mock_port}"
    container_name = f"aialchemy-workos-e2e-{os.getpid()}"

    with tempfile.TemporaryDirectory(prefix="aialchemy-workos-e2e-") as raw_directory:
        directory = Path(raw_directory)
        jwks, token = create_identity_material(directory, issuer)
        MockHandler.jwks = jwks
        server = http.server.ThreadingHTTPServer(("0.0.0.0", mock_port), MockHandler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(directory / "host-ca.pem", directory / "jwt-key.pem")
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        provider_base = f"https://host.docker.internal:{mock_port}/v1"
        config = directory / "config.yaml"
        config.write_text(
            f"""model_list:
  - model_name: gemini/gemini-3.5-flash
    model_info: {{mode: chat}}
    litellm_params:
      model: openai/mock-chat
      api_base: {provider_base}
      api_key: mock-provider-key
  - model_name: qwen/qwen3-embedding-8b
    model_info: {{mode: embedding}}
    litellm_params:
      model: openai/text-embedding-3-small
      api_base: {provider_base}
      api_key: mock-provider-key
  - model_name: cohere/rerank-v4.0-fast
    model_info: {{mode: rerank}}
    litellm_params:
      model: cohere/rerank-v4.0-fast
      api_base: {provider_base}
      api_key: mock-provider-key
  - model_name: mistral/mistral-ocr-4
    model_info: {{mode: ocr}}
    litellm_params:
      model: mistral/mistral-ocr-4
      api_base: {provider_base}
      api_key: mock-provider-key
litellm_settings:
  enable_post_custom_auth_checks: true
  drop_params: true
  turn_off_message_logging: true
general_settings:
  custom_auth: aialchemy_auth.runtime.workos_auth
  custom_auth_run_common_checks: true
  allow_requests_on_db_unavailable: false
  allow_client_side_credentials: false
  forward_llm_provider_auth_headers: false
"""
        )

        command = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--add-host",
            "host.docker.internal:host-gateway",
            "-p",
            f"127.0.0.1:{gateway_port}:4000",
            "-e",
            f"WORKOS_ISSUER={issuer}",
            "-e",
            f"WORKOS_JWKS_URL={issuer}/oauth2/jwks",
            "-e",
            "WORKOS_AUDIENCE=https://llmgateway.aialchemylabs.net",
            "-e",
            "WORKOS_ORG_ID=org_container_e2e",
            "-e",
            f"WORKOS_ALLOWED_MODELS={','.join(MODEL_CATALOG)}",
            "-e",
            "WORKOS_MCP_SERVERS=mock-mcp",
            "-e",
            'WORKOS_MCP_TOOL_PERMISSIONS_JSON={"mock-mcp":["safe_tool"]}',
            "-e",
            "SSL_CERT_FILE=/test-ca/host-ca.pem",
            "-v",
            f"{config}:/app/config.yaml:ro",
            "-v",
            f"{directory / 'host-ca.pem'}:/test-ca/host-ca.pem:ro",
            args.image,
        ]

        try:
            run(*command)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                with contextlib.suppress(Exception):
                    status, _ = request(gateway_port, "/health/liveliness")
                    if status == 200:
                        break
                time.sleep(1)
            else:
                logs = run("docker", "logs", container_name).stdout.decode(errors="replace")
                raise RuntimeError(f"Gateway did not become live:\n{logs[-8000:]}")

            status, _ = request(gateway_port, "/v1/models")
            require(status == 401, f"anonymous model discovery returned {status}, expected 401")
            status, _ = request(gateway_port, "/v1/models", token="invalid-token")
            require(status == 401, f"invalid bearer returned {status}, expected 401")
            status, models = request(gateway_port, "/v1/models", token=token)
            require(status == 200, f"valid WorkOS model discovery returned {status}: {models}")
            listed = {item.get("id") for item in models.get("data", []) if isinstance(item, dict)}
            require("gemini/gemini-3.5-flash" in listed, "configured chat model missing from authenticated discovery")
            require("mistral/mistral-ocr-4" in listed, "configured OCR model missing from authenticated discovery")

            status, chat = request(
                gateway_port,
                "/v1/chat/completions",
                token=token,
                body={"model": "gemini/gemini-3.5-flash", "messages": [{"role": "user", "content": "semantic probe"}]},
            )
            require(status == 200, f"chat dispatch returned {status}: {chat}")
            require(chat["choices"][0]["message"]["content"] == "container-e2e-chat-ok", "chat semantic response mismatch")

            status, embedding = request(
                gateway_port,
                "/v1/embeddings",
                token=token,
                body={"model": "qwen/qwen3-embedding-8b", "input": ["embedding probe"]},
            )
            require(status == 200, f"embedding dispatch returned {status}: {embedding}")
            require(embedding["data"][0]["embedding"] == [0.25, 0.75], "embedding response mismatch")

            status, rerank = request(
                gateway_port,
                "/rerank",
                token=token,
                body={"model": "cohere/rerank-v4.0-fast", "query": "probe", "documents": ["probe"]},
            )
            require(status == 200, f"rerank dispatch returned {status}: {rerank}")
            require(rerank["results"][0]["relevance_score"] == 0.99, "rerank response mismatch")

            status, ocr = request(
                gateway_port,
                "/ocr",
                token=token,
                body={"model": "mistral/mistral-ocr-4", "document": {"type": "document_url", "document_url": "https://example.invalid/probe.pdf"}},
            )
            require(status == 200, f"OCR dispatch returned {status}: {ocr}")
            require(ocr["pages"][0]["markdown"] == "container-e2e-ocr-ok", "OCR semantic response mismatch")

            status, denied = request(
                gateway_port,
                "/v1/chat/completions",
                token=token,
                body={"model": "unlisted/model", "messages": [{"role": "user", "content": "deny"}]},
            )
            require(status in {400, 401, 403, 404}, f"unlisted model unexpectedly returned {status}: {denied}")
            print("container-e2e: WorkOS auth and chat/embedding/rerank/OCR dispatch verified")
        finally:
            subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            server.shutdown()
            thread.join(timeout=5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
