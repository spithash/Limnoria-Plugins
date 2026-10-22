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
from supybot.commands import wrap
from supybot.i18n import PluginInternationalization
import re
import requests

_ = PluginInternationalization('CVESearch')


class CVESearch(callbacks.Plugin):
    """Look up records from the CVE Program's official GitHub CVE List."""

    threaded = True
    USER_AGENT = "Limnoria-CVESearch"
    CVE_PATTERN = re.compile(r'\bCVE-[0-9]{4}-[0-9]{4,}\b', re.IGNORECASE)
    URL_PATTERN = re.compile(r'https?://\S+', re.IGNORECASE)
    RAW_BASE = "https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves"

    @staticmethod
    def _clean(value):
        # External text must not inject IRC formatting or extra lines.
        value = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', str(value))
        return ' '.join(value.split())

    @classmethod
    def _english_text(cls, entries):
        entries = [entry for entry in entries if entry.get('value')]
        for entry in entries:
            if entry.get('lang', '').lower().split('-')[0] == 'en':
                return cls._clean(entry['value'])
        return cls._clean(entries[0]['value']) if entries else ''

    @classmethod
    def _cvss(cls, cna, adp):
        # Prefer the assigning CNA; use enrichment only if it has no CVSS.
        for containers in ([cna], adp):
            for key in ('cvssV4_0', 'cvssV3_1', 'cvssV3_0', 'cvssV2_0'):
                for container in containers:
                    for metric in container.get('metrics', []):
                        score = metric.get(key, {})
                        if score.get('baseScore') is not None:
                            version = score.get('version', key[4:].replace('_', '.'))
                            severity = score.get('baseSeverity', '')
                            source = container.get('providerMetadata', {}).get('shortName', '')
                            return cls._clean(
                                f"{score['baseScore']} {severity} (v{version}"
                                + (f", {source}" if source else '') + ")")
        return ''

    def _format_record(self, cve_id, record):
        metadata = record['cveMetadata']
        cna = record.get('containers', {}).get('cna', {})
        state = metadata.get('state', 'UNKNOWN')
        rejected = state == 'REJECTED'
        description = self._english_text(cna.get(
            'rejectedReasons' if rejected else 'descriptions', []))
        description = description or ('No rejection reason provided.' if rejected
                                      else 'No description provided.')
        limit = self.registryValue('maxDescriptionLength')
        if len(description) > limit:
            description = description[:limit - 1].rstrip() + '…'

        parts = [ircutils.mircColor(cve_id, 'teal')]
        if state != 'PUBLISHED':
            parts.append(ircutils.bold('Status:') + ' ' + self._clean(state))
        parts.append(ircutils.bold('Reason:' if rejected else 'Description:')
                     + ' ' + description)
        if not rejected:
            cvss = self._cvss(cna, record.get('containers', {}).get('adp', []))
            if cvss:
                parts.append(ircutils.bold('CVSS:') + ' ' + cvss)
        for label, key in (('Published:', 'datePublished'), ('Updated:', 'dateUpdated')):
            if metadata.get(key):
                parts.append(ircutils.bold(label) + ' ' + self._clean(metadata[key]))
        parts.append(ircutils.bold('URL:') + f" https://www.cve.org/CVERecord?id={cve_id}")
        return ' - '.join(parts)

    def _get_cve_info(self, cve_id):
        cve_id = cve_id.strip().upper()
        if not cve_id.startswith('CVE-'):
            cve_id = 'CVE-' + cve_id
        if not self.CVE_PATTERN.fullmatch(cve_id):
            return 'Error: Invalid CVE ID. Use CVE-YYYY-NNNN (at least four sequence digits).'

        _, year, number = cve_id.split('-')
        bucket = number[:-3] + 'xxx'
        url = f"{self.RAW_BASE}/{year}/{bucket}/{cve_id}.json"
        try:
            response = requests.get(url, headers={'User-Agent': self.USER_AGENT}, timeout=15)
            if response.status_code == 404:
                return (f"{cve_id}: No public record found in the official CVE List. "
                        "The ID may be reserved, unpublished, invalid, or not yet synced.")
            if response.status_code != 200:
                return f"Error: Unable to fetch {cve_id} from GitHub (HTTP {response.status_code})."
            record = response.json()
            if (not isinstance(record, dict) or record.get('dataType') != 'CVE_RECORD'
                    or record.get('cveMetadata', {}).get('cveId') != cve_id):
                return f"Error: Invalid CVE record received for {cve_id}."
            return self._format_record(cve_id, record)
        except requests.Timeout:
            return f"Error: GitHub request timed out for {cve_id}. Try again later."
        except requests.RequestException:
            return f"Error: Unable to reach GitHub for {cve_id}. Try again later."
        except (ValueError, TypeError, KeyError, AttributeError):
            return f"Error: Invalid CVE record received for {cve_id}."

    @wrap(["text"])
    def cve(self, irc, msg, args, cve_id):
        """<CVE-ID>
        Display information about a CVE (Common Vulnerabilities and Exposures)."""
        irc.reply(self._get_cve_info(cve_id))

    def doPrivmsg(self, irc, msg):
        # Check if the message is sent to a channel
        if not irc.isChannel(msg.channel):
            return

        
        channel = msg.channel
        network = irc.network

        # Check if the cveSnarfer is enabled for this channel and network
        if not self.registryValue('cveSnarfer', channel, network):
            return

        # Extract the text content from the message
        text = msg.args[1]

        # Ignore any command, no matter the prefix char
        if callbacks.addressed(irc, msg):
            return

        # Check if the message contains a CVE identifier pattern and is not a URL (http*)
        if not self.URL_PATTERN.search(text):
            matches = dict.fromkeys(match.upper() for match in self.CVE_PATTERN.findall(text))
            for cve_id in list(matches)[:3]:
                irc.reply(self._get_cve_info(cve_id))


Class = CVESearch

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
