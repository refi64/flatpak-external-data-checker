# Copyright © 2018–2019 Endless Mobile, Inc.
#
# Authors:
#       Joaquim Rocha <jrocha@endlessm.com>
#       Will Thompson <wjt@endlessm.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

from collections import OrderedDict
from checkers import ALL_CHECKERS
from lib.externaldata import (
    ExternalData, ExternalDataSource, ExternalDataFinishArg,
)

import json
import logging
import os
from ruamel.yaml import YAML

import gi
gi.require_version('Json', '1.0')
from gi.repository import Json  # noqa: E402

log = logging.getLogger(__name__)


class NoManifestCheckersFound(Exception):
    pass


class ManifestChecker:
    yaml = YAML()
    # ruamel preserves some formatting (such as comments and blank lines) but
    # not the indentation of the source file. These settings match the style
    # recommended at <https://github.com/flathub/flathub/wiki/YAML-Style-Guide>.
    yaml.indent(mapping=2, sequence=4, offset=2)

    def __init__(self, manifest):
        self._manifest = manifest
        self._external_data = {}

        # Initialize checkers
        self._checkers = [checker() for checker in ALL_CHECKERS]

        # Map from filename to parsed contents of that file. Sources may be
        # specified as references to external files, which is why there can be
        # more than one file even though the input is a single filename.
        self._manifest_contents = {}

        # Top-level manifest contents
        data = self._read_manifest(self._manifest)
        # Map from manifest path to [ExternalData]
        self._collect_external_data(self._manifest, data)

    @classmethod
    def _read_json_manifest(cls, manifest_path):
        '''Read manifest from 'manifest_path', which may contain C-style
        comments or multi-line strings (accepted by json-glib and hence
        flatpak-builder, but not Python's json module).'''

        # Round-trip through json-glib to get rid of comments, multi-line
        # strings, and any other invalid JSON
        parser = Json.Parser()
        parser.load_from_file(manifest_path)
        root = parser.get_root()
        clean_manifest = Json.to_string(root, False)

        return json.loads(clean_manifest, object_pairs_hook=OrderedDict)

    @classmethod
    def _read_yaml_manifest(cls, manifest_path):
        '''Read a YAML manifest from 'manifest_path'.'''
        with open(manifest_path, 'r') as f:
            return cls.yaml.load(f)

    def _read_manifest(self, manifest_path):
        _, ext = os.path.splitext(manifest_path)
        if ext in ('.yaml', '.yml'):
            contents = self._read_yaml_manifest(manifest_path)
        else:
            contents = self._read_json_manifest(manifest_path)
        self._manifest_contents[manifest_path] = contents
        return contents

    def _dump_manifest(self, path):
        """Writes back the cached contents of 'path', which may have been
        modified. For YAML, we make a best-effort attempt to preserve
        formatting; for JSON, we use the canonical 4-space indentation."""
        contents = self._manifest_contents[path]
        _, ext = os.path.splitext(path)
        with open(path, "w", encoding="utf-8") as fp:
            if ext in ('.yaml', '.yml'):
                self.yaml.dump(contents, fp)
            else:
                json.dump(obj=contents, fp=fp, indent=4)

    def _collect_external_data(self, path, data):
        self._get_module_data_from_json(path, data)
        self._get_finish_args_extra_data_from_json(path, data)

    def _get_finish_args_extra_data_from_json(self, path, json_data):
        finish_args = json_data.get('finish-args', [])
        external_data = self._external_data.setdefault(path, [])
        external_data.extend(ExternalDataFinishArg.from_args(finish_args))

    def _get_module_data_from_json(self, path, json_data):
        for module in json_data.get('modules', []):
            if isinstance(module, str):
                module_path = os.path.join(os.path.dirname(self._manifest),
                                           module)
                module = self._read_manifest(module_path)
            else:
                module_path = path

            sources = module.get('sources', [])
            external_data = self._external_data.setdefault(module_path, [])
            external_data.extend(ExternalDataSource.from_sources(sources))

    def check(self, filter_type=None):
        '''Perform the check for all the external data in the manifest

        It initializes an internal list of all the external data objects
        found in the manifest.
        '''

        if not self._checkers:
            raise NoManifestCheckersFound()

        ext_data_checked = []
        for path, external_data in self._external_data.items():
            log.debug("Checking sources in %s", path)

            n = len(external_data)
            for i, data in enumerate(external_data, 1):
                # Ignore if the type is not the one we care about
                if filter_type is not None and filter_type != data.type:
                    continue

                log.debug('[%d/%d] checking %s', i, n, data.filename)

                for checker in self._checkers:
                    checker.check(data)
                    if data.state != ExternalData.State.UNKNOWN:
                        break
                ext_data_checked.append(data)

        return ext_data_checked

    def get_external_data(self, only_type=None):
        '''Returns the list of the external data found in the manifest

        Should be called after the 'check' method.
        'only_type' can be given for filtering the data of that type.
        '''
        return [
            data
            for datas in self._external_data.values()
            for data in datas
            if only_type is None or data.type == only_type
        ]

    def get_outdated_external_data(self):
        '''Returns a list of the outdated external data

        Outdated external data are the ones that either are broken
        (unreachable URL) or have a new version.
        '''
        return [
            data
            for data in self.get_external_data()
            if data.state == ExternalData.State.BROKEN or data.new_version
        ]

    def _update_manifest(self, path, datas):
        changes = []
        for data in datas:
            if data.new_version is None:
                continue

            data.update()
            if data.new_version.version is not None:
                changes.append(
                    "Update {} to {}".format(
                        data.filename, data.new_version.version
                    )
                )
            else:
                changes.append("Update {}".format(data.filename))

        if changes:
            print("Updating {}".format(path))
            self._dump_manifest(path)

        return changes

    def update_manifests(self):
        """Updates references to external data in manifests."""
        changes = []
        for path, datas in self._external_data.items():
            changes.extend(self._update_manifest(path, datas))
        return changes
