# Contributing

Thank you for helping improve Voice Study Companion. The project values small,
reviewable changes and evidence-backed claims over feature breadth.

## Before opening a change

- Use only original or clearly redistributable code and media.
- Never add real study cards, collection databases, transcripts, credentials,
  provider responses, private hostnames, or deployment configuration.
- Record the origin, license, modification status, and required notice for each
  new dependency or asset.
- Keep Anki compatibility independently developed and do not imply affiliation
  or endorsement.
- Label experimental and planned behavior honestly; do not upgrade evidence
  status without the corresponding reproducible evidence.

## Development setup

The local demo has no runtime dependency outside Python 3.12. The exact
reviewed Python and Node versions are recorded in `toolchain.lock.json`:

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src
export VSC_MODE=demo
python3 -m voice_study_companion.server
```

Run Python and browser checks before proposing a change:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -p 'test_*.py' -v
npm ci
npx playwright install chromium webkit
npm run test:browser
```

Use the locked dependency files. Do not commit virtual environments,
`node_modules`, browser binaries, caches, recordings, or local reports.

## Change expectations

A contribution should include:

1. a focused explanation of the user-visible or boundary behavior;
2. tests for successful and fail-closed paths;
3. updated documentation and evidence wording when claims change;
4. provenance and notice updates for every new distributed artifact; and
5. synthetic fixtures that disclose no user or operational data.

Accessibility is part of completion: preserve keyboard operation, visible
focus, accessible names, reduced-motion behavior, and status announcements.

## Security and conduct

Follow [SECURITY.md](SECURITY.md) for sensitive reports. Be respectful,
specific, and patient in public discussion. Harassment, discrimination, and
publication of another person's private information are not accepted.

By contributing, you confirm that you have the right to submit your work under
the project's Apache-2.0 license.
