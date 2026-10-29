from gi.repository import Gtk, Pango, GObject, GLib
from typing import Optional

from ..lib.async_utils import _async
from ..lib.utils import gnu_naturalsize

from ..models.AppListElement import InstalledStatus
from ..models.PackageListElement import PackageListElement
from ..providers.providers_list import package_provider


class AppListBoxItem(Gtk.ListBoxRow):
    __gsignals__ = {
        'details-clicked': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (object,)),
        'update-requested': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (object,)),
    }

    def __init__(self, list_element: PackageListElement, show_details_btn=False, **kwargs):
        super().__init__(**kwargs)

        self._app: PackageListElement = list_element
        self._available_version: Optional[str] = None
        self.details_btn: Optional[Gtk.Button] = None

        col = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        col.set_css_classes(['app-listbox-item'])

        self.image_container = Gtk.Box()
        col.append(self.image_container)

        app_details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        self.title_label = Gtk.Label(
            label=list_element.name,
            halign=Gtk.Align.START,
            css_classes=['heading'],
            max_width_chars=70,
            ellipsize=Pango.EllipsizeMode.END,
        )
        app_details_box.append(self.title_label)

        self.subtitle_label = Gtk.Label(
            label=list_element.description or '',
            halign=Gtk.Align.START,
            lines=1,
            max_width_chars=100,
            ellipsize=Pango.EllipsizeMode.END,
            css_classes=['dim-label'],
            visible=bool(list_element.description),
        )
        app_details_box.append(self.subtitle_label)

        self.version_label = Gtk.Label(
            label='',
            margin_top=3,
            halign=Gtk.Align.START,
            css_classes=['caption'],
        )
        app_details_box.append(self.version_label)
        col.append(app_details_box)

        if show_details_btn:
            self.details_btn = Gtk.Button(icon_name='gl-right-symbolic', valign=Gtk.Align.CENTER)
            col.append(self.details_btn)

        self.update_btn = Gtk.Button(
            label=_('Update'),
            valign=Gtk.Align.CENTER,
            css_classes=['suggested-action', 'pill'],
            visible=False,
        )
        self.update_btn.connect('clicked', self._on_update_clicked)
        col.append(self.update_btn)

        self.progress_bar = Gtk.ProgressBar(show_text=True, visible=False, margin_top=4)
        app_details_box.append(self.progress_bar)

        self.set_child(col)

        if self._app.installed_status in [InstalledStatus.UPDATING, InstalledStatus.INSTALLING]:
            self.set_opacity(0.5)

        self.set_version_line(list_element.version, list_element.size)

    def _on_update_clicked(self, *_args):
        self.emit('update-requested', self._app)

    def load_icon(self):
        self._fetch_icon_async()

    @_async
    def _fetch_icon_async(self):
        icon = package_provider.get_icon(self._app)

        def apply():
            self.set_icon(icon)

        GLib.idle_add(apply)

    def set_icon(self, image: Gtk.Image):
        child = self.image_container.get_first_child()
        while child:
            self.image_container.remove(child)
            child = self.image_container.get_first_child()
        self.image_container.append(image)
        image.set_pixel_size(48)

    def set_version_line(
        self,
        installed_version: Optional[str],
        size: int,
        available_version: Optional[str] = None,
    ):
        parts = []
        if installed_version:
            if available_version and available_version != installed_version:
                parts.append(f'{installed_version} → {available_version}')
            else:
                parts.append(installed_version)
        if size:
            parts.append(gnu_naturalsize(size))
        self.version_label.set_label(' · '.join(parts) if parts else '')
        self.version_label.set_visible(bool(parts))

    def show_update_action(self, available_version: Optional[str] = None):
        self._available_version = available_version
        self._app.is_updatable_from_url = True
        self.update_btn.set_visible(True)
        self.set_version_line(self._app.version, self._app.size, available_version)

    def hide_update_action(self):
        self._app.is_updatable_from_url = False
        self.update_btn.set_visible(False)
        self.set_version_line(self._app.version, self._app.size)

    def set_update_progress(self, fraction: float, installing: bool = False):
        self.progress_bar.set_visible(True)
        self.update_btn.set_visible(False)
        fraction = min(max(fraction, 0.0), 1.0)
        self.progress_bar.set_fraction(fraction)
        if installing or fraction >= 0.92:
            self.progress_bar.set_text(_('Installing…'))
        else:
            self.progress_bar.set_text(f'{int(fraction * 100)}%')

    def clear_update_progress(self):
        self.progress_bar.set_visible(False)
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_text('')
