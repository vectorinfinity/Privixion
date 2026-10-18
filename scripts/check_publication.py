"""Check Git publication candidates without printing credential values.

Default: tracked plus nonignored untracked working files.
--staged: added/modified index blobs (used by the pre-commit hook).
--tracked: all index blobs (used in CI).
"""
import argparse
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {
    'secrets', '.secrets', 'private', 'data', 'uploads', 'originals', 'redacted',
    'exports', 'reports', 'backups', 'backup', 'logs', '.codex', '.agents', '.aws',
    '.ssh', '.git', '.venv', 'venv', 'env', 'node_modules', 'dist', 'build',
    '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.cache', '.vite',
    'coverage', 'htmlcov', 'test-results', 'playwright-report', 'tmp', 'temp', '.idea', '.vscode',
}
PRIVATE_FILES = (
    '.env', '.env.*', '*.env', '*.env.*', '.npmrc', '.pypirc', '*.key', '*.pem',
    '*.p12', '*.pfx', '*.hash', '*.enc', '*.session', '*password*.txt',
    'credentials*.json', '*service-account*.json', 'cookies*.json', '*.db', '*.db-*',
    '*.sqlite', '*.sqlite3', '*.sqlite-*', '*.sqlite3-*', '*.dump', '*.sql.gz',
    '*.zip', '*.tar', '*.tar.gz', '*.tgz', '*.7z', '*.rar', '*.pyc', '*.pyo',
    '*.log', '*.tmp', '*.bak', '*.tsbuildinfo', '.coverage', '.coverage.*',
    '.ds_store', 'thumbs.db', 'desktop.ini', '*.swp', '*.swo',
)
KEY_PATTERNS = (
    ('private-key material', re.compile(rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\r?\n')),
    ('GitHub credential', re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b')),
    ('AI API credential', re.compile(rb'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}\b')),
    ('AWS access identifier', re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')),
    ('Google API credential', re.compile(rb'\bAIza[0-9A-Za-z_-]{35}\b')),
    ('credential in connection URL', re.compile(rb'\b(?:postgres(?:ql)?(?:\+psycopg)?|mysql|redis)://[^\s/:]+:([^\s/@]+)@')),
)


def private_path(name):
    name = name.replace('\\', '/').lower()
    if name == '.env.example':
        return False
    path = PurePosixPath(name)
    return (any(part in PRIVATE_DIRS for part in path.parts[:-1])
            or any(fnmatch.fnmatchcase(path.name, pattern) for pattern in PRIVATE_FILES)
            or name == 'docs/verification.md'
            or fnmatch.fnmatchcase(name, 'docs/benchmark-*.json'))


def local_credentials(root):
    """Compare against local live values, in memory only; never log the values."""
    values = set()
    candidates = [root / '.env']
    directory = root / 'secrets'
    if directory.is_dir():
        candidates.extend(p for p in directory.rglob('*') if p.is_file() and not p.is_symlink())
    for path in candidates:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 1_000_000:
            continue
        data = path.read_bytes()
        name = path.name.lower()
        if name == 'artifact.key' and len(data) == 32:
            values.add(data)
        elif name.endswith('.hash') or ('password' in name and name.endswith('.txt')):
            if len(data.strip()) >= 8:
                values.add(data.strip())
        elif name == '.env' or name.endswith('.env'):
            for line in data.decode('utf-8', errors='ignore').splitlines():
                key, separator, value = line.partition('=')
                if separator and not key.lstrip().startswith('#') and re.search(r'PASSWORD|SECRET|TOKEN|KEY', key, re.I):
                    secret = value.strip().strip('\"\'').encode()
                    if len(secret) >= 8:
                        values.add(secret)
    return values


def content_findings(name, content, known_values=()):
    findings = []
    if any(value in content for value in known_values):
        findings.append('matches a local live credential or encryption key')
    for description, pattern in KEY_PATTERNS:
        for match in pattern.finditer(content):
            # Environment interpolation is source configuration, not an embedded password.
            if description == 'credential in connection URL' and any(marker in match.group(1) for marker in (b'${', b'{', b'PLACEHOLDER')):
                continue
            findings.append(description)
            break
    return [f'{name}: {description}' for description in findings]


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if result.returncode:
        raise RuntimeError('Git inspection failed. Initialize the project with `git init -b main` first.')
    return result.stdout


def candidates(root, mode):
    if mode == 'staged':
        raw = git(root, 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z')
    elif mode == 'tracked':
        raw = git(root, 'ls-files', '--cached', '-z')
    else:
        raw = git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z')
    return sorted(set(os.fsdecode(path) for path in raw.split(b'\0') if path))


def inspect(root, mode):
    issues, names = [], candidates(root, mode)
    known = local_credentials(root)
    index_modes = {}
    for entry in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if entry:
            metadata, name = entry.split(b'\t', 1)
            index_modes[os.fsdecode(name)] = metadata.split()[0]
    for name in names:
        if private_path(name):
            issues.append(f'{name}: private or generated path must not be published')
            continue
        path = root / name
        if index_modes.get(name) == b'120000' or path.is_symlink():
            issues.append(f'{name}: symbolic link requires manual review')
            continue
        if mode in {'staged', 'tracked'}:
            content = git(root, 'show', ':' + name)
        else:
            try:
                path.resolve().relative_to(root.resolve())
            except ValueError:
                issues.append(f'{name}: path resolves outside the project')
                continue
            if not path.is_file():
                issues.append(f'{name}: missing working file')
                continue
            content = path.read_bytes()
        issues.extend(content_findings(name, content, known))
    return names, issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--staged', action='store_true')
    group.add_argument('--tracked', action='store_true')
    args = parser.parse_args()
    mode = 'staged' if args.staged else 'tracked' if args.tracked else 'working'
    try:
        names, issues = inspect(ROOT, mode)
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if issues:
        print('Publication check FAILED. Credential values are intentionally omitted.', file=sys.stderr)
        for issue in issues:
            print('  ' + issue, file=sys.stderr)
        return 1
    print(f'Publication check passed: {len(names)} Git {mode} files; no blocked paths, known local credentials, or supported key patterns found.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
