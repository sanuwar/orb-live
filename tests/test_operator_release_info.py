from pathlib import Path
from src.operator_release_info import read_release_info
def test_release_info_is_privacy_safe_and_closed():
    data=read_release_info(Path.cwd()).to_dict(); assert data["build_identity_status"]=="VERIFIED" and data["pinned_file_count"]==20 and data["live_ready"] is False; assert "path" not in data and "remote_url" not in data
