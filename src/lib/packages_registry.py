"""Persistent registry of GitHub-tracked native packages."""
import json
import logging
import os
import uuid
from typing import Optional

from gi.repository import GLib

REGISTRY_NAME = 'repo-lever-packages.json'
LEGACY_REGISTRY_NAMES = ('repo-lever-packages.json', 'github-package-lever-packages.json')


def _registry_path() -> str:
    return os.path.join(GLib.get_user_config_dir(), REGISTRY_NAME)


def load() -> dict:
    path = _registry_path()
    if not os.path.isfile(path):
        legacy_paths = [os.path.join(GLib.get_user_config_dir(), n) for n in LEGACY_REGISTRY_NAMES]
        for legacy in legacy_paths:
            if not os.path.isfile(legacy):
                continue
            try:
                with open(legacy, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                save(data)
                return data if 'packages' in data else {'packages': []}
            except Exception as e:
                logging.error(e)
        return {'packages': []}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if 'packages' not in data:
            data['packages'] = []
        return data
    except Exception as e:
        logging.error(e)
        return {'packages': []}


def save(data: dict) -> None:
    path = _registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def upsert(entry: dict) -> dict:
    data = load()
    packages = data['packages']
    entry_id = entry.get('id')
    if not entry_id:
        entry_id = entry.get('repo_slug') or str(uuid.uuid4())
        entry['id'] = entry_id
    replaced = False
    for i, pkg in enumerate(packages):
        if pkg.get('id') == entry_id or (
            entry.get('repo_slug') and pkg.get('repo_slug') == entry.get('repo_slug')
        ):
            packages[i] = {**pkg, **entry}
            replaced = True
            break
    if not replaced:
        packages.append(entry)
    data['packages'] = packages
    save(data)
    return entry


def remove(entry_id: str) -> None:
    data = load()
    data['packages'] = [p for p in data['packages'] if p.get('id') != entry_id]
    save(data)


def get_by_id(entry_id: str) -> Optional[dict]:
    for pkg in load()['packages']:
        if pkg.get('id') == entry_id:
            return pkg
    return None
