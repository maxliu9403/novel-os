"""
Novel OS - Pluggable LLM Client

Supports any LLM provider via these backend types:

  - claude_cli   Claude Code CLI (no API key uses your `claude` login / subscription)
  - codex        Codex CLI (uses the current Codex login, no API key copied)
  - anthropic    Claude (anthropic SDK)
  - openai       Native OpenAI (openai SDK)
  - azure        Azure OpenAI (openai SDK, AzureOpenAI client)
  - gemini       Google Gemini (google-genai SDK)
  - openai_compatible  ANY endpoint that speaks OpenAI's /v1/chat/completions:
                       Kimi/Moonshot, Together, Groq, OpenRouter, DeepSeek,
                       Mistral, Fireworks, Ollama, vLLM, LM Studio, etc.

Convenience aliases (provider names that just preset openai_compatible's base_url):
  kimi, moonshot, groq, together, openrouter, deepseek, mistral, fireworks,
  ollama, lmstudio.

Configure via environment (or .env in project root):

  NOVEL_OS_LLM_PROVIDER    one of the names above (auto-detected if unset)
  NOVEL_OS_MODEL           model id / Azure deployment name
  NOVEL_OS_API_KEY         generic key for openai_compatible / aliases
  NOVEL_OS_BASE_URL        endpoint URL for 'openai_compatible'
  NOVEL_OS_MAX_TOKENS      int, default 8192

Provider-native keys also work as fallbacks:
  ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY / GOOGLE_API_KEY,
  KIMI_API_KEY / MOONSHOT_API_KEY, GROQ_API_KEY, TOGETHER_API_KEY,
  OPENROUTER_API_KEY, DEEPSEEK_API_KEY, MISTRAL_API_KEY, FIREWORKS_API_KEY,
  AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_VERSION.
"""

from __future__ import annotations

from contextvars import copy_context
try:
    from core.usage_tracking import current_request, http_attempt_started, observe_usage, tracking_enabled, usage_request
except ModuleNotFoundError as exc:
    if exc.name != "core":
        raise
    from usage_tracking import current_request, http_attempt_started, observe_usage, tracking_enabled, usage_request

import base64
from datetime import datetime, timezone
import json
import os
import signal
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Tuple


# Providers whose chat-completions transport accepts image input. Named
# aliases without this capability must not receive image bytes merely because
# they share the OpenAI shape. Arbitrary openai_compatible endpoints are
# validated by the vision-route connection capability.
IMAGE_INPUT_PROVIDERS = frozenset({"codex", "openai", "azure", "openai_compatible", "openrouter"})
# DeepSeek V4 exposes low/high/max thinking effort through the OpenAI-format
# ``reasoning_effort`` field. Its default is high, so an empty route field
# intentionally omits the parameter.
DEEPSEEK_REASONING_EFFORTS = frozenset({"low", "high", "max"})
# DeepSeek counts thinking tokens against max_tokens. A route's normal 8K
# output budget can be entirely consumed by a max-effort reasoning pass,
# leaving an empty content string. Keep a floor that leaves room for the
# actual answer; the provider still bills only tokens it generates.
DEEPSEEK_MIN_OUTPUT_TOKENS = {"low": 8192, "high": 16384, "max": 32768}

DEFAULT_MAX_TOKENS = 8192
DEFAULT_OPENAI_TIMEOUT_SECONDS = 1800.0
DEFAULT_OPENAI_SDK_MAX_RETRIES = 0

# Convenience aliases -> (base_url, default_model, env_var_name for key)
OPENAI_COMPAT_ALIASES = {
    "kimi":       ("https://api.moonshot.ai/v1",      "kimi-k2-0905-preview",        "KIMI_API_KEY"),
    "moonshot":   ("https://api.moonshot.ai/v1",      "kimi-k2-0905-preview",        "MOONSHOT_API_KEY"),
    "groq":       ("https://api.groq.com/openai/v1",  "llama-3.3-70b-versatile",     "GROQ_API_KEY"),
    "together":   ("https://api.together.xyz/v1",     "meta-llama/Llama-3.3-70B-Instruct-Turbo", "TOGETHER_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1",    "anthropic/claude-3.5-sonnet", "OPENROUTER_API_KEY"),
    "deepseek":   ("https://api.deepseek.com/v1",     "deepseek-chat",               "DEEPSEEK_API_KEY"),
    "mistral":    ("https://api.mistral.ai/v1",       "mistral-large-latest",        "MISTRAL_API_KEY"),
    "fireworks":  ("https://api.fireworks.ai/inference/v1", "accounts/fireworks/models/llama-v3p3-70b-instruct", "FIREWORKS_API_KEY"),
    "ollama":     ("http://localhost:11434/v1",       "llama3.2",                    "OLLAMA_API_KEY"),  # key usually ignored
    "lmstudio":   ("http://localhost:1234/v1",        "local-model",                 "LMSTUDIO_API_KEY"),
    "nvidia":     ("https://integrate.api.nvidia.com/v1", "meta/llama-3.3-70b-instruct", "NVIDIA_API_KEY"),
}

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-4o"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
DEFAULT_AZURE_API_VERSION = "2024-10-21"


# --------------------------------------------------------------------------- .env

def _load_dotenv_if_present() -> None:
    """Load .env from cwd. Uses python-dotenv if installed, else a minimal parser."""
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(env_path)
        return
    except ImportError:
        pass
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv_if_present()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _openai_compatible_options(timeout_seconds: float | None = None) -> dict:
    return {
        "timeout": timeout_seconds if timeout_seconds is not None else float(
            os.environ.get("NOVEL_OS_LLM_TIMEOUT_SECONDS", DEFAULT_OPENAI_TIMEOUT_SECONDS)
        ),
        "max_retries": int(
            os.environ.get(
                "NOVEL_OS_SDK_MAX_RETRIES",
                DEFAULT_OPENAI_SDK_MAX_RETRIES,
            )
        ),
    }


class LLMError(RuntimeError):
    """Raised when the LLM call cannot be made or fails."""


class LLMCancelledError(LLMError):
    """The caller cancelled this completion; it must not be retried automatically."""


# --------------------------------------------------------------------------- client

class LLMClient:
    """Provider-agnostic LLM client.

    Resolution order when ``provider`` is None:
      1. NOVEL_OS_LLM_PROVIDER env var
      2. First provider whose native key is set, in this order:
         anthropic, openai, azure, gemini, then any openai_compatible alias.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: float | None = None,
        reasoning_effort: str | None = None,
        azure_endpoint: str | None = None,
        azure_api_version: str | None = None,
        cancellation_event: threading.Event | None = None,
        diagnostic_callback: Callable[[dict], None] | None = None,
    ):
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("LLM timeout must be positive")
        self.cancellation_event = cancellation_event
        self.diagnostic_callback = diagnostic_callback
        self._check_cancelled()
        self.provider_name = (provider or self._resolve_provider()).lower()
        self.max_tokens = max_tokens or int(os.environ.get("NOVEL_OS_MAX_TOKENS", DEFAULT_MAX_TOKENS))
        self._explicit_base_url = base_url
        self._explicit_api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.reasoning_effort = reasoning_effort
        self.azure_endpoint = azure_endpoint
        self.azure_api_version = azure_api_version

        # Map alias -> openai_compatible with preset base_url/model/key
        self._backend, self.model = self._build_backend(model)
        # Official SDKs use httpx; its hook runs once per actual HTTP send,
        # including SDK-managed retries. Do not alter their retry policy.
        http_clients = (getattr(self._backend, "_client", None),
                        getattr(getattr(self._backend, "_api_client", None), "_httpx_client", None))
        for client in http_clients:
            hooks = getattr(client, "event_hooks", None)
            if isinstance(hooks, dict) and http_attempt_started not in hooks.get("request", []):
                hooks.setdefault("request", []).append(http_attempt_started)

    # ----- resolution

    @staticmethod
    def _resolve_provider() -> str:
        env_pick = os.environ.get("NOVEL_OS_LLM_PROVIDER")
        if env_pick:
            return env_pick
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        if os.environ.get("AZURE_OPENAI_API_KEY") and os.environ.get("AZURE_OPENAI_ENDPOINT"):
            return "azure"
        if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            return "gemini"
        for alias, (_, _, key_env) in OPENAI_COMPAT_ALIASES.items():
            if os.environ.get(key_env):
                return alias
        if os.environ.get("NOVEL_OS_API_KEY") and os.environ.get("NOVEL_OS_BASE_URL"):
            return "openai_compatible"
        # No paid key anywhere fall back to the Claude Code CLI if it's installed,
        # so subscription users get zero-config, zero-cost generation.
        if shutil.which("claude"):
            return "claude_cli"
        raise LLMError(
            "No LLM provider configured. Set NOVEL_OS_LLM_PROVIDER and a key, or "
            "one of: ANTHROPIC_API_KEY, OPENAI_API_KEY, AZURE_OPENAI_API_KEY, "
            "GEMINI_API_KEY, KIMI_API_KEY, GROQ_API_KEY, TOGETHER_API_KEY, "
            "OPENROUTER_API_KEY, DEEPSEEK_API_KEY, MISTRAL_API_KEY, "
            "FIREWORKS_API_KEY, or NOVEL_OS_BASE_URL+NOVEL_OS_API_KEY. "
            "Or install the Claude Code CLI and run `claude login` (no API key needed), "
            "then run `python -m core.setup_wizard`."
        )

    def _build_backend(self, model: Optional[str]) -> Tuple[object, str]:
        name = self.provider_name
        env_model = os.environ.get("NOVEL_OS_MODEL")

        if name == "claude_cli":
            # No SDK/client to build; the backend is the `claude` CLI itself.
            # A model is optional if unset, the CLI uses its configured default.
            return self._build_claude_cli(), model or env_model or ""
        if name == "codex":
            return self._build_codex_cli(), model or env_model or ""
        if name == "anthropic":
            return self._build_anthropic(), model or env_model or DEFAULT_ANTHROPIC_MODEL
        if name == "openai":
            return self._build_openai_native(), model or env_model or DEFAULT_OPENAI_MODEL
        if name == "azure":
            backend, deployment = self._build_azure(model or env_model)
            return backend, deployment
        if name == "gemini":
            return self._build_gemini(), model or env_model or DEFAULT_GEMINI_MODEL
        if name in OPENAI_COMPAT_ALIASES:
            base_url, default_model, key_env = OPENAI_COMPAT_ALIASES[name]
            key = self._explicit_api_key or os.environ.get(key_env) or os.environ.get("NOVEL_OS_API_KEY") or "not-needed"
            base_url = self._explicit_base_url or os.environ.get(f"{name.upper()}_BASE_URL", base_url)
            return self._build_openai_compatible(base_url, key), model or env_model or default_model
        if name == "openai_compatible":
            base_url = self._explicit_base_url or os.environ.get("NOVEL_OS_BASE_URL")
            key = self._explicit_api_key or os.environ.get("NOVEL_OS_API_KEY") or "not-needed"
            if not base_url:
                raise LLMError("openai_compatible requires NOVEL_OS_BASE_URL (or base_url=).")
            if not (model or env_model):
                raise LLMError("openai_compatible requires NOVEL_OS_MODEL (or model=).")
            return self._build_openai_compatible(base_url, key), model or env_model
        raise LLMError(f"Unknown provider '{name}'.")

    # ----- backend builders

    def _build_claude_cli(self) -> str:
        """Locate the Claude Code CLI. The 'backend' is just the path to the binary."""
        cli = shutil.which("claude")
        if not cli:
            raise LLMError(
                "The Claude Code CLI ('claude') was not found on PATH. Install it from "
                "https://docs.claude.com/claude-code, then run `claude login`."
            )
        return cli

    def _build_codex_cli(self) -> str:
        """Locate Codex; authentication remains in Codex's own credential store."""
        cli = shutil.which("codex")
        if not cli:
            raise LLMError(
                "The Codex CLI was not found in the API runtime. Install Codex and "
                "run `codex login`, or choose another provider connection."
            )
        return cli

    def _build_anthropic(self):
        try:
            import anthropic  # type: ignore
        except ImportError as e:
            raise LLMError("Install: pip install anthropic") from e
        key = self._explicit_api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY is not set.")
        options = {"timeout": self.timeout_seconds} if self.timeout_seconds is not None else {}
        if self.cancellation_event is not None:
            options["max_retries"] = 0
        if self._explicit_base_url:
            options["base_url"] = self._explicit_base_url
        return anthropic.Anthropic(api_key=key, **options)

    def _build_openai_native(self):
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as e:
            raise LLMError("Install: pip install openai") from e
        key = self._explicit_api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY is not set.")
        options = {"timeout": self.timeout_seconds} if self.timeout_seconds is not None else {}
        if self.cancellation_event is not None:
            options["max_retries"] = 0
        if self._explicit_base_url:
            options["base_url"] = self._explicit_base_url
        return OpenAI(api_key=key, **options)

    def _build_openai_compatible(self, base_url: str, api_key: str):
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as e:
            raise LLMError("Install: pip install openai") from e
        options = _openai_compatible_options(self.timeout_seconds)
        if self.cancellation_event is not None:
            options["max_retries"] = 0
        return OpenAI(
            api_key=api_key,
            base_url=base_url,
            **options,
        )

    def _build_azure(self, deployment: Optional[str]):
        try:
            from openai import AzureOpenAI  # type: ignore
        except ImportError as e:
            raise LLMError("Install: pip install openai") from e
        key = self._explicit_api_key or os.environ.get("AZURE_OPENAI_API_KEY")
        endpoint = self.azure_endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT")
        api_version = self.azure_api_version or os.environ.get("AZURE_OPENAI_API_VERSION", DEFAULT_AZURE_API_VERSION)
        self.azure_endpoint = endpoint
        self.azure_api_version = api_version
        if not (key and endpoint):
            raise LLMError("Azure needs AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT.")
        if not deployment:
            raise LLMError("Azure needs NOVEL_OS_MODEL set to the deployment name.")
        options = {"timeout": self.timeout_seconds} if self.timeout_seconds is not None else {}
        if self.cancellation_event is not None:
            options["max_retries"] = 0
        client = AzureOpenAI(
            api_key=key,
            api_version=api_version,
            azure_endpoint=endpoint,
            **options,
        )
        return client, deployment

    def _build_gemini(self):
        try:
            from google import genai  # type: ignore
        except ImportError as e:
            raise LLMError("Install: pip install google-genai") from e
        key = self._explicit_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set.")
        if self.cancellation_event is not None:
            from google.genai import types  # type: ignore
            options = {"retry_options": types.HttpRetryOptions(attempts=1)}
            if self.timeout_seconds is not None:
                options["timeout"] = int(self.timeout_seconds * 1000)
            return genai.Client(api_key=key, http_options=types.HttpOptions(**options))
        return genai.Client(api_key=key)

    # ----- public API

    @property
    def provider(self) -> str:
        return self.provider_name

    @property
    def supports_image_input(self) -> bool:
        return self.provider_name in IMAGE_INPUT_PROVIDERS

    def complete(self, system: str, user: str) -> str:
        self._check_cancelled()
        with usage_request(self.provider_name, self.model):
            return self._complete_observed(system, user)

    def _complete_observed(self, system: str, user: str) -> str:
        """Single-turn message → assistant text."""
        if getattr(self, "diagnostic_callback", None) is None:
            return self._complete_text(system, user)
        started = time.monotonic()
        self._record_diagnostic("request.start", provider=self.provider_name, model=self.model,
                                reasoning_effort=self.reasoning_effort, timeout_seconds=self.timeout_seconds,
                                max_tokens=self.max_tokens, configured_max_tokens=self.max_tokens,
                                effective_max_tokens=None if self.provider_name in {"codex", "claude_cli"} else self.max_tokens,
                                max_tokens_supported=self.provider_name not in {"codex", "claude_cli"},
                                timeout_scope="local_process" if self.provider_name in {"codex", "claude_cli"} else "sdk_request",
                                system_chars=len(system), user_chars=len(user),
                                system_bytes=len(system.encode("utf-8")), user_bytes=len(user.encode("utf-8")))
        try:
            result = self._complete_text(system, user)
        except BaseException as exc:
            event = "request.failed"
            if isinstance(exc, LLMCancelledError):
                event = "request.cancelled"
            elif isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) or isinstance(
                exc.__cause__, (TimeoutError, subprocess.TimeoutExpired)
            ) or "timeout" in type(exc).__name__.lower():
                event = "request.timeout"
            self._record_diagnostic(event, elapsed_seconds=round(time.monotonic() - started, 3),
                                    error_type=type(exc).__name__)
            raise
        self._record_diagnostic("request.completed", elapsed_seconds=round(time.monotonic() - started, 3),
                                output_chars=len(result), output_bytes=len(result.encode("utf-8")))
        return result

    def _record_diagnostic(self, event: str, **fields) -> None:
        """Metadata only. Logging failure must never change model execution."""
        if fields.get("usage"):
            observe_usage(fields["usage"], provider=self.provider_name,
                          replace=fields.get("usage_scope") == "request_total",
                          incomplete_fields=fields.get("usage_incomplete_fields", ()))
        callback = getattr(self, "diagnostic_callback", None)
        if callback is not None:
            try:
                callback({**fields, "event": event, "time": datetime.now(timezone.utc).isoformat(),
                          "request_id": current_request().request_id if current_request() else None})
            except Exception:
                pass

    def _complete_text(self, system: str, user: str) -> str:
        self._check_cancelled()
        if self.provider_name == "claude_cli":
            return self._complete_claude_cli(system, user)
        if self.provider_name == "codex":
            return self._complete_codex_cli(system, user)
        if self.provider_name == "anthropic":
            return self._call_http(lambda: self._complete_anthropic(system, user))
        if self.provider_name == "gemini":
            return self._call_http(lambda: self._complete_gemini(system, user))
        # openai, azure, openai_compatible, and all aliases share the chat-completions shape
        return self._call_http(lambda: self._complete_openai_shape(system, user))

    def _check_cancelled(self) -> None:
        event = getattr(self, "cancellation_event", None)
        if event is not None and event.is_set():
            raise LLMCancelledError("Model request was cancelled")

    def _close_cancelled_http(self) -> None:
        # Closing a synchronous SDK can itself block. Stop the caller's wait
        # immediately and close local transports independently, best effort.
        def close(resource):
            try:
                closer = getattr(resource, "close", None)
                if callable(closer):
                    closer()
            except Exception:
                pass

        for resource in (getattr(self, "_active_stream", None), self._backend):
            if resource is not None:
                threading.Thread(target=close, args=(resource,), daemon=True,
                                 name="llm-cancel-close").start()

    def _call_http(self, call) -> str:
        event = getattr(self, "cancellation_event", None)
        if event is None:
            return call()
        self._check_cancelled()
        completed = threading.Event()
        result = {}

        def request():
            try:
                self._check_cancelled()
                result["value"] = call()
            except BaseException as exc:
                result["error"] = exc
            finally:
                completed.set()

        # Python cannot terminate a blocked SDK thread. Cancellation releases
        # this local wait; close() usually interrupts transport I/O. A remote
        # provider may still finish the request, whose late result is discarded.
        threading.Thread(target=copy_context().run, args=(request,), daemon=True, name="llm-http-request").start()
        while True:
            done = completed.wait(0.05)
            if event.is_set():
                self._close_cancelled_http()
                raise LLMCancelledError("Model request was cancelled; remote completion may still be running")
            if done:
                if "error" in result:
                    raise result["error"]
                return result["value"]

    def complete_with_images(self, system: str, user: str, images) -> str:
        self._check_cancelled()
        with usage_request(self.provider_name, self.model):
            return self._complete_with_images_observed(system, user, images)

    def _complete_with_images_observed(
        self,
        system: str,
        user: str,
        images: tuple[bytes, ...] | list[bytes],
    ) -> str:
        """Single-turn multimodal review used by the cover semantic gate.

        Codex receives temporary local attachments. OpenAI-shaped providers
        receive data URLs. Unsupported text-only routes fail explicitly so the
        cover pipeline can preserve the candidate for human review.
        """
        payload = tuple(bytes(item) for item in images if item)
        self._check_cancelled()
        if not payload:
            raise LLMError("Multimodal completion requires at least one image")
        if self.provider_name not in IMAGE_INPUT_PROVIDERS:
            raise LLMError(
                f"Provider '{self.provider_name}' does not support image input. "
                "Configure the vision route to Codex, OpenAI, OpenRouter, or a "
                "vision-capable OpenAI-compatible connection."
            )
        if self.provider_name == "codex":
            return self._complete_codex_cli(system, user, images=payload)
        return self._call_http(lambda: self._complete_openai_shape_with_images(system, user, payload))

    def run_agent(self, agent_name: str, user: str, agents_dir: Optional[Path] = None) -> str:
        base = agents_dir or (Path(__file__).resolve().parent.parent / "agents")
        prompt_path = base / agent_name / "prompt.md"
        if not prompt_path.exists():
            raise LLMError(f"Agent prompt not found: {prompt_path}")
        return self.complete(prompt_path.read_text(encoding="utf-8"), user)

    # ----- backend completions

    def _complete_claude_cli(self, system: str, user: str) -> str:
        """Run the prompt through the Claude Code CLI in non-interactive print mode.

        Uses the user's `claude` login (subscription) no API key, no per-token billing.
        The user prompt is passed on stdin (not argv) to avoid length/quoting limits.
        """
        cli = self._backend  # path to the claude binary
        cmd = [
            cli,
            "-p",
            "--output-format", "json",
        ]
        if self.model:
            cmd += ["--model", self.model]
        # Fold the agent's system prompt into the message turn. Passing it via
        # --append-system-prompt buries the instruction beneath Claude Code's own
        # interactive base prompt, which makes the model ask for confirmation
        # instead of executing the task. Inlining the directive keeps it imperative.
        #
        # The preamble makes the CLI behave like a plain completion API: other
        # providers treat the system prompt as a standing order and just produce
        # the artifact; the `claude` CLI is interactive by default and will ask
        # clarifying questions unless told to act. This keeps behavior consistent.
        preamble = (
            "You are operating as a non-interactive text-generation engine, not a "
            "conversational assistant. Follow the ROLE and TASK below exactly and "
            "output ONLY the requested artifact (e.g. the prose, the outline, the "
            "JSON). Do not ask questions, do not add preamble, commentary, or "
            "meta-discussion. If information is missing, make reasonable creative "
            "choices and proceed.\n\n"
        )
        prompt = f"{preamble}# ROLE\n{system}\n\n# TASK / CONTEXT\n{user}" if system else user
        run_options = {"timeout": self.timeout_seconds} if self.timeout_seconds is not None else {}
        try:
            if self.cancellation_event is not None:
                proc = self._run_cancellable_cli(cmd, prompt, self.timeout_seconds, self.cancellation_event)
            else:
                proc = subprocess.run(
                    cmd, input=prompt, capture_output=True, text=True,
                    encoding="utf-8", **run_options,
                )
        except subprocess.TimeoutExpired as e:
            raise LLMError("Claude Code CLI timed out") from e
        except OSError as e:
            raise LLMError(f"Failed to invoke the Claude Code CLI: {e}") from e
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            hint = ""
            if "login" in stderr.lower() or "auth" in stderr.lower() or not stderr:
                hint = " you may need to run `claude login`."
            raise LLMError(f"Claude Code CLI failed (exit {proc.returncode}): {stderr}{hint}")
        out = (proc.stdout or "").strip()
        if not out:
            raise LLMError("Claude Code CLI returned no output.")
        try:
            data = json.loads(out)
        except json.JSONDecodeError as e:
            raise LLMError(f"Could not parse Claude Code CLI output as JSON: {e}") from e
        if isinstance(data, dict):
            observe_usage(data.get("usage"), provider="anthropic")
        # The print-mode JSON envelope carries the assistant text in "result".
        text = data.get("result") if isinstance(data, dict) else None
        if not text:
            raise LLMError(f"Claude Code CLI JSON had no 'result' text: {out[:200]}")
        return text

    def _complete_codex_cli(
        self,
        system: str,
        user: str,
        *,
        images: tuple[bytes, ...] = (),
    ) -> str:
        """Run Codex as a read-only, ephemeral, non-interactive completion."""
        cli = str(self._backend)
        preamble = (
            "Act only as a non-interactive text-generation engine. Follow the ROLE "
            "and TASK exactly. Return only the requested artifact, with no questions, "
            "preamble, progress report, or meta commentary. Do not inspect or modify "
            "workspace files. Make reasonable creative choices when context is missing.\n\n"
        )
        prompt = f"{preamble}# ROLE\n{system}\n\n# TASK / CONTEXT\n{user}" if system else f"{preamble}{user}"
        run_options = {"timeout": self.timeout_seconds} if self.timeout_seconds is not None else {}
        with tempfile.TemporaryDirectory(prefix="novel-os-codex-") as directory:
            output_path = Path(directory) / "last-message.txt"
            diagnostics = None
            if getattr(self, "diagnostic_callback", None) is not None or tracking_enabled():
                try:
                    from .llm_diagnostics import CLIDiagnostics
                except ImportError:  # Standalone imports used by the CLI entry points.
                    from llm_diagnostics import CLIDiagnostics
                diagnostics = CLIDiagnostics(lambda entry: self._record_diagnostic(
                    entry["event"], **{key: value for key, value in entry.items() if key != "event"}))

            def finish_diagnostics(event: str, **fields):
                if diagnostics is None:
                    return
                try:
                    size = output_path.stat().st_size
                    exists = True
                except OSError:
                    exists, size = False, 0
                try:
                    diagnostics.finish(event, final_output_exists=exists, final_output_bytes=size, **fields)
                except Exception:
                    pass

            command = [
                cli,
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ignore-rules",
                "--output-last-message",
                str(output_path),
            ]
            if diagnostics is not None:
                command.extend(["--json", "--color", "never"])
            if self.model:
                command.extend(["--model", self.model])
            if self.reasoning_effort:
                command.extend([
                    "--config",
                    f'model_reasoning_effort="{self.reasoning_effort}"',
                ])
            for index, data in enumerate(images, start=1):
                extension = ".jpg" if data.startswith(b"\xff\xd8\xff") else ".png"
                image_path = Path(directory) / f"review-{index}{extension}"
                image_path.write_bytes(data)
                command.extend(["--image", str(image_path)])
            telemetry = None
            if diagnostics is not None:
                try:
                    from .codex_telemetry import CodexTelemetry
                except ImportError:
                    from codex_telemetry import CodexTelemetry
                telemetry = CodexTelemetry(lambda entry: self._record_diagnostic(
                    entry["event"], **{key: value for key, value in entry.items() if key != "event"}),
                    enable_metrics=getattr(self, "diagnostic_callback", None) is not None).start()
                command.extend(telemetry.config_args)
            command.append("-")
            try:
                try:
                    if diagnostics is not None:
                        process = self._run_cancellable_cli(command, prompt, self.timeout_seconds,
                                                            self.cancellation_event or threading.Event(),
                                                            diagnostics=diagnostics, upstream_observer=telemetry)
                    elif self.cancellation_event is not None:
                        process = self._run_cancellable_cli(command, prompt, self.timeout_seconds, self.cancellation_event)
                    elif self.timeout_seconds is None:
                        process = subprocess.run(
                            command,
                            input=prompt,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            **run_options,
                        )
                    else:
                        process = self._run_codex_with_timeout(
                            command,
                            prompt,
                            self.timeout_seconds,
                        )
                finally:
                    if telemetry is not None:
                        telemetry.close()
            except subprocess.TimeoutExpired as exc:
                finish_diagnostics("cli.timeout", timeout_seconds=self.timeout_seconds)
                raise LLMError(
                    f"Codex CLI timed out after {self.timeout_seconds:g} seconds"
                ) from exc
            except OSError as exc:
                finish_diagnostics("cli.failed")
                raise LLMError(f"Failed to invoke the Codex CLI: {exc}") from exc
            except LLMCancelledError:
                finish_diagnostics("cli.cancelled")
                raise
            except BaseException:
                finish_diagnostics("cli.failed")
                raise
            if process.returncode != 0:
                finish_diagnostics("cli.failed", exit_code=process.returncode)
                detail = (process.stderr or process.stdout or "Codex command failed").strip()
                if len(detail) > 500:
                    detail = detail[-500:]
                raise LLMError(f"Codex CLI failed (exit {process.returncode}): {detail}")
            try:
                result = output_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                finish_diagnostics("cli.failed", exit_code=process.returncode)
                raise LLMError("Codex CLI returned no final message") from exc
            if not result:
                finish_diagnostics("cli.failed", exit_code=process.returncode)
                raise LLMError("Codex CLI returned an empty final message")
            finish_diagnostics("cli.completed", exit_code=process.returncode, output_chars=len(result))
            return result

    @staticmethod
    def _run_cancellable_cli(
        command: list[str], prompt: str, timeout_seconds: float | None,
        cancellation_event: threading.Event,
        *, diagnostics=None, upstream_observer=None,
    ) -> subprocess.CompletedProcess[str]:
        def observe(method, *args):
            if upstream_observer is not None:
                try:
                    upstream_observer.poll()
                except Exception:
                    pass
            if diagnostics is not None:
                try:
                    getattr(diagnostics, method)(*args)
                except Exception:
                    pass

        if cancellation_event.is_set():
            raise LLMCancelledError("Model request was cancelled")
        deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        # Python 3.11 communicate(None) no longer polls a partially written
        # stdin after TimeoutExpired. A slow-starting CLI and a large prompt
        # therefore deadlock. Hand over complete UTF-8 input via an anonymous
        # file and poll only output; no private Popen state or writer is needed.
        with tempfile.TemporaryFile(mode="w+b") as input_stream:
            input_stream.write(prompt.encode("utf-8"))
            input_stream.seek(0)
            if cancellation_event.is_set():
                raise LLMCancelledError("Model request was cancelled")
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            # Metrics have an independent batch exporter. Scope its observation
            # interval to this observed child, preserving the parent environment.
            child_env = None
            if (upstream_observer is not None and getattr(upstream_observer, "metrics_enabled", False)
                    and getattr(upstream_observer, "config_args", None)):
                child_env = {**os.environ, "OTEL_METRIC_EXPORT_INTERVAL": "5000"}
            process = subprocess.Popen(
                command, stdin=input_stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", start_new_session=True, env=child_env,
            )
            observe("start", process.pid)
            # Entire input + EOF are available to the child; this does not
            # indicate that the CLI consumed it or contacted the model yet.
            observe("input_sent")
            try:
                while True:
                    if cancellation_event.is_set():
                        raise LLMCancelledError("Model request was cancelled")
                    remaining = deadline - time.monotonic() if deadline is not None else 0.1
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout_seconds)
                    try:
                        stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                    except subprocess.TimeoutExpired as exc:
                        observe("observe", exc.output, exc.stderr)
                        observe("heartbeat")
                        continue
                    observe("observe", stdout, stderr)
                    if cancellation_event.is_set():
                        raise LLMCancelledError("Model request was cancelled")
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
                # A helper can ignore TERM after its leader exits. Always clear it.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=1)
                if diagnostics is not None:
                    # Drain shutdown output before the pipes and temporary result
                    # disappear. Only safe event metadata reaches the log sink.
                    try:
                        stdout, stderr = process.communicate(timeout=0.2)
                        observe("observe", stdout, stderr)
                    except subprocess.TimeoutExpired as exc:
                        observe("observe", exc.output, exc.stderr)
                    except (OSError, ValueError):
                        pass
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()

    @staticmethod
    def _run_codex_with_timeout(
        command: list[str],
        prompt: str,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(prompt, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            raise
        return subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout,
            stderr,
        )

    def _complete_anthropic(self, system: str, user: str) -> str:
        resp = self._backend.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        observe_usage(getattr(resp, "usage", None), provider=self.provider_name, observed_model=getattr(resp, "model", None))
        return "".join(getattr(b, "text", "") or "" for b in resp.content)

    def _complete_openai_shape(self, system: str, user: str) -> str:
        max_tokens = self.max_tokens
        if self.provider_name == "deepseek":
            floor = DEEPSEEK_MIN_OUTPUT_TOKENS.get(self.reasoning_effort or "high")
            if floor:
                max_tokens = max(max_tokens, floor)
        request = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if (self.provider_name == "deepseek"
                and self.reasoning_effort in DEEPSEEK_REASONING_EFFORTS):
            request["reasoning_effort"] = self.reasoning_effort
        if not _env_flag("NOVEL_OS_LLM_STREAMING", True):
            resp = self._backend.chat.completions.create(**request)
            observe_usage(getattr(resp, "usage", None), provider=self.provider_name, observed_model=getattr(resp, "model", None))
            return resp.choices[0].message.content or ""

        # Official OpenAI streaming emits usage only when explicitly requested.
        # Third-party compatibility routes may reject this option; do not add
        # an automatic retry merely to obtain accounting metadata.
        if self.provider_name in {"openai", "azure"}:
            request["stream_options"] = {"include_usage": True}
        stream = self._backend.chat.completions.create(stream=True, **request)
        cancellable = getattr(self, "cancellation_event", None) is not None
        if cancellable:
            self._active_stream = stream
        parts = []
        print("   Response stream started", end="", flush=True)
        progress_started = True
        last_progress = time.monotonic()
        try:
            self._check_cancelled()
            for chunk in stream:
                observe_usage(getattr(chunk, "usage", None), provider=self.provider_name, observed_model=getattr(chunk, "model", None))
                self._check_cancelled()
                now = time.monotonic()
                if now - last_progress >= 5:
                    print(".", end="", flush=True)
                    last_progress = now
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None)
                if not content:
                    continue
                parts.append(content)
        except Exception:
            if progress_started:
                print(" interrupted", flush=True)
            raise
        finally:
            if cancellable:
                try:
                    closer = getattr(stream, "close", None)
                    if callable(closer):
                        closer()
                except Exception:
                    pass
                self._active_stream = None
        if progress_started:
            print(" done", flush=True)
        return "".join(parts)

    def _complete_openai_shape_with_images(
        self,
        system: str,
        user: str,
        images: tuple[bytes, ...],
    ) -> str:
        content: list[dict[str, object]] = [{"type": "text", "text": user}]
        for data in images:
            mime = "image/jpeg" if data.startswith(b"\xff\xd8\xff") else "image/png"
            encoded = base64.b64encode(data).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}", "detail": "high"},
            })
        response = self._backend.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        )
        observe_usage(getattr(response, "usage", None), provider=self.provider_name, observed_model=getattr(response, "model", None))
        return response.choices[0].message.content or ""

    def _complete_gemini(self, system: str, user: str) -> str:
        from google.genai import types  # type: ignore
        resp = self._backend.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=self.max_tokens,
            ),
        )
        observe_usage(getattr(resp, "usage_metadata", None), provider="gemini", observed_model=getattr(resp, "model_version", None))
        return resp.text or ""
