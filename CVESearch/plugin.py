###
# Copyright (c) 2023, Stathis Xantinidis @spithash
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

from supybot import ircutils, callbacks
from supybot.commands import *
from supybot.i18n import PluginInternationalization
import requests
from lxml import html

_ = PluginInternationalization('CVESearch')


class CVESearch(callbacks.Plugin):
    """Search and display CVE information from Circl CVE database."""

    def _get_cve_info(self, cve_id):
        if not cve_id.upper().startswith('CVE-'):
            cve_id = 'CVE-' + cve_id

        url = f"https://cve.circl.lu/cve/{cve_id}"
        response = requests.get(url)

        if response.status_code == 200:
            tree = html.fromstring(response.content)

            # Check if the CVE does not exist
            if tree.xpath('//h1[text()="This CVE does not exist"]'):
                return f"Error: {cve_id} does not exist."

            # Extract summary information
            summary_elements = tree.xpath('//td[@class="warning"][contains(text(), "Summary")]/following-sibling::td[@class="info"]/text()')
            summary = summary_elements[0].strip() if summary_elements else "Summary not found"

            # Extract other information
            last_major_update_elements = tree.xpath('//td[@class="warning"][contains(text(), "Last major update")]/following-sibling::td[@class="info"]/text()')
            last_major_update = last_major_update_elements[0].strip() if last_major_update_elements else "Information not available"

            published_elements = tree.xpath('//td[@class="warning"][contains(text(), "Published")]/following-sibling::td[@class="info"]/text()')
            published = published_elements[0].strip() if published_elements else "Information not available"

            last_modified_elements = tree.xpath('//td[@class="warning"][contains(text(), "Last modified")]/following-sibling::td[@class="info"]/text()')
            last_modified = last_modified_elements[0].strip() if last_modified_elements else "Information not available"

            # Construct the output message with formatting
            output_lines = [
                ircutils.mircColor(f"{cve_id}", 'teal') + " - " + f"{ircutils.bold('Summary:')} {summary}",
                ircutils.bold("Last Major Update:") + f" {last_major_update}",
                ircutils.bold("Published:") + f" {published}",
                ircutils.bold("Last Modified:") + f" {last_modified}",
                ircutils.bold("URL:") + f" {url}"
            ]
            return ' - '.join(output_lines)
        else:
            error_message = f"Error: Unable to fetch information for CVE {cve_id}. Status Code: {response.status_code}"
            return ircutils.mircColor(error_message, 'red')

    @wrap(["text"])
    def cve(self, irc, msg, args, cve_id):
        """<CVE ID>
        Display information about a CVE."""
        irc.reply(self._get_cve_info(cve_id))

Class = CVESearch

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
