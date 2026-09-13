"""AI model catalog with metadata (backend source of truth).

Models are OpenRouter slugs (``vendor/model``). Each declares its context
window, capabilities (tools/vision) and OpenRouter's list price per 1k tokens.
This metadata powers:
- the model picker and token counter in the agent creation wizard,
- the per-model price figures the catalog API exposes to clients.

IDs are kept in sync with `apps/web/lib/providers.ts`. Keep both in sync when
adding models. Any other OpenRouter slug still works when typed by hand: the
catalog is the curated offer, not a whitelist.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    # The vendor behind the slug, for grouping and labels.
    provider: str
    label: str
    family: str
    context_window: int
    max_output_tokens: int
    supports_tools: bool
    supports_vision: bool
    # List price per 1,000 tokens, in USD.
    input_price_per_1k: float
    output_price_per_1k: float
    badge: str = ""
    note: str = ""


# Standard approximation to estimate tokens without a tokenizer: ~4 chars/token.
CHARS_PER_TOKEN = 4

# Transcription model an agent gets unless it picks another one.
DEFAULT_AUDIO_MODEL = "openai/gpt-4o-mini-transcribe"


_MODELS: tuple[ModelInfo, ...] = (
    # OpenAI
    ModelInfo("openai/gpt-5.6-luna", "openai", "GPT-5.6 Luna", "gpt-5.6", 1_050_000, 128_000, True, True,
              0.0002, 0.0012, "Most affordable", "High volume, chat and cost-sensitive automations."),
    ModelInfo("openai/gpt-5.6-terra", "openai", "GPT-5.6 Terra", "gpt-5.6", 1_050_000, 128_000, True, True,
              0.002, 0.012, "Balanced", "A good balance of capability, speed and price."),
    ModelInfo("openai/gpt-5.6-sol", "openai", "GPT-5.6 Sol", "gpt-5.6", 1_050_000, 128_000, True, True,
              0.002, 0.01, "Top capability", "Complex work and more demanding responses."),
    ModelInfo("openai/gpt-5.5", "openai", "GPT-5.5", "gpt-5.5", 1_050_000, 128_000, True, True,
              0.005, 0.03, "Previous generation", "Available for compatibility and gradual migrations."),
    ModelInfo("openai/gpt-5.4", "openai", "GPT-5.4", "gpt-5.4", 1_050_000, 128_000, True, True,
              0.0025, 0.015, "Previous generation", "Superseded by GPT-5.6 Terra at a lower price."),
    ModelInfo("openai/gpt-5.4-mini", "openai", "GPT-5.4 mini", "gpt-5.4", 400_000, 128_000, True, True,
              0.00075, 0.0045, "Previous generation", "Mid-tier option from the previous family."),
    ModelInfo("openai/gpt-5.4-nano", "openai", "GPT-5.4 nano", "gpt-5.4", 400_000, 128_000, True, True,
              0.0002, 0.00125, "Previous generation", "Superseded by GPT-5.6 Luna."),
    ModelInfo("openai/gpt-4.1", "openai", "GPT-4.1", "gpt-4.1", 1_047_576, 32_768, True, True,
              0.002, 0.008, "No reasoning step", "Lower latency for instruction following and tool calls."),
    ModelInfo("openai/gpt-4.1-mini", "openai", "GPT-4.1 mini", "gpt-4.1", 1_047_576, 32_768, True, True,
              0.0004, 0.0016, "No reasoning step", "Economical option with a wide context window."),
    ModelInfo("openai/gpt-4.1-nano", "openai", "GPT-4.1 nano", "gpt-4.1", 1_047_576, 32_768, True, True,
              0.0001, 0.0004, "Most affordable", "The lowest-cost text model in the line-up."),
    # Google Gemini
    ModelInfo("google/gemini-3.8-flash", "google", "Gemini 3.8 Flash", "gemini-3", 1_048_576, 65_536, True, True,
              0.00075, 0.00375, "Current", "Fast, balanced model for agents and applications."),
    ModelInfo("google/gemini-3.7-flash", "google", "Gemini 3.7 Flash", "gemini-3", 1_048_576, 65_536, True, True,
              0.00075, 0.00375, "Stable", "The previous Flash release, same price."),
    ModelInfo("google/gemini-3.6-flash", "google", "Gemini 3.6 Flash", "gemini-3", 1_048_576, 65_536, True, True,
              0.00075, 0.00375, "Stable", "Kept for agents that were tuned on it."),
    ModelInfo("google/gemini-3.5-flash", "google", "Gemini 3.5 Flash", "gemini-3", 1_048_576, 65_536, True, True,
              0.0015, 0.009, "Previous generation", "Superseded by Gemini 3.8 Flash at a lower price."),
    ModelInfo("google/gemini-3.5-flash-lite", "google", "Gemini 3.5 Flash-Lite", "gemini-3", 1_048_576, 65_536, True, True,
              0.0003, 0.0025, "Economical", "The lowest-cost alternative in the Gemini 3.5 family."),
    ModelInfo("google/gemini-3.1-flash-lite", "google", "Gemini 3.1 Flash-Lite", "gemini-3", 1_048_576, 65_536, True, True,
              0.00025, 0.0015, "Economical", "Very cheap, for simple high-volume chat."),
    # Anthropic
    ModelInfo("anthropic/claude-sonnet-5", "anthropic", "Claude Sonnet 5", "claude", 1_000_000, 128_000, True, True,
              0.002, 0.01, "Balanced", "A mix of speed and intelligence for production."),
    ModelInfo("anthropic/claude-opus-5", "anthropic", "Claude Opus 5", "claude", 1_000_000, 128_000, True, True,
              0.005, 0.025, "Top capability", "Complex tasks, reasoning and demanding agent flows."),
    ModelInfo("anthropic/claude-fable-5", "anthropic", "Claude Fable 5", "claude", 1_000_000, 128_000, True, True,
              0.01, 0.05, "Maximum capability", "Deep research and long autonomous runs."),
    ModelInfo("anthropic/claude-haiku-4.5", "anthropic", "Claude Haiku 4.5", "claude", 200_000, 64_000, True, True,
              0.001, 0.005, "Fast", "Quick responses and simpler workloads."),
    # DeepSeek
    ModelInfo("deepseek/deepseek-v4-flash", "deepseek", "DeepSeek V4 Flash", "deepseek-v4", 1_048_576, 384_000, True, False,
              0.0000657, 0.0001313, "Economical", "High-volume chat and agents with up to 1M context."),
    ModelInfo("deepseek/deepseek-v4-pro", "deepseek", "DeepSeek V4 Pro", "deepseek-v4", 1_048_576, 393_216, True, False,
              0.0016, 0.0032, "Advanced", "Reasoning, code and complex long-running flows."),
    # xAI
    ModelInfo("x-ai/grok-4.5", "xai", "Grok 4.5", "grok-4", 500_000, 450_000, True, True,
              0.002, 0.006, "Current", "xAI's main model for code, agents and general work."),
    # Meta
    ModelInfo("meta-llama/llama-4-maverick", "meta", "Llama 4 Maverick", "llama-4", 1_048_576, 115_200, True, True,
              0.0002, 0.000696, "Open weights", "Meta's open model, cheap and multimodal."),
)

# Speech-to-text models, priced so a transcription the provider did not price
# (OpenRouter reports no vendor charge for audio) can be valued at list price.
# Never offered as chat models. Audio tokens in, text tokens out.
_AUDIO_MODEL_INFO: tuple[ModelInfo, ...] = (
    ModelInfo("openai/gpt-4o-mini-transcribe", "openai", "GPT-4o mini Transcribe", "transcribe", 16_000, 2_000, False, False,
              0.003, 0.005),
    ModelInfo("openai/gpt-4o-transcribe", "openai", "GPT-4o Transcribe", "transcribe", 16_000, 2_000, False, False,
              0.006, 0.01),
)

_BY_ID: dict[str, ModelInfo] = {model.id: model for model in (*_MODELS, *_AUDIO_MODEL_INFO)}


def list_models() -> list[ModelInfo]:
    """All catalog models, in declaration order."""
    return list(_MODELS)


def get_model(model_id: str) -> ModelInfo | None:
    """Metadata for a model by its ID (chat or audio), or None if not in the catalog."""
    return _BY_ID.get(model_id)


def estimate_tokens(text: str) -> int:
    """Quick token estimate (~4 characters per token)."""
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


# Capability model lists the app offers (mirrored by the frontend as its
# loading fallback in apps/web/lib/providers.ts). Transcription goes through
# OpenRouter's audio endpoint; image understanding uses any vision-capable
# chat model.
AUDIO_MODELS = (DEFAULT_AUDIO_MODEL, "openai/gpt-4o-transcribe", "openai/gpt-transcribe")
IMAGE_MODELS = tuple(model.id for model in _MODELS if model.supports_vision)


def available_models() -> dict:
    """Model ids a workspace can pick, per provider and capability.

    A stock install offers the whole catalog. A deployment may narrow this
    (for example to the models its managed credentials can actually serve),
    which is why the frontend asks instead of trusting its static lists.
    """
    return {"chat": {"openrouter": [model.id for model in _MODELS]}, "image": list(IMAGE_MODELS), "audio": list(AUDIO_MODELS)}
