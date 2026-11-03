"""Tests for credential-free space, location, aviation, and QSL helpers."""

import json
import re
import unittest

from . import extras, radio
from .test_radio import NOW


TLE = b'''ISS
1 25544U 98067A   26306.75682168  .00005437  00000-0  10620-3 0  9991
2 25544  51.6331 334.3796 0007124 340.7366  19.3352 15.49154261593232
'''
TRANSMITTERS = json.dumps([
    {'norad_cat_id': 25544, 'description': 'APRS', 'mode': 'AFSK',
     'alive': True, 'status': 'active', 'downlink_low': 145825000,
     'uplink_low': 145825000},
    {'norad_cat_id': 25544, 'description': 'Inactive', 'mode': 'FM',
     'alive': False, 'status': 'inactive', 'downlink_low': 145800000},
    {'norad_cat_id': 99999, 'description': 'Wrong satellite', 'mode': 'FM',
     'alive': True, 'status': 'active', 'downlink_low': 145800000},
]).encode()
LAUNCHES = json.dumps({'results': [
    {'name': 'Already launched', 'net': '2026-11-03T11:00:00Z', 'status': {'id': 3}},
    {'name': 'Falcon 9 | Example', 'net': '2026-11-04T12:00:00Z',
     'status': {'id': 1, 'name': 'Go for Launch'},
     'net_precision': {'name': 'Minute'},
     'launch_service_provider': {'name': 'SpaceX'},
     'pad': {'name': 'Pad 39A', 'location': {'name': 'Kennedy Space Center'}}},
]}).encode()
AIRCRAFT = json.dumps({'now': NOW * 1000, 'ac': [
    {'hex': '469c35', 'flight': 'TEST123 ', 'r': 'SX-ABC', 't': 'A320',
     'lat': 37.99, 'lon': 23.74, 'seen': 10, 'seen_pos': 10,
     'alt_baro': 12000, 'gs': 250},
    {'hex': '123456', 'flight': 'OLD', 'seen': 1000},
]}).encode()
METAR = b'2026/11/03 12:00\nLGAV 031200Z 20014KT 9999 SCT025 21/13 Q1010 NOSIG\n'
LOTW = b'W1AW,2026-11-02,12:30:00\nSV1ABC,2026-11-03,11:30:00\n'
EQSL = b'''<html><body>Grid Square FN31pr<br>
Effective dates at this QTH: 01 Jan 1992 to 31 Dec 2020<br>
I logged in on 07-Feb-2014<br>Last Activity:<br>
<img src="Graphics/AuthGuaranteed.gif"></body></html>'''
CALLBOOK = json.dumps({
    'status': 'VALID', 'type': 'CLUB',
    'current': {'callsign': 'W1AW', 'operClass': ''},
    'trustee': {'callsign': 'AF5ID'}, 'name': 'ARRL HQ OPERATORS CLUB',
    'address': {'attn': 'ATTN: JOSHUA D WELLS', 'line1': '225 MAIN ST',
                'line2': 'NEWINGTON, CT 06111'},
    'location': {'latitude': '41.714707', 'longitude': '-72.728411',
                 'gridsquare': 'FN31pr'},
}).encode()
FEST = b'''<html><form action="/hamfests/search"></form><ul><li>
<div class="img"></div><span>11/10/2026</span>
<a href="/hamfests/example-hamfest" title="Example Hamfest">More</a>
<strong>Location:</strong> Tampa, FL<br>
</li></ul></html>'''


class ExtraToolsTestCase(unittest.TestCase):
    def testCleanPreservesZero(self):
        self.assertEqual(radio.clean(0), '0')
        self.assertEqual(radio.clean(None), '')

    def testQthCoordinatesAndBearing(self):
        self.assertIn('37.50000,23.00000', extras.qth('KM17')[0])
        self.assertIn('Maidenhead KM17ux', extras.qth('37.98,23.73')[0])
        self.assertIn('initial bearing', extras.qth('KM17 de FN31')[0])
        self.assertRegex(extras.grid(90, 180), r'^[A-R]{2}\d{2}[a-x]{2}$')
        with self.assertRaises(ValueError):
            extras.qth('Athens')

    def testSunMoonAndPolarLocations(self):
        sun = extras.sky('Sun', 'KM17', NOW)
        self.assertIn('azimuth', sun[0])
        self.assertIn('next rise', sun[1])
        self.assertIn('UTC', sun[1])
        moon = extras.sky('Moon', 'KM17', NOW)
        self.assertIn('illuminated', moon[0])
        self.assertIn('distance', moon[0])
        for body in ('Sun', 'Moon'):
            self.assertEqual(len(extras.sky(body, '89,0', NOW)), 2)

    def testSatelliteLookupAndPasses(self):
        self.assertEqual(extras.satellite(TLE, 'ARISS')[1], 25544)
        self.assertEqual(extras.satellite(TLE, '25544')[0], 'ISS')
        lines = extras.satpass(TLE, 'ISS KM17 2', NOW)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[1].startswith('AOS '))
        for line in lines[1:]:
            peak = float(re.search(r'elev ([\d.]+)°', line)[1])
            self.assertGreaterEqual(peak, 5)
            self.assertIn('LOS ', line)
        for query in ('ISS KM17 99', 'ISS bad-grid'):
            with self.assertRaises(ValueError):
                extras.pass_options(query)
        with self.assertRaises(ValueError):
            extras.satpass(TLE, 'ISS KM17', NOW + 30 * 86400)
        with self.assertRaises(ValueError):
            extras.satellite(TLE, 'nonexistent')

    def testSatelliteFrequenciesExcludeOtherAndInactiveRecords(self):
        lines = extras.satinfo(TRANSMITTERS, 'ISS', 25544)
        self.assertEqual(len(lines), 2)
        self.assertIn('1 transmitters', lines[0])
        self.assertIn('down 145.825000 MHz', lines[1])
        self.assertIn('up 145.825000 MHz', lines[1])

    def testLaunchIgnoresFinishedLaunches(self):
        lines = extras.launch(LAUNCHES, NOW)
        self.assertIn('Falcon 9 | Example', lines[0])
        self.assertIn('04 Nov 12:00 UTC', lines[0])
        self.assertIn('SpaceX', lines[1])
        self.assertNotIn('Already launched', ' '.join(lines))

    def testAircraftQueriesAndFreshness(self):
        self.assertEqual(extras.adsb_query('0x469c35'), ('hex/469c35', None))
        self.assertEqual(extras.adsb_query('reg:N123AB'), ('reg/N123AB', None))
        self.assertEqual(extras.adsb_query('AAL100'), ('callsign/AAL100', None))
        self.assertTrue(extras.adsb_query('KM17')[0].startswith('point/'))
        lines = extras.adsb(AIRCRAFT, (37.98, 23.73), NOW)
        self.assertEqual(len(lines), 1)
        self.assertIn('12000 ft, speed 250 kt', lines[0])
        self.assertIn('TEST123', lines[0])
        self.assertIn('km from query', lines[0])
        ground = json.loads(AIRCRAFT)
        ground['ac'] = [{'hex': '123abc', 'r': 'N123AB', 'lat': 37.99,
                         'lon': 23.74, 'seen': 1, 'seen_pos': 1,
                         'alt_baro': 'ground', 'gs': 0}]
        self.assertIn('speed 0 kt', extras.adsb(json.dumps(ground), None, NOW)[0])
        with self.assertRaises(ValueError):
            extras.adsb(AIRCRAFT, None, NOW + 3600)
        with self.assertRaises(ValueError):
            extras.adsb_query('../../etc/passwd')

    def testMetarStationAndTimestamp(self):
        self.assertIn('LGAV 031200Z', extras.metar(METAR, 'LGAV')[0])
        self.assertIn('2026/11/03 12:00 UTC', extras.metar(METAR, 'LGAV')[0])
        with self.assertRaises(ValueError):
            extras.metar(METAR, 'KJFK')

    def testPublicQslSources(self):
        self.assertIn('W1AW: LoTW 2026-11-02 12:30:00', extras.lotw(LOTW, 'W1AW'))
        self.assertIn('not found', extras.lotw(LOTW, 'W2XYZ'))
        self.assertEqual(extras.eqsl(EQSL, 'W1AW'),
                         'W1AW: eQSL (AG) FN31pr; last login: 07-Feb-2014')
        self.assertIn('not registered', extras.eqsl(b'is not registered yet', 'W1AW'))
        with self.assertRaises(ValueError):
            extras.eqsl(b'<html>login required</html>', 'W1AW')

    def testPublicCallbookData(self):
        lines = extras.callbook(CALLBOOK, 'W1AW')
        self.assertEqual(len(lines), 3)
        self.assertIn('USA: ARRL HQ OPERATORS CLUB -- Club class -- trustee: AF5ID', lines[0])
        self.assertIn('225 MAIN ST; NEWINGTON, CT 06111; USA', lines[1])
        self.assertEqual(lines[2], 'FN31pr 41.714707, -72.728411 [src: FCC]')
        self.assertEqual(extras.callbook(b'{"status":"INVALID"}', 'K1BAD'), [])

    def testHamfestSearchAndExplicitEmptyResults(self):
        payload = extras.fest_payload('FL', NOW)
        self.assertIn(b'data%5BLocation%5D%5Bstate%5D=FL', payload)
        lines = extras.fest(FEST, 'FL', NOW)
        self.assertEqual(len(lines), 1)
        self.assertIn('2026-11-10: Example Hamfest | Tampa, FL', lines[0])
        self.assertIn('No ARRL-listed', extras.fest(
            b'<form action="/hamfests/search"></form>No records found matching your query.',
            'FL', NOW)[0])
        with self.assertRaises(ValueError):
            extras.fest(b'<html>unexpected</html>', 'FL', NOW)
        with self.assertRaises(ValueError):
            extras.fest_payload('Athens', NOW)

    def testHofhRandomIndexAndSearch(self):
        self.assertTrue(extras.hofh(None)[0].startswith('HoFH #'))
        self.assertIn('Only tube propagation', extras.hofh('1')[0])
        self.assertIn('tube', extras.hofh('tube')[0].lower())
        with self.assertRaises(ValueError):
            extras.hofh('99999')
