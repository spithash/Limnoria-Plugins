"""NOAA report parsing tests, without network requests."""

from datetime import date
import json
import unittest

from supybot import ircutils

from . import plugin, reports


XRAY = json.dumps([{
    'time_tag': '2026-11-03T11:35:00Z', 'current_class': 'B2.9',
    'end_time': '2026-11-03T06:19:00Z',
    'max_time': '2026-11-03T06:15:00Z', 'max_class': 'M2.5',
}]).encode()

KINDEX = b''':Issued: 2026 Nov 03 0030 UTC
NOAA Kp index breakdown Nov 03-Nov 05 2026

             Nov 03       Nov 04       Nov 05
00-03UT       1.67         4.00         2.67
03-06UT       2.00         5.00 (G1)    3.00
06-09UT       1.67         6.00 (G2)    3.67
09-12UT       1.33         8.00 (G4)    2.67
12-15UT       0.67         1.33         2.67
15-18UT       0.67         0.67         1.67
18-21UT       1.67         0.67         1.67
21-00UT       1.33         1.67         2.67

Rationale: Example forecast.
'''

OUTLOOK = b''':Issued: 2026 Nov 02 0218 UTC
# UTC date, radio flux, planetary A index, largest Kp index
2026 Nov 02      95          10          3
2026 Nov 03     100           5          2
2026 Nov 04     150          20          5
2026 Nov 05     200          50          8
'''


class ReportTestCase(unittest.TestCase):
    def testXrayUsesObservationTimeRatherThanFlareEnd(self):
        line, = reports.xray(XRAY)
        self.assertEqual(ircutils.stripFormatting(line),
                         'X-ray status: B2.9 as of 03 Nov 2026 11:35 UTC; '
                         'latest reported flare peak: M2.5 at 03 Nov 2026 06:15 UTC')
        self.assertIn('\x0308M2.5\x03', line)

    def testFlareColors(self):
        for value, color in (('A1.0', '03'), ('B2.9', '03'), ('C1.2', '03'),
                             ('M2.5', '08'), ('X9.9', '04'), ('X10.0', '06')):
            self.assertEqual(reports._flare(value), '\x03%s%s\x03' % (color, value))
        with self.assertRaises(ValueError):
            reports._flare('No Report')

    def testKindexKeepsDecimalsAndHandlesStormAnnotations(self):
        lines = reports.kindex(KINDEX, plugin._color_index, plugin.K_COLORS)
        self.assertEqual(len(lines), 4)
        self.assertIn('00,03,06,09,12,15,18,21', lines[0])
        self.assertIn('Kp: 1.67 2 1.67 1.33 0.67 0.67 1.67 1.33',
                      ircutils.stripFormatting(lines[1]))
        self.assertIn('\x03084\x03', lines[2])
        self.assertIn('\x03045\x03', lines[2])
        self.assertIn('\x03128\x03', lines[2])
        with self.assertRaises(ValueError):
            reports.kindex(KINDEX.replace(b'21-00UT', b'22-00UT'),
                           plugin._color_index, plugin.K_COLORS)

    def testOutlookFiltersPastDaysAndColorsEveryDay(self):
        lines = reports.forecast(
            OUTLOOK, plugin._color_index, plugin.SFI_COLORS,
            plugin.A_COLORS, plugin.K_COLORS, today=date(2026, 11, 3))
        self.assertEqual(len(lines), 4)
        self.assertIn('2026-11-03 to 2026-11-05 UTC', lines[0])
        for line in lines[1:]:
            graph = ircutils.stripFormatting(line).split(': ', 1)[1].split(' |')[0]
            self.assertEqual(len(graph), 3)
        self.assertTrue(lines[1].endswith('range 2–8'))
        self.assertTrue(lines[2].endswith('range 5–50'))
        self.assertTrue(lines[3].endswith('range 100–200'))
        self.assertIn('\x0304', lines[3])  # SFI 100
        self.assertIn('\x0303', lines[3])  # SFI 150
        self.assertIn('\x0312', lines[3])  # SFI 200

    def testBadOrExpiredReportsAreRejected(self):
        for content in (b'[]', b'<html>', b'[{}]', b'null'):
            with self.subTest(content=content):
                with self.assertRaises((ValueError, KeyError)):
                    reports.xray(content)
        with self.assertRaises(ValueError):
            reports.forecast(OUTLOOK, plugin._color_index, plugin.SFI_COLORS,
                             plugin.A_COLORS, plugin.K_COLORS,
                             today=date(2026, 12, 1))
        with self.assertRaises(ValueError):
            reports.kindex(b'<html>', plugin._color_index, plugin.K_COLORS)
