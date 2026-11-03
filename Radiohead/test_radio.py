"""Tests for offline tools and external radio-data parsers."""

from datetime import datetime, timezone
import json
import unittest

from . import radio


NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc).timestamp()
STATIONS = json.dumps([
    {'station': {'name': 'Old nearby', 'code': 'OLD', 'latitude': '37.9', 'longitude': '23.7'},
     'time': NOW - 7200, 'cs': 100, 'fof2': 10, 'mufd': 30},
    {'station': {'name': 'Low confidence', 'code': 'LOW', 'latitude': '37.9', 'longitude': '23.7'},
     'time': NOW - 300, 'cs': 10, 'fof2': 10, 'mufd': 30},
    {'station': {'name': 'Athens', 'code': 'AT138', 'latitude': '38', 'longitude': '23.5'},
     'time': NOW - 600, 'cs': 95, 'fof2': 7.5, 'mufd': 24},
    {'station': {'name': 'Distant', 'code': 'OTHER', 'latitude': '-30', 'longitude': '262.3'},
     'time': NOW - 300, 'cs': 100, 'fof2': 8, 'mufd': 25},
]).encode()
POTA = json.dumps([
    {'activator': 'SV1ABC/P', 'frequency': '14063.5', 'mode': 'CW',
     'reference': 'GR-0001', 'name': 'Example Park', 'locationDesc': 'GR',
     'spotTime': NOW - 60, 'spotter': 'G3AAA', 'invalid': None},
    {'activator': 'W1AW', 'frequency': '7074', 'mode': 'FT8',
     'reference': 'US-0001', 'name': 'Another Park', 'locationDesc': 'US',
     'spotTime': NOW - 120, 'spotter': 'W2ABC'},
    {'activator': 'SV1OLD', 'frequency': '14063', 'mode': 'CW',
     'reference': 'GR-0002', 'spotTime': NOW - 7200},
]).encode()
PSK = ('<receptionReports><receptionReport senderCallsign="SV1ABC/P" '
       'receiverCallsign="G3AAA" receiverLocator="IO91" '
       'frequency="14063500" mode="CW" flowStartSeconds="%d" />'
       '</receptionReports>' % (NOW - 50)).encode()
ACTIVITY = b'# frequency score extra columns\n14074000 10 1 2\n14075000 5 1 2\n7074000 3 1 2\n'
CALENDAR = b'''BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART:20261103T110000Z
DTEND:20261103T130000Z
SUMMARY:Current\\, contest
URL:https://www.contestcalendar.com/contestdetails.php?ref=1
END:VEVENT
BEGIN:VEVENT
DTSTART:20261104T110000Z
DTEND:20261104T130000Z
SUMMARY:Upcoming long contest
 continued title
END:VEVENT
BEGIN:VEVENT
DTSTART:20261101T110000Z
DTEND:20261101T130000Z
SUMMARY:Finished contest
END:VEVENT
END:VCALENDAR
'''


class RadioToolsTestCase(unittest.TestCase):
    def testMorseRoundTripAndGreek(self):
        encoded, = radio.morse('CQ TEST / 73?')
        self.assertEqual(radio.unmorse(encoded), ['CQ TEST / 73?'])
        self.assertEqual(radio.morse('SOS'), ['... --- ...'])
        self.assertEqual(radio.unmorse('... --- ...   --... ...--'), ['SOS 73'])
        self.assertEqual(radio.unmorse('--greek .- -...'), ['ΑΒ'])
        self.assertEqual(radio.unmorse('--cyrillic .- -...'), ['АБ'])
        for value in ('--unknown ...', '..........'):
            with self.assertRaises(ValueError):
                radio.unmorse(value)
        with self.assertRaises(ValueError):
            radio.morse('hello 😀')

    def testQcodeLookupAndSearch(self):
        self.assertTrue(radio.qcode('qrm?')[0].startswith('QRM:'))
        self.assertIn('interfered', radio.qcode('QRM')[0].lower())
        matches = radio.qcode('interference')
        self.assertLessEqual(len(matches), 5)
        self.assertEqual(radio.qcode('no-such-qcode-zzzzz'), ['No matching Q-codes.'])
        self.assertEqual(len(radio.table('qcodes')), 290)

    def testCoaxUnitsAndInterpolation(self):
        # Table value: LMR-400 is 0.9 dB per 100 ft at 50 MHz.
        self.assertIn('estimated loss 0.90 dB', radio.coax('LMR-400 50 MHz 100 ft')[0])
        self.assertIn('estimated loss 0.90 dB', radio.coax('lmr400 50000 kHz 30.48 m')[0])
        self.assertIn('estimated loss 1.80 dB', radio.coax('LMR-400 220MHz 100ft')[0])
        self.assertIn('estimated loss 0.00 dB', radio.coax('LMR-400 50MHz 0m')[0])
        # Midpoint of the 30 and 50 MHz values, no extrapolation.
        self.assertIn('estimated loss 0.80 dB', radio.coax('LMR-400 40MHz')[0])
        for value in ('LMR-400 1MHz', 'LMR-400 10000MHz', 'LMR-400 50MHz -2m',
                      'madeup 50MHz', 'RG-58 50MHz 20watts'):
            with self.assertRaises(ValueError):
                radio.coax(value)
        self.assertIn('RG-58/U', radio.coax('RG58 50MHz')[0])
        self.assertIn('LMR-400', radio.coax('list')[0])

    def testGridCentersAndCoordinateValidation(self):
        self.assertEqual(radio.coordinates('KM17'), (37.5, 23.0))
        self.assertEqual(radio.coordinates('AA00'), (-89.5, -179.0))
        self.assertEqual(radio.coordinates('RR99'), (89.5, 179.0))
        self.assertEqual(radio.coordinates('37.98,23.73'), (37.98, 23.73))
        lat, lon = radio.coordinates('KM17ux')
        self.assertAlmostEqual(lat, 37.9791667)
        self.assertAlmostEqual(lon, 23.7083333)
        for value in ('ZZ99', 'KM179', 'nan,1', '91,0', '0,181', 'Athens'):
            with self.assertRaises(ValueError):
                radio.coordinates(value)

    def testIonosondeChoosesFreshConfidentStation(self):
        line, = radio.iono(STATIONS, 'KM17', NOW)
        self.assertIn('Athens (AT138)', line)
        self.assertIn('10 min old', line)
        self.assertIn('MUF(D) 24.00 MHz, foF2 7.50 MHz', line)
        self.assertNotIn('Old nearby', line)
        self.assertIn('No ionosonde', radio.iono(STATIONS, 'KM17', NOW + 7200)[0])

    def testPotaFiltersAndFrequencies(self):
        lines = radio.potaspots(POTA, '--20m --cw 2', NOW)
        self.assertEqual(len(lines), 1)
        self.assertIn('SV1ABC/P | 14063.5 kHz CW', lines[0])
        self.assertIn('GR-0001', lines[0])
        self.assertIn('W1AW', radio.potaspots(POTA, '40m DIGI', NOW)[0])
        self.assertIn('No matching', radio.potaspots(POTA, '10m PHONE', NOW)[0])
        for value in ('20m 99', 'unknown', 'CW -3'):
            with self.assertRaises(ValueError):
                radio.spot_options(value)

    def testPSKSpotsConvertHertzToKilohertz(self):
        rows = radio.psk_rows(PSK, 'SV1ABC/P', NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['frequency'], 14063.5)
        self.assertIn('G3AAA', radio.format_spot(rows[0]))
        self.assertEqual(radio.psk_rows(PSK, 'W1AW', NOW), [])

    def testActivityAggregatesByBandAndRejectsHTML(self):
        self.assertEqual(radio.activity_query('FT8 KM17'), ('FT8', 'KM17'))
        self.assertEqual(radio.activity_query('KM'), ('', 'KM'))
        self.assertEqual(radio.activity_query('FM KM17'), ('FM', 'KM17'))
        line, = radio.activity(ACTIVITY, 'FT8', 'KM17')
        self.assertIn('40m=3', line)
        self.assertIn('20m=15', line)
        with self.assertRaises(ValueError):
            radio.activity(b'<html>Challenge</html>', 'FT8', 'KM17')
        with self.assertRaises(ValueError):
            radio.activity_query('FT8 KM17ux')

    def testContestTimesFoldingAndExpiredEvents(self):
        lines = radio.contests(CALENDAR, NOW)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith('NOW: Current, contest'))
        self.assertTrue(lines[1].startswith('NEXT: Upcoming long contestcontinued title'))
        self.assertIn('03 Nov 11:00 UTC to 03 Nov 13:00 UTC', lines[0])
        with self.assertRaises(ValueError):
            radio.contests(b'<html>', NOW)

    def testExternalTextCannotInjectIRCFormatting(self):
        self.assertEqual(radio.clean('hello\r\n\x0304world'), 'hello 04world')
