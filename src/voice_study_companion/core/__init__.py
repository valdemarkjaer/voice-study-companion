"""Provider-neutral streaming voice-study primitives.

Adapters translate provider-specific values at this package boundary.  The
core itself depends only on public contracts and never performs a scheduler
review action.
"""

from .domain import (
    AudioChunk,
    AudioEncoding,
    AudioFrame,
    AuthorizedUtterance,
    Capability,
    DialogueIntent,
    DialoguePlan,
    ErrorCode,
    GradeResult,
    GradeVerdict,
    LanguageTag,
    Operation,
    PipelineError,
    ProviderCapabilities,
    SpeechHints,
    SpeechSegment,
    Transcript,
    TurnIdentity,
    Usage,
    UsageAmounts,
    UtteranceKind,
    UtterancePolicy,
)

__all__ = [
    "AudioChunk",
    "AudioEncoding",
    "AudioFrame",
    "AuthorizedUtterance",
    "Capability",
    "DialogueIntent",
    "DialoguePlan",
    "ErrorCode",
    "GradeResult",
    "GradeVerdict",
    "LanguageTag",
    "Operation",
    "PipelineError",
    "ProviderCapabilities",
    "SpeechHints",
    "SpeechSegment",
    "Transcript",
    "TurnIdentity",
    "Usage",
    "UsageAmounts",
    "UtteranceKind",
    "UtterancePolicy",
]
