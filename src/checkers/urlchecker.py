# URL Checker: verifies if an external data URL is still accessible.  Does not need an
# x-checker-data entry and works with all external data types that have a URL. However,
# if you're dealing with a generic link that redirects to a versioned archive that
# changes, e.g.:
#
#    http://example.com/last-version -> http://example.com/prog_1.2.3.gz
#
# Then you can specify some some metadata in the manifest file to tell the checker where
# to look:
#
#   "x-checker-data": {
#       "type": "rotating-url",
#       "url": "http://example.com/last-version"
#   }
#
# Copyright © 2018-2019 Endless Mobile, Inc.
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
import logging
import urllib.error

from lib.externaldata import ExternalData, ExternalFile, Checker
from lib import utils

log = logging.getLogger(__name__)


class URLChecker(Checker):
    def check(self, external_data):
        is_rotating = external_data.checker_data.get('type') == 'rotating-url'
        if is_rotating:
            url = external_data.checker_data['url']
        else:
            url = external_data.current_version.url

        log.debug("Getting extra data info from %s; may take a while", url)

        try:
            new_url, data, checksum, size = utils.get_extra_data_info_from_url(url)
        except urllib.error.HTTPError as e:
            log.warning('%s returned %s', url, e)
            external_data.state = ExternalData.State.BROKEN
        except Exception:
            log.exception('Unexpected exception while checking %s', url)
            external_data.state = ExternalData.State.BROKEN
        else:
            if url.endswith(".AppImage"):
                version_string = utils.extract_appimage_version(
                    external_data.filename, data,
                )
                log.debug("%s is version %s", external_data.filename, version_string)
            else:
                version_string = None

            new_version = ExternalFile(
                new_url if is_rotating else url,
                checksum, size, version_string,
            )
            if external_data.current_version.matches(new_version):
                log.debug("URL %s still valid", new_url)
                external_data.state = ExternalData.State.VALID
            else:
                external_data.state = ExternalData.State.BROKEN
                external_data.new_version = new_version
