import os
from pathlib import Path


class Settings:
    database_url = os.getenv('DATABASE_URL', 'sqlite:///./data/dev.db')
    broker_url = os.getenv('REDIS_URL', 'redis://redis:6379/0')
    data_dir = Path(os.getenv('DATA_DIR', './data'))
    key_file = Path(os.getenv('ARTIFACT_KEY_FILE', './secrets/artifact.key'))
    password_file = Path(os.getenv('ADMIN_HASH_FILE', './secrets/admin.hash'))
    cookie_secure = os.getenv('COOKIE_SECURE', 'false').lower() == 'true'
    public_origin = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
    max_bytes = int(os.getenv('MAX_FILE_MB', '50')) * 1024 * 1024
    max_pages = int(os.getenv('MAX_PAGES', '500'))
    max_batch = int(os.getenv('MAX_BATCH', '20'))
    session_hours = 8
    processing_timeout = int(os.getenv('PROCESSING_TIMEOUT', '1800'))


settings = Settings()
