"""
LLM client module for AuraBot.
Provides intelligent conversational responses using Google Gemini.
"""

from typing import Optional, List, Dict
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

# Maximum number of conversation turns (user + assistant pairs) to keep in history
DEFAULT_MAX_HISTORY_TURNS = 10


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

            assistant_text = self._extract_text(response)

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
