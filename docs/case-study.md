# Case study: designing voice study around learner control

## The product problem

Flashcard review has a precise authority model: a question is shown, a learner
attempts recall, the answer is revealed, and a review action affects future
scheduling. A conversational interface can accidentally blur those steps.
Playback may trigger capture, a pause may be mistaken for completion, the
official answer may leak into evaluation context, or an inferred grade may be
treated as permission to advance.

The project began with a narrower question: **what would a fluent voice-study
experience look like if those boundaries remained explicit?**

## Product principles

### 1. Protect recall, not a timer

The learner should be able to pause briefly, add a remembered detail, or ask a
terminology question. Slow-recall aggregation is still experimental; the
system does not claim to understand hesitation semantically or wait forever.
Explicit “keep thinking” and “finish” controls remain the reliable fallback.

<!-- claim:slow_recall_aggregation -->

### 2. Make the official answer opt-in

After evaluation, the normal state is quiet. The system does not automatically
read the complete answer and does not repeat the question. An explicit answer
request crosses the reveal boundary; a separate explicit request is required
to repeat or explain the prompt.

<!-- claim:official_answer_on_request -->

### 3. Treat grading as feedback

The evaluation contract distinguishes correct, partial, incorrect, and
ungradable input. It can report covered, missing, and incorrect concepts, but a
low-confidence, malformed, or contradictory result fails closed. Feedback is
for study support, never clinical judgment.

<!-- claim:structured_partial_credit -->

### 4. Keep scheduling with the learner's study system

The integration boundary leaves synchronization, due state, and scheduling to
a user-owned Anki installation. Review operations are tokenized and idempotent,
and a proposed rating is inert until learner confirmation. The public demo
simulates this contract without bundling or contacting Anki.

<!-- claim:learner_confirmed_review -->
<!-- claim:anki_compatibility_boundary -->

### 5. Design mixed language intentionally

The target study behavior includes Portuguese, English, and mixed utterances.
Rather than promise universal language understanding, the design separates
language selection, curated terminology expansion, and ambiguity handling.

<!-- claim:bilingual_terminology -->

## From production-shaped problem to public demonstration

A credible portfolio artifact needed to be useful without exposing a real
study collection, private topology, credentials, provider configuration, or
licensed course material. The public slice therefore uses:

- original synthetic questions and answers;
- generated shape and tone media;
- deterministic transcription, evaluation, speech, and synchronization;
- a loopback-only dependency-free server;
- a responsive browser interface;
- an evidence ledger that separates implemented, verified, experimental, and
  planned behavior.

<!-- claim:credential_free_public_demo -->

This is deliberately less spectacular than a live model call. It is more
auditable: the main walkthrough is deterministic, makes no external model or
API calls, and can be tested without credentials. Its external model/API usage
cost is therefore US$0 in deterministic demo mode. That scope excludes local
compute and connectivity and makes no price claim about future live providers.

## What the demo proves

Automated evidence currently demonstrates:

- the browser does not receive the answer before reveal;
- evaluation and answer request are distinct operations;
- the answer request returns only answer content;
- complete and transit profiles select the intended synthetic cards;
- simulated sync happens at session boundaries;
- pinned Chromium and WebKit automation covers phone, tablet, and desktop
  viewport walkthroughs, including selected keyboard, focus, accessible-name,
  contrast, reduced-motion, image-alternative, and voice-status checks;
- the server rejects non-loopback binding and cross-origin mutation.

It does **not** prove natural voice quality, every mobile lifecycle, acoustic
echo cancellation, every provider adapter, complete WCAG conformance, Safari
or iOS certification, native-app certification, real-device acceptance, or
coordinated multidevice presence.

<!-- claim:responsive_web_coverage -->
<!-- claim:multidevice_presence_boundary -->

## The key engineering lesson

Provider neutrality is not mainly a configuration switch. It is a set of
behavioral contracts. If adapters disagree about when a turn ends, what context
may include the official answer, whether speech can be interrupted, or who may
submit a review, changing providers changes product semantics.

The architecture therefore makes phase, purpose, authority, and failure modes
first-class values. Deterministic fakes exercise those contracts before a live
adapter introduces latency, pricing, network, or audio-device variability.

<!-- claim:provider_neutral_voice_core -->
<!-- claim:phase_aware_audio_boundaries -->

## Boundaries beyond this showcase

This repository deliberately stops at an installable, loopback-only local
demonstration.
Repository hosting, release operations, and production deployment are
governance and operational concerns outside its capability evidence. A future
general assistant and coordinated ambient/multidevice presence are separate
planned work, not hidden features of this study demo.

<!-- claim:general_assistant_separation_boundary -->

The authoritative wording and evidence statuses live in
[`../evidence/claims.json`](../evidence/claims.json).
