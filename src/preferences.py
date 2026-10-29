import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Adw, Gio  # noqa

from .lib.constants import FETCH_UPDATES_ARG
from .models.Settings import Settings
from .lib.utils import portal
from .lib.ini_config import Config
from dbus import Array as DBusArray


class Preferences(Adw.PreferencesWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.settings = Settings.settings
        self.set_title(_('Repo Lever preferences'))

        page = Adw.PreferencesPage()

        updates_group = Adw.PreferencesGroup(title=_('Updates'))
        autofetch_row = self.create_boolean_settings_entry(
            _('Check updates on system startup'),
            'fetch-updates-in-background',
            _('Show a notification when updates are found (with an option to install them)'),
        )
        updates_group.add(autofetch_row)
        autofetch_row.connect('notify::active', self.on_background_fetchupdates_changed)

        debug_group = Adw.PreferencesGroup(title=_('Debugging'))
        debug_group.add(self.create_boolean_settings_entry(
            _('Enable debug logs'),
            'debug-logs',
            _('More verbose logging; restart required'),
        ))

        page.add(updates_group)
        page.add(debug_group)
        self.add(page)

    def create_boolean_settings_entry(self, label: str, key: str, subtitle: str = None) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=label, subtitle=subtitle)
        self.settings.bind(key, row, 'active', Gio.SettingsBindFlags.DEFAULT)
        return row

    def on_background_fetchupdates_changed(self, *args):
        key = 'fetch-updates-in-background'
        value: bool = self.settings.get_boolean(key)
        Config.parser[Config.parser.default_section][key] = Config.return_boolean(value)
        Config.write()

        inter = portal('org.freedesktop.portal.Background')
        inter.RequestBackground('', {
            'reason': 'Repo Lever update checks',
            'autostart': value,
            'background': True,
            'commandline': DBusArray(['gearlever', f'--{FETCH_UPDATES_ARG}']),
        })
