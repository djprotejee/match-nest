from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import os


class IsolatedTestCase(TestCase):
    def setUp(self):
        super().setUp()
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"DATABASE_URL": "", "TURSO_DATABASE_URL": "", "TURSO_AUTH_TOKEN": ""})
        env.start()
        self.addCleanup(env.stop)
        db = patch("app.storage.DB_PATH", Path(temp.name) / "test.sqlite")
        db.start()
        self.addCleanup(db.stop)
        from app.providers.http_cache import clear_http_memory
        clear_http_memory()
        self.addCleanup(clear_http_memory)
        import app.providers.api_football as api
        import app.providers.football_data as football
        import app.providers.registry as registry
        for module, name in [(api, "api_football_cache_path"), (football, "football_cache_path"), (football, "team_id_cache_path"), (registry, "events_cache_path")]:
            original = getattr(module, name)
            cache = patch.object(module, name, side_effect=lambda *args, _original=original, **kwargs: Path(temp.name) / _original(*args, **kwargs).name)
            cache.start()
            self.addCleanup(cache.stop)
        from app.storage import invalidate_entity_records_cache
        invalidate_entity_records_cache()
        self.addCleanup(invalidate_entity_records_cache)
