"""
LLM client module for AuraBot.
Provides intelligent conversational responses using Google Gemini or local Ollama.
"""

import os
import re
from dataclasses import dataclass
from typing import Optional, List, Dict

import requests

from google import genai
from google.genai import types


# Default system prompt defining AuraBot's personality and behaviour
DEFAULT_SYSTEM_PROMPT = (
    "You are AuraBot, a friendly and knowledgeable wellness companion designed to "
    "help desk workers stay healthy and comfortable throughout their workday.\n\n"
    "Personality & tone:\n"
    "- Warm, supportive, and conversational — like a caring colleague.\n"
    "- Speak in natural, complete sentences.\n"
    "- Be concise (1-3 sentences) because your responses are spoken aloud via "
    "text-to-speech, so keep them easy to listen to.\n"
    "- Avoid bullet points, markdown, code blocks, or any visual formatting.\n\n"
    "Your expertise:\n"
    "- Posture correction, stretching, and micro-exercises for desk workers.\n"
    "- Hydration and nutrition reminders.\n"
    "- Eye-strain prevention (20-20-20 rule, etc.).\n"
    "- Mental wellness — stress relief, breathing exercises, focus tips.\n"
    "- General friendly conversation to keep the user company.\n\n"
    "Important rules:\n"
    "- Never refuse to chat on any safe topic; you are a companion first.\n"
    "- If the user seems stressed or tired, gently suggest a break or exercise.\n"
    "- Do NOT handle timer commands — those are managed separately.\n"
    "- Do NOT use emojis, special characters, or asterisks.\n"
    "- Avoid overly long responses; remember everything you say is spoken aloud."
)

# Ollama gets a stricter, brevity-first system prompt (same persona, short replies like Gemini)
OLLAMA_SYSTEM_PROMPT = (
    "CRITICAL: Your reply must be 1 to 3 short sentences only. No lists, no numbering, no bullet points. "
    "Plain conversational text only — your reply is spoken aloud by text-to-speech.\n\n"
    "You are AuraBot, a friendly wellness companion for desk workers. Be warm and concise. "
    "Expertise: posture, stretching, hydration, eye strain (20-20-20), stress relief. "
    "Never use emojis or formatting. Do not handle timer commands. If asked for tips, give one short tip in 1-2 sentences."
)

# Maximum number of conversation turns (user + assistant pairs) to keep in history
DEFAULT_MAX_HISTORY_TURNS = 10

# Default sampling temperature and output limits for backends
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_OUTPUT_TOKENS = 200
DEFAULT_OLLAMA_MAX_OUTPUT_TOKENS = 256

# Generic error message shared by all LLM backends
GENERIC_ERROR_TEXT = (
    "I'm having a little trouble thinking right now. Could you say that again?"
)

# Canned reply when user asks who/what the bot is (avoids empty LLM responses)
_IDENTITY_INTRO = (
    "I'm AuraBot, your friendly wellness companion. "
    "I'm here to help you stay healthy and comfortable at your desk."
)

# Phrases that indicate an identity question (normalized: lower, stripped)
_IDENTITY_PHRASES = (
    "who are you",
    "what are you",
    "what is your name",
    "what's your name",
    "your name",
    "who is this",
    "what is this",
    "what is aura",
    "who is aura",
    "are you a bot",
    "are you a robot",
)


@dataclass
class GeminiModelProfile:
    """
    Configuration profile for the Gemini backend.

    Centralises common knobs (model name, system prompt, temperature, etc.) so they
    can be shared between AuraBot and diagnostic tools.
    """

    model: str
    system_prompt: str
    max_history_turns: int = DEFAULT_MAX_HISTORY_TURNS
    temperature: float = DEFAULT_TEMPERATURE
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS


@dataclass
class OllamaModelProfile:
    """
    Configuration profile for the Ollama backend.

    Encapsulates model selection, host, and generation options for local fallback.
    """

    model: str
    host: str = "http://127.0.0.1:11434"
    system_prompt: Optional[str] = None
    max_history_turns: int = DEFAULT_MAX_HISTORY_TURNS
    temperature: float = DEFAULT_TEMPERATURE
    max_output_tokens: int = DEFAULT_OLLAMA_MAX_OUTPUT_TOKENS
    timeout_seconds: float = 60.0


def build_gemini_profile_from_env(
    model: Optional[str] = None,
    system_prompt: Optional[str] = None,
    max_history_turns: Optional[int] = None,
    temperature: Optional[float] = None,
    max_output_tokens: Optional[int] = None,
) -> GeminiModelProfile:
    """
    Build a GeminiModelProfile using explicit overrides first, then environment variables,
    and finally hard-coded defaults.

    Env vars used (all optional):
      - GEMINI_MODEL
      - GEMINI_TEMPERATURE
      - GEMINI_MAX_HISTORY_TURNS
      - GEMINI_MAX_OUTPUT_TOKENS
      - LLM_SYSTEM_PROMPT  (overrides DEFAULT_SYSTEM_PROMPT when set)
    """

    env_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    env_temperature = os.getenv("GEMINI_TEMPERATURE")
    env_history = os.getenv("GEMINI_MAX_HISTORY_TURNS")
    env_max_tokens = os.getenv("GEMINI_MAX_OUTPUT_TOKENS")
    env_system_prompt = os.getenv("LLM_SYSTEM_PROMPT")

    resolved_model = model or env_model
    resolved_system_prompt = system_prompt or env_system_prompt or DEFAULT_SYSTEM_PROMPT

    if max_history_turns is not None:
        resolved_history = max_history_turns
    elif env_history is not None:
        try:
            resolved_history = int(env_history)
        except ValueError:
            resolved_history = DEFAULT_MAX_HISTORY_TURNS
    else:
        resolved_history = DEFAULT_MAX_HISTORY_TURNS

    if temperature is not None:
        resolved_temperature = temperature
    elif env_temperature is not None:
        try:
            resolved_temperature = float(env_temperature)
        except ValueError:
            resolved_temperature = DEFAULT_TEMPERATURE
    else:
        resolved_temperature = DEFAULT_TEMPERATURE

    if max_output_tokens is not None:
        resolved_max_tokens = max_output_tokens
    elif env_max_tokens is not None:
        try:
            resolved_max_tokens = int(env_max_tokens)
        except ValueError:
            resolved_max_tokens = DEFAULT_MAX_OUTPUT_TOKENS
    else:
        resolved_max_tokens = DEFAULT_MAX_OUTPUT_TOKENS

    return GeminiModelProfile(
        model=resolved_model,
        system_prompt=resolved_system_prompt,
        max_history_turns=resolved_history,
        temperature=resolved_temperature,
        max_output_tokens=resolved_max_tokens,
    )


def build_ollama_profile_from_env(
    model: Optional[str] = None,
    host: Optional[str] = None,
    system_prompt: Optional[str] = None,
    max_history_turns: Optional[int] = None,
    temperature: Optional[float] = None,
    max_output_tokens: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
) -> OllamaModelProfile:
    """
    Build an OllamaModelProfile using explicit overrides first, then environment variables,
    and finally hard-coded defaults.

    Env vars used (all optional):
      - OLLAMA_MODEL
      - OLLAMA_HOST
      - OLLAMA_TEMPERATURE
      - OLLAMA_MAX_HISTORY_TURNS
      - OLLAMA_MAX_OUTPUT_TOKENS
      - OLLAMA_TIMEOUT_SECONDS
      - LLM_SYSTEM_PROMPT  (overrides OLLAMA_SYSTEM_PROMPT when set)
    """

    env_model = os.getenv("OLLAMA_MODEL", "lfm2.5-thinking")
    env_host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    env_temperature = os.getenv("OLLAMA_TEMPERATURE")
    env_history = os.getenv("OLLAMA_MAX_HISTORY_TURNS")
    env_max_tokens = os.getenv("OLLAMA_MAX_OUTPUT_TOKENS")
    env_timeout = os.getenv("OLLAMA_TIMEOUT_SECONDS")
    env_system_prompt = os.getenv("LLM_SYSTEM_PROMPT")

    resolved_model = model or env_model
    resolved_host = host or env_host
    resolved_system_prompt = system_prompt or env_system_prompt or OLLAMA_SYSTEM_PROMPT

    if max_history_turns is not None:
        resolved_history = max_history_turns
    elif env_history is not None:
        try:
            resolved_history = int(env_history)
        except ValueError:
            resolved_history = DEFAULT_MAX_HISTORY_TURNS
    else:
        resolved_history = DEFAULT_MAX_HISTORY_TURNS

    if temperature is not None:
        resolved_temperature = temperature
    elif env_temperature is not None:
        try:
            resolved_temperature = float(env_temperature)
        except ValueError:
            resolved_temperature = DEFAULT_TEMPERATURE
    else:
        resolved_temperature = DEFAULT_TEMPERATURE

    if max_output_tokens is not None:
        resolved_max_tokens = max_output_tokens
    elif env_max_tokens is not None:
        try:
            resolved_max_tokens = int(env_max_tokens)
        except ValueError:
            resolved_max_tokens = DEFAULT_OLLAMA_MAX_OUTPUT_TOKENS
    else:
        resolved_max_tokens = DEFAULT_OLLAMA_MAX_OUTPUT_TOKENS

    if timeout_seconds is not None:
        resolved_timeout = timeout_seconds
    elif env_timeout is not None:
        try:
            resolved_timeout = float(env_timeout)
        except ValueError:
            resolved_timeout = 60.0
    else:
        resolved_timeout = 60.0

    return OllamaModelProfile(
        model=resolved_model,
        host=resolved_host,
        system_prompt=resolved_system_prompt,
        max_history_turns=resolved_history,
        temperature=resolved_temperature,
        max_output_tokens=resolved_max_tokens,
        timeout_seconds=resolved_timeout,
    )


def _is_identity_question(user_text: str) -> bool:
    """True if the user is asking who/what the bot is."""
    if not user_text or not user_text.strip():
        return False
    q = user_text.strip().lower()
    return any(p in q for p in _IDENTITY_PHRASES)


def _strip_think_blocks(text: str) -> str:
    """Remove <think>...</think> blocks from model output so only the spoken reply is used."""
    if not text:
        return text
    # Remove <think>...</think> (case-insensitive, multiline); then any unclosed <think> to end
    out = re.sub(r"(?si)<think>.*?</think>\s*", "", text)
    out = re.sub(r"(?si)<think>[\s\S]*", "", out)
    return out.strip()


def _extract_think_content(text: str) -> str:
    """Extract inner text of the first <think>...</think> block; use when reply is entirely inside think."""
    if not text:
        return ""
    m = re.search(r"(?si)<think>(.*?)(?:</think>|$)", text)
    return m.group(1).strip() if m else ""


def _truncate_to_sentences(text: str, max_sentences: int = 3, max_chars: int = 220) -> str:
    """Keep only the first max_sentences sentences, under max_chars; strip numbering/lists for TTS."""
    if not text or max_sentences <= 0:
        return text
    # Remove leading numbered/bullet lines (e.g. "1. ", "2. ", "- ")
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        line = line.strip()
        if re.match(r"^[\d]+[.)]\s*", line):
            line = re.sub(r"^[\d]+[.)]\s*", "", line)
        if line.startswith("- "):
            line = line[2:]
        if line:
            cleaned.append(line)
    text = " ".join(cleaned)
    # Split on sentence boundaries and take first max_sentences
    parts = re.split(r"(?<=[.!?])\s+", text)
    chosen = [p.strip() for p in parts[:max_sentences] if p.strip()]
    out = " ".join(chosen) if chosen else text.strip()
    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0].rsplit(".", 1)[0]
        if out and not out.endswith("."):
            out = out + "."
    return out


def _is_echoed_system_prompt(response: str, system_prompt: str) -> bool:
    """True if the model echoed the system prompt instead of replying."""
    if not response or not system_prompt:
        return False
    r = response.strip()
    # Exact match: response starts with the first 50 chars of system prompt
    prefix = system_prompt.strip()[:50]
    if r.startswith(prefix) or prefix in r[:80]:
        return True
    # Paraphrased echo: small models often output instruction text as their reply
    # (e.g. "CRITICAL: Your reply must be a single sentence that addresses...")
    if r.startswith("CRITICAL:") and ("your reply must be" in r[:120].lower() or "reply must be" in r[:120].lower()):
        return True
    return False


class LLMClient:
    """
    Google Gemini LLM client with conversation history management.
    
    Maintains a sliding window of conversation history so the model
    has multi-turn context while keeping token usage bounded.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.5-flash",
        system_prompt: Optional[str] = None,
        max_history_turns: int = DEFAULT_MAX_HISTORY_TURNS,
        temperature: float = 0.7,
        max_output_tokens: int = 200,
    ):
        """
        Initialize the LLM client.

        Args:
            api_key: Google AI API key. If None, the SDK reads GEMINI_API_KEY
                     or GOOGLE_API_KEY from environment variables.
            model: Gemini model identifier.
            system_prompt: Custom system prompt. Falls back to DEFAULT_SYSTEM_PROMPT.
            max_history_turns: Max conversation turn-pairs to retain.
            temperature: Sampling temperature (0.0 = deterministic, 1.0 = creative).
            max_output_tokens: Maximum tokens in the model's response.
        """
        # Initialise the GenAI client
        if api_key:
            self._client = genai.Client(api_key=api_key)
        else:
            # SDK auto-reads GEMINI_API_KEY or GOOGLE_API_KEY from env
            self._client = genai.Client()

        self._model = model
        self._system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self._max_history_turns = max_history_turns
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

        # Conversation history: list of {"role": "user"|"model", "content": str}
        self._history: List[Dict[str, str]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_response(self, user_text: str) -> str:
        """
        Generate a conversational response for the given user input.

        The full conversation history (up to the configured window) is sent
        to the model so it can produce contextually relevant replies.

        Args:
            user_text: The user's spoken/transcribed input.

        Returns:
            The model's response text, or a graceful fallback on error.
        """
        if _is_identity_question(user_text):
            self._history.append({"role": "user", "content": user_text})
            self._trim_history()
            self._history.append({"role": "model", "content": _IDENTITY_INTRO})
            self._trim_history()
            return _IDENTITY_INTRO

        # Append user message to history
        self._history.append({"role": "user", "content": user_text})
        self._trim_history()

        # Build the contents list for the API call
        contents = self._build_contents()

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=self._system_prompt,
                    temperature=self._temperature,
                    max_output_tokens=self._max_output_tokens,
                ),
            )

            assistant_text = _strip_think_blocks(self._extract_text(response))

            # Append assistant message to history
            self._history.append({"role": "model", "content": assistant_text})
            self._trim_history()

            return assistant_text

        except Exception as e:
            # Remove the user message we just appended so history stays clean
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
            print(f"LLM error: {e}")
            return GENERIC_ERROR_TEXT

    def clear_history(self):
        """Clear all conversation history."""
        self._history.clear()

    def get_history_length(self) -> int:
        """Return the current number of messages in history."""
        return len(self._history)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_contents(self) -> list:
        """
        Build the contents list from conversation history.
        
        Returns:
            List of Content objects for the Gemini API.
        """
        contents = []
        for msg in self._history:
            contents.append(
                types.Content(
                    role=msg["role"],
                    parts=[types.Part.from_text(text=msg["content"])],
                )
            )
        return contents

    def _trim_history(self):
        """
        Keep only the most recent N turn-pairs in history.
        A turn-pair is one user message + one model message (2 entries).
        """
        max_messages = self._max_history_turns * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]

    @staticmethod
    def _extract_text(response) -> str:
        """
        Extract plain text from a Gemini GenerateContentResponse.

        Args:
            response: The API response object.

        Returns:
            Cleaned response text.
        """
        try:
            text = response.text or ""
        except (AttributeError, ValueError):
            # Fallback: iterate candidates
            try:
                text = ""
                for candidate in response.candidates:
                    for part in candidate.content.parts:
                        text += part.text
            except Exception:
                text = ""

        # Strip whitespace and any stray markdown formatting
        text = text.strip()
        return text if text else "Hmm, I didn't quite get that. Could you try again?"


class OllamaLLMClient:
    """
    Local Ollama LLM client with the same interface as LLMClient.

    Talks to an Ollama server (e.g. http://127.0.0.1:11434) for free,
    offline-capable conversation. Suitable for Raspberry Pi 5 with small models
    (e.g. tinyllama, phi, smollm).
    """

    def __init__(
        self,
        model: str = "lfm2.5-thinking",
        host: str = "http://127.0.0.1:11434",
        system_prompt: Optional[str] = None,
        max_history_turns: int = DEFAULT_MAX_HISTORY_TURNS,
        temperature: float = 0.7,
        max_output_tokens: int = 256,
        timeout_seconds: float = 60.0,
    ):
        """
        Initialize the Ollama client.

        Args:
            model: Ollama model name (e.g. lfm2.5-thinking, granite4:1b, tinyllama).
            host: Base URL of the Ollama server (default http://127.0.0.1:11434).
            system_prompt: Custom system prompt. If None, no system message is sent (Ollama/model default).
            max_history_turns: Max conversation turn-pairs to retain.
            temperature: Sampling temperature (0.0 = deterministic, 1.0 = creative).
            max_output_tokens: Max tokens per reply (default 256; responses are truncated to 1-3 sentences for TTS).
            timeout_seconds: Request timeout for generate calls.
        """
        self._model = model
        self._base_url = host.rstrip("/")
        self._system_prompt = system_prompt
        self._max_history_turns = max_history_turns
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._timeout = timeout_seconds
        self._history: List[Dict[str, str]] = []

    def _api_model(self) -> str:
        """Model name for API calls; use :latest if no tag so Ollama resolves correctly."""
        if ":" in self._model:
            return self._model
        return f"{self._model}:latest"

    def generate_response(self, user_text: str) -> str:
        """
        Generate a conversational response using the Ollama chat API.

        Args:
            user_text: The user's spoken/transcribed input.

        Returns:
            The model's response text, or a graceful fallback on error.
        """
        if _is_identity_question(user_text):
            self._history.append({"role": "user", "content": user_text})
            self._trim_history()
            self._history.append({"role": "model", "content": _IDENTITY_INTRO})
            self._trim_history()
            return _IDENTITY_INTRO

        self._history.append({"role": "user", "content": user_text})
        self._trim_history()

        messages = self._build_ollama_messages()

        url = f"{self._base_url}/api/chat"
        try:
            r = requests.post(
                url,
                json={
                    "model": self._api_model(),
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": self._temperature,
                        "num_predict": self._max_output_tokens,
                    },
                },
                timeout=self._timeout,
            )
            if r.status_code == 404:
                print(f"Ollama: model '{self._model}' not found. Pull it with: ollama pull {self._model}")
                if self._history and self._history[-1].get("role") == "user":
                    self._history.pop()
                return GENERIC_ERROR_TEXT
            r.raise_for_status()
            data = r.json()
            msg = data.get("message") or {}
            raw_content = (msg.get("content") or "").strip()
            raw_thinking = (msg.get("thinking") or "").strip()
            assistant_text = _strip_think_blocks(raw_content)
            if not assistant_text:
                thinking = _strip_think_blocks(raw_thinking)
                if thinking:
                    assistant_text = _truncate_to_sentences(thinking, max_sentences=3)
                if not assistant_text:
                    think_inline = _extract_think_content(raw_content)
                    if think_inline:
                        assistant_text = _truncate_to_sentences(think_inline, max_sentences=3)
            if not assistant_text:
                eval_count = data.get("eval_count", 0)
                print(
                    f"Ollama: empty reply (eval_count={eval_count}). "
                    f"content_len={len(raw_content)}, thinking_len={len(raw_thinking)}. "
                    f"content_preview={repr(raw_content[:150])!r}"
                )
                assistant_text = "Hmm, I didn't quite get that. Could you try again?"
            # Some small models echo the system prompt; treat that as no real reply
            if _is_echoed_system_prompt(assistant_text, self._system_prompt):
                assistant_text = "I'm here to help. What would you like to talk about?"
            else:
                # Enforce short reply: keep first 1-3 sentences only (same as Gemini)
                assistant_text = _truncate_to_sentences(assistant_text, max_sentences=3)

            self._history.append({"role": "model", "content": assistant_text})
            self._trim_history()
            return assistant_text

        except requests.exceptions.ConnectionError as e:
            print(
                f"Ollama LLM error: Cannot reach Ollama at {self._base_url}. "
                "Is Ollama running? Start it with: ollama serve"
            )
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
            return GENERIC_ERROR_TEXT
        except Exception as e:
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
            print(f"Ollama LLM error: {e}")
            return GENERIC_ERROR_TEXT

    def clear_history(self):
        """Clear all conversation history."""
        self._history.clear()

    def get_history_length(self) -> int:
        """Return the current number of messages in history."""
        return len(self._history)

    def _build_ollama_messages(self) -> List[Dict[str, str]]:
        """Build messages for Ollama: optional system + history with role 'assistant' for model."""
        out: List[Dict[str, str]] = []
        if self._system_prompt:
            out.append({"role": "system", "content": self._system_prompt})
        for msg in self._history:
            role = "assistant" if msg["role"] == "model" else msg["role"]
            out.append({"role": role, "content": msg["content"]})
        return out

    def _trim_history(self):
        """Keep only the most recent N turn-pairs in history."""
        max_messages = self._max_history_turns * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]


class HybridLLMClient:
    """
    Hybrid LLM client that routes between a primary and fallback backend.

    Exposes the same public API as LLMClient / OllamaLLMClient:
      - generate_response(user_text: str) -> str
      - clear_history()
      - get_history_length() -> int

    It is intentionally conservative: if the primary backend fails (raises) or
    returns a clearly degraded generic error message, the request is retried
    against the fallback backend.
    """

    _GENERIC_ERROR_TEXT = GENERIC_ERROR_TEXT

    def __init__(
        self,
        primary_client,
        fallback_client,
        primary_name: str = "gemini",
        fallback_name: str = "ollama",
        logger=None,
    ):
        """
        Args:
            primary_client: Instance of LLMClient or OllamaLLMClient used first.
            fallback_client: Instance of LLMClient or OllamaLLMClient used when primary fails.
            primary_name: Human-readable name for logging (e.g. 'gemini').
            fallback_name: Human-readable name for logging (e.g. 'ollama').
            logger: Optional AuraBotLogger-like object with log_general(message, level, metadata=None).
        """
        self._primary = primary_client
        self._fallback = fallback_client
        self._primary_name = primary_name
        self._fallback_name = fallback_name
        self._logger = logger

    def _log(self, message: str, level: str = "INFO", metadata: Optional[Dict] = None):
        if self._logger is not None and hasattr(self._logger, "log_general"):
            try:
                self._logger.log_general(message, level, metadata=metadata)
                return
            except Exception:
                # Fall back to print if logger fails
                pass
        print(message)

    def _looks_like_generic_error(self, text: str) -> bool:
        if not text:
            return True
        t = text.strip()
        if t == self._GENERIC_ERROR_TEXT:
            return True
        # Loose heuristic: starts with the first few words of the generic error
        prefix = self._GENERIC_ERROR_TEXT[:32]
        return t.startswith(prefix)

    def generate_response(self, user_text: str) -> str:
        """
        Try the primary backend first; on failure or generic error, fall back.
        """
        primary_error = None
        primary_reply: Optional[str] = None

        try:
            primary_reply = self._primary.generate_response(user_text)
        except Exception as exc:
            primary_error = str(exc)

        if primary_error is None and primary_reply is not None and not self._looks_like_generic_error(primary_reply):
            return primary_reply

        # Primary failed or gave a non-useful error message; fall back to secondary.
        metadata = {
            "primary_backend": self._primary_name,
            "fallback_backend": self._fallback_name,
        }
        if primary_error:
            metadata["primary_error"] = primary_error
            self._log(
                f"HybridLLMClient: primary backend '{self._primary_name}' failed, "
                f"falling back to '{self._fallback_name}'.",
                "WARNING",
                metadata=metadata,
            )
        else:
            # Primary responded but with a generic error text.
            metadata["primary_reply_preview"] = (primary_reply or "")[:120]
            self._log(
                f"HybridLLMClient: primary backend '{self._primary_name}' returned generic error text, "
                f"falling back to '{self._fallback_name}'.",
                "WARNING",
                metadata=metadata,
            )

        try:
            fallback_reply = self._fallback.generate_response(user_text)
            return fallback_reply
        except Exception as exc:
            # If fallback also fails, surface a single generic error.
            metadata["fallback_error"] = str(exc)
            self._log(
                "HybridLLMClient: both primary and fallback backends failed.",
                "ERROR",
                metadata=metadata,
            )
            return self._GENERIC_ERROR_TEXT

    def clear_history(self):
        """Clear history on both underlying clients."""
        for client in (self._primary, self._fallback):
            if hasattr(client, "clear_history"):
                try:
                    client.clear_history()
                except Exception:
                    continue

    def get_history_length(self) -> int:
        """Return the maximum history length reported by the underlying clients."""
        lengths = []
        for client in (self._primary, self._fallback):
            if hasattr(client, "get_history_length"):
                try:
                    lengths.append(int(client.get_history_length()))
                except Exception:
                    continue
        return max(lengths) if lengths else 0
