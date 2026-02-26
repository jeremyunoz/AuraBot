"""
LLM client module for AuraBot.
Provides intelligent conversational responses using Google Gemini or local Ollama.
"""

import re
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
            return "I'm having a little trouble thinking right now. Could you say that again?"

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
                return "I'm having a little trouble thinking right now. Could you say that again?"
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
            return "I'm having a little trouble thinking right now. Could you say that again?"
        except Exception as e:
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
            print(f"Ollama LLM error: {e}")
            return "I'm having a little trouble thinking right now. Could you say that again?"

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
