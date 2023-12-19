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

    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

    def _get_cve_info(self, cve_id):
        if not cve_id.upper().startswith('CVE-'):
            cve_id = 'CVE-' + cve_id

        url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"
        headers = {'User-Agent': self.USER_AGENT}
        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            tree = html.fromstring(response.content)

            # Check if the CVE does not exist
            if tree.xpath('//h1[text()="This CVE does not exist"]'):
                return f"Error: {cve_id} does not exist."

            # Extract description information
            description_elements = tree.xpath('//h3[@data-testid="vuln-description-title" and contains(text(),"Description")]/following-sibling::p[@data-testid="vuln-description"]/text()')
            description = description_elements[0].strip() if description_elements else "Description not found"

            # Extract NVD Published Date
            published_date_elements = tree.xpath('//strong[contains(text(),"Published Date:")]/following-sibling::span[@data-testid="vuln-published-on"]//text()')
            published_date = ' '.join(published_date_elements).strip() if published_date_elements else "N/A"
            last_modified_elements = tree.xpath('//strong[contains(text(),"Last Modified:")]/following-sibling::span[@data-testid="vuln-last-modified-on"]//text()')
            last_modified = ' '.join(last_modified_elements).strip() if last_modified_elements else "N/A"


            # Construct the output message with formatting
            output_lines = [
                ircutils.mircColor(f"{cve_id}", 'teal') + " - " +
                f"{ircutils.bold('Description:')} {description}",
                ircutils.bold("Published Date:") + f" {published_date}",
                ircutils.bold("Last Modified Date:") + f" {last_modified}",
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

