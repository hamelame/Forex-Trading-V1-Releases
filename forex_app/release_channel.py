import json, os
from pathlib import Path

CHANNEL_FILE = Path("publisher_release_channel.json")

DEFAULT_GITHUB_REPO = "hamelame/Forex-Trading-V1-Releases"
DEFAULT_MANIFEST_URL = ""

def get_release_channel(config=None):
    config=config or {}
    file_data={}
    try:
        if CHANNEL_FILE.exists():
            file_data=json.loads(CHANNEL_FILE.read_text(encoding="utf-8"))
    except Exception:
        file_data={}
    github = (
        os.environ.get("FXAI_UPDATE_GITHUB_REPO","").strip()
        or str(file_data.get("github_repo","")).strip()
        or str(config.get("publisher_update_github_repo","")).strip()
        or DEFAULT_GITHUB_REPO
    )
    manifest = (
        os.environ.get("FXAI_UPDATE_MANIFEST_URL","").strip()
        or str(file_data.get("manifest_url","")).strip()
        or str(config.get("publisher_update_manifest_url","")).strip()
        or DEFAULT_MANIFEST_URL
    )
    return github, manifest

def save_github_channel(repo):
    repo=(repo or "").strip().strip("/")
    if repo.count("/") != 1:
        raise ValueError("Use GitHub format owner/repository")
    CHANNEL_FILE.write_text(json.dumps({"github_repo":repo},indent=2),encoding="utf-8")
    return repo
