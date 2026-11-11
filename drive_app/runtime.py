from pathlib import Path

from .api import DriveClient
from .auth import RcloneAuth
from .logging_setup import configure_logging
from .storage import app_directory as app_directory, clean_stale_sessions, private_directory


def prepare_root(root: Path) -> None:
    private_directory(root)
    configure_logging(root)


def create_runtime(root: Path):
    from .manager import Manager
    import json
    prepare_root(root)
    clean_stale_sessions(root)
    settings = {}
    try:
        settings = json.loads((root / "preferences.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    auth = RcloneAuth(root)
    manager = Manager(root, lambda: DriveClient(auth),
                      concurrency=max(1, min(8, int(settings.get("concurrency", 4)))),
                      bandwidth_limit=max(0, int(float(settings.get("bandwidth_mib", 0)) * 1024 * 1024)))
    return manager, auth
