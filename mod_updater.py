#!/usr/bin/python3
"""
This module provides a simple method to manage updating and installing mods
on a given factorio server.

It is currently not intended to be imported and instead should be executed
directly as a python script.
"""
import argparse
from enum import Enum, auto
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

# External URL processing library
# http://docs.python-requests.org/en/master/user/quickstart/
import requests


def _validate_hash(checksum: str, target: str,
                   bsize: int=65536) -> bool:
    """
    Checks to see if the file specified by target matches the provided sha1
    checksum.

    Keyword Arguments:
    checksum -- sha1 digest to be matched
    target   -- path to the file which must be validated
    """
    hasher = hashlib.sha1()

    with open(target, 'rb') as target_fp:
        block = target_fp.read(bsize)
        while len(block) > 0:
            hasher.update(block)
            block = target_fp.read(bsize)

    return hasher.hexdigest() == checksum


class ModUpdater():
    """
    Internal class managing the current version and state of the mods on this
    server.
    """

    class Mode(Enum):
        """Possible execution modes"""
        LIST = auto()
        UPDATE = auto()

    def __init__(self, settings_path: str, mod_path: str, fact_path: str,
                 creds: hash):
        """
        Initialize the updater class with all mandatory and optional arguments.

        Keyword arguments:
        settings_path -- absolute path to the server-settings.json file
        mod_path      -- absolute path to the factorio mod directory
        fact_ver      -- local factorio version
        """
        self.mod_server_url = 'https://mods.factorio.com'
        self.mod_path = mod_path

        # Get the credentials to download mods
        if settings_path is not None:
            self._parse_settings(settings_path)

        # Parse username and token
        if 'username' in creds and creds['username'] is not None:
            self.username = creds['username']
        elif 'username' in self.settings:
            self.username = self.settings['username']
        else:
            self.token = None

        if 'token' in creds and creds['token'] is not None:
            self.token = creds['token']
        elif 'token' in self.settings:
            self.token = self.settings['token']
        else:
            self.token = None

        # Ensure username and token were specified
        if self.username is None or self.username == '':
            errmsg = (
                'error: username not specified in server-settings.json'
                ' or via cli!'
                )
            print(errmsg, file=sys.stderr)
            sys.exit(1)

        if self.token is None or self.token == '':
            errmsg = (
                'error: token not specified in server-settings.json'
                ' or via cli!'
                )
            print(errmsg, file=sys.stderr)
            sys.exit(1)

        # Begin processing
        self._determine_version(fact_path)
        self._parse_mod_list()
        self._retrieve_mod_metadata()

    def _determine_version(self, fact_path: str):
        """Determine the local factorio version"""
        if not os.path.exists(fact_path):
            errmsg = (
                "error: factorio binary '{fpath_path}' does not exist!"
                )
            print(errmsg, file=sys.stderr)
            sys.exit(1)

        try:
            output = subprocess.check_output(
                [fact_path, '--version'],
                universal_newlines=True)
            ver_re = re.compile(r'Version: (\d+)[.](\d+)[.](\d+) .*\n',
                                re.RegexFlag.M)
            match = ver_re.match(output)
            if match:
                version = {}
                version['major'] = match.group(1)
                version['minor'] = match.group(2)
                version['patch'] = match.group(3)
                version['release'] = '{}.{}'.format(
                    version['major'],
                    version['minor'])
                self.fact_version = version
            else:
                errmsg = (
                    'Unable to parse version from:\n{output}'.format(
                        output=output)
                    )
                print(errmsg, file=sys.stderr)
                sys.exit('1')

        except subprocess.CalledProcessError as error:
            errmsg = (
                'error: failed to run  \'{fpath} --version\': '
                '{errstr}').format(fpath=fact_path, errstr=error.stderr)
            print(errmsg, file=sys.stderr)
            sys.exit(1)

        print('Factorio Release: {release}\n'.format(
            release=self.fact_version['release']))

    def _parse_settings(self, settings_path: str):
        """Process the specified server-settings.json file."""
        try:
            with open(settings_path, 'r') as settings_fp:
                self.settings = json.load(settings_fp)
        except IOError as error:
            errmsg = (
                'error: failed to open file \'{fname}\': '
                '{errstr}').format(fname=settings_path, errstr=error.strerror)
            print(errmsg, file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as error:
            errmsg = (
                'error: failed to parse json file \'{fname}\': '
                '{errstr}').format(fname=settings_path, errstr=error.msg)
            print(errmsg, file=sys.stderr)
            sys.exit(1)

    def _retrieve_mod_metadata(self):
        """
        Pull the latest metadata for each mod from the factorio server
        See https://wiki.factorio.com/Mod_portal_API for details
        """
        print("Retrieving metadata", end='')
        for mod, data in self.mods.items():
            mod_url = self.mod_server_url + '/api/mods/' + mod + '/full'
            with requests.get(mod_url) as req:
                if not req.status_code == 200:
                    continue
                data['metadata'] = req.json()

            # Find the latest release for this version of factorio
            matching_releases = []
            for rel in data['metadata']['releases']:
                rel_ver = rel['info_json']['factorio_version']
                if rel_ver == self.fact_version['release']:
                    matching_releases.append(rel)

            data['latest'] = matching_releases[-1]
            print('.', end='', flush=True)
        print('complete!')

        for mod, data in self.mods.items():
            if 'metadata' not in data:
                warnmsg = (
                    "Warning: Unable to retrieve metadata for"
                    " {mod}, skipped!".format(mod=mod))
                print(warnmsg)

    def _parse_mod_list(self):
        """Process the mod-list.json within mod_path."""
        mod_list_path = os.path.join(self.mod_path, 'mod-list.json')
        try:
            settings_fp = open(mod_list_path, 'r')
            mod_json = json.load(settings_fp)
            self.mods = {}
            if 'mods' in mod_json:
                for mod in mod_json['mods']:
                    entry = {}
                    entry['enabled'] = mod['enabled']
                    self.mods[mod['name']] = entry
            else:
                print('Invalid mod-list.json file \
                      \'{path}\'!'.format(path=mod_list_path),
                      file=sys.stderr)
                exit(1)

            # Remove the 'base' mod as it's not relevant to this process
            if 'base' in self.mods:
                del self.mods['base']
        except IOError as error:
            errmsg = (
                'error: failed to open file \'{fname}\': '
                '{errstr}').format(fname=mod_list_path, errstr=error.strerror)
            print(errmsg, file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as error:
            errmsg = (
                'error: failed to parse json file \'{fname}\': '
                '{errstr}').format(fname=mod_list_path, errstr=error.msg)
            print(errmsg, file=sys.stderr)
            sys.exit(1)

        # Collect the installed state & versions
        self.mod_files = \
            glob.glob('{mod_path}/*.zip'.format(mod_path=self.mod_path))
        installed_mods = {}
        mod_pattern = re.compile('^(.*)_(.*)[.]zip$')
        for entry in self.mod_files:
            basename = os.path.basename(entry)
            match = mod_pattern.fullmatch(basename)
            if match:
                installed_mods[match.group(1)] = match.group(2)

        for mod, data in self.mods.items():
            if mod in installed_mods:
                data['installed'] = True
                data['version'] = installed_mods[mod]
            else:
                data['installed'] = False

    def list(self):
        """Lists the mods installed on this server."""
        # Find the longest mod name
        max_len = 0
        for mod in self.mods:
            mod_len = len(mod)
            max_len = mod_len if mod_len > max_len else max_len

        print('{:<{width}}\tenabled\tinstalled\tcurrent_v\tlatest_v'.format(
            'mod_name',
            width=max_len))
        for mod, data in self.mods.items():
            print('{:<{width}}\t{enbld}\t{inst}\t\t{cver}\t\t{lver}'.format(
                mod,
                enbld=str(data['enabled']),
                inst=str(data['installed']),
                cver=data['version'] if data['installed'] else 'N/A',
                lver=data['latest']['version'] if 'latest' in data else 'N/A',
                width=max_len))

    def override_credentials(self, username: str, token: str):
        """Replaces the values provided in server-settings.json"""
        if username is not None:
            self.username = username
        if token is not None:
            self.token = token

    def update(self):
        """
        Updates all mods currently installed on this server to the latest
        release
        """
        for mod, data in self.mods.items():
            if 'latest' not in data:
                warnmsg = (
                    "{mod}: Missing metadata, skipping update!".format(
                        mod=mod))
                print(warnmsg)
            else:
                self._prune_old_releases(mod)
                self._download_latest_release(mod)

    def _prune_old_releases(self, mod: str):
        """
        Deletes any locally installed versions older than the latest release.

        Keyword Arguments:
        mod -- name of the target to update
        """
        data = self.mods[mod]
        latest_version = data['latest']['version']

        # Declare the patterns
        mod_pattern = re.compile('^{mod}_.*[.]zip$'.format(mod=mod))
        version_pattern = re.compile('^{mod}_{ver}.zip$'.format(
            mod=mod, ver=latest_version))

        # Build the parse list
        basenames = [os.path.basename(x) for x in self.mod_files]
        inst_rels = [x for x in basenames if mod_pattern.fullmatch(x)]
        for rel in inst_rels:
            if version_pattern.fullmatch(rel):
                continue

            print("{mod}: removing '{target}'".format(
                mod=mod, target=rel))

            rel_path = os.path.join(self.mod_path, rel)
            try:
                os.remove(rel_path)
            except OSError as error:
                errmsg = (
                    'error: failed to remove \'{fname}\': '
                    '{errstr}').format(fname=rel_path,
                                       errstr=error.strerror)
                print(errmsg, file=sys.stderr)
                sys.exit(1)

    def _download_latest_release(self, mod: str):
        """
        Retrieves the latest version of the specified mod compatible with the
        factorio release present on this server.

        Keyword Arguments:
        mod -- name of the target to update
        """
        data = self.mods[mod]
        latest = data['latest']
        target = os.path.join(self.mod_path, latest['file_name'])

        validate = download = False

        v_cur = data['version'] if 'version' in data else 'N/A'
        v_new = latest['version']
        if data['installed']:
            if v_new == v_cur:
                print("{mod}: validating installed '{version}'...".format(
                    mod=mod, version=v_cur), end='')
                validate = True
            else:
                print("{mod}: updating from '{v_cur}' to '{v_new}'...".format(
                    mod=mod, v_new=v_new, v_cur=v_cur), end='')
                download = True
        else:
            print("{mod}: downloading version '{version}'...".format(
                mod=mod, version=v_new), end='')
            download = True

        if validate:
            if _validate_hash(latest['sha1'], target):
                print('Valid!')
            else:
                print('Invalid! Downloading...', end='')
                download = True

        if download:
            creds = {'username': self.username, 'token': self.token}
            dl_url = self.mod_server_url + latest['download_url']
            with requests.get(dl_url, params=creds, stream=True) as req:
                if req.status_code == 200:
                    with open(target, 'wb') as target_file:
                        shutil.copyfileobj(req.raw, target_file)
                        target_file.flush()
                else:
                    warnmsg = (
                        "Unable to retrieve, skipping!".format(
                            mod=mod))
                    print(warnmsg)

            if _validate_hash(latest['sha1'], target):
                print('Complete!')
            else:
                print('Download did not match checksum!')


if __name__ == "__main__":
    DESC_TEXT = 'Updates mods for a target factorio installation'
    PARSER = argparse.ArgumentParser(description=DESC_TEXT)
    # Username
    PARSER.add_argument(
        '-u',
        '--username',
        dest='username',
        help='factorio.com username overriding server-settings.json')
    # Token
    PARSER.add_argument(
        '-t',
        '--token',
        dest='token',
        help='factorio.com API token overriding server-settings.json')
    # Server Settings
    PARSER.add_argument(
        '-s',
        '--server-settings',
        dest='settings_path',
        required=True,
        help='Absolute path to the server-settings.json file')
    # Factorio mod directory
    PARSER.add_argument(
        '-m',
        '--mod-directory',
        dest='mod_path',
        required=True,
        help='Absolute path to the mod directory')
    # Factorio binary absolute path
    PARSER.add_argument(
        '--fact-path',
        dest='fact_path',
        required=True,
        help='Absolute path to the factorio binary')
    # Possible Execution modes
    MODE_GROUP = PARSER.add_mutually_exclusive_group(required=True)
    MODE_GROUP.add_argument(
        '--list',
        dest='mode',
        action='store_const',
        const=ModUpdater.Mode.LIST,
        help='List the currently installed mods with versions')
    MODE_GROUP.add_argument(
        '--update',
        dest='mode',
        action='store_const',
        const=ModUpdater.Mode.UPDATE,
        help='Update all mods to their latest release')

    ARGS = PARSER.parse_args()
    UPDATER = ModUpdater(
        settings_path=ARGS.settings_path,
        mod_path=ARGS.mod_path,
        fact_path=ARGS.fact_path,
        creds={'username': ARGS.username, 'token': ARGS.token})

    if ARGS.mode == ModUpdater.Mode.LIST:
        UPDATER.list()
    elif ARGS.mode == ModUpdater.Mode.UPDATE:
        UPDATER.update()
