import logging
import os
from typing import Callable, Optional

from gi.repository import GLib, Gtk, Gio, Gdk

from ..lib import packages_registry
from ..lib.constants import TMP_DIR
from ..lib.github_releases import preview_github_install, resolve_github_package_asset
from ..lib.ini_config import Config
from ..lib.native_package import (
    download_file,
    find_desktop_basename,
    install_package_file,
    install_package_files,
    is_package_installed,
    package_icon_path,
    path_visible_in_sandbox,
    query_installed_version,
    query_installed_package_arch,
    query_package_name_from_file,
    remove_package,
)
from ..lib import terminal
from ..models.AppListElement import InstalledStatus
from ..models.PackageListElement import PackageListElement, PackageUpdateLogic


class NativePackageProvider:
    supports_launch = True
    install_primary_label = _('Install')

    def __init__(self):
        self.name = _('Package')
        self.icon = 'gl-application-x-executable-symbolic'
        self.download_dir = os.path.join(TMP_DIR, 'package-downloads')
        os.makedirs(self.download_dir, exist_ok=True)

    @staticmethod
    def stable_path(registry_id: str) -> str:
        return f'repo-lever://{registry_id}'

    @staticmethod
    def registry_entry_for_el(el: PackageListElement) -> Optional[dict]:
        reg_id = Config.get_app_config(el).get('registry_id')
        if reg_id:
            return packages_registry.get_by_id(reg_id)
        for pkg in packages_registry.load()['packages']:
            if pkg.get('package_path') == el.file_path or pkg.get('id') == reg_id:
                return pkg
        return None

    @staticmethod
    def is_virtual_path(path: str) -> bool:
        return bool(path) and path.startswith('repo-lever://')

    def sync_element_status(self, el: PackageListElement) -> None:
        if self.is_installed(el):
            el.set_installed_status(InstalledStatus.INSTALLED)
            self.refresh_data(el)
        elif self.registry_entry_for_el(el):
            el.set_installed_status(InstalledStatus.REGISTRY_ONLY)

    def reinstall_from_registry(self, el: PackageListElement) -> PackageListElement:
        reg = self.registry_entry_for_el(el) or {}
        slug = reg.get('repo_slug') or el.description
        if not slug or '/' not in slug:
            website = Config.get_app_config(el).get('website', '')
            if website:
                url = website
            else:
                raise Exception(_('No GitHub repository is recorded for this entry'))
        else:
            url = f'https://github.com/{slug}'
        return self.install_from_github_url(url)

    def _ensure_github_update_config(self, el: PackageListElement, repo_slug: str, allow_prereleases: bool = False):
        from ..models.GithubPackageUpdater import GithubPackageUpdater

        if not repo_slug or '/' not in repo_slug:
            return
        update_conf = Config.get_app_update_config(el)
        if update_conf.get('manager') != GithubPackageUpdater.name or not update_conf.get('repo'):
            Config.set_app_update_config(el, GithubPackageUpdater, {
                'repo': repo_slug,
                'repo_filename': '*',
                'allow_prereleases': allow_prereleases,
            })

    def _entry_to_element(self, entry: dict) -> PackageListElement:
        registry_id = entry.get('id') or entry.get('repo_slug', '')
        package_name = entry.get('package_name', '')
        if package_name and is_package_installed(package_name):
            status = InstalledStatus.INSTALLED
            version = query_installed_version(package_name) or entry.get('release_tag')
        else:
            status = InstalledStatus.REGISTRY_ONLY
            version = entry.get('release_tag')

        el = PackageListElement(
            name=entry.get('display_name') or package_name or registry_id or _('Package'),
            description=entry.get('repo_slug') or '',
            provider=self.name,
            installed_status=status,
            file_path=self.stable_path(registry_id),
            trusted=True,
            version=version,
            size=entry.get('size', 0) or 0,
        )
        if not Config.get_app_config(el):
            Config.set_app_config(el, {
                'registry_id': registry_id,
                'package_name': package_name,
                'release_tag': entry.get('release_tag', ''),
                'release_asset_id': entry.get('release_asset_id', ''),
                'website': f"https://github.com/{entry['repo_slug']}" if entry.get('repo_slug') else '',
            })
        if entry.get('repo_slug'):
            self._ensure_github_update_config(
                el,
                entry['repo_slug'],
                entry.get('allow_prereleases', False),
            )
        return el

    def list_installed(self) -> list[PackageListElement]:
        output = []
        for entry in packages_registry.load()['packages']:
            try:
                output.append(self._entry_to_element(entry))
            except Exception as e:
                logging.warning(e)
        return output

    def _cleanup_download(self, path: str) -> None:
        if path and os.path.isfile(path) and path.startswith(self.download_dir):
            try:
                os.remove(path)
            except OSError as e:
                logging.debug(e)

    def install_file(self, el: PackageListElement):
        logging.info('Installing package: %s', el.file_path)
        el.set_installed_status(InstalledStatus.INSTALLING)
        src = el.file_path
        if not os.path.isfile(src):
            raise Exception(_('Package file is missing'))

        install_package_file(src)
        self._register_installed_package(el, src)

    def _register_installed_package(self, el: PackageListElement, src: str) -> PackageListElement:
        package_name = query_package_name_from_file(src) or el.name
        el.name = package_name
        el.version = query_installed_version(package_name)
        el.set_installed_status(InstalledStatus.INSTALLED)

        reg = self.registry_entry_for_el(el) or {}
        entry_id = reg.get('id') or Config.get_app_config(el).get('registry_id') or package_name
        entry = {
            'id': entry_id,
            'display_name': el.name,
            'package_name': package_name,
            'package_path': self.stable_path(entry_id),
            'repo_slug': reg.get('repo_slug'),
            'release_tag': Config.get_app_config(el).get('release_tag') or reg.get('release_tag'),
            'release_asset_id': Config.get_app_config(el).get('release_asset_id') or reg.get('release_asset_id'),
            'allow_prereleases': reg.get('allow_prereleases', False),
            'size': el.size,
        }
        packages_registry.upsert(entry)
        el.file_path = self.stable_path(entry_id)
        Config.set_app_config(el, {
            **Config.get_app_config(el),
            'registry_id': entry_id,
            'package_name': package_name,
        })
        if entry.get('repo_slug'):
            self._ensure_github_update_config(el, entry['repo_slug'], entry.get('allow_prereleases', False))
        self._cleanup_download(src)
        return el

    def _build_update_list_element(self, el: PackageListElement, update_file_path: str) -> PackageListElement:
        reg = self.registry_entry_for_el(el)
        list_element = PackageListElement(
            name=el.name,
            description=el.description,
            provider=self.name,
            installed_status=InstalledStatus.NOT_INSTALLED,
            file_path=update_file_path,
            trusted=True,
            size=os.path.getsize(update_file_path),
            version=Config.get_app_config(el).get('release_tag'),
        )
        list_element.update_logic = PackageUpdateLogic.REPLACE
        list_element.updating_from = el
        if reg:
            Config.set_app_config(list_element, {
                'registry_id': reg['id'],
                'package_name': Config.get_app_config(el).get('package_name'),
                'release_tag': Config.get_app_config(el).get('release_tag', ''),
                'release_asset_id': Config.get_app_config(el).get('release_asset_id', ''),
                'website': Config.get_app_config(el).get('website', ''),
            })
        return list_element

    def preview_install_from_github_url(self, url: str, allow_prereleases: bool = False) -> Optional[dict]:
        return preview_github_install(url, allow_prereleases)

    def install_from_github_url(
        self,
        url: str,
        allow_prereleases: bool = False,
        *,
        use_latest: bool = True,
        explicit_tag: Optional[str] = None,
        progress_cb: Optional[Callable[[float], None]] = None,
    ) -> PackageListElement:
        tag = explicit_tag
        if not use_latest and tag is None:
            from ..lib.github_releases import parse_release_tag_from_url
            tag = parse_release_tag_from_url(url)

        resolved = resolve_github_package_asset(
            url,
            allow_prereleases,
            use_latest=use_latest,
            explicit_tag=tag if not use_latest else None,
        )
        if not resolved:
            raise Exception(_('Could not find a release with a matching .rpm/.deb for this system'))

        asset = resolved['asset']
        os.makedirs(self.download_dir, exist_ok=True)
        dest = os.path.join(self.download_dir, asset['name'])
        logging.info('Downloading %s', asset['browser_download_url'])

        def download_progress(fraction: float) -> None:
            if progress_cb:
                progress_cb(fraction * 0.5)

        download_file(asset['browser_download_url'], dest, download_progress)
        if progress_cb:
            progress_cb(0.51)

        registry_id = resolved['repo_slug']
        packages_registry.upsert({
            'id': registry_id,
            'display_name': resolved['repo'],
            'repo_slug': resolved['repo_slug'],
            'release_tag': resolved.get('tag_name'),
            'release_asset_id': str(asset.get('id', '')),
            'package_path': self.stable_path(registry_id),
            'allow_prereleases': allow_prereleases,
            'size': asset.get('size', 0),
        })
        el = PackageListElement(
            name=resolved['repo'],
            description=resolved['repo_slug'],
            provider=self.name,
            installed_status=InstalledStatus.NOT_INSTALLED,
            file_path=dest,
            trusted=True,
            size=asset.get('size', 0),
            version=resolved.get('tag_name'),
        )
        Config.set_app_config(el, {
            'registry_id': registry_id,
            'release_tag': resolved.get('tag_name', ''),
            'release_asset_id': str(asset.get('id', '')),
            'website': f"https://github.com/{resolved['repo_slug']}",
        })
        self._ensure_github_update_config(el, resolved['repo_slug'], allow_prereleases)
        self.install_file(el)
        if progress_cb:
            progress_cb(1.0)
        return el

    def uninstall(self, el: PackageListElement, force_delete=False, remove_configuration=True):
        reg = self.registry_entry_for_el(el)
        package_name = Config.get_app_config(el).get('package_name') or (reg or {}).get('package_name')
        if package_name and is_package_installed(package_name):
            remove_package(package_name)
        if reg and reg.get('id'):
            packages_registry.remove(reg['id'])
        if remove_configuration:
            Config.delete_app_config(el)
            Config.delete_app_update_config(el)
        el.set_installed_status(InstalledStatus.NOT_INSTALLED)

    def is_installed(self, el: PackageListElement) -> bool:
        package_name = Config.get_app_config(el).get('package_name')
        if package_name:
            return is_package_installed(package_name)
        return False

    def is_updatable(self, el: PackageListElement) -> bool:
        return False

    def _themed_icon_for_package(self, package_name: str) -> Optional[Gtk.Image]:
        from ..lib.native_package import find_desktop_path

        desktop_path = find_desktop_path(package_name)
        if not desktop_path:
            return None
        desktop_visible = path_visible_in_sandbox(desktop_path)
        if not desktop_visible:
            return None
        icon_key = None
        try:
            with open(desktop_visible, encoding='utf-8', errors='replace') as f:
                for line in f:
                    if line.startswith('Icon='):
                        icon_key = line.split('=', 1)[1].strip()
                        break
        except OSError:
            return None
        if not icon_key or icon_key.startswith('/') or os.path.sep in icon_key:
            return None
        display = Gdk.Display.get_default()
        if not display:
            return None
        theme = Gtk.IconTheme.get_for_display(display)
        try:
            paintable = theme.lookup_icon(
                icon_key,
                None,
                64,
                1,
                Gtk.TextDirection.NONE,
                Gtk.IconLookupFlags.FORCE_SIZE,
            )
            return Gtk.Image.new_from_paintable(paintable)
        except Exception:
            return None

    def get_icon(self, el: PackageListElement) -> Gtk.Image:
        package_name = Config.get_app_config(el).get('package_name')
        if package_name:
            icon_path = package_icon_path(package_name)
            visible = path_visible_in_sandbox(icon_path) if icon_path else None
            if visible:
                return Gtk.Image.new_from_file(visible)
            themed = self._themed_icon_for_package(package_name)
            if themed:
                return themed
        return Gtk.Image.new_from_icon_name('gl-application-x-executable-symbolic')

    def get_description(self, el: PackageListElement) -> str:
        reg = self.registry_entry_for_el(el)
        if reg and reg.get('repo_slug'):
            return reg['repo_slug']
        return el.description or ''

    def refresh_data(self, el: PackageListElement):
        package_name = Config.get_app_config(el).get('package_name')
        if package_name:
            el.version = query_installed_version(package_name) or el.version

    def refresh_arch(self, el: PackageListElement):
        package_name = Config.get_app_config(el).get('package_name')
        el.architecture = query_installed_package_arch(package_name)

    def run(self, el: PackageListElement):
        package_name = Config.get_app_config(el).get('package_name')
        desktop = find_desktop_basename(package_name)
        if desktop:
            terminal.host_threaded_sh(['gtk-launch', desktop])
            return
        website = Config.get_app_config(el).get('website')
        if website:
            terminal.host_sh(['xdg-open', website])
            return
        raise Exception(_('No application menu entry was found for this package'))

    def reload_metadata(self, el: PackageListElement):
        pass

    def update_from_url(self, manager, el: PackageListElement, status_cb: callable) -> PackageListElement | None:
        from ..models.Models import DownloadInterruptedException

        def download_cb(progress: float) -> None:
            if status_cb:
                status_cb(min(progress * 0.9, 0.9))

        try:
            update_file_path, _f_hash = manager.download(download_cb)
        except DownloadInterruptedException:
            return None

        if status_cb:
            status_cb(0.92)

        list_element = self._build_update_list_element(el, update_file_path)
        self.install_file(list_element)
        if status_cb:
            status_cb(1.0)
        return list_element

    def batch_update_from_url(
        self,
        items: list[tuple],
    ) -> list[PackageListElement]:
        """Download updates in parallel, then install all packages with one pkexec."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from ..models.Models import DownloadInterruptedException

        if not items:
            return []

        def download_one(manager, el, status_cb):
            def download_cb(progress: float) -> None:
                if status_cb:
                    status_cb(min(progress * 0.85, 0.85))

            path, _asset = manager.download(download_cb)
            return el, path, status_cb

        downloaded: list[tuple[PackageListElement, str, callable]] = []
        errors: list[Exception] = []
        workers = min(4, len(items))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(download_one, manager, el, status_cb): (el, status_cb)
                for manager, el, status_cb in items
            }
            for future in as_completed(futures):
                el, status_cb = futures[future]
                try:
                    _el, path, cb = future.result()
                    downloaded.append((_el, path, cb))
                    if status_cb:
                        status_cb(0.88)
                except DownloadInterruptedException:
                    errors.append(DownloadInterruptedException())
                except Exception as e:
                    logging.error(e)
                    errors.append(e)

        if errors or len(downloaded) != len(items):
            for _el, path, _cb in downloaded:
                self._cleanup_download(path)
            if errors:
                raise errors[0]
            raise Exception(_('Not all updates could be downloaded'))

        paths = [path for _el, path, _cb in downloaded]
        for _el, _path, status_cb in downloaded:
            if status_cb:
                status_cb(0.92)

        install_package_files(paths)

        results: list[PackageListElement] = []
        for el, path, status_cb in downloaded:
            list_element = self._build_update_list_element(el, path)
            self._register_installed_package(list_element, path)
            if status_cb:
                status_cb(1.0)
            results.append(list_element)

        return results

    def is_app_running(self, el: PackageListElement) -> bool:
        return False

    def extraction_folder_cleanup(self):
        pass

    def get_package_format(self, el: PackageListElement) -> str:
        reg = self.registry_entry_for_el(el)
        ext = (reg or {}).get('package_ext', '')
        if ext in ('.rpm', '.deb'):
            return ext.upper().strip('.')
        return ''
