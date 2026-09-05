"""Central LLM provider registry.

Two things live here that used to be spread across ``bot.py``,
``livekit_agent.py`` and ``vocare_eot.py``:

1. Resolving *which* model to talk to from the environment.
2. Constructing the runtime-specific client for it.

The important concept is ``wire`` -- the request protocol, not the vendor.
Every provider the stack supported before Bedrock (openai, azure, foundry,
cerebras, groq, mistral, deepseek) speaks the OpenAI ``/chat/completions``
format, so switching between them is only ever a change of base URL, key and
model name. Bedrock does not: it uses SigV4 request signing and the Converse
API, with a different tool-call schema and different streaming frames. No value
of ``LLM_BASE_URL`` can bridge that, so callers must branch on ``spec.wire``
rather than on ``spec.provider``.

Env cascades differ per profile on purpose. ``bot.py`` and ``livekit_agent.py``
historically read different variable names for the same concept (see
``_resolve_openai_wire``), and both are deployed. Unifying them would silently
change which key a running revision picks up, so each profile keeps its own
lookup order and only the Bedrock branch is shared.
"""

from __future__ import annotations

import inspect
import os
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

# Bedrock model identifiers carrying one of these prefixes are cross-region
# inference profiles: Bedrock may serve them from any region in the geography,
# so an "apac." profile invoked in ap-southeast-2 can execute in Tokyo, Seoul,
# Mumbai or Singapore. That silently defeats Australian data residency while
# still looking like a Sydney deployment, so we refuse them unless the operator
# opts in explicitly.
_CROSS_REGION_PREFIXES = ("us.", "eu.", "apac.", "global.", "us-gov.")

_DEFAULT_BEDROCK_REGION = "ap-southeast-2"

# Providers that speak the OpenAI wire format. Anything not listed here needs an
# explicit wire mapping below.
_OPENAI_WIRE_PROVIDERS = {
    "openai",
    "azure",
    "azure_openai",
    "foundry",
    "cerebras",
    "groq",
    "mistral",
    "deepseek",
}

_BEDROCK_PROVIDERS = {"bedrock", "aws", "aws_bedrock"}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _bool_env(name: str, default: bool = False) -> bool:
    raw = _env(name)
    return raw.lower() in {"1", "true", "yes", "on"} if raw else default


@dataclass(frozen=True)
class LLMSpec:
    """A fully resolved target for one LLM call site."""

    provider: str
    wire: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    region: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_bedrock(self) -> bool:
        return self.wire == "bedrock"

    def describe(self) -> str:
        """Log-safe summary. Never includes the key."""
        where = self.region or self.base_url or "default endpoint"
        return f"provider={self.provider} wire={self.wire} model={self.model} at {where}"


def wire_for(provider: str) -> str:
    """Map a provider name to its request protocol."""
    provider = provider.strip().lower()
    if provider in _BEDROCK_PROVIDERS:
        return "bedrock"
    if provider in _OPENAI_WIRE_PROVIDERS:
        return "openai"
    # Unknown providers are assumed OpenAI-compatible, which is how every
    # non-Bedrock provider has been added to this stack so far.
    return "openai"


def bedrock_region() -> str:
    """Region for Bedrock calls, defaulting to Sydney for data residency."""
    return (
        _env("BEDROCK_REGION")
        or _env("AWS_REGION")
        or _env("AWS_DEFAULT_REGION")
        or _DEFAULT_BEDROCK_REGION
    )


def assert_region_pinned(model: str, region: str) -> None:
    """Reject cross-region inference profiles unless explicitly allowed.

    Raises rather than warning: a sovereignty guarantee that degrades to a log
    line nobody reads is not a guarantee. Set BEDROCK_ALLOW_CROSS_REGION=true to
    override when residency is not required.
    """
    identifier = model.strip()
    if "inference-profile/" in identifier:
        identifier = identifier.split("inference-profile/", 1)[1]

    matched = next(
        (p for p in _CROSS_REGION_PREFIXES if identifier.lower().startswith(p)),
        None,
    )
    if not matched:
        return

    if _bool_env("BEDROCK_ALLOW_CROSS_REGION", False):
        logger.warning(
            "Bedrock model {} is a '{}' cross-region inference profile; requests "
            "may be served outside {}. Allowed via BEDROCK_ALLOW_CROSS_REGION.",
            model,
            matched.rstrip("."),
            region,
        )
        return

    raise RuntimeError(
        f"Bedrock model {model!r} is a '{matched.rstrip('.')}' cross-region "
        f"inference profile, which can execute outside {region} and breaks "
        f"Australian data residency. Use a direct on-demand model ID available "
        f"in {region}, or set BEDROCK_ALLOW_CROSS_REGION=true to accept this."
    )


def _resolve_bedrock(role: str) -> LLMSpec:
    """Bedrock resolution, shared by every profile.

    No default model: Bedrock IDs are region- and account-specific (they depend
    on which models the account has been granted), so guessing one would fail at
    call time with a confusing error rather than at startup with a clear one.
    """
    prefix = "EOT_" if role == "eot" else ""
    model = (
        _env(f"{prefix}BEDROCK_MODEL_ID")
        or _env(f"{prefix}BEDROCK_MODEL")
        or _env(f"{prefix}LLM_MODEL")
        or _env("BEDROCK_MODEL_ID")
        or _env("BEDROCK_MODEL")
        or _env("LLM_MODEL")
    )
    if not model:
        raise RuntimeError(
            "LLM_PROVIDER=bedrock requires an explicit model. Set BEDROCK_MODEL_ID "
            "(or LLM_MODEL) to an on-demand model ID enabled in your account, "
            f"e.g. one available in {bedrock_region()}."
        )

    region = bedrock_region()
    assert_region_pinned(model, region)

    extra: dict[str, Any] = {}
    # Bedrock Guardrails are the usual control for a GRC deployment; pass them
    # through when configured so callers do not each reinvent this.
    if guardrail_id := _env("BEDROCK_GUARDRAIL_ID"):
        extra["guardrail_id"] = guardrail_id
        extra["guardrail_version"] = _env("BEDROCK_GUARDRAIL_VERSION", "DRAFT")

    return LLMSpec(
        provider="bedrock",
        wire="bedrock",
        model=model,
        # Credentials come from the standard AWS chain (task role, IRSA,
        # instance profile, or AWS_* env vars) rather than a key in app config.
        api_key=None,
        base_url=None,
        region=region,
        extra=extra,
    )


def _resolve_openai_wire(profile: str, provider: str) -> LLMSpec:
    """Reproduce each call site's historical env cascade exactly.

    These orderings differ between profiles and that is deliberate -- both are
    deployed, and changing which variable wins could repoint a live revision at
    a different endpoint. Treat this function as frozen behaviour.
    """
    if profile == "livekit":
        if provider == "cerebras":
            model = _env("CEREBRAS_MODEL") or _env("LLM_MODEL") or "gpt-oss-120b"
            api_key = _env("CEREBRAS_API_KEY")
            base_url = (_env("CEREBRAS_BASE_URL") or "https://api.cerebras.ai/v1").rstrip("/")
        else:
            model = (
                _env("LLM_MODEL")
                or _env("OPENAI_MODEL")
                or _env("AZURE_MODEL")
                or _env("AZURE_OPENAI_DEPLOYMENT")
                or "gpt-4o-mini"
            )
            api_key = (
                _env("AZURE_OPENAI_API_KEY")
                or _env("AZURE_OPENAI_KEY")
                or _env("OPENAI_API_KEY")
                or _env("OPENAI_API")
            )
            base_url = (
                _env("LLM_BASE_URL")
                or _env("OPENAI_BASE_URL")
                or _env("AZURE_PROJECT_ENDPOINT")
                or _env("AZURE_OPENAI_ENDPOINT")
            ).rstrip("/")
            # The plugin appends the operation path itself, so strip any that
            # leaked into the configured endpoint.
            for suffix in ("/responses", "/chat/completions"):
                if base_url.endswith(suffix):
                    base_url = base_url[: -len(suffix)]

    elif profile == "pipecat":
        model = (
            _env("LLM_MODEL")
            or _env(f"{provider.upper()}_MODEL")
            or {
                "openai": "gpt-4o-mini",
                "azure": "gpt-5.4-mini",
                "azure_openai": "gpt-5.4-mini",
                "foundry": "gpt-5.4-mini",
                "cerebras": "gpt-oss-120b",
                "deepseek": "deepseek-v4-pro",
            }.get(provider, "")
        )
        if provider == "openai":
            api_key = _env("OPENAI_API") or _env("OPENAI_API_KEY")
        elif provider in {"azure", "azure_openai", "foundry"}:
            api_key = (
                _env("AZURE_OPENAI_API_KEY")
                or _env("AZURE_AI_FOUNDRY_API_KEY")
                or _env("AZURE_API_KEY")
            )
        else:
            api_key = _env(f"{provider.upper()}_API_KEY") or _env(f"{provider.upper()}_API")

        if provider in {"azure", "azure_openai", "foundry"}:
            base_url = (
                _env("LLM_BASE_URL")
                or _env("AZURE_OPENAI_ENDPOINT")
                or _env("AZURE_AI_FOUNDRY_ENDPOINT")
                or _env("AZURE_OPENAI_BASE_URL")
            )
        elif provider == "deepseek":
            base_url = _env("LLM_BASE_URL") or _env(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            )
        else:
            base_url = _env("LLM_BASE_URL") or _env("OPENAI_BASE_URL")

    else:
        raise ValueError(f"Unknown profile: {profile!r}")

    return LLMSpec(
        provider=provider,
        wire="openai",
        model=model,
        api_key=api_key or None,
        base_url=base_url or None,
        region=None,
    )


def resolve_spec(
    profile: str,
    role: str = "main",
    provider: str | None = None,
) -> LLMSpec:
    """Resolve the LLM target for a call site.

    ``profile`` selects the historical env cascade ("livekit" or "pipecat").
    ``role`` distinguishes the conversational model ("main") from the
    end-of-turn classifier ("eot"), which may run on a cheaper model.
    ``provider`` overrides LLM_PROVIDER for callers that select one per-request.
    """
    provider = (provider or _env("LLM_PROVIDER")).lower()
    if not provider:
        if profile == "pipecat":
            # bot.py has always required this to be set explicitly.
            raise RuntimeError("LLM_PROVIDER is not set")
        provider = "openai"

    if wire_for(provider) == "bedrock":
        return _resolve_bedrock(role)
    return _resolve_openai_wire(profile, provider)


def _construct(factory: Any, /, **kwargs: Any) -> Any:
    """Call ``factory`` with only the kwargs it actually accepts.

    Plugin constructor signatures drift between releases, and a rejected kwarg
    would take down the whole agent at startup. Dropping unknown ones keeps a
    provider swap from becoming a version-pinning exercise; what was dropped is
    logged so it is visible rather than silent.
    """
    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return factory(**kwargs)

    accepts_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    if accepts_any:
        return factory(**kwargs)

    supported = {k: v for k, v in kwargs.items() if k in params}
    if dropped := sorted(set(kwargs) - set(supported)):
        logger.warning(
            "{} does not accept {}; continuing without them.",
            getattr(factory, "__name__", factory),
            ", ".join(dropped),
        )
    return factory(**supported)


def _first_supported(factory: Any, names: tuple[str, ...], default: str) -> str:
    """Return the first keyword in ``names`` that ``factory`` accepts.

    Providers spell the same concept differently -- region is ``aws_region`` or
    ``region``, an output cap is ``max_completion_tokens``, ``max_output_tokens``
    or ``max_tokens`` -- and passing every spelling would be a duplicate-argument
    error on any constructor taking ``**kwargs``.
    """
    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return default
    return next((name for name in names if name in params), default)


_REGION_KWARGS = ("aws_region", "region")
_MAX_TOKEN_KWARGS = ("max_completion_tokens", "max_output_tokens", "max_tokens")


def _apply_max_tokens(factory: Any, kwargs: dict[str, Any]) -> None:
    """Translate a canonical ``max_tokens`` override to the provider's spelling.

    Done explicitly rather than left to ``_construct`` because a dropped output
    cap is not a cosmetic loss: the voice pipeline relies on it to keep replies
    short enough to speak, so silently losing it would change agent behaviour.
    """
    if (value := kwargs.pop("max_tokens", None)) is None:
        return
    kwargs[_first_supported(factory, _MAX_TOKEN_KWARGS, "max_tokens")] = value


def make_livekit_llm(spec: LLMSpec, **overrides: Any) -> Any:
    """Build a LiveKit LLM plugin instance for ``spec``."""
    if spec.is_bedrock:
        try:
            from livekit.plugins import aws
        except ImportError as exc:  # pragma: no cover - depends on deployment image
            raise RuntimeError(
                "LLM_PROVIDER=bedrock needs the AWS plugin. "
                "Install livekit-plugins-aws (pin it to the same version as "
                "livekit-agents)."
            ) from exc

        logger.info("Creating LiveKit LLM | {}", spec.describe())
        kwargs: dict[str, Any] = {
            "model": spec.model,
            _first_supported(aws.LLM, _REGION_KWARGS, "region"): spec.region,
        }
        kwargs.update(overrides)
        _apply_max_tokens(aws.LLM, kwargs)
        return _construct(aws.LLM, **kwargs)

    from livekit.plugins import openai

    logger.info("Creating LiveKit LLM | {}", spec.describe())
    kwargs = {"model": spec.model, "api_key": spec.api_key}
    if spec.base_url:
        kwargs["base_url"] = spec.base_url
    kwargs.update(overrides)
    _apply_max_tokens(openai.LLM, kwargs)
    return _construct(openai.LLM, **kwargs)


def make_pipecat_llm(spec: LLMSpec, system_instruction: str = "", **overrides: Any) -> Any:
    """Build a Pipecat LLM service for ``spec``.

    Only the Bedrock branch lives here; the OpenAI-wire providers stay in
    ``bot.py:create_llm`` where their per-provider settings already are.
    """
    if not spec.is_bedrock:
        raise ValueError(
            "make_pipecat_llm only handles the bedrock wire; "
            "OpenAI-wire providers are built in bot.py:create_llm"
        )

    try:
        from pipecat.services.aws.llm import AWSBedrockLLMService
    except ImportError as exc:  # pragma: no cover - depends on deployment image
        raise RuntimeError(
            "LLM_PROVIDER=bedrock needs Pipecat's AWS service. "
            "Install pipecat-ai with the aws extra (pipecat-ai[aws])."
        ) from exc

    logger.info("Creating Pipecat LLM | {}", spec.describe())
    kwargs: dict[str, Any] = {
        "model": spec.model,
        _first_supported(AWSBedrockLLMService, _REGION_KWARGS, "aws_region"): spec.region,
        "system_instruction": system_instruction,
    }
    kwargs.update(overrides)
    _apply_max_tokens(AWSBedrockLLMService, kwargs)
    return _construct(AWSBedrockLLMService, **kwargs)
