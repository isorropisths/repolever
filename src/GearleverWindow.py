import logging

from .InstalledAppsList import InstalledAppsList
from .AppDetails import AppDetails
from .MultiUpdate import MultiUpdate
from .models.AppListElement import AppListElement
from .models.Settings import Settings
from .lib.constants import APP_RESOURCE_PREFIX
from .lib import utils

from gi.repository import Gtk, Adw, Gio, GLib


class GearleverWindow(Adw.Window):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.settings = Settings.settings

        self.container_stack = Adw.Leaflet(
            can_unfold=False, can_navigate_back=True, can_navigate_forward=False
        )
        self.app_lists_stack = Adw.ViewStack()

        self.titlebar = Adw.HeaderBar()
        self.view_title_widget = Adw.ViewSwitcherTitle(stack=self.app_lists_stack)

        self.add_button_child = Adw.ButtonContent(
            icon_name='gl-plus-symbolic',
            tooltip_text=_('Install from GitHub'),
            label=_('Install'),
        )
        self.add_button = Gtk.Button(child=self.add_button_child)
        self.add_button.connect('clicked', lambda *_: self.on_install_from_link())

        self.back_button = Gtk.Button(icon_name='gl-left-symbolic')
        self.back_button.connect('clicked', self.on_left_button_clicked)
        self.left_button = self.add_button

        self.search_btn = Gtk.Button(child=Adw.ButtonContent(icon_name='gl-loupe-large'))
        self.search_btn.connect('clicked', self.on_trigger_search_mode)

        menu_obj = Gtk.Builder.new_from_resource(f'{APP_RESOURCE_PREFIX}/gtk/main-menu.ui')
        self.menu_button = Gtk.MenuButton(
            icon_name='open-menu', menu_model=menu_obj.get_object('primary_menu')
        )

        self.titlebar.pack_start(self.left_button)
        self.titlebar.pack_end(self.menu_button)
        self.titlebar.pack_end(self.search_btn)
        self.titlebar.set_title_widget(self.view_title_widget)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(self.titlebar)

        self.set_title(_('Repo Lever'))
        self.set_default_size(700, 700)

        self.toast_overlay = Adw.ToastOverlay()

        self.installed_stack = Gtk.Stack()
        self.app_details = AppDetails()
        self.app_details.connect('uninstalled-app', self.on_uninstalled_app)
        self.app_details.connect('update-started', self.on_app_update_started)
        self.app_details.connect('update-ended', self.on_app_update_ended)

        self.installed_apps_list = InstalledAppsList()
        self.installed_apps_list.refresh_list()

        self.multi_update = MultiUpdate()
        self.multi_update.connect('go-back', self.on_left_button_clicked)

        self.installed_stack.add_child(self.installed_apps_list)
        self.installed_stack.set_visible_child(self.installed_apps_list)

        utils.add_page_to_adw_stack(
            self.app_lists_stack,
            self.installed_stack,
            'installed',
            _('Installed'),
            'gl-application-x-executable-symbolic',
        )

        self.container_stack.append(self.app_lists_stack)
        self.container_stack.append(self.app_details)
        self.container_stack.append(self.multi_update)

        self.installed_apps_list.connect('selected-app', self.on_selected_installed_app)
        self.container_stack.connect('notify::visible-child', self.on_container_stack_change)

        self.connect('close-request', self.on_close_request)
        self.connect('notify::maximized', self.on_window_maximixed_changed)

        self.toast_overlay.set_child(self.container_stack)
        toolbar_view.set_content(self.toast_overlay)
        self.set_content(toolbar_view)

        if self.settings.get_boolean('is-maximized'):
            self.maximize()

    def on_selected_installed_app(self, source: Gtk.Widget, list_element: AppListElement):
        self.app_details.set_app_list_element(list_element)
        self.container_stack.set_transition_type(Adw.LeafletTransitionType.OVER)
        self.container_stack.set_visible_child(self.app_details)

    def on_show_installed_list(self, source: Gtk.Widget = None, data=None):
        self.container_stack.set_transition_type(Adw.LeafletTransitionType.OVER)
        self.container_stack.set_visible_child(self.app_lists_stack)

    def on_left_button_clicked(self, *args):
        container_visible = self.container_stack.get_visible_child()
        if container_visible in (self.app_details, self.multi_update):
            self.titlebar.set_title_widget(self.view_title_widget)
            self.on_show_installed_list()

    def on_container_stack_change(self, widget, data):
        in_app_details = self.container_stack.get_visible_child() is self.app_details
        in_multi_update = self.container_stack.get_visible_child() is self.multi_update
        in_apps_list = self.container_stack.get_visible_child() is self.app_lists_stack

        if in_app_details:
            self._set_header_left_button(self.back_button)
            self.search_btn.set_visible(False)
        elif in_multi_update:
            self.left_button.set_visible(False)
        else:
            self.search_btn.set_visible(True)
            self._set_header_left_button(self.add_button)

        self.view_title_widget.set_visible(not in_app_details)

    def on_uninstalled_app(self, widget, data):
        self.installed_apps_list.refresh_list()
        self.on_show_installed_list(widget, data)

    def on_app_update_started(self, *args):
        self.left_button.set_visible(False)
        self.left_button.set_sensitive(False)

    def on_app_update_ended(self, *args):
        self.left_button.set_visible(True)
        self.left_button.set_sensitive(True)

    def on_install_from_link(self):
        from .InstallFromLinkDialog import InstallFromLinkDialog

        def on_done():
            self.installed_apps_list.refresh_list()
            self.toast_overlay.add_toast(Adw.Toast(title=_('Package installed')))

        InstallFromLinkDialog(parent=self, on_success=on_done).present()

    def on_close_request(self, widget):
        return False

    def on_window_maximixed_changed(self, *args):
        self.settings.set_boolean('is-maximized', self.is_maximized())

    def on_trigger_search_mode(self, *args):
        self.installed_apps_list.trigger_search_mode()

    def _set_header_left_button(self, widget: Gtk.Widget):
        if self.left_button is widget:
            widget.set_visible(True)
            return
        self.left_button.set_visible(False)
        self.titlebar.remove(self.left_button)
        self.left_button = widget
        self.titlebar.pack_start(self.left_button)
        self.left_button.set_visible(True)
