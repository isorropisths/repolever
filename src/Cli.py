import sys
import os
import logging
import json
import traceback
from contextlib import redirect_stdout
from .lib.constants import APP_ID
from gi.repository import Gio # noqa
from .BackgroudUpdatesFetcher import BackgroudUpdatesFetcher
from .lib.constants import FETCH_UPDATES_ARG
from .lib.utils import make_option, check_internet
from .providers.providers_list import package_provider
from .models.PackageListElement import PackageUpdateLogic, PackageListElement
from .lib.ini_config import Config
from .models.UpdateManagerChecker import UpdateManagerChecker
from .models.UpdateManager import UpdateManager

class Cli():
    options = [
        make_option('integrate', description='Install a package from a GitHub repository URL (see --help)'),
        make_option('update', description='Update a tracked package'),
        make_option('remove', description='Uninstall a tracked package'),
        make_option('remove-all', description='Remove all tracked packages from Repo Lever'),
        make_option('list-installed', description='List tracked packages'),
        make_option('list-updates', description='List available updates'),
        make_option('list-update-managers', description='List update backends'),
        make_option('set-update-url', description='(DEPRECATED) Set a custom update url'),
        make_option('set-update-source', description='Set update source for a tracked package'),
        make_option(FETCH_UPDATES_ARG, description='Check for updates in the background (startup hook)'),
    ]

    @staticmethod
    def ask(message: str, options: list) -> str:
        _input = None

        message = message.strip() + ' '

        while not _input in options:
            _input = input(message)

        return _input

    @staticmethod
    def from_options(argv):
        if len(argv) < 2:
            return -1

        opt = Cli._get_invoked_option(argv)

        if opt:
            name = str(opt.long_name).replace('-', '_')
            getattr(Cli, name)(argv)
            sys.exit(0)


        if '--help' in argv:
            table = [[f'--{o.long_name}', o.description] for o in Cli.options]
            text = f'Usage: flatpak run {APP_ID} [OPTION...]'
            text += f'\nFor a better user experience, please set `alias repolever=\'flatpak run {APP_ID}\'` in your .bashrc file'
            Cli._print_help_if_requested(argv, table, text)

        return -1

    @staticmethod
    def fetch_updates(argv):
        BackgroudUpdatesFetcher.fetch()

    @staticmethod
    def update(argv):
        Cli._print_help_if_requested(argv, [
            ['--yes | -y', 'Skips any interactive question'],
            ['--force', 'Forces updates for running applications'],
            ['--all', 'Updates all packages'],
        ], text='Usage: --update <file_path>')

        assume_yes = ('-y' in argv) or ('--yes' in argv)
        update_all = ('--all' in argv)
        force = ('--force' in argv)

        updates: list[PackageListElement] = []

        if not check_internet():
            print('Internet connection not available')
            sys.exit(1)

        if update_all:
            Cli.list_updates([])
            
            if not assume_yes:
                ans = Cli.ask('\nDo you really want to update all packages? (y/N)', ['y', 'Y', 'n', 'N'])
                if ans.lower() != 'y':
                    sys.exit(0)

            assume_yes = True
            installed = package_provider.list_installed()
            
            for el in installed:
                manager = UpdateManagerChecker.check_url_for_app(el)

                if manager and manager.is_update_available():
                    updates.append(el)
        else:
            g_file = Cli._get_file_from_args(argv)
            el = Cli._get_list_element_from_gfile(g_file)
            updates = [el]

        if not updates:
            sys.exit(0)

        for el in updates:
            if package_provider.is_app_running(el) and (not force):
                print(f'{el.file_path} was skipped because the application is running; use --force to skip this check.')
                continue

            manager = UpdateManagerChecker.check_url_for_app(el)

            if not manager:
                print('No update method was found for this package')
                sys.exit(0)

            if not assume_yes:
                ans = Cli.ask('Do you really want to update this package? (y/N)', ['y', 'Y', 'n', 'N'])
                if ans.lower() != 'y':
                    sys.exit(0)

            print(f'Downloading update from: {manager.name}')
            package_provider.update_from_url(manager, el, 
                lambda s: print("\rStatus: " + str(round(s * 100)) + "%", end=""))

            print(f'\n{el.file_path} updated successfully')

    @staticmethod
    def remove(argv):
        Cli._print_help_if_requested(argv, [
            ['Usage: --remove <file_path>'],
            ['--delete', 'Deletes the package file from disk, instead of trashing it'],
            ['--yes | -y', 'Skips any interactive question'],
        ])

        g_file = Cli._get_file_from_args(argv)
        force = ('--delete' in argv)
        assume_yes = ('-y' in argv) or ('--yes' in argv)
        el = Cli._get_list_element_from_gfile(g_file)
        if not package_provider.is_installed(el):
            print('This package is not integrated')
            sys.exit(1)

        q = 'Do you really want to delete this package?'

        if force:
            q += ' This action is irreversible'

        ans = 'n'
        if not assume_yes:
            ans = Cli.ask(f'{q} (y/N)', ['y', 'Y', 'n', 'N'])

        if ans.lower() == 'y' or assume_yes:
            package_provider.uninstall(el, force_delete=force)
            print(f'{el.file_path} was removed sucessfully')

    @staticmethod
    def remove_all(argv):
        Cli._print_help_if_requested(argv, [
            ['Usage: --remove-all'],
            ['--yes | -y', 'Skips any interactive question'],
        ])

        assume_yes = ('-y' in argv) or ('--yes' in argv)
        apps = package_provider.list_installed()

        q = 'Do you really want to delete all packages?'

        ans = 'n'
        if not assume_yes:
            ans = Cli.ask(f'{q} (y/N)', ['y', 'Y', 'n', 'N'])

        if ans.lower() == 'y' or assume_yes:
            for el in apps:
                if package_provider.is_installed(el):
                    print(f'Removing {el.file_path}')
                    package_provider.uninstall(el, force_delete=True)

    @staticmethod
    def set_update_source(argv):
        u_managers = ', '.join([n.name for n in UpdateManagerChecker.get_models()])
        el = None

        manager_name = Cli._get_arg_value(argv, '--manager')
        selected_model: UpdateManager = UpdateManagerChecker.get_model_by_name(manager_name or '')
        manager_config_template = []

        if (not manager_name) or (not selected_model):
            Cli._print_help_if_requested(argv, [
                ['--manager <manager>', f'Specify an update manager between: {u_managers}'],
                ['--unset', f'Unset a custom config for an app'],
            ], text='Usage: --set-update-source <file_path> --manager <manager> [...OPTIONS]')

            print('Error: "%s" is not a valid update manager' % manager_name)
            sys.exit(1)

        manager: UpdateManager = selected_model(el=None)
        manager_config_template = manager.get_config_from_form()

        Cli._print_help_if_requested(argv, [
            [f'--manager {manager_name}'],
            *[[f'OPTION {k}=<value>'] for k in manager_config_template.keys()]
        ], text='Usage: --set-update-source <file_path> --manager <manager> [...OPTIONS]')

        g_file = Cli._get_file_from_args(argv)
        el = Cli._get_list_element_from_gfile(g_file)
        manager: UpdateManager = selected_model(el=el, embedded=None)  # type: ignore

        update_options = Cli._get_key_pairs(argv)

        if '--unset' in argv:
            Config.delete_app_update_config(el)
            sys.exit(0)

        if set(manager_config_template.keys()) != set(update_options.keys()):
            print('Missing or invalid update configuration, required keys: ' + ', '.join(manager_config_template.keys()))
            sys.exit(1)

        boolean_vals = ['0', '1', 'false', 'true']
        for k, v in manager_config_template.items():
            if type(v) == bool:
                if update_options[k] not in boolean_vals:
                    print(f'{k} is not a boolean value, allowed values: ' + '/'.join(boolean_vals))
                    sys.exit(1)

                update_options[k] = (update_options[k] in ['1', 'true'])

        manager.validate_config(update_options)

        Config.delete_app_update_config(el)
        Config.set_app_update_config(el, manager, update_options)

        sys.exit(0)

    @staticmethod
    def integrate(argv):
        Cli._print_help_if_requested(argv, [
            ['--yes | -y', 'Skip confirmation prompts'],
        ], text='Usage: --integrate <github-repo-url>')

        url = None
        for a in argv[1:]:
            if a.startswith('-'):
                continue
            if 'github.com' in a or a.startswith('http'):
                url = a
                break

        if not url:
            print('Usage: --integrate https://github.com/owner/repository')
            print('Repo Lever only installs from GitHub URLs so updates can be tracked.')
            sys.exit(1)

        assume_yes = ('-y' in argv) or ('--yes' in argv)
        if not assume_yes:
            ans = Cli.ask(f'Install latest release from {url}? (y/N) ', ['y', 'Y', 'n', 'N'])
            if ans.lower() != 'y':
                sys.exit(0)

        package_provider.install_from_github_url(url)
        print('Package installed successfully')
        sys.exit(0)

    @staticmethod
    def list_installed(argv):
        Cli._print_help_if_requested(argv, [
            ['--json', 'Prints a versioned, machine-readable JSON document'],
        ])

        apps = package_provider.list_installed()
        json_output = '--json' in argv

        table = []
        for a in apps:
            if json_output:
                with redirect_stdout(sys.stderr):
                    manager = UpdateManagerChecker.check_url_for_app(a)

                table.append(Cli._make_app_json(a, manager))
                continue

            manager: UpdateManager | None = UpdateManagerChecker.check_url_for_app(a)
            update_mng = 'UpdatesNotAvailable'
            if manager:
                update_mng = manager.name

                if manager.embedded:
                    update_mng += '|embedded'

            update_mng = f'[{update_mng}]'

            v = a.version or 'Not specified'
            table.append([a.name, f'[{v}]', update_mng, a.file_path])

        if json_output:
            print(json.dumps({
                'schema_version': 1,
                'installed': table,
            }, ensure_ascii=False))
            return

        Cli._print_table(table)

    @staticmethod
    def list_updates(argv):
        Cli._print_help_if_requested(argv, [
            ['--json', 'Prints a versioned, machine-readable JSON document'],
        ])

        installed = package_provider.list_installed()
        table = []
        updates = []
        json_output = '--json' in argv

        if not check_internet():
            print('Internet connection not available',
                  file=sys.stderr if json_output else sys.stdout)
            sys.exit(1)

        for el in installed:
            manager = UpdateManagerChecker.check_url_for_app(el)
            if not manager:
                continue

            try:
                if json_output:
                    with redirect_stdout(sys.stderr):
                        update_available = manager.is_update_available()
                else:
                    update_available = manager.is_update_available()

                if update_available:
                    if json_output:
                        updates.append(Cli._make_app_json(el, manager))
                        continue

                    s = f'[Update available, {manager.name}]'
                    if manager.embedded:
                        s = f'[Update available, {manager.name}|embedded]'
                    row = [el.name, s, el.file_path]
                    # if '-v' in argv:
                    #     row.append(json.dumps(dict(manager.get_config())))

                    table.append(row)
            except Exception as e:
                logging.error(traceback.format_exc())

        if json_output:
            print(json.dumps({
                'schema_version': 1,
                'updates': updates,
            }, ensure_ascii=False))
            return

        if not table:
            print('No updates available')
            return

        Cli._print_table(table)

    @staticmethod
    def _make_app_json(el, manager):
        try:
            with redirect_stdout(sys.stderr):
                running = package_provider.is_app_running(el)
        except Exception:
            running = None
            logging.error(traceback.format_exc())

        desktop_file_path = getattr(el, 'desktop_file_path', None)
        return {
            'name': el.name,
            'path': el.file_path,
            'desktop_id': os.path.basename(desktop_file_path) if desktop_file_path else None,
            'current_version': getattr(el, 'version', None),
            'available_version': None,
            'download_size': None,
            'manager': manager.name if manager else None,
            'embedded_source': bool(manager and manager.embedded),
            'running': running,
        }

    @staticmethod
    def list_update_managers(argv):
        models = UpdateManagerChecker.get_models()
        table = []

        for m in models:
            table.append([m.label, m.name])

        Cli._print_table(table)

    @staticmethod
    def _get_key_pairs(argv: list[str]) -> dict:
        result = {}
        for pair in argv:
            if '=' in pair:
                key, value = pair.split('=', 1)
                result[key] = value

        return result

    @staticmethod
    def _get_arg_value(argv: list[str], arg_name: str):
        if not arg_name in argv:
            return None

        i = argv.index(arg_name)

        if len(argv) < (i + 2):
            print(f'Error: {arg_name} requires a value')
            sys.exit(1)

        val = argv[i + 1]
        return val

    @staticmethod
    def _print_table(table):
        if (not table):
            return

        longst_row = 0

        for r in table:
            if len(r) > longst_row:
                longst_row = len(r)

        
        for r in table:
            if len(r) < longst_row:
                while len(r) < longst_row:
                    r.append('')

        longest_cols = [
            (max([len(str(row[i])) for row in table]) + 3)
            for i in range(len(table[0]))
        ]

        row_format = "".join(["{:<" + str(longest_col) + "}" for longest_col in longest_cols])
        for row in table:
            print(row_format.format(*row))

    @staticmethod
    def _get_invoked_option(argv):
        for opt in Cli.options:
            long_name = str(opt.long_name)
            if f'--{long_name}' == argv[1]:
                return opt

        return None

    @staticmethod
    def _print_help_if_requested(argv, help: list, text=''):
        if '--help' in argv:
            opt = Cli._get_invoked_option(argv)
            if opt:
                print(str(opt.description))

            if text:
                print(text)

            Cli._print_table(help)
            print('\nMore documentation available at https://github.com/isorropisths/repolever')
            sys.exit(0)

    @staticmethod
    def _get_file_from_args(args):
        for a in args:
            if a.startswith('-'):
                continue
            g_file = Gio.File.new_for_path(a)
            path = g_file.get_path()
            if os.path.isfile(path):
                for pkg in package_provider.list_installed():
                    if pkg.file_path == path:
                        return g_file
        print('Error: specify a tracked package file path (see --list-installed)')
        sys.exit(1)

    @staticmethod
    def _get_list_element_from_gfile(g_file: Gio.File):
        el = None

        for a in package_provider.list_installed():
            if a.file_path == g_file.get_path():
                el = a
                break

        if not el:
            print('Error: package not integrated')
            sys.exit(1)
    
        return el
