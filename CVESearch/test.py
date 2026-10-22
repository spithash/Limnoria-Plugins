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

from supybot.test import *
from copy import deepcopy
from unittest.mock import Mock, patch
import requests
from . import plugin


class CVESearchTestCase(PluginTestCase):
    plugins = ('CVESearch',)

    def setUp(self):
        super().setUp()
        self.record = {
            'dataType': 'CVE_RECORD',
            'cveMetadata': {'cveId': 'CVE-2024-12345', 'state': 'PUBLISHED',
                            'datePublished': '2024-01-01T12:00:00Z',
                            'dateUpdated': '2024-02-01T12:00:00Z'},
            'containers': {'cna': {
                'providerMetadata': {'shortName': 'example'},
                'descriptions': [{'lang': 'fr', 'value': 'French'},
                                 {'lang': 'en', 'value': 'A serious\n vulnerability.'}],
                'metrics': [{'cvssV3_1': {'version': '3.1', 'baseScore': 9.8,
                                         'baseSeverity': 'CRITICAL'}}]}}
        }
        self.plugin = self.irc.getCallback('CVESearch')

    def fetch(self, record=None, status=200):
        response = Mock(status_code=status)
        response.json.return_value = deepcopy(self.record if record is None else record)
        with patch.object(plugin.requests, 'get', return_value=response) as get:
            result = self.plugin._get_cve_info('2024-12345')
            get.assert_called_once_with(
                plugin.CVESearch.RAW_BASE + '/2024/12xxx/CVE-2024-12345.json',
                headers={'User-Agent': 'Limnoria-CVESearch'}, timeout=15)
        return ircutils.stripFormatting(result)

    def testPublished(self):
        result = self.fetch()
        for text in ('Description: A serious vulnerability.',
                     'CVSS: 9.8 CRITICAL (v3.1, example)',
                     'Published: 2024-01-01T12:00:00Z',
                     'Updated: 2024-02-01T12:00:00Z',
                     'https://www.cve.org/CVERecord?id=CVE-2024-12345'):
            self.assertIn(text, result)

    def testMissing(self):
        result = self.fetch(status=404)
        self.assertIn('No public record', result)
        self.assertNotIn('does not exist', result)

    def testRejected(self):
        record = deepcopy(self.record)
        record['cveMetadata']['state'] = 'REJECTED'
        record['containers']['cna']['rejectedReasons'] = [
            {'lang': 'en', 'value': 'Duplicate of another CVE.'}]
        result = self.fetch(record)
        self.assertIn('Status: REJECTED', result)
        self.assertIn('Reason: Duplicate of another CVE.', result)
        self.assertNotIn('CVSS:', result)

    def testSparseRecord(self):
        record = deepcopy(self.record)
        record['containers'] = {}
        self.assertIn('No description provided.', self.fetch(record))

    def testAdpScoreAndCnaPriority(self):
        record = deepcopy(self.record)
        record['containers']['adp'] = [{
            'providerMetadata': {'shortName': 'enricher'},
            'metrics': [{'cvssV4_0': {'version': '4.0', 'baseScore': 10.0,
                                      'baseSeverity': 'CRITICAL'}}]}]
        self.assertIn('v3.1, example', self.fetch(record))
        record['containers']['cna']['metrics'] = []
        self.assertIn('v4.0, enricher', self.fetch(record))

    def testTruncationAndControlCharacters(self):
        record = deepcopy(self.record)
        record['containers']['cna']['descriptions'] = [
            {'lang': 'en', 'value': '\x01\x02' + 'x' * 600}]
        result = self.fetch(record)
        self.assertIn('x' * 499 + '…', result)
        self.assertNotIn('x' * 500, result)
        self.assertNotIn('\x01', result)

    def testInvalidInput(self):
        with patch.object(plugin.requests, 'get') as get:
            self.assertIn('Invalid CVE ID', self.plugin._get_cve_info('../test'))
            get.assert_not_called()

    def testIdNormalizationAndBuckets(self):
        for cve_id, bucket in (('cve-2024-0001', '0xxx'),
                               ('CVE-2024-12345678', '12345xxx')):
            record = deepcopy(self.record)
            record['cveMetadata']['cveId'] = cve_id.upper()
            with patch.object(plugin.requests, 'get', return_value=Mock(
                    status_code=200, json=Mock(return_value=record))) as get:
                self.assertNotIn('Error:', self.plugin._get_cve_info(cve_id))
                self.assertIn('/' + bucket + '/', get.call_args.args[0])

    def testFailures(self):
        for error, expected in ((requests.Timeout(), 'timed out'),
                                (requests.ConnectionError(), 'Unable to reach'),
                                (ValueError(), 'Invalid CVE record')):
            with patch.object(plugin.requests, 'get', side_effect=error):
                self.assertIn(expected, self.plugin._get_cve_info('2024-12345'))
        self.assertIn('HTTP 429', self.fetch(status=429))
        self.assertIn('Invalid CVE record', self.fetch({'dataType': 'wrong'}))
        record = deepcopy(self.record)
        record['cveMetadata']['cveId'] = 'CVE-2024-9999'
        self.assertIn('Invalid CVE record', self.fetch(record))

    def testCommand(self):
        with patch.object(plugin.requests, 'get', return_value=Mock(
                status_code=200, json=Mock(return_value=self.record))):
            self.assertRegexp('cve 2024-12345', 'A serious vulnerability')


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
