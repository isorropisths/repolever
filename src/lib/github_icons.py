"""Fetch and cache GitHub repository owner avatars for list icons."""
import logging
import os
from typing import Optional

import requests
from gi.repository import GLib

_ICON_CACHE: dict[str, str] = {}


def _cache_dir() -> str:
    path = os.path.join(GLib.get_user_cache_dir(), 'repo-lever', 'icons')
    os.makedirs(path, exist_ok=True)
    return path


def avatar_path_for_repo(repo_slug: str) -> Optional[str]:
    if not repo_slug or '/' not in repo_slug:
        return None
    if repo_slug in _ICON_CACHE and os.path.isfile(_ICON_CACHE[repo_slug]):
        return _ICON_CACHE[repo_slug]

    safe = repo_slug.replace('/', '_')
    dest = os.path.join(_cache_dir(), f'{safe}.png')
    if os.path.isfile(dest):
        _ICON_CACHE[repo_slug] = dest
        return dest

    owner, _repo = repo_slug.split('/', 1)
    try:
        # Avoid the GitHub REST API (rate limits); github.com serves the owner avatar.
        avatar_url = f'https://github.com/{owner}.png?size=128'
        img = requests.get(avatar_url, timeout=15, allow_redirects=True)
        img.raise_for_status()
        with open(dest, 'wb') as f:
            f.write(img.content)
        _ICON_CACHE[repo_slug] = dest
        return dest
    except Exception as e:
        logging.debug('Avatar fetch failed for %s: %s', repo_slug, e)
        return None
