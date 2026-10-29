from gi.repository import Gtk, Adw, GObject, Gio, GLib
from typing import List

import logging

from time import sleep
from .providers.providers_list import package_provider
from .models.PackageListElement import PackageListElement
from .models.AppListElement import InstalledStatus
from .components.FilterEntry import FilterEntry
from .components.CustomComponents import NoAppsFoundRow
from .components.AppListBoxItem import AppListBoxItem
from .preferences import Preferences
from .WelcomeScreen import WelcomeScreen
from .lib.constants import APP_RESOURCE_PREFIX
from .lib.utils import get_application_window, check_internet, show_message_dialog
from .lib.async_utils import _async, idle
from .models.UpdateManagerChecker import UpdateManagerChecker

fetch_updates_cache = None


class InstalledAppsList(Gtk.ScrolledWindow):
    __gsignals__ = {
        'selected-app': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (object,)),
        'update-all': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (object,)),
    }

    CHECK_FOR_UPDATES_LABEL = _('Check for updates')
    NO_UPDATES_FOUND_LABEL = _('No updates found')
    CHECK_INCONCLUSIVE_LABEL = _('Could not check for updates')
    CHECKING_FOR_UPDATES_LABEL = _('Checking updates...')
    NO_CONNECTION_LABEL = _('No Connection')
    UPDATE_ALL_LABEL = _('Update all')

    def __init__(self):
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp_size = 600

        self.container_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.updates_fetched = False
        self.installed_apps_list_slot = Gtk.Box()
        self.installed_apps_list = Gtk.ListBox(css_classes=['boxed-list'])
        self.installed_apps_list.connect('row-activated', self.on_activated_row)

        self.installed_apps_list_slot.append(self.installed_apps_list)

        self.installed_apps_list_rows: List[AppListBoxItem] = []
        self.no_apps_found_row = NoAppsFoundRow(visible=False)

        self.filter_query: str = ''
        self.filter_entry = FilterEntry(
            _('Filter installed applications'),
            capture=get_application_window(),
            maximum_size=clamp_size,
        )
        self.filter_entry.search_entry.connect('search-changed', self.trigger_filter_list)

        title_row = Gtk.Box(margin_bottom=15, spacing=10)
        title_row.append(
            Gtk.Label(
                label=_('Installed applications'),
                css_classes=['title-2'],
                hexpand=True,
                halign=Gtk.Align.START,
            )
        )

        self.updates_btn = Gtk.Button(label=self.CHECK_FOR_UPDATES_LABEL)
        self.updates_check_spinner = Gtk.Spinner(width_request=120, height_request=36)
        self.updates_check_stack = Gtk.Stack()
        self.updates_check_stack.add_named(self.updates_btn, 'button')
        self.updates_check_stack.add_named(self.updates_check_spinner, 'spinner')
        self.updates_check_stack.set_visible_child_name('button')

        self._updates_check_running = False

        self.update_all_btn = Gtk.Button(
            label=self.UPDATE_ALL_LABEL,
            visible=False,
            css_classes=['suggested-action'],
        )
        self.update_all_progress = Gtk.ProgressBar(show_text=True, width_request=160, visible=False)

        self.updates_btn.connect('clicked', self.on_fetch_updates_btn_clicked)
        self.update_all_btn.connect('clicked', self.on_update_all_btn_clicked)

        self.updates_btn_row = Gtk.Box(spacing=10)
        self.updates_btn_row.append(self.updates_check_stack)
        self.updates_btn_row.append(self.update_all_btn)
        self.updates_btn_row.append(self.update_all_progress)

        self.updates_header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.END)
        self.updates_header_box.append(self.updates_btn_row)

        title_row.append(self.updates_header_box)

        self.main_box.append(title_row)
        self.main_box.append(self.installed_apps_list_slot)

        clamp = Adw.Clamp(child=self.main_box, maximum_size=clamp_size, margin_top=20, margin_bottom=20)

        self.clamp_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.clamp_container.append(self.filter_entry)
        self.clamp_container.append(clamp)

        builder = Gtk.Builder.new_from_resource(f'{APP_RESOURCE_PREFIX}/gtk/empty-list-placeholder.ui')
        self.placeholder = builder.get_object('target')
        builder.get_object('open-preferences').connect('clicked', self.open_preferences)
        builder.get_object('show-welcome-screen').connect('clicked', self.on_open_welcome_screen)

        self.container_stack.add_child(self.clamp_container)
        self.container_stack.add_child(self.placeholder)

        self.set_child(self.container_stack)

    def _set_updates_check_busy(self, busy: bool):
        self._updates_check_running = busy
        self.updates_btn.set_sensitive(not busy)
        if busy:
            self.updates_check_stack.set_visible_child_name('spinner')
            self.updates_check_spinner.start()
        else:
            self.updates_check_spinner.stop()
            self.updates_check_stack.set_visible_child_name('button')

    def _set_updates_btn_label(self, label: str, with_updates_icon: bool = False):
        if with_updates_icon:
            self.updates_btn.set_child(
                Adw.ButtonContent(label=label, icon_name='gl-arrow-circular-top-right')
            )
        else:
            self.updates_btn.set_child(None)
            self.updates_btn.set_label(label)

    def _set_update_all_progress(self, visible: bool, fraction: float = 0.0, text: str = ''):
        self.update_all_progress.set_visible(visible)
        if visible:
            self.update_all_btn.set_visible(False)
            self.update_all_progress.set_fraction(min(max(fraction, 0.0), 1.0))
            self.update_all_progress.set_text(text or f'{int(fraction * 100)}%')

    def _restore_update_all_visibility(self, updates_available: int):
        self._set_update_all_progress(False)
        self.update_all_btn.set_visible(updates_available > 0)
        self.update_all_btn.set_sensitive(True)

    def _row_for_app(self, app: PackageListElement) -> AppListBoxItem | None:
        reg_id = app.get_config().get('registry_id')
        for row in self.installed_apps_list_rows:
            if row._app.get_config().get('registry_id') == reg_id:
                return row
        return None

    def _make_update_status_cb(self, row: AppListBoxItem, index: int, total: int):
        def status_cb(progress: float):
            overall = (index + progress) / total

            def update_ui():
                self._set_update_all_progress(
                    True,
                    overall,
                    _('Updating {current} of {total}').format(current=index + 1, total=total),
                )
                row.set_update_progress(progress, installing=progress >= 0.92)
                return False

            GLib.idle_add(update_ui)

        return status_cb

    def _make_row_update_status_cb(self, row: AppListBoxItem):
        def status_cb(progress: float):
            GLib.idle_add(
                lambda p=progress: row.set_update_progress(p, installing=p >= 0.92)
            )

        return status_cb

    def run_all_updates(self):
        self.on_update_all_btn_clicked()

    def on_activated_row(self, listbox, row: Gtk.ListBoxRow):
        self.filter_entry.set_search_mode(False)
        self.emit('selected-app', row._app)

    def trigger_search_mode(self):
        self.filter_entry.set_search_mode(not self.filter_entry.get_search_mode())

    def refresh_list(self):
        global fetch_updates_cache

        preserve = self.updates_fetched and fetch_updates_cache is not None
        cached = dict(fetch_updates_cache) if preserve else None

        self.installed_apps_list.remove_all()
        self.installed_apps_list_rows = []

        installed: List[PackageListElement] = package_provider.list_installed()

        for i in installed:
            list_row = AppListBoxItem(i, activatable=True, selectable=False, hexpand=True)
            if i.installed_status is InstalledStatus.REGISTRY_ONLY:
                list_row.set_version_line(i.version, i.size)
                list_row.subtitle_label.set_label(_('Not installed on your system'))
                list_row.subtitle_label.set_visible(True)
            else:
                list_row.set_version_line(i.version, i.size)
            list_row.connect('update-requested', self.on_row_update_requested)
            list_row.load_icon()
            self.installed_apps_list_rows.append(list_row)
            self.installed_apps_list.append(list_row)

        if installed:
            self.container_stack.set_visible_child(self.clamp_container)
        else:
            self.container_stack.set_visible_child(self.placeholder)
            self.updates_fetched = False
            fetch_updates_cache = None

        self.installed_apps_list.append(self.no_apps_found_row)
        self.no_apps_found_row.set_visible(False)

        self.installed_apps_list.set_sort_func(lambda r1, r2: self.sort_installed_apps_list(r1, r2))
        self.installed_apps_list.invalidate_sort()

        if preserve and cached:
            self.complete_updates_fetch(
                cached['registry_ids'],
                cached.get('available_versions', {}),
                cached['updates_available'],
                cached.get('checks_inconclusive', 0),
            )
        else:
            self.updates_fetched = False
            self._set_updates_btn_label(self.CHECK_FOR_UPDATES_LABEL)
            self.update_all_btn.set_visible(False)
            self.updates_btn.set_sensitive(True)

    @_async
    def fetch_updates(self, cache=False):
        global fetch_updates_cache

        if not check_internet():
            def _show_no_connection():
                self._set_updates_btn_label(self.NO_CONNECTION_LABEL)
                self.updates_btn.add_css_class('destructive-action')
                self.updates_btn.set_sensitive(True)

            def _reset_btn():
                self._set_updates_btn_label(self.CHECK_FOR_UPDATES_LABEL)
                self.updates_btn.remove_css_class('destructive-action')
                return GLib.SOURCE_REMOVE

            GLib.idle_add(_show_no_connection)
            GLib.timeout_add_seconds(3, _reset_btn)
            return

        if cache and fetch_updates_cache:
            self.complete_updates_fetch(
                fetch_updates_cache['registry_ids'],
                fetch_updates_cache.get('available_versions', {}),
                fetch_updates_cache['updates_available'],
                fetch_updates_cache.get('checks_inconclusive', 0),
            )
            return

        GLib.idle_add(lambda: self._set_updates_check_busy(True))

        registry_ids = []
        available_versions = {}
        updates_available = 0
        checks_inconclusive = 0

        rows = self.installed_apps_list_rows

        for row in rows:
            if row._app.installed_status is not InstalledStatus.INSTALLED:
                continue
            manager = UpdateManagerChecker.check_url_for_app(row._app)
            if not manager:
                continue
            try:
                available = manager.is_update_available()
                if available is True:
                    updates_available += 1
                    reg_id = row._app.get_config().get('registry_id')
                    if reg_id:
                        registry_ids.append(reg_id)
                    avail = manager.get_available_version()
                    if reg_id and avail:
                        available_versions[reg_id] = avail
                elif available is None:
                    checks_inconclusive += 1
                    logging.warning(
                        'Update check inconclusive for %s (GitHub API or asset match)',
                        row._app.name,
                    )
            except Exception as e:
                logging.error(e)

        self.updates_fetched = True
        fetch_updates_cache = {
            'registry_ids': registry_ids,
            'available_versions': available_versions,
            'updates_available': updates_available,
            'checks_inconclusive': checks_inconclusive,
        }

        sleep(0.5)
        self.complete_updates_fetch(
            registry_ids,
            available_versions,
            updates_available,
            checks_inconclusive,
        )

    @idle
    def complete_updates_fetch(
        self,
        registry_ids: list,
        available_versions: dict,
        updates_available: int,
        checks_inconclusive: int = 0,
    ):
        for row in self.installed_apps_list_rows:
            reg_id = row._app.get_config().get('registry_id')
            if reg_id in registry_ids:
                row.show_update_action(available_versions.get(reg_id))
            else:
                row.hide_update_action()

        if updates_available == 0 and checks_inconclusive > 0:
            self._set_updates_btn_label(self.CHECK_INCONCLUSIVE_LABEL)
        elif updates_available == 0:
            self._set_updates_btn_label(self.NO_UPDATES_FOUND_LABEL)
        else:
            self._set_updates_btn_label(
                _('{n} updates found').format(n=updates_available),
                True,
            )

        self._set_updates_check_busy(False)
        self._restore_update_all_visibility(updates_available)

    def on_fetch_updates_btn_clicked(self, *args):
        if self._updates_check_running:
            return
        self.fetch_updates(cache=False)

    def on_row_update_requested(self, row: AppListBoxItem, app: PackageListElement):
        manager = UpdateManagerChecker.check_url_for_app(app)
        if not manager:
            return
        self._run_single_update(app, manager)

    def _remove_app_from_updates_cache(self, reg_id: str | None) -> None:
        global fetch_updates_cache
        if not reg_id or not fetch_updates_cache:
            return
        ids = fetch_updates_cache.get('registry_ids', [])
        if reg_id not in ids:
            return
        ids.remove(reg_id)
        fetch_updates_cache['updates_available'] = max(
            0, fetch_updates_cache.get('updates_available', 0) - 1
        )
        fetch_updates_cache.get('available_versions', {}).pop(reg_id, None)

    @_async
    def _run_single_update(self, app: PackageListElement, manager):
        row = self._row_for_app(app)
        if row:
            GLib.idle_add(lambda: row.set_update_progress(0.0))

        try:
            status_cb = self._make_row_update_status_cb(row) if row else lambda _p: None

            result = package_provider.update_from_url(manager, app, status_cb)
            if result:
                reg_id = app.get_config().get('registry_id')
                self._remove_app_from_updates_cache(reg_id)
                if row:
                    GLib.idle_add(row.hide_update_action)
                GLib.idle_add(self.refresh_list)
            elif row:
                GLib.idle_add(row.clear_update_progress)
        except Exception as e:
            logging.error(e)
            if row:
                GLib.idle_add(row.clear_update_progress)
            GLib.idle_add(lambda: show_message_dialog(str(e)))
        finally:
            if row:
                GLib.idle_add(row.clear_update_progress)

    @_async
    def _run_all_updates_async(self):
        targets: list[tuple[AppListBoxItem, PackageListElement, object]] = []
        for row in self.installed_apps_list_rows:
            if not row.update_btn.get_visible():
                continue
            if row._app.installed_status is not InstalledStatus.INSTALLED:
                continue
            manager = UpdateManagerChecker.check_url_for_app(row._app)
            if manager:
                targets.append((row, row._app, manager))

        if not targets:
            return

        total = len(targets)
        GLib.idle_add(lambda: self.update_all_btn.set_visible(True))
        GLib.idle_add(lambda: self.update_all_btn.set_sensitive(False))
        GLib.idle_add(lambda: self.updates_btn.set_sensitive(False))
        GLib.idle_add(
            lambda: self._set_update_all_progress(
                True,
                0.0,
                _('Updating {current} of {total}').format(current=1, total=total),
            )
        )

        batch_items = [
            (manager, app, self._make_update_status_cb(row, index, total))
            for index, (row, app, manager) in enumerate(targets)
        ]

        try:
            package_provider.batch_update_from_url(batch_items)
            for row, app, _manager in targets:
                reg_id = app.get_config().get('registry_id')
                self._remove_app_from_updates_cache(reg_id)
                GLib.idle_add(row.hide_update_action)
                GLib.idle_add(row.clear_update_progress)
        except Exception as e:
            logging.error(e)
            for row, _app, _manager in targets:
                GLib.idle_add(row.clear_update_progress)
            GLib.idle_add(lambda err=str(e): show_message_dialog(err))

        GLib.idle_add(lambda: self._set_update_all_progress(False))
        GLib.idle_add(lambda: self.updates_btn.set_sensitive(True))
        GLib.idle_add(self.refresh_list)

    def trigger_filter_list(self, widget):
        if not self.installed_apps_list:
            return

        self.filter_query = widget.get_text()

        for row in self.installed_apps_list_rows:
            if not len(self.filter_query):
                row.set_visible(True)
                continue
            visible = self.filter_query.lower().replace(' ', '') in row._app.name.lower()
            row.set_visible(visible)

        self.no_apps_found_row.set_visible(True)
        for row in self.installed_apps_list_rows:
            if row.get_visible():
                self.no_apps_found_row.set_visible(False)
                break

    def sort_installed_apps_list(self, row: AppListBoxItem, row1: AppListBoxItem):
        if not hasattr(row1, '_app'):
            return 1
        if not hasattr(row, '_app') or row._app.name.lower() < row1._app.name.lower():
            return -1
        return 1

    def open_preferences(self, widget):
        Preferences().present()

    def on_update_all_btn_clicked(self, *args):
        self._run_all_updates_async()

    def on_open_welcome_screen(self, widget):
        WelcomeScreen().present(self)
