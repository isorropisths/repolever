import dataclasses
from enum import Enum
from typing import List, Optional

from xdg import DesktopEntry

from .AppListElement import InstalledStatus


class PackageUpdateLogic(Enum):
    REPLACE = 'REPLACE'
    KEEP = 'KEEP'


@dataclasses.dataclass
class PackageListElement:
    name: str
    description: str
    provider: str
    installed_status: InstalledStatus
    file_path: str
    trusted: bool = False
    is_updatable_from_url = False
    env_variables: List[str] = dataclasses.field(default_factory=lambda: [])
    exec_arguments: str = ''
    desktop_entry: Optional[DesktopEntry.DesktopEntry] = None
    update_logic: Optional[PackageUpdateLogic] = None
    architecture: Optional[str] = None
    updating_from: Optional['PackageListElement'] = None
    version: Optional[str] = None
    local_file: Optional[bool] = None
    external_folder: bool = False
    desktop_file_path: Optional[str] = None
    size: int = 0

    def set_installed_status(self, installed_status: InstalledStatus):
        self.installed_status = installed_status

    def set_trusted(self):
        self.trusted = True

    def get_config(self):
        from ..lib.ini_config import Config
        return Config.get_app_config(self)
