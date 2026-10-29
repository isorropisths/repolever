import logging
from gi.repository import Gtk, Adw, Gio, GLib

from .models.Settings import Settings
from .lib.constants import APP_RESOURCE_PREFIX


class WelcomeScreen(Adw.Dialog):
    def __init__(self):
        super().__init__()
        self.set_content_height(480)
        self.set_content_width(520)

        toolbar_view = Adw.ToolbarView()
        self.titlebar = Adw.HeaderBar()
        toolbar_view.add_top_bar(self.titlebar)

        carousel = Adw.Carousel(spacing=120)
        dots = Adw.CarouselIndicatorDots()
        dots.set_carousel(carousel)
        self.titlebar.set_title_widget(dots)

        first_page = Gtk.Builder.new_from_resource(f'{APP_RESOURCE_PREFIX}/gtk/tutorial/1.ui')
        last_page = Gtk.Builder.new_from_resource(f'{APP_RESOURCE_PREFIX}/gtk/tutorial/last.ui')
        pages = [first_page.get_object('target'), last_page.get_object('target')]
        for page in pages:
            carousel.append(page)

        last_page.get_object('close-window').connect('clicked', lambda *_: self.close())

        left = Gtk.Button(icon_name='go-previous', css_classes=['circular'])
        left.set_sensitive(False)
        left.connect('clicked', lambda *_: carousel.scroll_to(pages[0], True))

        right = Gtk.Button(icon_name='go-next', css_classes=['circular'])
        right.connect('clicked', lambda *_: carousel.scroll_to(pages[1], True))

        carousel.connect('page-changed', lambda _w, index: (
            left.set_sensitive(index > 0),
            right.set_sensitive(index < len(pages) - 1),
        ))

        overlay = Gtk.Overlay(child=carousel)
        overlay.add_overlay(left)
        overlay.add_overlay(right)
        left.set_halign(Gtk.Align.START)
        left.set_valign(Gtk.Align.CENTER)
        left.set_margin_start(12)
        right.set_halign(Gtk.Align.END)
        right.set_valign(Gtk.Align.CENTER)
        right.set_margin_end(12)

        toolbar_view.set_content(overlay)
        self.set_child(toolbar_view)

        if Settings.settings.get_boolean('first-run'):
            Settings.settings.set_boolean('first-run', False)
