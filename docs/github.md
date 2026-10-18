# Publishing this project to GitHub

The application source, Dockerfiles, dependency manifests and lockfile, tests,
scripts, documentation, and clearly synthetic examples belong in the repository.
Dependencies, builds, caches, local benchmark/session reports, runtime documents,
backups, and credentials are excluded. The existing private files stay on disk.
 
## Prepare and inspect

For a new checkout/folder without Git metadata:

```powershell
git init -b main
git config core.hooksPath .githooks
```

Use `python` below, or `.\.venv\Scripts\python.exe` if using the local Windows
virtual environment. The check uses only Python's standard library and Git.

```powershell
python scripts/check_publication.py
git add .
python scripts/check_publication.py --staged
git diff --cached --stat
git diff --cached --name-only
```

Review the staged list. It must not include `.env`, `secrets/`, `data/`, passwords,
keys, personal documents, archives, `node_modules/`, or virtual environments.
Never use `git add -f` for private files. Ignore rules do not protect files that
have already been committed; the check rejects blocked paths even if force-added.
The `.env.example` file intentionally contains a blank database password.

## Commit and push

Create an empty GitHub repository first. Replace `YOUR-USERNAME` and
`YOUR-REPOSITORY` with its owner and name; do not put an access token in the URL.

```powershell
git commit -m "Initial document redaction application"
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

Authenticate using Git Credential Manager, GitHub CLI, or SSH. Enable the
repository's secret scanning/push protection settings where available. The
workflow runs publication checks, frontend build checks, and the complete backend
suite with temporary synthetic test data and no live credentials.

## After cloning

Run `python scripts/bootstrap.py` to create fresh local credentials and an
encryption key, then follow the README. Do not copy another installation's keys
unless deliberately restoring its encrypted backup. Enable the hook in each new
clone using `git config core.hooksPath .githooks`; local Git config is not cloned.

Do not drag the entire working directory into GitHub's web uploader: that bypasses
Git's ignore rules. Publish using the inspected Git file list instead.
