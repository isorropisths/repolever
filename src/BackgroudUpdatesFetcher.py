import logging
from typing import List, Tuple

from .lib.terminal import host_sh
from .lib.utils import check_internet
from .lib.constants import (
    APP_ID,
    ONE_UPDATE_AVAILABLE_LABEL,
    UPDATES_AVAILABLE_LABEL,
)
from .models.UpdateManager import UpdateManager
from .models.PackageListElement import PackageListElement
from .providers.providers_list import package_provider
from .models.UpdateManagerChecker import UpdateManagerChecker

NOTIFY_ACTION_INSTALL = 'repo-lever-install-updates'
DESKTOP_ENTRY_ID = 'com.tsolakidis.repolever.desktop'


class BackgroudUpdatesFetcher():
    @staticmethod
    def _collect_pending() -> List[Tuple[PackageListElement, UpdateManager]]:
        pending: List[Tuple[PackageListElement, UpdateManager]] = []

        for el in package_provider.list_installed():
            manager = UpdateManagerChecker.check_url_for_app(el)
            if not manager:
                continue

            logging.debug('Checking app with: %s', manager.name)

            try:
                if manager.is_update_available():
                    pending.append((el, manager))
            except Exception as e:
                logging.error(e)

        return pending

    @staticmethod
    def _format_app_names(pending: List[Tuple[PackageListElement, UpdateManager]]) -> str:
        names = [el.name for el, _ in pending]
        if len(names) == 1:
            return names[0]
        if len(names) == 2:
            return _('{first} and {second}').format(first=names[0], second=names[1])
        if len(names) <= 5:
            return ', '.join(names[:-1]) + _(', and ') + names[-1]
        shown = ', '.join(names[:4])
        return _('{list}, and {n} more').format(list=shown, n=len(names) - 4)

    @staticmethod
    def install_pending(pending: List[Tuple[PackageListElement, UpdateManager]]) -> tuple[int, int]:
        ok = 0
        failed = 0
        for el, manager in pending:
            try:
                result = package_provider.update_from_url(manager, el, lambda _progress: None)
                if result:
                    ok += 1
                else:
                    failed += 1
            except Exception as e:
                logging.error(e)
                failed += 1
        return ok, failed

    @staticmethod
    def fetch():
        logging.warn('Fetching updates in the background')

        if not check_internet():
            return

        pending = BackgroudUpdatesFetcher._collect_pending()

        if not pending:
            logging.warn('No available updates found')
            return

        count = len(pending)
        if count == 1:
            title = ONE_UPDATE_AVAILABLE_LABEL
        else:
            title = UPDATES_AVAILABLE_LABEL.replace('{n}', str(count))

        names_text = BackgroudUpdatesFetcher._format_app_names(pending)
        body = _('Updates are ready for: {names}. Open Repo Lever to review, or use Install Updates (your password may be required).').format(
            names=names_text
        )

        install_label = _('Install Updates')
        expire_time = str(1000 * 60 * 30)

        try:
            result = host_sh([
                'notify-send',
                '--app-name=Repo Lever',
                f'--icon={APP_ID}',
                f'--hint=string:desktop-entry:{DESKTOP_ENTRY_ID}',
                title,
                body,
                f'--action={install_label}={NOTIFY_ACTION_INSTALL}',
                f'--expire-time={expire_time}',
                '--wait',
            ])
        except Exception as e:
            logging.error('notify-send failed: %s', e)
            return

        result = (result or '').strip()
        if result == NOTIFY_ACTION_INSTALL:
            ok, failed = BackgroudUpdatesFetcher.install_pending(pending)
            if failed == 0:
                host_sh([
                    'notify-send',
                    '--app-name=Repo Lever',
                    _('Updates installed'),
                    _('Installed updates for: {names}').format(names=names_text),
                ])
            else:
                host_sh([
                    'notify-send',
                    '--app-name=Repo Lever',
                    _('Some updates failed'),
                    _('Installed {ok}; failed {failed}. Open Repo Lever for details.').format(ok=ok, failed=failed),
                ])
