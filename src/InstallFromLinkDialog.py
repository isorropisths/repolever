import logging

from gi.repository import Adw, Gtk, GLib

from .lib.async_utils import _async
from .providers.providers_list import package_provider


class InstallFromLinkDialog(Adw.Window):
    def __init__(self, parent: Gtk.Window, on_success=None, **kwargs):
        super().__init__(transient_for=parent, modal=True, **kwargs)
        self.on_success = on_success
        self.set_title(_('Install from GitHub'))
        self.set_default_size(480, 320)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=24,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )
        self.set_content(box)

        self.entry = Gtk.Entry(placeholder_text=_('https://github.com/owner/repository'))
        self.entry.set_input_purpose(Gtk.InputPurpose.URL)
        self.entry.connect('activate', lambda *_: self.on_install_clicked())

        label = Gtk.Label(
            label=_(
                'Paste a GitHub repository URL, a release page, or a direct link to a .rpm/.deb file. '
                'When no release is specified, the latest release is used. '
                'Repo Lever picks the package that matches your CPU architecture and, on Fedora, your release version.'
            ),
            wrap=True,
            xalign=0,
            css_classes=['dim-label'],
        )

        self.progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False)
        self.status_label = Gtk.Label(xalign=0, css_classes=['dim-label'])
        self.progress_bar = Gtk.ProgressBar(show_text=True)
        self.progress_box.append(self.status_label)
        self.progress_box.append(self.progress_bar)

        self.error_label = Gtk.Label(visible=False, css_classes=['error'], wrap=True, xalign=0)

        install_btn = Gtk.Button(label=_('Install'), css_classes=['suggested-action'])
        install_btn.connect('clicked', lambda *_: self.on_install_clicked())
        cancel_btn = Gtk.Button(label=_('Cancel'))
        cancel_btn.connect('clicked', lambda *_: self.close())

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.END)
        actions.append(cancel_btn)
        actions.append(install_btn)

        box.append(label)
        box.append(self.entry)
        box.append(self.error_label)
        box.append(self.progress_box)
        box.append(actions)

    def _set_busy(self, busy: bool):
        self.progress_box.set_visible(busy)
        if busy:
            self._set_progress(0.0, _('Preparing…'))
        else:
            self.progress_bar.set_fraction(0.0)
            self.progress_bar.set_text('')

    def _set_progress(self, fraction: float, status: str):
        self.status_label.set_label(status)
        fraction = min(max(fraction, 0.0), 1.0)
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(f'{int(fraction * 100)}%')

    def _idle_progress(self, fraction: float, status: str):
        GLib.idle_add(self._set_progress, fraction, status)

    def on_install_clicked(self):
        url = self.entry.get_text().strip()
        if not url:
            self.error_label.set_label(_('Enter a GitHub URL'))
            self.error_label.set_visible(True)
            return
        self.error_label.set_visible(False)
        self._set_busy(True)
        self._prepare_install_async(url)

    @_async
    def _prepare_install_async(self, url: str):
        try:
            preview = package_provider.preview_install_from_github_url(url)
            if not preview:
                GLib.idle_add(
                    self._finish,
                    False,
                    _(
                        'Could not resolve that GitHub repository or release. '
                        'Use a repository URL, a release page, or a direct link to a .rpm/.deb file '
                        '(for example …/releases/download/v1.0/package.fc44.x86_64.rpm). '
                        'If you recently checked for updates, wait a minute and try again—GitHub may be rate-limiting the app.'
                    ),
                )
                return
            if preview.get('is_outdated'):
                GLib.idle_add(self._prompt_outdated_release, url, preview)
            else:
                self._run_install(url, use_latest=True)
        except Exception as e:
            logging.exception(e)
            GLib.idle_add(self._finish, False, str(e))

    def _prompt_outdated_release(self, url: str, preview: dict):
        self._set_busy(False)
        requested = preview.get('requested_tag') or '?'
        latest = preview.get('latest_tag') or '?'

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=_('Older release selected'),
            body=_(
                'This link points at release {requested}. The newest release on GitHub is {latest}.\n\n'
                'Install the older release anyway, or install the latest instead?'
            ).format(requested=requested, latest=latest),
        )
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('older', _('Install this release'))
        dialog.add_response('latest', _('Install latest release'))
        dialog.set_response_appearance('latest', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response('cancel')

        def on_response(d, response: str):
            if response == 'cancel':
                return
            self._set_busy(True)
            if response == 'latest':
                self._run_install(url, use_latest=True)
            else:
                self._run_install(url, use_latest=False, explicit_tag=preview.get('requested_tag'))

        dialog.connect('response', on_response)
        dialog.present()

    @_async
    def _run_install(self, url: str, use_latest: bool = True, explicit_tag: str | None = None):
        try:

            def progress_cb(fraction: float):
                if fraction >= 1.0:
                    self._idle_progress(1.0, _('Finishing…'))
                elif fraction > 0.5:
                    self._idle_progress(
                        fraction,
                        _('Installing… (your password may be required)'),
                    )
                else:
                    self._idle_progress(fraction, _('Downloading…'))

            package_provider.install_from_github_url(
                url,
                use_latest=use_latest,
                explicit_tag=explicit_tag,
                progress_cb=progress_cb,
            )
            GLib.idle_add(self._finish, True, None)
        except Exception as e:
            logging.exception(e)
            GLib.idle_add(self._finish, False, str(e))

    def _finish(self, ok: bool, err: str | None):
        self._set_busy(False)
        if ok:
            if self.on_success:
                self.on_success()
            self.close()
        else:
            self.error_label.set_label(err or _('Installation failed'))
            self.error_label.set_visible(True)
