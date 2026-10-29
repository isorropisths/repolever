import logging
import re
from typing import Optional

from ..lib.ini_config import Config
from ..models.PackageListElement import PackageListElement

from .UpdateManager import UpdateManager
from .GithubPackageUpdater import GithubPackageUpdater


class UpdateManagerChecker():
    @staticmethod
    def get_models() -> list[type[UpdateManager]]:
        return [GithubPackageUpdater]

    @staticmethod
    def get_model_by_name(manager_label: str) -> type[UpdateManager]:
        item = list(filter(lambda m: m.name == manager_label, UpdateManagerChecker.get_models()))

        if not item:
            raise Exception('Invalid model name: ' + manager_label)

        return item[0]

    @staticmethod
    def check_url_for_app(el: PackageListElement) -> Optional[UpdateManager]:
        app_conf = Config.get_app_update_config(el)
        update_url_manager: str | None = app_conf.get('manager', None)

        if not update_url_manager:
            return None

        model = UpdateManagerChecker.get_model_by_name(update_url_manager)
        return model(el=el)

    @staticmethod
    def check_app_embedded_url(el: PackageListElement) -> Optional[str]:
        return None
