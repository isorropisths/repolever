import logging
from gi.repository import Gtk, GObject, Adw, Gdk, Gio, GLib

from .models.AppListElement import InstalledStatus
from .models.PackageListElement import PackageListElement, PackageUpdateLogic
from .providers.providers_list import package_provider
from .lib.async_utils import _async, idle, debounce
from .lib.utils import get_application_window
from .models.UpdateManagerChecker import UpdateManagerChecker
from .components.AppListBoxItem import AppListBoxItem

class MultiUpdate(Gtk.ScrolledWindow):
    __gsignals__ = {
        "go-back": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (bool, )),
    }

    def __init__(self) -> None:
        super().__init__()
        self.ACTION_ROW_ICON_SIZE = 45

        self.app_list: list[PackageListElement] = []
        self.app_list_box_items: list[AppListBoxItem] = []
        self.app_list_box = Gtk.ListBox(css_classes=['boxed-list'])
        self.current_update_manager = None
        self.green_light = True

        self.cancel_update_btn = Gtk.Button(
            label=_('Cancel update'), halign=Gtk.Align.CENTER)
        
        view_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, 
            spacing=10
        )

        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, 
            margin_top=30, 
            margin_bottom=10, 
            margin_start=20, 
            margin_end=20,
            spacing=20
        )

        main_box.append(self.cancel_update_btn)
        main_box.append(self.app_list_box)

        clamp = Adw.Clamp(child=main_box)

        self.progress_bar = Gtk.ProgressBar(
            css_classes=['osd', 'horizontal', 'top'], show_text=True,
        )

        view_box.append(self.progress_bar)
        view_box.append(clamp)

        self.cancel_update_btn.connect('clicked', self.on_cancel_update_btn)
        self.set_child(view_box)

    @idle
    def update_progress_fraction(self, p, text: str = ''):
        self.progress_bar.set_fraction(p)
        if text:
            self.progress_bar.set_text(text)
        else:
            self.progress_bar.set_text(f'{int(p * 100)}%')

    def count_not_installed(self):
        not_installed_count = 0
        for el in self.app_list:
            if el.installed_status is InstalledStatus.NOT_INSTALLED:
                not_installed_count += 1

        return not_installed_count

    @idle
    def create_app_row(self, el: PackageListElement):
        package_provider.refresh_data(el)
        icon = package_provider.get_icon(el)

        row = AppListBoxItem(el, show_details_btn=False)
        row.set_icon(icon)
        row.set_opacity(0.5)

        self.app_list_box.append(row)
        self.app_list_box_items.append(row)

    @idle
    def on_update_end(self):
        self.emit('go-back', None)

    @idle
    def mark_as_updated(self, el: PackageListElement, row: AppListBoxItem):
        row.clear_update_progress()
        row.set_opacity(1)

    def _make_status_cb(self, index: int, total: int, row: AppListBoxItem):
        def status_cb(progress: float):
            overall = (index + progress) / total

            def update_ui():
                self.update_progress_fraction(
                    overall,
                    _('Updating {current} of {total}').format(current=index + 1, total=total),
                )
                row.set_update_progress(progress, installing=progress >= 0.92)
                return False

            GLib.idle_add(update_ui)

        return status_cb

    @_async
    def update_all(self):
        total = len(self.app_list)
        if not total:
            self.on_update_end()
            return

        if len(self.app_list) > 1:
            items = []
            for i, el in enumerate(self.app_list):
                if not self.green_light:
                    break
                row = self.app_list_box_items[i]
                manager = UpdateManagerChecker.check_url_for_app(el)
                items.append((manager, el, self._make_status_cb(i, total, row)))
            try:
                package_provider.batch_update_from_url(items)
                for i, el in enumerate(self.app_list):
                    self.mark_as_updated(el, self.app_list_box_items[i])
            except Exception as e:
                logging.error(e)
        else:
            for i, el in enumerate(self.app_list):
                if not self.green_light:
                    break
                row = self.app_list_box_items[i]
                manager = UpdateManagerChecker.check_url_for_app(el)
                self.current_update_manager = manager
                package_provider.update_from_url(
                    manager,
                    el,
                    self._make_status_cb(i, total, row),
                )
                self.mark_as_updated(el, row)

        self.update_progress_fraction(1, _('Done'))
        self.on_update_end()

    def start(self):
        self.green_light = True
        self.progress_bar.set_fraction(0)
        self.progress_bar.set_text('')
        self.app_list_box.remove_all()
        self.app_list = []
        self.app_list_box_items = []
        self.check_updatables()

    @_async
    def check_updatables(self):
        installed = package_provider.list_installed()

        for el in installed:
            manager = UpdateManagerChecker.check_url_for_app(el)
            if not manager:
                continue

            try:
                if manager.is_update_available() and \
                    (not package_provider.is_app_running(el)):
                    self.app_list.append(el)
                    self.create_app_row(el)
            except Exception as e:
                pass

        self.update_all()

    def on_cancel_update_btn(self, button):
        self.green_light = False
        if self.current_update_manager:
            self.current_update_manager.cancel_download()
