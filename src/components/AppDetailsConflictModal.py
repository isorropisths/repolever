from typing import Optional, Callable
from gi.repository import Gtk, GObject, Adw
from ..lib.utils import get_application_window
from ..models.PackageListElement import PackageListElement, PackageUpdateLogic

class AppDetailsConflictModal():
    def __init__(self, app_name):
        self.modal = Adw.MessageDialog.new(
            get_application_window(), 
            _('Conflict with "{app_name}"').format(app_name=app_name), 
            _('There is already an app with the same name, how do you want to proceed?')
        )

        self.modal.add_response('cancel', _('Cancel'))
        self.modal.set_response_appearance('cancel', Adw.ResponseAppearance.DESTRUCTIVE)

        self.modal.add_response(PackageUpdateLogic.KEEP.value, _('Keep both'))

        self.modal.add_response(PackageUpdateLogic.REPLACE.value, _('Replace'))
        self.modal.set_response_appearance(PackageUpdateLogic.REPLACE.value, Adw.ResponseAppearance.SUGGESTED)