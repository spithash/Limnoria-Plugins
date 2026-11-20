###
# Copyright (c) 2026, spithash @ Libera
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
from supybot import world
from types import SimpleNamespace
from unittest.mock import Mock, patch

from .plugin import SubReddit


class SubRedditTestCase(PluginTestCase):
    plugins = ('SubReddit',)

    def testOnlyRedditDispatcherIsPublicCommand(self):
        plugin = self.irc.getCallback('SubReddit')
        self.assertTrue(plugin.isCommandMethod('reddit'))
        self.assertFalse(plugin.isCommandMethod('_setkey'))
        self.assertFalse(plugin.isCommandMethod('_subscribe'))

    def testSubredditNameValidation(self):
        self.assertTrue(SubReddit.SUBREDDIT_RE.fullmatch('RTLSDR'))
        self.assertTrue(SubReddit.SUBREDDIT_RE.fullmatch('some_subreddit'))
        self.assertFalse(SubReddit.SUBREDDIT_RE.fullmatch('r/RTLSDR'))
        self.assertFalse(SubReddit.SUBREDDIT_RE.fullmatch('../bad'))

    def testMonthKeyUsesUtcYearAndMonth(self):
        from datetime import datetime, timezone
        now = datetime(2026, 11, 30, 23, 59, tzinfo=timezone.utc)
        self.assertEqual(SubReddit._month_key(now), '2026-11')
        self.assertEqual(SubReddit._seconds_to_next_month(now), 60)

    def testNetworkStateIsCaseInsensitiveAndSeparate(self):
        plugin = self.irc.getCallback('SubReddit')
        state = {'Libera': {'#same': ['one']}, 'OFTC': {'#same': ['two']}}
        self.assertEqual(plugin._network_state(state, 'libera')['#same'],
                         ['one'])
        self.assertEqual(plugin._network_state(state, 'oftc')['#same'],
                         ['two'])

    def testWhitespaceInApiKeyIsRejectedWithoutConverterCollision(self):
        plugin = self.irc.getCallback('SubReddit')
        reply = Mock()
        message = SimpleNamespace(args=['TestBot'])
        plugin._setkey(reply, message, 'rl_live_bad key')
        reply.error.assert_called_once()

    def testSubscriptionsAreDiscoveredAcrossNetworks(self):
        plugin = self.irc.getCallback('SubReddit')
        libera = SimpleNamespace(
            network='Libera', state=SimpleNamespace(channels={'##rtlsdr': {}}))
        oftc = SimpleNamespace(
            network='OFTC', state=SimpleNamespace(channels={'#radio': {}}))
        state = {
            'libera': {'##rtlsdr': ['rtlsdr']},
            'OFTC': {'#radio': ['amateurradio']},
        }
        with patch.object(plugin, '_load_json', return_value=state), \
                patch.object(world, 'ircs', [libera, oftc]):
            feeds = plugin._subscriptions()
        self.assertEqual(feeds['rtlsdr'], [(libera, '##rtlsdr')])
        self.assertEqual(feeds['amateurradio'], [(oftc, '#radio')])

    def testManualPullReportsSuccessfulBaseline(self):
        plugin = self.irc.getCallback('SubReddit')
        reply = Mock()
        feeds = {'rtlsdr': [(self.irc, '##rtlsdr')]}
        with patch.object(plugin, '_subscriptions', return_value=feeds), \
                patch.object(plugin, '_request_count',
                             return_value={'month': '2026-11', 'count': 0}), \
                patch.object(plugin, 'registryValue', return_value=990), \
                patch.object(plugin, '_fetch', return_value=0) as fetch, \
                patch.object(plugin, '_poll_interval', return_value=900):
            plugin._pull(reply)
        fetch.assert_called_once_with('rtlsdr', feeds['rtlsdr'])
        response = reply.reply.call_args.args[0]
        self.assertIn('1 succeeded', response)
        self.assertIn('0 new post(s) announced', response)


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
