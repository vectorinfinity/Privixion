# Security and private data

Never commit live document content, originals, redacted outputs, audit exports,
backups, administrator passwords, encryption keys, session cookies, or populated
environment files. The repository's `.gitignore` excludes these paths and common
credential/database/archive formats. Only `.env.example` is public; it has no
password or key values.

`scripts/bootstrap.py` generates each installation's private credentials and
encryption key locally. GitHub Actions uses temporary test keys and synthetic
documents; it does not need your live `.env`, administrator password, database
password, or encryption key. Sample identifiers and test passwords in test-only
fixtures are synthetic and are not deployment credentials.

Run `python scripts/check_publication.py` before staging and
`python scripts/check_publication.py --staged` before committing. The installed
pre-commit hook checks actual staged blobs, even when a working copy has since
been edited. It blocks private paths, local credential values, and common API key
patterns without printing those values. The CI job checks committed files too.
These checks complement review; they cannot recognize every possible secret.

For a vulnerability report, use the repository's private security advisory
feature if the owner enables it. Otherwise contact the repository owner privately.
Do not put passwords, real documents, encryption keys, or sensitive screenshots in
public issues, pull requests, logs, or sample data.
