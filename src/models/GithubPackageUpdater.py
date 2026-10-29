import logging
import os
from typing import Callable, Optional

import requests

from ..lib.github_releases import fetch_latest_release
from ..lib.native_package import pick_release_asset
from ..lib.native_package import package_extension
from ..lib.ini_config import Config
from ..providers.NativePackageProvider import NativePackageProvider
from ..providers.providers_list import package_provider
from .Models import DownloadInterruptedException
from .StaticFileUpdater import StaticFileUpdater
from .UpdateManager import UpdateManager
from ..components.AdwEntryRowDefault import AdwEntryRowDefault
from gi.repository import Adw


def _normalize_release_tag(tag: str) -> str:
    t = (tag or '').strip().lower()
    if t.startswith('v'):
        t = t[1:]
    return t


class GithubPackageUpdater(UpdateManager):
    name = 'GithubPackageUpdater'
    label = 'GitHub releases'
    handles_embedded = None

    def __init__(self, el, embedded=None) -> None:
        super().__init__(embedded=embedded, el=el)
        self.staticfile_manager: Optional[StaticFileUpdater] = None
        self.repo_url_row = None
        self.allow_prereleases_row = None
        self._target_cache: Optional[dict] = None
        self._target_cache_failed = False

    def clear_target_cache(self) -> None:
        self._target_cache = None
        self._target_cache_failed = False

    def _repo_slug(self) -> Optional[str]:
        config = self.get_config()
        repo = config.get('repo', '').strip()
        if repo and '/' in repo:
            return repo
        reg = NativePackageProvider.registry_entry_for_el(self.el)
        if reg and reg.get('repo_slug'):
            return reg['repo_slug']
        return None

    def fetch_target_asset(self, *, force: bool = False):
        if not force and self._target_cache is not None:
            return self._target_cache
        if not force and self._target_cache_failed:
            return None

        repo_slug = self._repo_slug()
        if not repo_slug:
            self._target_cache_failed = True
            return None
        parts = repo_slug.split('/')
        if len(parts) != 2:
            self._target_cache_failed = True
            return None
        owner, repo = parts[0], parts[1]
        allow_prereleases = self.get_config().getboolean('allow_prereleases', False)
        release = fetch_latest_release(owner, repo, allow_prereleases)
        if not release:
            self._target_cache_failed = True
            return None
        asset = pick_release_asset(release.get('assets', []), package_extension())
        if not asset:
            self._target_cache_failed = True
            return None
        self._target_cache = {'release': release, 'asset': asset}
        self._target_cache_failed = False
        return self._target_cache

    def download(self, status_update_cb: Callable[[float], None]) -> tuple[str, str]:
        self.clear_target_cache()
        target = self.fetch_target_asset(force=True)
        if not target:
            raise Exception(_('No matching .rpm/.deb found in the latest GitHub release'))
        url = target['asset']['browser_download_url']
        self.staticfile_manager = StaticFileUpdater(self.el, url)
        fname, _etag = self.staticfile_manager.download(status_update_cb)
        self.staticfile_manager = None
        asset_id = str(target['asset'].get('id', ''))
        tag = target['release'].get('tag_name', '')
        Config.set_app_config(self.el, {
            **Config.get_app_config(self.el),
            'release_tag': tag,
            'release_asset_id': asset_id,
        })
        return fname, asset_id

    def cancel_download(self):
        if self.staticfile_manager:
            self.staticfile_manager.cancel_download()
            self.staticfile_manager = None

    def cleanup(self):
        if self.staticfile_manager:
            self.staticfile_manager.cleanup()

    def get_available_version(self) -> Optional[str]:
        target = self.fetch_target_asset()
        if target:
            return target['release'].get('tag_name')
        return None

    def is_update_available(self):
        if not self.el:
            return False
        target = self.fetch_target_asset()
        if not target:
            return None
        config = Config.get_app_config(self.el)
        reg = NativePackageProvider.registry_entry_for_el(self.el)
        stored_id = (
            config.get('release_asset_id') or (reg or {}).get('release_asset_id') or ''
        ).strip()
        stored_tag = (
            config.get('release_tag') or (reg or {}).get('release_tag') or ''
        ).strip()
        asset_id = str(target['asset'].get('id', ''))
        tag = target['release'].get('tag_name', '')
        if stored_tag and tag:
            if _normalize_release_tag(stored_tag) != _normalize_release_tag(tag):
                return True
            return False
        if stored_id and asset_id and stored_id != asset_id:
            if stored_id.startswith('direct:'):
                return False
            return True
        if not stored_tag and not stored_id:
            return True
        return False

    def load_form_rows(self):
        config = self.get_config()
        repo = config.get('repo', '') or (self._repo_slug() or '')

        self.repo_url_row = AdwEntryRowDefault(
            icon_name='gl-git',
            title=_('GitHub repository (owner/repo)'),
        )
        if repo:
            self.repo_url_row.set_text(repo)

        self.allow_prereleases_row = Adw.SwitchRow(
            title=_('Allow pre-releases'),
            active=config.getboolean('allow_prereleases', False),
        )
        return [self.repo_url_row, self.allow_prereleases_row]

    def get_config_from_form(self):
        repo = self.repo_url_row.get_text().strip() if self.repo_url_row else ''
        allow = self.allow_prereleases_row.get_active() if self.allow_prereleases_row else False
        return {
            'repo': repo,
            'repo_filename': '*',
            'allow_prereleases': allow,
        }

    def validate_config(self, config):
        if len(config.get('repo', '').split('/')) != 2:
            raise Exception(_('Enter a valid GitHub repository as owner/repo'))
