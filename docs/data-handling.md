# Data handling

This document describes the credential-free demonstration included in the
public candidate. It is not a privacy statement for a future live provider.

## Data used by the demo

| Data | Source | Lifetime | Destination |
| --- | --- | --- | --- |
| Synthetic cards | Bundled original source recipes | Repository/runtime | Local browser only |
| Typed answer | Learner during the session | In-memory session | Local process only |
| Simulated voice answer | Deterministic fixture | One request | Local process only |
| Evaluation result | Deterministic evaluator | In-memory session | Local browser only |
| Synthetic image/audio | Generated original recipes | One request/browser playback | Local browser only |
| Sync result | Deterministic synchronizer | In-memory session | Local browser only |

## What is not collected

Demo mode does not request microphone permission, read a real collection,
contact a model provider, use analytics, set tracking cookies, persist learner
answers, or transmit data to a cloud service. Default HTTP request logging is
disabled.

The server accepts loopback binding only. Host and Origin checks prevent the
demo from being treated as a general network service. Closing the process
discards its in-memory sessions.

## Media

The bundled study content is synthetic. Shape media is generated as SVG and
sound media as deterministic WAV bytes; no user recording or opaque copied
asset is required. The included screenshots and walkthrough video were
produced from a clean synthetic session, stripped of identifying metadata, and
bound to the exact digests in [`../reports/media-review.json`](../reports/media-review.json).

## Live-adapter boundary

A future live adapter would create a new data flow. Before it can be enabled,
its documentation must identify:

- which audio, text, and metadata leave the device;
- provider and model selection;
- retention and training policy;
- region and subprocessors when applicable;
- cancellation, retry, and deletion behavior;
- usage/cost metering; and
- the exact credential and consent boundary.

No such adapter is enabled in this public demo.

## Anki compatibility

The demo does not open or synchronize an Anki collection. The architecture
reserves synchronization and scheduling authority for a user-owned Anki
installation and represents that boundary with a deterministic local adapter.
Voice Study Companion is independently developed and is not affiliated with,
endorsed by, or sponsored by Anki or AnkiWeb.

## Test and report hygiene

Contributors must use synthetic fixtures. Test failures and retained reports
must not contain credentials, learner answers, private paths, hostnames, raw
scanner matches, or collection content. Security findings should be reported
through the private channel described in [../SECURITY.md](../SECURITY.md).
