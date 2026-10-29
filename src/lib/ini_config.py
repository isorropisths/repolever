import logging
import os
import configparser
import hashlib

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import GLib  # noqa


class Config:
    path = os.path.join(GLib.get_user_config_dir(), 'repo-lever.conf')
    _legacy_paths = [
        os.path.join(GLib.get_user_config_dir(), 'hub-lever.conf'),
        os.path.join(GLib.get_user_config_dir(), 'gearlever.conf'),
    ]
    parser = configparser.ConfigParser(interpolation=None)

    @staticmethod
    def return_boolean(v):
        return 'yes' if v else 'no'

    @staticmethod
    def exists():
        if os.path.exists(Config.path):
            return True
        return any(os.path.exists(p) for p in Config._legacy_paths)

    @staticmethod
    def refresh():
        if not os.path.exists(Config.path):
            for legacy in Config._legacy_paths:
                if os.path.exists(legacy):
                    logging.info(f'Migrating config from {legacy} to {Config.path}')
                    with open(legacy, 'r', encoding='utf-8') as src, open(
                        Config.path, 'w', encoding='utf-8'
                    ) as dest:
                        dest.write(src.read())
                    break
        if os.path.exists(Config.path):
            Config.parser.read(Config.path)

    @staticmethod
    def write():
        logging.info(f'Writing config to {Config.path}')
        with open(Config.path, 'w', encoding='utf-8') as f:
            Config.parser.write(f)

    @staticmethod
    def get_app_hash(el):
        fp = getattr(el, 'file_path', '') or ''
        if fp.startswith('repo-lever://'):
            reg_id = fp.removeprefix('repo-lever://')
            if reg_id:
                return hashlib.md5(reg_id.encode()).hexdigest()

        Config.refresh()
        for section in Config.parser.sections():
            if not section.startswith('app.') or '.update_manager' in section:
                continue
            sec = Config.parser[section]
            if sec.get('file_path') == fp:
                reg_id = sec.get('registry_id')
                if reg_id:
                    return hashlib.md5(reg_id.encode()).hexdigest()
                return section.removeprefix('app.')

        return hashlib.md5(fp.encode()).hexdigest()

    @staticmethod
    def _section_hash_for_write(el, data: dict) -> str:
        reg_id = data.get('registry_id')
        if reg_id:
            return hashlib.md5(reg_id.encode()).hexdigest()
        return Config.get_app_hash(el)

    @staticmethod
    def get_app_config(el):
        h = Config.get_app_hash(el)
        k = f'app.{h}'

        if Config.parser.has_section(k):
            return dict(Config.parser[f'app.{h}'])
        return {}

    @staticmethod
    def delete_app_config(el):
        h = Config.get_app_hash(el)
        j = f'app.{h}'
        k = f'app.{h}.update_manager'

        for section in [j, k]:
            if Config.parser.has_section(section):
                logging.info(f'Deleting config section {section}')
                Config.parser.remove_section(section)

        Config.write()

    @staticmethod
    def set_app_config(el, data: dict):
        h = Config._section_hash_for_write(el, data)
        logging.info(f'Setting app config for {el.name} (app.{h}): {data}')
        data['name'] = el.name
        data['file_path'] = el.file_path
        Config.parser[f'app.{h}'] = data
        Config.write()

    @staticmethod
    def get_app_update_config(el):
        h = Config.get_app_hash(el)
        k = f'app.{h}.update_manager'

        if not Config.parser.has_section(k):
            Config.parser.add_section(k)

        return Config.parser[f'app.{h}.update_manager']

    @staticmethod
    def set_app_update_config(el, manager, data):
        h = Config.get_app_hash(el)
        logging.info(
            f'Setting update config for {el.name} (app.{h}.update_manager), manager={manager.name}: {data}'
        )
        Config.parser[f'app.{h}.update_manager'] = data
        Config.parser[f'app.{h}.update_manager']['manager'] = manager.name
        Config.write()

    @staticmethod
    def delete_app_update_config(el):
        h = Config.get_app_hash(el)
        k = f'app.{h}.update_manager'
        if Config.parser.has_section(k):
            logging.info(f'Deleting update config section {k}')
            Config.parser.remove_section(k)

        Config.write()
