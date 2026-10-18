import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('publication', Path(__file__).resolve().parents[1] / 'check_publication.py')
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


class PublicationTests(unittest.TestCase):
    def test_private_paths_and_safe_example(self):
        for name in ('.env', 'secrets/artifact.key', 'frontend/.env.local', '.aws/credentials',
                     'backup.zip', 'data/example.pdf', 'backend/__pycache__/app.pyc',
                     'frontend/node_modules/a.js', 'docs/verification.md', 'docs/benchmark-local.json'):
            with self.subTest(name=name):
                self.assertTrue(publication.private_path(name))
        for name in ('.env.example', 'backend/app/main.py', 'examples/sample.txt', 'frontend/package-lock.json'):
            self.assertFalse(publication.private_path(name))

    def test_known_values_are_detected_but_not_printed(self):
        credential = b'local-' + b'credential-' + b'not-for-publication'
        issues = publication.content_findings('source.py', b'value=' + credential, [credential])
        self.assertTrue(issues)
        self.assertNotIn(credential.decode(), '\n'.join(issues))

    def test_provider_key_detection(self):
        token = b'gh' + b'p_' + b'A' * 36
        self.assertTrue(publication.content_findings('source.py', token))
        self.assertFalse(publication.content_findings('compose.yaml', b'postgresql+psycopg://app:${POSTGRES_PASSWORD:?Run bootstrap}@db:5432/app'))

    def test_hook_checks_staged_blob_not_clean_working_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            token = b'gh' + b'p_' + b'A' * 36
            source = root / 'app.py'
            source.write_bytes(token)
            subprocess.run(['git', '-C', str(root), 'add', 'app.py'], check=True)
            source.write_text('safe working copy\n')
            _, issues = publication.inspect(root, 'staged')
            self.assertTrue(any('GitHub credential' in issue for issue in issues))

    def test_force_added_private_file_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / '.gitignore').write_text('.env\n')
            (root / '.env').write_text('POSTGRES_PASSWORD=' + 'private-test-value\n')
            subprocess.run(['git', '-C', str(root), 'add', '-f', '.env'], check=True)
            _, issues = publication.inspect(root, 'staged')
            self.assertTrue(any('private or generated path' in issue for issue in issues))
            self.assertNotIn('private-test-value', '\n'.join(issues))


if __name__ == '__main__':
    unittest.main()
