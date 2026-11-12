import os
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session", autouse=True)
def fresh_log_cache(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Decode with the code under test: a cache entry left by an older decoder would otherwise be read instead."""
    before = os.environ.get("XDG_CACHE_HOME")
    os.environ["XDG_CACHE_HOME"] = str(tmp_path_factory.mktemp("cache"))
    yield
    if before is None:
        del os.environ["XDG_CACHE_HOME"]
    else:
        os.environ["XDG_CACHE_HOME"] = before
