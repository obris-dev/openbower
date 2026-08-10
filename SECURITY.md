# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report privately via GitHub's
[**Report a vulnerability**](https://github.com/obris-dev/openbower/security/advisories/new)
button (the repo's Security tab, then Advisories). If you can't use that,
email **security@openbower.ai** instead.

We aim to acknowledge within 3 business days and will keep you updated as
we investigate. Once a fix ships we're glad to credit you, unless you'd
rather stay anonymous.

## Scope

OpenBower is self-hosted and BYO-key: AI models and search providers run
on credentials you supply, against your own instance. The areas we care
most about:

- Authentication and account scoping (sessions, the OAuth client).
- The agent research path: fetching and searching against untrusted URLs
  (SSRF containment) and prompt-injection handling of fetched content.
- Handling of user-supplied provider API keys and any secrets at rest.
- The CSV import parser (untrusted file input).

## Supported versions

Pre-1.0: fixes land on `main`. Pin a commit if you need stability.
