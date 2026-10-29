"""Detect OS package type, pick GitHub release assets, and install/query packages."""
import logging
import os
import platform
import re
from typing import Callable, Optional

import requests
from gi.repository import Gio

from .terminal import host_sh

RPM_MIMES = {
    'application/x-rpm',
    'application/vnd.rpm',
    'application/x-redhat-package-manager',
}
DEB_MIMES = {
    'application/vnd.debian.binary-package',
    'application/x-debian-package',
    'application/x-deb',
}

ARCH = platform.machine()
IS_X86 = re.compile(r'(\-|\_|\.)(x86_64|x86-64|amd64|64bit|64-bit)(\-|\_|\.|$)', re.I)
IS_ARM = re.compile(r'(\-|\_|\.)(aarch64|arm64|armv8)(\-|\_|\.|$)', re.I)
IS_ARM32 = re.compile(r'(\-|\_|\.)(armv7|armhf|armv7l)(\-|\_|\.|$)', re.I)
IS_I386 = re.compile(r'(\-|\_|\.)(i386|i686|x86)(\-|\_|\.|$)', re.I)


def package_extension() -> str:
    if os.path.isfile('/etc/debian_version') and not os.path.isfile('/etc/fedora-release'):
        return '.deb'
    return '.rpm'


def preferred_mimes() -> set[str]:
    if package_extension() == '.deb':
        return DEB_MIMES
    return RPM_MIMES


def _content_type(path: str) -> str:
    gfile = Gio.File.new_for_path(path)
    return gfile.query_info('standard::content-type', Gio.FileQueryInfoFlags.NONE, None).get_content_type()


def is_native_package_file(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.rpm', '.deb'):
        return True
    try:
        return _content_type(path) in (RPM_MIMES | DEB_MIMES)
    except Exception:
        return False


def _fedora_version() -> Optional[int]:
    path = '/etc/fedora-release'
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
        m = re.search(r'(?:Fedora Linux release|Fedora release)\s+(\d+)', text)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _distro_score(name: str) -> int:
    """Prefer RPM filenames that match this Fedora/RHEL version (e.g. fc44, f43)."""
    n = name.lower()
    score = 0
    fv = _fedora_version()
    if not fv:
        return 0

    for m in re.finditer(r'(?:\.|_|-)(?:fc|f)(\d{2})(?:\.|_|-|$)', n):
        tag_ver = int(m.group(1))
        if tag_ver == fv:
            score += 70
        elif tag_ver == fv - 1 or tag_ver == fv + 1:
            score += 15
        else:
            score -= 90

    for m in re.finditer(r'fedora[^\d]*(\d+)', n):
        tag_ver = int(m.group(1))
        if tag_ver == fv:
            score += 70
        elif abs(tag_ver - fv) <= 1:
            score += 15
        else:
            score -= 90

    if re.search(rf'(?:\.|_|-){fv}(?:\.|_|-|$)', n):
        score += 40

    return score


def _arch_score(name: str) -> int:
    n = name.lower()
    if ARCH in ('x86_64', 'amd64'):
        if IS_ARM.search(n) or IS_ARM32.search(n):
            return -100
        if IS_X86.search(n) or 'x64' in n:
            return 50
        if 'universal' in n or 'all' in n.split('.'):
            return 10
        return 5
    if ARCH in ('aarch64', 'arm64'):
        if IS_X86.search(n) or IS_I386.search(n):
            return -100
        if IS_ARM.search(n):
            return 50
        return 5
    return 0


def pick_release_asset(
    assets: list[dict],
    ext: Optional[str] = None,
    preferred_name: Optional[str] = None,
) -> Optional[dict]:
    ext = ext or package_extension()
    ext = ext.lower()
    candidates = [a for a in assets if a.get('name', '').lower().endswith(ext)]
    if not candidates:
        return None

    if preferred_name:
        for a in candidates:
            if a.get('name') == preferred_name:
                return a

    def total_score(asset: dict) -> int:
        name = asset.get('name', '')
        return _arch_score(name) + _distro_score(name)

    candidates.sort(key=lambda a: (total_score(a), a.get('size', 0)), reverse=True)
    best = candidates[0]
    if _arch_score(best['name']) < 0:
        return None
    return best


def download_file(url: str, dest: str, progress_cb: Optional[Callable[[float], None]] = None) -> None:
    resp = requests.get(url, stream=True, timeout=300)
    resp.raise_for_status()
    total = int(resp.headers.get('content-length', 0) or 0)
    downloaded = 0
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            f.write(chunk)
            downloaded += len(chunk)
            if progress_cb and total > 0:
                progress_cb(min(downloaded / total, 0.99))
    if progress_cb:
        progress_cb(1.0)


def query_package_name_from_file(package_path: str) -> Optional[str]:
    ext = os.path.splitext(package_path)[1].lower()
    try:
        if ext == '.rpm':
            out = host_sh(['rpm', '-qp', '--queryformat', '%{NAME}', package_path])
            return out.strip() or None
        if ext == '.deb':
            out = host_sh(['dpkg-deb', '-f', package_path, 'Package'])
            return out.strip() or None
    except Exception as e:
        logging.error(e)
    return None


def query_installed_version(package_name: str) -> Optional[str]:
    if not package_name:
        return None
    try:
        if package_extension() == '.deb':
            out = host_sh(['dpkg-query', '-W', '-f=${Version}', package_name], return_stderr=True)
            if out.strip():
                return out.strip()
            return None
        out = host_sh(['rpm', '-q', '--queryformat', '%{VERSION}-%{RELEASE}', package_name], return_stderr=True)
        if out.strip() and 'is not installed' not in out:
            return out.strip()
    except Exception:
        pass
    return None


def is_package_installed(package_name: str) -> bool:
    return query_installed_version(package_name) is not None


def query_installed_package_arch(package_name: str) -> Optional[str]:
    """Return the CPU architecture of an installed package (e.g. x86_64, noarch)."""
    if not package_name or not is_package_installed(package_name):
        return None
    try:
        if package_extension() == '.deb':
            out = host_sh(
                ['dpkg-query', '-W', '-f=${Architecture}', package_name],
                return_stderr=True,
            )
        else:
            out = host_sh(
                ['rpm', '-q', '--queryformat', '%{ARCH}', package_name],
                return_stderr=True,
            )
        arch = out.strip()
        return arch or None
    except Exception:
        return None


def normalize_cpu_arch(arch: str) -> str:
    a = (arch or '').lower()
    if a in ('x86_64', 'amd64', 'x86-64', 'x64'):
        return 'x86_64'
    if a in ('aarch64', 'arm64'):
        return 'aarch64'
    if a in ('i386', 'i686', 'x86'):
        return 'i386'
    return a


def cpu_architectures_compatible(system_arch: str, package_arch: str) -> bool:
    if not package_arch or package_arch.lower() == 'noarch':
        return True
    if str(package_arch).upper() == 'UNKNOWN':
        return True
    return normalize_cpu_arch(system_arch) == normalize_cpu_arch(package_arch)


def path_visible_in_sandbox(host_path: str) -> Optional[str]:
    """Map an absolute host path to a path readable inside the Flatpak sandbox."""
    if not host_path:
        return None
    if os.path.isfile(host_path):
        return host_path
    if host_path.startswith('/'):
        mapped = '/run/host' + host_path
        if os.path.isfile(mapped):
            return mapped
    return None


def install_package_file(package_path: str) -> None:
    ext = os.path.splitext(package_path)[1].lower()
    path = os.path.abspath(package_path)
    if ext == '.rpm':
        for cmd in (
            ['pkexec', 'dnf', 'install', '-y', path],
            ['pkexec', 'rpm', '-Uvh', path],
        ):
            try:
                host_sh(cmd)
                return
            except Exception as e:
                logging.warning('Install attempt failed: %s', e)
        raise Exception(_('Could not install RPM package (dnf or rpm failed)'))
    if ext == '.deb':
        for cmd in (
            ['pkexec', 'apt-get', 'install', '-y', path],
            ['pkexec', 'dpkg', '-i', path],
        ):
            try:
                host_sh(cmd)
                return
            except Exception as e:
                logging.warning('Install attempt failed: %s', e)
        raise Exception(_('Could not install Debian package (apt or dpkg failed)'))
    raise Exception(_('Unsupported package type'))


def install_package_files(package_paths: list[str]) -> None:
    """Install several local packages in one privileged command (one password prompt)."""
    paths = [os.path.abspath(p) for p in package_paths if p and os.path.isfile(p)]
    if not paths:
        return
    ext = os.path.splitext(paths[0])[1].lower()
    if ext == '.rpm':
        for cmd in (
            ['pkexec', 'dnf', 'install', '-y', *paths],
            ['pkexec', 'rpm', '-Uvh', *paths],
        ):
            try:
                host_sh(cmd)
                return
            except Exception as e:
                logging.warning('Batch RPM install failed: %s', e)
        raise Exception(_('Could not install RPM packages (dnf or rpm failed)'))
    if ext == '.deb':
        for cmd in (
            ['pkexec', 'apt-get', 'install', '-y', *paths],
            ['pkexec', 'dpkg', '-i', *paths],
        ):
            try:
                host_sh(cmd)
                return
            except Exception as e:
                logging.warning('Batch Debian install failed: %s', e)
        raise Exception(_('Could not install Debian packages (apt or dpkg failed)'))
    raise Exception(_('Unsupported package type'))


def remove_package(package_name: str) -> None:
    if not package_name:
        return
    if package_extension() == '.deb':
        host_sh(['pkexec', 'apt-get', 'remove', '-y', package_name])
    else:
        host_sh(['pkexec', 'dnf', 'remove', '-y', package_name])


def find_desktop_path(package_name: str) -> Optional[str]:
    """Return the installed .desktop file path for a package, if any."""
    if not package_name:
        return None
    try:
        if package_extension() == '.deb':
            out = host_sh(['dpkg', '-L', package_name], return_stderr=True)
        else:
            out = host_sh(['rpm', '-ql', package_name], return_stderr=True)
        for line in out.splitlines():
            line = line.strip()
            if '/applications/' in line and line.endswith('.desktop'):
                if not line.startswith('/'):
                    line = '/' + line
                return line
    except Exception as e:
        logging.debug('find_desktop_path: %s', e)
    return None


def find_desktop_basename(package_name: str) -> Optional[str]:
    """Return a .desktop file name suitable for gtk-launch, if installed."""
    path = find_desktop_path(package_name)
    return os.path.basename(path) if path else None


def _resolve_icon_path(icon_name: str, desktop_path: str) -> Optional[str]:
    if not icon_name:
        return None
    direct = path_visible_in_sandbox(icon_name)
    if direct:
        return direct
    desktop_visible = path_visible_in_sandbox(desktop_path) or desktop_path
    desktop_dir = os.path.dirname(desktop_visible)
    for rel in (
        icon_name,
        f'{icon_name}.png',
        f'{icon_name}.svg',
        f'../icons/hicolor/scalable/apps/{icon_name}.svg',
        f'../icons/hicolor/512x512/apps/{icon_name}.png',
        f'../icons/hicolor/256x256/apps/{icon_name}.png',
        f'../icons/hicolor/128x128/apps/{icon_name}.png',
        f'../icons/hicolor/64x64/apps/{icon_name}.png',
        f'../icons/hicolor/48x48/apps/{icon_name}.png',
    ):
        candidate = os.path.normpath(os.path.join(desktop_dir, rel))
        visible = path_visible_in_sandbox(candidate)
        if visible:
            return visible
    for size in ('512x512', '256x256', '128x128', '64x64', '48x48', 'scalable'):
        for ext in ('png', 'svg'):
            candidate = f'/usr/share/icons/hicolor/{size}/apps/{icon_name}.{ext}'
            visible = path_visible_in_sandbox(candidate)
            if visible:
                return visible
    pix = f'/usr/share/pixmaps/{icon_name}.png'
    return path_visible_in_sandbox(pix)


def package_icon_path(package_name: str) -> Optional[str]:
    desktop_path = find_desktop_path(package_name)
    if not desktop_path:
        return None
    desktop_visible = path_visible_in_sandbox(desktop_path)
    if not desktop_visible:
        return None
    try:
        icon_key = None
        with open(desktop_visible, encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.startswith('Icon='):
                    icon_key = line.split('=', 1)[1].strip()
                    break
        return _resolve_icon_path(icon_key, desktop_path) if icon_key else None
    except Exception as e:
        logging.debug('package_icon_path: %s', e)
    return None
