"""Parse GitHub URLs and fetch release metadata for native packages."""
import logging
import re
import threading
import time
from typing import Optional
from html import unescape
from urllib.parse import quote, unquote, urlsplit

import requests

from .native_package import pick_release_asset, package_extension

_RELEASE_CACHE: dict[str, tuple[float, dict]] = {}
_RELEASE_CACHE_TTL_SEC = 90
_inflight: dict[str, threading.Event] = {}
_inflight_lock = threading.Lock()

GITHUB_REPO_RE = re.compile(
    r'^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/?#]+)',
    re.I,
)


def _cache_get(key: str) -> Optional[dict]:
    entry = _RELEASE_CACHE.get(key)
    if not entry:
        return None
    ts, data = entry
    if time.time() - ts > _RELEASE_CACHE_TTL_SEC:
        del _RELEASE_CACHE[key]
        return None
    return data


def _cache_set(key: str, data: dict) -> None:
    _RELEASE_CACHE[key] = (time.time(), data)


def clear_release_cache() -> None:
    _RELEASE_CACHE.clear()


def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith('http'):
        url = 'https://' + url.lstrip('/')
    return url


def parse_github_repo_url(url: str) -> Optional[tuple[str, str]]:
    url = _normalize_url(url)
    m = GITHUB_REPO_RE.match(url)
    if not m:
        return None
    owner, repo = m.group('owner'), m.group('repo')
    if repo.endswith('.git'):
        repo = repo[:-4]
    return owner, repo


def parse_asset_filename_from_url(url: str) -> Optional[str]:
    path = urlsplit(_normalize_url(url)).path
    base = path.rsplit('/', 1)[-1]
    ext = package_extension().lower()
    if base.lower().endswith(ext):
        return base
    return None


def parse_release_tag_from_url(url: str) -> Optional[str]:
    """Return a release tag if the URL points at a specific release or asset."""
    parts = urlsplit(_normalize_url(url)).path.strip('/').split('/')
    try:
        idx = parts.index('releases')
    except ValueError:
        return None
    if idx + 1 >= len(parts):
        return None
    kind = parts[idx + 1]
    if kind == 'latest':
        return _latest_tag_via_web(parts[0], parts[1]) if len(parts) >= 2 else None
    if kind in ('tag', 'download') and idx + 2 < len(parts):
        return unquote(parts[idx + 2])
    return None


def parse_releases_download_url(url: str) -> Optional[tuple[str, str, str, str]]:
    """Return owner, repo, tag, asset filename for a releases/download/… link."""
    url = _normalize_url(url)
    parsed = parse_github_repo_url(url)
    if not parsed:
        return None
    owner, repo = parsed
    parts = urlsplit(url).path.strip('/').split('/')
    try:
        idx = parts.index('releases')
    except ValueError:
        return None
    if idx + 3 >= len(parts) or parts[idx + 1] != 'download':
        return None
    tag = parts[idx + 2]
    name = parts[idx + 3]
    ext = package_extension().lower()
    if not name.lower().endswith(ext):
        return None
    return owner, repo, tag, name


def _github_headers() -> dict[str, str]:
    return {'Accept': 'application/vnd.github+json'}


_WEB_HEADERS = {
    'User-Agent': 'Repo-Lever (https://github.com/isorropisths/repolever)',
    'Accept': 'text/html,application/xhtml+xml',
}

_WEB_ASSET_PATH_RE = re.compile(
    r'/(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases/download/(?P<tag>[^/]+)/(?P<name>[^"?\s]+\.(?:rpm|deb))',
    re.I,
)


def _latest_tag_via_web(owner: str, repo: str) -> Optional[str]:
    url = f'https://github.com/{owner}/{repo}/releases/latest'
    try:
        resp = requests.get(url, headers=_WEB_HEADERS, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        parts = urlsplit(resp.url).path.strip('/').split('/')
        if 'tag' in parts:
            idx = parts.index('tag')
            if idx + 1 < len(parts):
                return unquote(parts[idx + 1])
    except Exception as e:
        logging.debug('Web latest release tag failed for %s/%s: %s', owner, repo, e)
    return None


def _release_assets_via_web(owner: str, repo: str, tag: str) -> list[dict]:
    """Parse release download links from GitHub HTML (no REST API quota)."""
    tag_path = quote(tag, safe='')
    pages = (
        f'https://github.com/{owner}/{repo}/releases/expanded_assets/{tag_path}',
        f'https://github.com/{owner}/{repo}/releases/tag/{tag_path}',
    )
    assets: list[dict] = []
    seen: set[str] = set()
    for page_url in pages:
        try:
            resp = requests.get(page_url, headers=_WEB_HEADERS, timeout=30)
            resp.raise_for_status()
            html = unescape(resp.text)
        except Exception as e:
            logging.debug('Web release page failed %s: %s', page_url, e)
            continue
        for m in _WEB_ASSET_PATH_RE.finditer(html):
            if m.group('owner') != owner or m.group('repo') != repo:
                continue
            if unquote(m.group('tag')) != unquote(tag):
                continue
            name = m.group('name')
            if name in seen:
                continue
            seen.add(name)
            dl_tag = m.group('tag')
            assets.append({
                'name': name,
                'browser_download_url': (
                    f'https://github.com/{owner}/{repo}/releases/download/{dl_tag}/{name}'
                ),
                'id': name,
                'size': 0,
            })
        if assets:
            break
    return assets


def _release_from_web(owner: str, repo: str, tag: str) -> Optional[dict]:
    assets = _release_assets_via_web(owner, repo, tag)
    if not assets:
        return None
    return {
        'tag_name': tag,
        'assets': assets,
        'id': tag,
    }


def _wait_inflight(cache_key: str) -> Optional[dict]:
    with _inflight_lock:
        event = _inflight.get(cache_key)
    if not event:
        return None
    event.wait(timeout=60)
    return _cache_get(cache_key)


def _begin_inflight(cache_key: str) -> Optional[threading.Event]:
    with _inflight_lock:
        if cache_key in _inflight:
            return None
        event = threading.Event()
        _inflight[cache_key] = event
        return event


def _end_inflight(cache_key: str, event: threading.Event) -> None:
    event.set()
    with _inflight_lock:
        _inflight.pop(cache_key, None)


def fetch_latest_release(owner: str, repo: str, allow_prereleases: bool = False) -> Optional[dict]:
    cache_key = f'latest:{owner}/{repo}:{allow_prereleases}'
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    wait_result = _wait_inflight(cache_key)
    if wait_result is not None:
        return wait_result

    event = _begin_inflight(cache_key)
    if event is None:
        wait_result = _wait_inflight(cache_key)
        return wait_result if wait_result is not None else _cache_get(cache_key)

    headers = _github_headers()
    result: Optional[dict] = None
    try:
        if allow_prereleases:
            url = f'https://api.github.com/repos/{owner}/{repo}/releases'
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            releases = resp.json()
            for rel in releases:
                if rel.get('draft'):
                    continue
                if rel.get('assets'):
                    result = rel
                    break
        else:
            url = f'https://api.github.com/repos/{owner}/{repo}/releases/latest'
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        logging.error(e)
        result = None
    finally:
        _end_inflight(cache_key, event)

    if result:
        _cache_set(cache_key, result)
        return result

    tag = _latest_tag_via_web(owner, repo)
    if tag:
        result = _release_from_web(owner, repo, tag)
        if result:
            _cache_set(cache_key, result)
    return result


def fetch_release_by_tag(owner: str, repo: str, tag: str) -> Optional[dict]:
    cache_key = f'tag:{owner}/{repo}:{tag}'
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    wait_result = _wait_inflight(cache_key)
    if wait_result is not None:
        return wait_result

    event = _begin_inflight(cache_key)
    if event is None:
        wait_result = _wait_inflight(cache_key)
        return wait_result if wait_result is not None else _cache_get(cache_key)

    headers = _github_headers()
    tag_path = quote(tag, safe='')
    url = f'https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag_path}'
    result: Optional[dict] = None
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        logging.error(e)
        result = None
    finally:
        _end_inflight(cache_key, event)

    if result:
        _cache_set(cache_key, result)
        return result

    result = _release_from_web(owner, repo, tag)
    if result:
        _cache_set(cache_key, result)
    return result


def release_is_older_than(requested: dict, latest: dict) -> bool:
    if not requested or not latest:
        return False
    if requested.get('id') == latest.get('id'):
        return False
    req_at = requested.get('published_at') or ''
    lat_at = latest.get('published_at') or ''
    if req_at and lat_at:
        return req_at < lat_at
    return requested.get('tag_name') != latest.get('tag_name')


def _resolve_direct_download(
    repo_url: str,
    allow_prereleases: bool,
) -> Optional[dict]:
    """Install from releases/download/tag/file.rpm without calling the releases API."""
    direct = parse_releases_download_url(repo_url)
    if not direct:
        return None
    owner, repo, tag, name = direct
    download_url = _normalize_url(repo_url).split('?')[0]
    asset = {
        'name': name,
        'browser_download_url': download_url,
        'id': f'direct:{tag}:{name}',
        'size': 0,
    }
    target = {
        'tag_name': tag,
        'assets': [asset],
        'id': f'direct:{tag}',
    }
    latest = fetch_latest_release(owner, repo, allow_prereleases)
    return {
        'owner': owner,
        'repo': repo,
        'repo_slug': f'{owner}/{repo}',
        'release': target,
        'asset': asset,
        'tag_name': tag,
        'latest_release': latest or target,
        'requested_tag': tag,
        'is_outdated': release_is_older_than(target, latest) if latest else False,
    }


def resolve_github_package_asset(
    repo_url: str,
    allow_prereleases: bool = False,
    *,
    use_latest: bool = True,
    explicit_tag: Optional[str] = None,
) -> Optional[dict]:
    parsed = parse_github_repo_url(repo_url)
    if not parsed:
        return None
    owner, repo = parsed
    tag = explicit_tag if explicit_tag is not None else parse_release_tag_from_url(repo_url)
    preferred_asset = parse_asset_filename_from_url(repo_url)

    direct = _resolve_direct_download(repo_url, allow_prereleases)
    if direct:
        return direct

    latest: Optional[dict] = None

    if use_latest or not tag:
        latest = fetch_latest_release(owner, repo, allow_prereleases)
        if not latest:
            return None
        target = latest
    else:
        target = fetch_release_by_tag(owner, repo, tag)
        if not target:
            return None
        latest = fetch_latest_release(owner, repo, allow_prereleases)

    asset = pick_release_asset(
        target.get('assets', []),
        package_extension(),
        preferred_name=preferred_asset,
    )
    if not asset:
        return None

    if latest is None:
        latest = target

    return {
        'owner': owner,
        'repo': repo,
        'repo_slug': f'{owner}/{repo}',
        'release': target,
        'asset': asset,
        'tag_name': target.get('tag_name'),
        'latest_release': latest,
        'requested_tag': tag,
        'is_outdated': release_is_older_than(target, latest) if tag and latest else False,
    }


def preview_github_install(url: str, allow_prereleases: bool = False) -> Optional[dict]:
    """Check what would be installed; does not download."""
    tag = parse_release_tag_from_url(url)
    if tag:
        resolved = resolve_github_package_asset(
            url, allow_prereleases, use_latest=False, explicit_tag=tag
        )
    else:
        resolved = resolve_github_package_asset(url, allow_prereleases, use_latest=True)
        if resolved:
            resolved['is_outdated'] = False
    if not resolved:
        return None
    latest = resolved.get('latest_release') or resolved['release']
    return {
        'repo_slug': resolved['repo_slug'],
        'requested_tag': resolved['release'].get('tag_name'),
        'latest_tag': latest.get('tag_name'),
        'is_outdated': resolved.get('is_outdated', False),
    }
