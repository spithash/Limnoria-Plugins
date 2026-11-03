###
# Copyright (c) 2026, Stathis Xantinidis @ https://github.com/spithash
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
from unittest.mock import patch
import json
import time

from . import plugin
from .test_reports import ReportTestCase, XRAY, KINDEX
from .test_radio import (RadioToolsTestCase, NOW, STATIONS, POTA, PSK,
                         ACTIVITY, CALENDAR)
from .test_extras import (ExtraToolsTestCase, TLE, TRANSMITTERS, LAUNCHES,
                          AIRCRAFT, METAR, LOTW, EQSL, FEST, CALLBOOK)


FEED = b'''<solar><solardata>
<updated> 03 Nov 2026 1103 GMT</updated>
<solarflux>97</solarflux><sunspots>34</sunspots>
<aindex> 7</aindex><kindex> 2</kindex>
<calculatedconditions>
<band name="80m-40m" time="day">Fair</band>
<band name="30m-20m" time="day">Good</band>
<band name="17m-15m" time="day">Fair</band>
<band name="12m-10m" time="day">Poor</band>
<band name="80m-40m" time="night">Good</band>
<band name="30m-20m" time="night">Good</band>
<band name="17m-15m" time="night">Fair</band>
<band name="12m-10m" time="night">Poor</band>
</calculatedconditions></solardata></solar>'''

EFFECTIVE = json.dumps({'24h': [
    {'time': time.time(), 'sfi': 89.5, 'ssn': 35.3}
], '6h': [{'time': time.time(), 'sfi': 91.0, 'ssn': 38.0}]}).encode()


def feed_for_url(url, **kwargs):
    return FEED if 'hamqsl.com' in url else EFFECTIVE


class RadioheadTestCase(PluginTestCase):
    plugins = ('Radiohead',)

    def testSolarSummaryReusesBandsCache(self):
        with patch.object(plugin.utils.web, 'getUrl', side_effect=feed_for_url) as fetch:
            self.assertRegexp('solar', r'^Solar indices as of .*eSFI.*eSSN')
            self.assertRegexp('solar', r'^Solar indices as of')
            self.assertEqual(fetch.call_count, 2)

    def testLaunchAndSpaceXCommandsShareSearchCache(self):
        with patch.object(plugin.time, 'time', return_value=NOW):
            with patch.object(plugin.utils.web, 'getUrl', return_value=LAUNCHES) as fetch:
                for command in ('spacex', 'launch SpaceX'):
                    self.assertRegexp(command, r'Falcon 9.*04 Nov 12:00 UTC')
                    self.assertIn('SpaceX', self.irc.takeMsg().args[1])
                self.assertEqual(fetch.call_count, 1)
                self.assertError('launch Rocket Lab')  # Global upstream quota.

    def testSatelliteCommands(self):
        def source(url, **kwargs):
            return TRANSMITTERS if 'satnogs' in url else TLE
        with patch.object(plugin.time, 'time', return_value=NOW):
            with patch.object(plugin.utils.web, 'getUrl', side_effect=source):
                self.assertRegexp('satinfo ISS', r'ISS .*NORAD 25544')
                self.assertIn('145.825000 MHz', self.irc.takeMsg().args[1])
                self.assertRegexp('satinfo 25544', r'NORAD 25544')
                self.irc.takeMsg()
                self.assertRegexp('satpass ISS KM17 1', r'ISS .*passes at KM17')
                self.assertTrue(self.irc.takeMsg().args[1].startswith('AOS '))

    def testPublicQslCommandAndPartialFailure(self):
        def source(url, **kwargs):
            if 'callook' in url:
                return CALLBOOK
            return LOTW if 'lotw' in url else EQSL
        with patch.object(plugin.utils.web, 'getUrl', side_effect=source):
            self.assertRegexp('qsl W1AW', r'W1AW: USA: .*trustee: AF5ID')
            self.assertIn('225 MAIN ST', self.irc.takeMsg().args[1])
            self.assertIn('[src: FCC]', self.irc.takeMsg().args[1])
            self.assertIn('W1AW: LoTW 2026-11-02 12:30:00',
                          self.irc.takeMsg().args[1])
            self.assertEqual(self.irc.takeMsg().args[1],
                             'W1AW: eQSL (AG) FN31pr; last login: 07-Feb-2014')
        cb = self.irc.getCallback('Radiohead')
        cb._radio_cache.clear()
        with patch.object(plugin.utils.web, 'getUrl',
                          side_effect=[CALLBOOK, plugin.utils.web.Error('503'), EQSL]):
            self.assertRegexp('qsl W1AW', r'W1AW: USA: .*trustee: AF5ID')
            self.irc.takeMsg()
            self.irc.takeMsg()
            self.assertIn('lotw data unavailable', self.irc.takeMsg().args[1])
            self.assertEqual(self.irc.takeMsg().args[1],
                             'W1AW: eQSL (AG) FN31pr; last login: 07-Feb-2014')

    def testAviationCommands(self):
        with patch.object(plugin.time, 'time', return_value=NOW):
            with patch.object(plugin.utils.web, 'getUrl', return_value=AIRCRAFT):
                self.assertRegexp('adsb 0x469c35', r'TEST123.*12000 ft')
            with patch.object(plugin.utils.web, 'getUrl', return_value=METAR):
                self.assertRegexp('metar LGAV', r'LGAV 031200Z.*12:00 UTC')

    def testHofhQthAndSkyCommands(self):
        self.assertRegexp('hofh 1', r'HoFH #1: Only tube propagation')
        self.assertRegexp('qth KM17 de FN31', r'37.50000,23.00000.*initial bearing')
        for command, label in (('sun KM17', 'Sun'), ('moon KM17', 'Moon')):
            self.assertRegexp(command, label + r' at KM17.*azimuth.*altitude')
            self.assertIn('next rise', self.irc.takeMsg().args[1])

    def testFestPostsStateSearch(self):
        with patch.object(plugin.time, 'time', return_value=NOW):
            with patch.object(plugin.utils.web, 'getUrl', return_value=FEST) as fetch:
                self.assertRegexp('fest FL', r'Example Hamfest.*Tampa, FL')
                self.assertIn(b'data%5BLocation%5D%5Bstate%5D=FL', fetch.call_args.kwargs['data'])

    def testExtraValidationDoesNotFetch(self):
        with patch.object(plugin.utils.web, 'getUrl') as fetch:
            for command in ('metar ../../bad', 'satpass ISS bad-grid', 'fest ZZ',
                            'qsl bad!call', 'qth Athens', 'sun bad-grid', 'adsb ../../bad'):
                self.assertError(command)
            fetch.assert_not_called()

    def testWWVPrivateMessageRejected(self):
        with patch.object(plugin.schedule, 'addEvent') as add:
            self.assertError('wwv')
            add.assert_not_called()

    def testOfflineRadioCommands(self):
        self.assertResponse('morse SOS', '... --- ...')
        self.assertResponse('cw CQ', '-.-. --.-')
        self.assertResponse('unmorse ... --- ... / --... ...--', 'SOS 73')
        self.assertResponse('demorse ... --- ...', 'SOS')
        self.assertResponse('unmorse --greek .- -...', 'ΑΒ')
        self.assertRegexp('qcode QRM', r'^QRM: .*interfered')
        self.assertRegexp('coax LMR-400 50MHz 100ft', r'estimated loss 0.90 dB')
        self.assertRegexp('atten LMR-400 50MHz 100ft', r'estimated loss 0.90 dB')
        self.assertRegexp('coax list', r'Cable types: .*LMR-400')

    def testIonoCommand(self):
        with patch.object(plugin.time, 'time', return_value=NOW):
            with patch.object(plugin.utils.web, 'getUrl', return_value=STATIONS) as fetch:
                self.assertRegexp('iono KM17', r'Nearest reporting ionosonde .*Athens')
                self.assertRegexp('iono 37.98,23.73', r'Nearest reporting ionosonde .*Athens')
                self.assertEqual(fetch.call_count, 1)

    def testPotaCommand(self):
        with patch.object(plugin.time, 'time', return_value=NOW):
            with patch.object(plugin.utils.web, 'getUrl', return_value=POTA) as fetch:
                self.assertRegexp('potaspots 20m CW 1', r'SV1ABC/P.*14063.5 kHz CW')
                self.assertRegexp('potaspots --40m --digi 1', r'W1AW.*7074 kHz FT8')
                self.assertEqual(fetch.call_count, 1)

    def testSpotsCommandAndGlobalPSKThrottle(self):
        def source(url, **kwargs):
            return PSK if 'pskreporter' in url else POTA
        with patch.object(plugin.time, 'time', return_value=NOW):
            with patch.object(plugin.utils.web, 'getUrl', side_effect=source) as fetch:
                self.assertRegexp('spots SV1ABC/P 1', r'SV1ABC/P.*14063.5 kHz CW.*PSK Reporter')
                self.assertRegexp('spots SV1ABC/P 1', r'SV1ABC/P.*PSK Reporter')
                self.assertEqual(fetch.call_count, 2)
                self.assertRegexp('spots W1AW 1', r'W1AW.*POTA')
                notice = self.irc.takeMsg()
                self.assertIsNotNone(notice)
                self.assertIn('PSK Reporter', notice.args[1])
                self.assertEqual(fetch.call_count, 2)

    def testSpotsWorksWhenPSKIsBlocked(self):
        with patch.object(plugin.time, 'time', return_value=NOW):
            with patch.object(plugin.utils.web, 'getUrl',
                              side_effect=[plugin.utils.web.Error('HTTP 403'), POTA]):
                self.assertRegexp('spots SV1ABC/P 1', r'SV1ABC/P.*POTA')
                notice = self.irc.takeMsg()
                self.assertIn('Unavailable sources: PSK Reporter', notice.args[1])

    def testActivityCommand(self):
        with patch.object(plugin.utils.web, 'getUrl', return_value=ACTIVITY) as fetch:
            self.assertRegexp('activity FT8 KM17', r'FT8 from grid KM17.*40m=3, 20m=15')
            self.assertRegexp('activity FT8 KM17', r'20m=15')
            self.assertEqual(fetch.call_count, 1)

    def testContestsCommand(self):
        with patch.object(plugin.time, 'time', return_value=NOW):
            with patch.object(plugin.utils.web, 'getUrl', return_value=CALENDAR):
                self.assertRegexp('contests', r'^NOW: Current, contest')
                upcoming = self.irc.takeMsg()
                self.assertIsNotNone(upcoming)
                self.assertTrue(upcoming.args[1].startswith('NEXT: Upcoming'))

    def testRadioValidationDoesNotFetch(self):
        with patch.object(plugin.utils.web, 'getUrl') as fetch:
            for command in ('iono bad-grid', 'spots bad!call', 'potaspots 99',
                            'activity FT8 KM17ux', 'coax fake 50MHz',
                            'morse 😀', 'unmorse ............'):
                self.assertError(command)
            fetch.assert_not_called()

    def testRadioNetworkFailuresAndRecovery(self):
        with patch.object(plugin.utils.web, 'getUrl',
                          side_effect=plugin.utils.web.Error('HTTP 403')) as fetch:
            self.assertError('activity FT8 KM17')
            self.assertError('activity FT8 KM17')
            self.assertEqual(fetch.call_count, 1)
        for command in ('iono KM17', 'potaspots', 'contests'):
            with patch.object(plugin.utils.web, 'getUrl', return_value=b'<html>'):
                self.assertError(command)

    def testXrayCommandCachesResults(self):
        with patch.object(plugin.utils.web, 'getUrl', return_value=XRAY) as fetch:
            for unused in range(2):
                self.assertRegexp('xray', r'X-ray status: .*B2.9.*11:35 UTC.*M2.5')
            fetch.assert_called_once_with(
                'https://services.swpc.noaa.gov/json/goes/primary/xray-flares-latest.json',
                timeout=15, size=262144)

    def testKindexAliasesShareCache(self):
        with patch.object(plugin.utils.web, 'getUrl', return_value=KINDEX) as fetch:
            for command in ('kindex', 'ki', 'kf'):
                self.assertRegexp(command, r'Kp forecast issued .*0030 UTC')
                for day in ('Nov 03', 'Nov 04', 'Nov 05'):
                    response = self.irc.takeMsg()
                    self.assertIsNotNone(response)
                    self.assertTrue(response.args[1].startswith(day + ':'))
            self.assertEqual(fetch.call_count, 1)

    def testReportErrors(self):
        for command in ('xray', 'kindex', 'forecast'):
            for failure in (b'<html>', plugin.utils.web.Error('timeout')):
                with self.subTest(command=command, failure=failure):
                    with patch.object(plugin.utils.web, 'getUrl',
                                      side_effect=[failure]):
                        self.assertError(command)

    def testBands(self):
        with patch.object(plugin.utils.web, 'getUrl', side_effect=feed_for_url) as fetch:
            for unused in range(2):
                self.assertResponse(
                    'bands',
                    'Bands as of \x0203 Nov 2026 1103 GMT\x02: '
                    '\x02SFI\x02=\x030497\x03 \x02SN\x02=34 '
                    '\x02A\x02=\x03037\x03 \x02K\x02=\x03032\x03 '
                    '\x02eSFI\x02=\x030489.5\x03 '
                    '\x02eSSN\x02=35.3')
                for expected in (
                    '| 12m-10m | day: \x0304Poor\x03 | night: \x0304Poor\x03 |',
                    '| 17m-15m | day: \x0308Fair\x03 | night: \x0308Fair\x03 |',
                    '| 30m-20m | day: \x0303Good\x03 | night: \x0303Good\x03 |',
                    '| 80m-40m | day: \x0308Fair\x03 | night: \x0303Good\x03 |',
                ):
                    response = self.irc.takeMsg()
                    self.assertIsNotNone(response)
                    self.assertEqual(response.args[1], expected)
            self.assertEqual(fetch.call_count, 2)
            fetch.assert_any_call(
                'https://www.hamqsl.com/solarxml.php',
                timeout=15, size=65536)
            fetch.assert_any_call(
                'https://prop.kc2g.com/api/essn.json?days=1',
                timeout=10, size=262144)

    def testUnavailableFeed(self):
        with patch.object(plugin.utils.web, 'getUrl',
                          side_effect=plugin.utils.web.Error('timeout')):
            self.assertError('bands')

    def testInvalidFeed(self):
        with patch.object(plugin.utils.web, 'getUrl', return_value=b'<html>'):
            self.assertError('bands')

    def testIncompleteFeedCanRecover(self):
        with patch.object(plugin.utils.web, 'getUrl', side_effect=[
                b'<solar><solardata/></solar>', FEED, EFFECTIVE]) as fetch:
            self.assertError('bands')
            self.assertRegexp('bands', r'Bands as of .*1103 GMT\x02: ')
            for unused in range(4):
                self.irc.takeMsg()
            self.assertEqual(fetch.call_count, 3)

    def testEffectiveIndicesUnavailable(self):
        for unavailable in (plugin.utils.web.Error('timeout'), b'<html>',
                            b'{"24h": []}', b'{"24h": null}',
                            b'{"24h":[{"time":0,"sfi":89.5,"ssn":35.3}]}',
                            b'{"24h":[{"time":null}]}'):
            with self.subTest(response=unavailable):
                self.irc.getCallback('Radiohead')._bands_cache = None
                with patch.object(plugin.utils.web, 'getUrl',
                                  side_effect=[FEED, unavailable]):
                    self.assertRegexp(
                        'bands', r'Bands as of .*eSFI\x02=N/A .*eSSN\x02=N/A')
                    for unused in range(4):
                        self.assertIsNotNone(self.irc.takeMsg())

    def testSolarColors(self):
        # Expected palette from qrmbot, testing both sides of every boundary.
        boundaries = [(85, '05', '04'), (105, '04', '07'),
                      (125, '07', '08'), (145, '08', '03'),
                      (165, '03', '09'), (185, '09', '12'),
                      (205, '12', '10'), (225, '10', '11'),
                      (235, '11', '06')]
        for boundary, before, after in boundaries:
            for value, color in ((boundary - 0.1, before), (boundary, after)):
                self.assertEqual(
                    plugin._color_index(str(value), plugin.SFI_COLORS),
                    '\x03%s%s\x03' % (color, value))
        for value in ('No Report', '-1', 'nan', 'inf'):
            self.assertEqual(
                plugin._color_index(value, plugin.SFI_COLORS), value)

    def testIndexColors(self):
        for scale, maximum, cases in (
            (plugin.A_COLORS, 400,
             [('0', '03'), ('19.9', '03'), ('20', '08'), ('29.9', '08'),
              ('30', '04'), ('49.9', '04'), ('50', '05'), ('99.9', '05'),
              ('100', '12'), ('400', '12')]),
            (plugin.K_COLORS, 9,
             [('0', '03'), ('3.9', '03'), ('4', '08'), ('4.9', '08'),
              ('5', '04'), ('7.9', '04'), ('8', '12'), ('9', '12')]),
        ):
            for value, color in cases:
                with self.subTest(value=value, maximum=maximum):
                    self.assertEqual(
                        plugin._color_index(value, scale, maximum),
                        '\x03%s%s\x03' % (color, value))
            for value in ('No Report', '-1', 'nan', str(maximum + 1)):
                self.assertEqual(
                    plugin._color_index(value, scale, maximum), value)


class RadioheadChannelTestCase(ChannelPluginTestCase):
    plugins = ('Radiohead',)

    def testWWVMinuteBoundaryAndDuplicateSuppression(self):
        # Two seconds before the minute: leave enough time for a four-second
        # announcement by choosing the following minute instead.
        with patch.object(plugin, 'time', wraps=plugin.time) as clock:
            clock.time.return_value = NOW + 58
            with patch.object(plugin.schedule, 'addEvent') as add:
                self.assertRegexp('wwv', r'WWV text tone scheduled')
                self.assertEqual(add.call_count, 2)
                announce, beep = add.call_args_list
                self.assertEqual(announce.args[1], NOW + 116)
                self.assertEqual(beep.args[1], NOW + 120)
                self.assertRegexp('wwv', r'already scheduled')
                self.assertEqual(add.call_count, 2)
                announce.args[0]()
                self.assertIn('12 hours 02 minutes', self.irc.takeMsg().args[1])
                beep.args[0]()
                self.assertEqual(self.irc.takeMsg().args[1], '<beep>')
                self.assertEqual(self.irc.getCallback('Radiohead')._wwv_events, {})

    def testWWVEventsCancelledOnUnload(self):
        with patch.object(plugin.schedule, 'addEvent') as add:
            self.assertRegexp('wwv', r'WWV text tone scheduled')
            with patch.object(plugin.schedule, 'removeEvent') as remove:
                self.irc.getCallback('Radiohead').die()
                self.assertEqual(remove.call_count, 2)
                self.assertEqual({call.args[0] for call in remove.call_args_list},
                                 {call.kwargs['name'] for call in add.call_args_list})


# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
