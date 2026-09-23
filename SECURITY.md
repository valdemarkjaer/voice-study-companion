# Security policy

## Supported versions

This project is still a reviewed release candidate. Until a public release is
tagged, only the newest candidate is eligible for security fixes. After a
release, this section will list supported versions explicitly.

## Reporting a vulnerability

Do not include credentials, private study material, personal data, collection
files, or exploit details in a public issue.

When this repository is public, use its **Security → Report a vulnerability**
workflow (GitHub private vulnerability reporting). If that workflow is not
available, open a public issue containing only the statement that a private
reporting channel is required; do not include sensitive details.

A report should include:

- affected version or candidate digest;
- the smallest safe reproduction;
- observed and expected behavior;
- impact and any known preconditions; and
- whether the reporter has already shared the issue elsewhere.

Maintainers should acknowledge a private report within five business days.
Disclosure timing is coordinated after impact and remediation are understood.

## Security properties of demo mode

- The server binds only to a loopback address.
- Host, Origin, and cross-site request metadata are validated.
- Sessions and learner responses remain in process memory.
- Default request logging is disabled.
- The demo requires no provider, collection, or private-network credential.
- Static responses use a restrictive content security policy and disable MIME
  sniffing.
- The official answer is unavailable before an explicit reveal transition.

These properties describe the included deterministic demo. They do not imply
that an arbitrary live provider, reverse proxy, hosted deployment, or native
wrapper inherits the same security posture.

## Out of scope for the public demo

- real collection content or synchronization;
- paid-provider credentials and model endpoints;
- production deployment configuration;
- ambient or background capture;
- medical or clinical decision support; and
- denial-of-service testing against third-party infrastructure.

Please use only synthetic fixtures in reports and tests.
