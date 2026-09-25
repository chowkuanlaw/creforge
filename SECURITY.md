# Security policy

## Supported versions

Security fixes are released for the latest minor version on PyPI.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on
[the repository](https://github.com/chowkuanlaw/creforge/security/advisories/new).

You should get an acknowledgement within 7 days. Please include steps to reproduce
and the affected version.

## Scope

creforge reads only its own YAML profiles and the files it wrote itself, and makes
no network calls. Relevant reports include:

- Unsafe handling of profile YAML (creforge uses `yaml.safe_load`).
- Path handling when writing or reading output directories.
- Any case where generated output could contain or be derived from real personal
  data. That would break the project's core guarantee and is treated as a security
  issue.
