"""Offline radio tools and parsers for community radio data sources."""

from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import html
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def clean(value, limit=160):
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', html.unescape(
        str(value if value is not None else '')))
    return ' '.join(value.split())[:limit]


@lru_cache(maxsize=3)
def table(name):
    return json.loads(Path(__file__).with_name(name + '.json').read_text())


def morse(text):
    mapping = table('morse')['encode']
    text = text.upper()
    if not text.strip():
        raise ValueError('Provide text to encode.')
    if len(text) > 120:
        raise ValueError('Use at most 120 characters per Morse request.')
    unsupported = sorted(set(text) - set(mapping) - set(' \t\n'))
    if unsupported:
        raise ValueError('Unsupported Morse characters: ' + clean(''.join(unsupported)))
    return [' / '.join(' '.join(mapping[c] for c in word) for word in text.split())]


def unmorse(text):
    language = 'intl'
    names = {'--greek': 'greek', '--russian': 'cyrillic', '--cyrillic': 'cyrillic',
             '--hebrew': 'hebrew',
             '--japanese': 'japanese', '--wabun': 'japanese'}
    if text.startswith('--'):
        option, sep, text = text.partition(' ')
        language = names.get(option.lower())
        if language is None:
            raise ValueError('Unknown Morse alphabet.')
    if len(text) > 700:
        raise ValueError('Use at most 700 Morse symbols per request.')
    mapping = table('morse')['decode'][language]
    text = re.sub(r'\s{3,}', ' / ', text.replace('_', '-').strip())
    tokens = re.findall(r'/|[^\s/]+', text)
    unknown = sorted(set(tokens) - set(mapping))
    if unknown:
        raise ValueError('Unknown Morse symbols: ' + clean(' '.join(unknown)))
    if not tokens:
        raise ValueError('Provide Morse letters separated by spaces; use / between words.')
    return [''.join(mapping[token] for token in tokens)]


def qcode(query):
    codes = table('qcodes')
    query = query.strip()
    key = query.upper().rstrip('?')
    if key in codes:
        return [key + ': ' + clean(codes[key], 1400)]
    if len(query) < 2:
        raise ValueError('Use a Q-code or a search term of at least two characters.')
    matches = [(code, text) for code, text in sorted(codes.items())
               if query.casefold() in (code + ' ' + text).casefold()]
    if not matches:
        return ['No matching Q-codes.']
    lines = [code + ': ' + clean(text, 220) for code, text in matches[:4]]
    if len(matches) > 4:
        lines.append('%d matches total; narrow your search.' % len(matches))
    return lines


def coax(text):
    cables = table('coax')
    if text.lower().strip() == 'list':
        return ['Cable types: ' + ', '.join(sorted(cables))]
    match = re.fullmatch(
        r'(\S+)(?:\s+([\d.]+)\s*(GHz|MHz|kHz|Hz))?'
        r'(?:\s+([\d.]+)\s*(m|meters?|metres?|ft|feet))?', text.strip(), re.I)
    if not match:
        raise ValueError('Usage: coax <type> <frequency> MHz [<length> m|ft]; or coax list.')
    name, frequency, freq_unit, length, length_unit = match.groups()
    aliases = {key.upper().replace('-', ''): key for key in cables}
    aliases['RG58'] = 'RG-58/U'
    name = aliases.get(name.upper().replace('-', ''))
    if name is None:
        raise ValueError('Unknown cable type. Use coax list.')
    points = cables[name]
    if frequency is None:
        return [name + ' nominal loss (dB/100 ft): ' + '; '.join(
            '%g MHz: %g' % (f, loss) for f, loss in points)]
    frequency = float(frequency) * {'ghz': 1000, 'mhz': 1,
                                   'khz': .001, 'hz': .000001}[freq_unit.lower()]
    if not points[0][0] <= frequency <= points[-1][0]:
        raise ValueError('Supported frequency range for %s: %g–%g MHz.'
                         % (name, points[0][0], points[-1][0]))
    length = float(length) if length is not None else 100.0
    length_unit = length_unit.lower() if length_unit else 'ft'
    if not math.isfinite(length) or length < 0 or length > 1000000:
        raise ValueError('Length must be between 0 and 1000000.')
    length_ft = length if length_unit in ('ft', 'feet') else length / .3048
    low = max(point for point in points if point[0] <= frequency)
    high = min(point for point in points if point[0] >= frequency)
    loss = low[1] if low[0] == high[0] else (
        low[1] + (high[1] - low[1]) * (frequency - low[0]) / (high[0] - low[0]))
    total = loss * length_ft / 100
    return ['%s at %g MHz, %g %s: estimated loss %.2f dB '
            '(%.1f%% power remaining); nominal %.2f dB/100 ft. '
            'Matched cable only; connectors excluded.'
            % (name, frequency, length, length_unit, total,
               100 * 10 ** (-total / 10), loss)]


def coordinates(query):
    query = query.strip().upper()
    if ',' in query:
        try:
            lat, lon = map(float, query.split(','))
        except ValueError:
            raise ValueError('Use a Maidenhead grid (KM17) or latitude,longitude.') from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError('Latitude must be -90..90; longitude -180..180.')
        return lat, lon
    if not re.fullmatch(r'[A-R]{2}(?:\d{2}(?:[A-X]{2}(?:\d{2})?)?)?', query):
        raise ValueError('Use a 2, 4, 6, or 8-character grid, or latitude,longitude.')
    lon, lat = -180.0, -90.0
    lon_width, lat_width = 360.0, 180.0
    for offset, base in ((0, 18), (2, 10), (4, 24), (6, 10)):
        if offset >= len(query):
            break
        lon_width, lat_width = lon_width / base, lat_width / base
        first, second = query[offset:offset + 2]
        x, y = ((int(first), int(second)) if base == 10 else
                (ord(first) - ord('A'), ord(second) - ord('A')))
        lon += x * lon_width
        lat += y * lat_width
    return lat + lat_width / 2, lon + lon_width / 2


def timestamp(value):
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError('Invalid observation time')
        return float(value)
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime('%d %b %H:%M UTC')


def _distance(lat1, lon1, lat2, lon2):
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    delta = math.radians(lon2 - lon1)
    value = math.sin((lat2 - lat1) / 2) ** 2 + (
        math.cos(lat1) * math.cos(lat2) * math.sin(delta / 2) ** 2)
    return 12742 * math.asin(math.sqrt(min(1, max(0, value))))


def iono(content, query, now):
    lat, lon = coordinates(query)
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError('Invalid KC2G station data')
    candidates = []
    for row in data:
        try:
            station = row['station']
            age = now - timestamp(row['time'])
            if not -300 <= age <= 3600 or (row.get('cs') is not None and float(row['cs']) <= 20):
                continue
            measurements = []
            for key, label in (('mufd', 'MUF(D)'), ('fof2', 'foF2')):
                value = float(row[key]) if row.get(key) is not None else 0
                if math.isfinite(value) and value > 0:
                    measurements.append('%s %.2f MHz' % (label, value))
            if not measurements:
                continue
            slat, slon = float(station['latitude']), float(station['longitude'])
            slon = (slon + 180) % 360 - 180
            if not -90 <= slat <= 90 or not math.isfinite(slon):
                continue
            distance = _distance(lat, lon, slat, slon)
            candidates.append((distance, row, station, measurements, age))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    if not candidates:
        return ['No ionosonde with usable measurements and confidence reported '
                'within the last hour. Source: prop.kc2g.com']
    distance, row, station, values, age = min(candidates, key=lambda item: item[0])
    confidence = 'unknown' if row.get('cs') is None else '%g' % float(row['cs'])
    return ['Nearest reporting ionosonde to %s: %s (%s), %.0f km; %s '
            '(%d min old, confidence %s): %s. Station measurements, not a path forecast.'
            % (clean(query, 30), clean(station['name'], 65), clean(station['code'], 10),
               distance, utc(timestamp(row['time'])), max(0, int(age / 60)),
               confidence, ', '.join(values))]


BANDS = ((.1357, .1378, '2200m'), (.472, .479, '630m'),
         (1.8, 2.0, '160m'), (3.5, 4.0, '80m'), (5.0, 5.5, '60m'),
         (7.0, 7.3, '40m'), (10.1, 10.15, '30m'), (14.0, 14.35, '20m'),
         (18.068, 18.168, '17m'), (21.0, 21.45, '15m'), (24.89, 24.99, '12m'),
         (26.965, 27.405, '11m'), (28.0, 29.7, '10m'), (40.0, 45.0, '8m'),
         (50.0, 54.0, '6m'), (69.9, 71.0, '4m'), (144.0, 148.0, '2m'),
         (222.0, 225.0, '1.25m'), (420.0, 450.0, '70cm'),
         (902.0, 928.0, '33cm'), (1240.0, 1300.0, '23cm'))


def band(hz):
    mhz = float(hz) / 1e6
    return next((name for low, high, name in BANDS if low <= mhz <= high), None)


def callsign(value):
    value = value.upper()
    if (not re.fullmatch(r'[A-Z0-9]+(?:/[A-Z0-9]+)*', value)
            or not any(c.isalpha() for c in value)
            or not any(c.isdigit() for c in value) or len(value) > 25):
        raise ValueError('Provide a valid callsign, for example SV1ABC or SV1ABC/P.')
    return value


def spot_options(text):
    selected_band, mode, limit = None, None, 3
    for item in text.upper().split():
        item = item.removeprefix('--')
        if item.isdigit():
            limit = int(item)
            if not 1 <= limit <= 5:
                raise ValueError('Choose a spot limit from 1 to 5.')
        elif item.lower() in {row[2] for row in BANDS}:
            selected_band = item.lower()
        elif item in ('CW', 'SSB', 'PHONE', 'DIGI', 'FT8', 'FT4', 'FM', 'AM', 'RTTY', 'JS8'):
            mode = item
        else:
            raise ValueError('Use optional band, mode, and limit: potaspots 20m CW 3.')
    return selected_band, mode, limit


def pota_rows(content, now):
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError('Invalid POTA spots response')
    rows = []
    for row in data:
        try:
            when = timestamp(row['spotTime'])
            frequency = float(row['frequency'])  # API frequencies are kHz.
            if row.get('invalid') or not -300 <= now - when <= 3600:
                continue
            if not math.isfinite(frequency) or frequency <= 0:
                continue
            rows.append({'time': when, 'frequency': frequency,
                         'call': callsign(row['activator']),
                         'mode': clean(row['mode'], 12).upper(),
                         'receiver': clean(row.get('spotter'), 25),
                         'info': clean('%s %s %s' % (row['reference'],
                                       row.get('name') or '', row.get('locationDesc') or ''), 85),
                         'source': 'POTA'})
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    return sorted(rows, key=lambda row: row['time'], reverse=True)


def format_spot(row):
    return '%s | %g kHz %s | %s | %s | via %s (%s)' % (
        row['call'], row['frequency'], row['mode'], row['info'],
        utc(row['time']), row['receiver'] or '?', row['source'])


def potaspots(content, text, now):
    selected_band, mode, limit = spot_options(text)
    rows = pota_rows(content, now)
    def matches(row):
        if selected_band and band(row['frequency'] * 1000) != selected_band:
            return False
        if mode == 'PHONE':
            return row['mode'] in ('SSB', 'USB', 'LSB', 'FM', 'AM')
        if mode == 'DIGI':
            return row['mode'] in ('FT8', 'FT4', 'RTTY', 'PSK31', 'JS8', 'JT65', 'JT9')
        return mode is None or row['mode'] == mode
    matches = [row for row in rows if matches(row)]
    return ([format_spot(row) for row in matches[:limit]] or
            ['No matching POTA spots reported within the last hour.'])


def psk_rows(content, target, now):
    root = ET.fromstring(content)
    if root.tag != 'receptionReports':
        raise ValueError('Invalid PSK Reporter reception data')
    rows = []
    for report in root.findall('receptionReport'):
        try:
            when = float(report.get('flowStartSeconds'))
            hz = float(report.get('frequency'))
            call = callsign(report.get('senderCallsign', ''))
            if call != target or not -300 <= now - when <= 3600 or not 0 < hz < 1e12:
                continue
            rows.append({'time': when, 'frequency': hz / 1000, 'call': call,
                         'mode': clean(report.get('mode') or '?', 12),
                         'receiver': clean(report.get('receiverCallsign'), 25),
                         'info': clean(report.get('receiverLocator') or '', 12),
                         'source': 'PSK Reporter'})
        except (TypeError, ValueError):
            continue
    return rows


def activity_query(text):
    parts = text.split()
    mode, grid = '', None
    known_modes = {'AM', 'FM', 'CW', 'SSB', 'FT8', 'FT4', 'RTTY', 'PSK31',
                   'PSK63', 'JT65', 'JT9', 'JS8', 'MSK144', 'Q65', 'WSPR'}
    for part in parts:
        if len(parts) > 1 and part.upper() in known_modes:
            if mode:
                raise ValueError('Provide one mode and one grid: activity FT8 KM17.')
            mode = part.upper()
        elif re.fullmatch(r'[A-Ra-r]{2}(?:\d{2})?', part):
            if grid:
                raise ValueError('Provide one grid region.')
            grid = part.upper()
        elif re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,11}', part):
            if mode:
                raise ValueError('Provide one mode and one grid: activity FT8 KM17.')
            mode = part.upper()
        else:
            raise ValueError('Use a 2/4-character grid and optional mode: activity FT8 KM17.')
    if not grid:
        raise ValueError('Provide your grid: activity FT8 KM17 or activity KM17.')
    return mode, grid


def activity(content, mode, grid):
    text = content.decode('utf-8') if isinstance(content, bytes) else content
    if re.search(r'<html|<!doctype|challenge|too many requests|storm clouds', text, re.I):
        raise ValueError('PSK Reporter activity feed is unavailable or rate limited.')
    scores = Counter()
    numeric_rows = 0
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        fields = line.split()
        try:
            hz, score = float(fields[0]), float(fields[1])
            if not math.isfinite(score) or score < 0 or not math.isfinite(hz):
                continue
        except (ValueError, IndexError):
            continue
        numeric_rows += 1
        name = band(hz)
        if name and score:
            scores[name] += score
    if not numeric_rows and text.strip() and not text.lstrip().startswith('#'):
        raise ValueError('Unrecognized PSK Reporter activity response')
    prefix = '%s from grid %s (last 15 min, PSK Reporter): ' % (mode or 'ALL', grid)
    if not scores:
        return [prefix + 'no reported activity.']
    order = {name: index for index, (_, _, name) in enumerate(BANDS)}
    return [prefix + ', '.join('%s=%g' % (key, scores[key])
                              for key in sorted(scores, key=order.get))]


def contests(content, now, limit=5):
    text = content.decode('utf-8') if isinstance(content, bytes) else content
    text = re.sub(r'\r?\n[ \t]', '', text)  # iCalendar folded lines
    if 'BEGIN:VCALENDAR' not in text or 'END:VCALENDAR' not in text:
        raise ValueError('Invalid contest calendar')
    events = []
    for block in re.findall(r'BEGIN:VEVENT\s*\n(.*?)END:VEVENT', text, re.S):
        fields = {}
        for line in block.splitlines():
            key, sep, value = line.strip().partition(':')
            if sep:
                fields[key.split(';')[0]] = value
        try:
            # This source publishes UTC event times. Do not guess local zones.
            start = datetime.strptime(fields['DTSTART'], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()
            end = datetime.strptime(fields['DTEND'], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()
            title = fields['SUMMARY'].replace('\\,', ',').replace('\\;', ';').replace('\\n', ' ')
            if end <= now or end <= start:
                continue
            url = fields.get('URL', '')
            if not url.startswith('https://www.contestcalendar.com/'):
                url = 'https://www.contestcalendar.com/weeklycont.php'
            events.append((start, end, clean(title, 110), clean(url, 140)))
        except (KeyError, ValueError):
            continue
    events.sort(key=lambda event: (event[0] > now, event[0]))
    return [('%s: %s | %s to %s | %s' % (
        'NOW' if start <= now else 'NEXT', title, utc(start), utc(end), url))
        for start, end, title, url in events[:limit]] or [
            'No current or upcoming contests in the published weekly calendar.']
