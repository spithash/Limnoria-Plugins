"""Credential-free space, aviation, location, and amateur radio helpers."""

import csv
from datetime import datetime, timedelta, timezone
import io
import json
import math
import random
import re

import ephem
from bs4 import BeautifulSoup

from . import radio


def grid(lat, lon):
    lat = min(math.nextafter(180.0, -math.inf), max(0, lat + 90))
    lon = min(math.nextafter(360.0, -math.inf), max(0, lon + 180))
    return ('%s%s%d%d%s%s' % (
        chr(65 + int(lon / 20)), chr(65 + int(lat / 10)),
        int(lon % 20 / 2), int(lat % 10),
        chr(97 + min(23, int((lon % 2) * 12))),
        chr(97 + min(23, int((lat % 1) * 24)))))


def qth(text):
    parts = re.split(r'\s+de\s+', text.strip(), flags=re.I)
    if len(parts) > 2:
        raise ValueError('Usage: qth <grid|lat,lon> [de <grid|lat,lon>].')
    lat, lon = radio.coordinates(parts[0])
    line = '%s: %.5f,%.5f; Maidenhead %s' % (
        radio.clean(parts[0], 30), lat, lon, grid(lat, lon))
    if len(parts) == 2:
        olat, olon = radio.coordinates(parts[1])
        delta = math.radians(lon - olon)
        x = math.sin(delta) * math.cos(math.radians(lat))
        y = (math.cos(math.radians(olat)) * math.sin(math.radians(lat))
             - math.sin(math.radians(olat)) * math.cos(math.radians(lat)) * math.cos(delta))
        bearing = math.degrees(math.atan2(x, y)) % 360
        line += '; %.0f km, initial bearing %.0f° from %s' % (
            radio._distance(olat, olon, lat, lon), bearing,
            radio.clean(parts[1], 30))
    return [line]


def observer(location, now):
    lat, lon = radio.coordinates(location)
    obs = ephem.Observer()
    obs.lat, obs.lon = str(lat), str(lon)
    obs.elevation, obs.pressure, obs.horizon = 0, 0, '0'
    obs.date = datetime.fromtimestamp(now, timezone.utc).replace(tzinfo=None)
    return obs


def ephem_time(value):
    return value.datetime().replace(tzinfo=timezone.utc).timestamp()


def sky(body_name, location, now):
    obs = observer(location, now)
    body = ephem.Sun(obs) if body_name == 'Sun' else ephem.Moon(obs)
    line = '%s at %s as of %s: azimuth %.1f°, altitude %.1f°' % (
        body_name, radio.clean(location, 30), radio.utc(now),
        math.degrees(float(body.az)), math.degrees(float(body.alt)))
    if body_name == 'Moon':
        line += '; illuminated %.1f%%, distance %.0f km' % (
            body.phase, body.earth_distance * 149597870.7)
    events = []
    for label, method in (('next rise', obs.next_rising), ('next set', obs.next_setting)):
        try:
            events.append('%s %s' % (label, radio.utc(ephem_time(method(body)))))
        except ephem.AlwaysUpError:
            events.append('%s: always above horizon' % label)
        except ephem.NeverUpError:
            events.append('%s: below horizon throughout this cycle' % label)
    return [line, '; '.join(events) + ' (sea level, refraction disabled).']


def tle_records(content):
    text = content.decode('utf-8') if isinstance(content, bytes) else content
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    records = []
    for index in range(len(lines) - 2):
        if not lines[index].startswith(('1 ', '2 ')) and lines[index + 1].startswith('1 ') and lines[index + 2].startswith('2 '):
            try:
                number = int(lines[index + 1][2:7])
                records.append((lines[index].removeprefix('0 '), number,
                                lines[index + 1], lines[index + 2]))
            except ValueError:
                continue
    if not records:
        raise ValueError('The satellite orbital-elements source returned no TLEs.')
    return records


def satellite(content, query):
    def norm(value):
        return re.sub(r'[^A-Z0-9]', '', value.upper())
    key = norm(query)
    aliases = {'ARISS': 'ISS', 'ZARYA': 'ISS', 'AO7': 'AO07'}
    key = aliases.get(key, key)
    records = tle_records(content)
    matches = [record for record in records if key == norm(record[0])
               or (query.isdigit() and int(query) == record[1])]
    if not matches:
        matches = [record for record in records if key and key in norm(record[0])]
    if len(matches) != 1:
        if matches:
            raise ValueError('Ambiguous satellite: ' + ', '.join(row[0] for row in matches[:6]))
        raise ValueError('Satellite not found in AMSAT orbital elements. Try ISS, SO-50, or a NORAD ID.')
    return matches[0]


def pass_options(text):
    parts = text.split()
    count = 2
    if len(parts) >= 3 and parts[-1].isdigit():
        count = int(parts.pop())
    if not 1 <= count <= 3 or len(parts) < 2:
        raise ValueError('Usage: satpass <satellite> <grid|lat,lon> [count 1–3].')
    location = parts.pop()
    radio.coordinates(location)
    return ' '.join(parts), location, count


def satpass(content, text, now):
    query, location, count = pass_options(text)
    name, number, line1, line2 = satellite(content, query)
    sat = ephem.readtle(name, line1, line2)
    age = now - ephem_time(sat._epoch)
    if not -86400 <= age <= 14 * 86400:
        raise ValueError('Satellite orbital elements are too old or future-dated for a useful pass prediction.')
    obs = observer(location, now)
    lines = ['%s (NORAD %d) passes at %s; TLE epoch %s. '
             'Horizon crossings; peak elevation ≥5°; visibility does not confirm radio activity.'
             % (radio.clean(name, 45), number, radio.clean(location, 25),
                radio.utc(ephem_time(sat._epoch)))]
    for unused in range(50):
        try:
            rise, az_rise, peak, altitude, setting, az_set = obs.next_pass(sat)
        except (ValueError, ephem.AlwaysUpError, ephem.NeverUpError):
            break
        if rise is None or setting is None:
            break
        rise_ts, set_ts = ephem_time(rise), ephem_time(setting)
        if rise_ts > now + 48 * 3600:
            break
        obs.date = setting + ephem.minute
        if rise_ts < now or math.degrees(float(altitude)) < 5:
            continue
        lines.append('AOS %s az %.0f° | peak %s elev %.1f° | LOS %s az %.0f°'
                     % (radio.utc(rise_ts), math.degrees(float(az_rise)),
                        radio.utc(ephem_time(peak)), math.degrees(float(altitude)),
                        radio.utc(set_ts), math.degrees(float(az_set))))
        if len(lines) - 1 >= count:
            break
    if len(lines) == 1:
        lines.append('No qualifying complete passes found within the next 48 hours.')
    return lines


def satinfo(content, name, number):
    data = json.loads(content)
    if isinstance(data, dict) and isinstance(data.get('results'), list):
        data = data['results']
    if not isinstance(data, list):
        raise ValueError('Invalid SatNOGS transmitter response')
    transmitters, seen = [], set()
    for row in data:
        if not isinstance(row, dict) or row.get('norad_cat_id') != number:
            continue
        if row.get('status') != 'active' or row.get('alive') is False:
            continue
        freqs = []
        for prefix, label in (('downlink', 'down'), ('uplink', 'up')):
            low, high = row.get(prefix + '_low'), row.get(prefix + '_high')
            if isinstance(low, (int, float)) and math.isfinite(low) and low > 0:
                value = '%.6f' % (low / 1e6)
                if isinstance(high, (int, float)) and math.isfinite(high) and high > low:
                    value += '–%.6f' % (high / 1e6)
                freqs.append('%s %s MHz' % (label, value))
        if not freqs:
            continue
        line = '%s | %s | %s' % (radio.clean(row.get('description'), 80),
                                    radio.clean(row.get('mode') or '?', 15), ', '.join(freqs))
        if line not in seen:
            transmitters.append(line)
            seen.add(line)
    header = '%s (NORAD %d): %d transmitters listed active in SatNOGS; '
    header += 'not confirmation of current transmissions.'
    lines = [header % (radio.clean(name, 50), number, len(transmitters))] + transmitters[:3]
    if len(transmitters) > 3:
        lines.append('More: https://db.satnogs.org/satellites/?norad_cat_id=%d' % number)
    return lines


def launch(content, now):
    data = json.loads(content)
    if not isinstance(data, dict) or not isinstance(data.get('results'), list):
        raise ValueError('Invalid Launch Library response')
    rows = []
    for row in data['results']:
        try:
            when = radio.timestamp(row['net'])
            status = row.get('status') or {}
            if when < now or status.get('id') in (3, 4, 6, 7):
                continue
            rows.append((when, row))
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return ['No matching future launch in the returned schedule.']
    when, row = min(rows, key=lambda item: item[0])
    provider = (row.get('launch_service_provider') or {}).get('name', '?')
    pad = row.get('pad') or {}
    site = '%s, %s' % (pad.get('name', '?'), (pad.get('location') or {}).get('name', '?'))
    precision = (row.get('net_precision') or {}).get('name') or 'unspecified'
    return ['%s | NET %s (precision: %s) | %s' % (
        radio.clean(row.get('name'), 110), radio.utc(when), radio.clean(precision, 30),
        radio.clean((row.get('status') or {}).get('name'), 45)),
        'Provider: %s | Site: %s | Schedule may change. Source: Launch Library 2.'
        % (radio.clean(provider, 55), radio.clean(site, 140))]


def adsb_query(text):
    text = text.strip()
    if re.fullmatch(r'0x[0-9a-fA-F]{6}', text):
        return 'hex/' + text[2:].lower(), None
    if ',' in text or re.fullmatch(r'[A-Ra-r]{2}\d{2}(?:[A-Xa-x]{2}(?:\d{2})?)?', text):
        lat, lon = radio.coordinates(text)
        return 'point/%g/%g/25' % (lat, lon), (lat, lon)
    match = re.fullmatch(r'(?:(reg|call):)?([A-Za-z0-9-]{2,12})', text)
    if not match:
        raise ValueError('Use adsb <callsign|reg:registration|0xHEX|grid|lat,lon>.')
    kind, value = match.groups()
    return ('reg/' if kind == 'reg' or (kind is None and '-' in value) else 'callsign/') + value.upper(), None


def adsb(content, location, now):
    data = json.loads(content)
    if not isinstance(data, dict) or not isinstance(data.get('ac'), list):
        raise ValueError('Invalid ADS-B response')
    feed_time = float(data['now']) / 1000
    if not math.isfinite(feed_time) or not -300 <= now - feed_time <= 300:
        raise ValueError('ADS-B source data is stale or future-dated.')
    rows = []
    for row in data['ac']:
        if not isinstance(row, dict):
            continue
        try:
            seen = float(row.get('seen', 0))
            if not 0 <= seen <= 300:
                continue
            distance = 0
            if location:
                lat, lon = float(row['lat']), float(row['lon'])
                if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                    continue
                if float(row.get('seen_pos', seen)) > 300:
                    continue
                distance = radio._distance(*location, lat, lon)
            rows.append((distance, row, seen))
        except (KeyError, ValueError, TypeError):
            continue
    if not rows:
        return ['No recently reported matching aircraft in adsb.lol coverage.']
    rows.sort(key=lambda row: row[0])
    lines = []
    for distance, row, seen in rows[:3]:
        altitude = row.get('alt_baro')
        altitude = 'ground' if altitude == 'ground' else (
            '%s ft' % radio.clean(altitude, 10) if altitude is not None else '?')
        speed = '%s kt' % radio.clean(row['gs'], 10) if row.get('gs') is not None else '?'
        position = ''
        if row.get('lat') is not None and row.get('lon') is not None:
            position = ' | %.4f,%.4f' % (float(row['lat']), float(row['lon']))
        hexcode = radio.clean(row.get('hex'), 6)
        if not re.fullmatch(r'[0-9a-fA-F]{6}', hexcode):
            continue
        line = '%s (%s, %s, %s) | alt %s, speed %s%s | last seen %s' % (
            radio.clean(row.get('flight') or '?', 15), hexcode,
            radio.clean(row.get('r') or '?', 15), radio.clean(row.get('t') or '?', 10),
            altitude, speed, position, radio.utc(feed_time - seen))
        if location:
            line += ' | %.0f km from query' % distance
        lines.append(line + ' | https://adsb.lol/' + hexcode)
    return lines or ['No usable matching aircraft records.']


def metar(content, station):
    text = content.decode('utf-8') if isinstance(content, bytes) else content
    lines = text.strip().splitlines()
    if len(lines) < 2 or not re.fullmatch(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}', lines[0]):
        raise ValueError('Invalid NOAA METAR report')
    report = radio.clean(' '.join(lines[1:]), 650)
    if not re.match(r'(?:METAR |SPECI )?' + re.escape(station) + r'\s', report):
        raise ValueError('NOAA returned a METAR for a different station.')
    return ['%s | observed %s UTC' % (report, lines[0])]


STATES = set('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN '
             'MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split())


def fest_payload(state, now):
    from urllib.parse import urlencode
    state = state.strip().upper()
    if state not in STATES:
        raise ValueError('Use a two-letter US state, e.g. fest FL. No Google geocoding key is needed.')
    today = datetime.fromtimestamp(now, timezone.utc).date()
    return urlencode({'_method': 'POST', 'data[Location][state]': state,
                      'data[Date][start]': today.strftime('%m/%d/%Y'),
                      'data[Date][end]': (today + timedelta(days=90)).strftime('%m/%d/%Y')}).encode()


def fest(content, state, now):
    soup = BeautifulSoup(content, 'html.parser')
    if not soup.find('form', action='/hamfests/search'):
        raise ValueError('ARRL returned an unrecognized hamfest search page.')
    if 'No records found matching your query' in soup.get_text():
        return ['No ARRL-listed hamfests found in %s for the next 90 days.' % state]
    today = datetime.fromtimestamp(now, timezone.utc).date()
    rows, seen = [], set()
    for item in soup.find_all('li'):
        link = item.find('a', href=re.compile(r'^/hamfests/[^/?]+'))
        if not link or link['href'] in seen:
            continue
        date = re.search(r'\b(\d{1,2}/\d{1,2}/\d{4})\b', item.get_text(' ', strip=True))
        if not date:
            continue
        try:
            when = datetime.strptime(date[1], '%m/%d/%Y').date()
        except ValueError:
            continue
        if not today <= when <= today + timedelta(days=90):
            continue
        title = link.get('title') or link.get_text(' ', strip=True)
        location = ''
        strong = item.find('strong', string=re.compile(r'Location:'))
        if strong:
            location = radio.clean(strong.next_sibling, 70)
        rows.append((when, '%s: %s | %s | https://www.arrl.org%s' % (
            when.isoformat(), radio.clean(title, 100), location, link['href'])))
        seen.add(link['href'])
    if not rows:
        raise ValueError('No parseable hamfest entries found; the ARRL page format may have changed.')
    return [line for unused, line in sorted(rows)[:3]]


def lotw(content, target):
    text = content.decode('utf-8-sig') if isinstance(content, bytes) else content
    if '<html' in text[:1000].lower() or len(text.splitlines()) < 2:
        raise ValueError('Invalid public LoTW activity list')
    valid_rows = 0
    found = None
    for row in csv.reader(io.StringIO(text)):
        if len(row) != 3 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', row[1]):
            continue
        valid_rows += 1
        if row[0].strip().upper() == target:
            found = '%s %s UTC' % (row[1], row[2])
    if not valid_rows:
        raise ValueError('Invalid public LoTW activity list')
    return ('%s: LoTW %s' % (target, found) if found else
            '%s: not found in the public LoTW activity list.' % target)


def eqsl(content, target):
    soup = BeautifulSoup(content, 'html.parser')
    text = soup.get_text(' ', strip=True)
    if 'is not registered yet' in text:
        return target + ': not registered with eQSL.'
    if 'not registered any more' in text:
        return target + ': no longer registered with eQSL.'
    if 'Effective dates at this QTH' not in text:
        raise ValueError('Unrecognized public eQSL profile response')
    line = target + ': eQSL'
    if soup.find('img', src=re.compile('AuthGuaranteed', re.I)):
        line += ' (AG)'
    match = re.search(r'Grid Square\s+([A-R]{2}\d{2}(?:[A-Xa-x]{2})?)', text)
    if match:
        line += ' ' + match[1]
    login = re.search(r'I logged in\s+(on\s+[^;]+?)(?:Last Activity|$)', text)
    if login:
        line += '; last login: ' + radio.clean(login[1].removeprefix('on '), 45)
    return line


def callbook(content, target):
    """Format public US amateur license information from Callook/FCC data."""
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError('Invalid public callsign lookup response')
    if data.get('status') != 'VALID':
        return []
    lines = []
    name = radio.clean(data.get('name'), 80)
    current = data.get('current')
    current = current if isinstance(current, dict) else {}
    license_type = radio.clean(data.get('type'), 24).lower()
    oper_class = radio.clean(current.get('operClass'), 24)
    if name:
        line = target + ': USA: ' + name
        if oper_class:
            line += ' -- ' + oper_class + ' class'
        elif license_type:
            line += ' -- ' + license_type.title() + ' class'
        trustee = data.get('trustee')
        trustee = trustee if isinstance(trustee, dict) else {}
        trustee_call = radio.clean(trustee.get('callsign'), 16)
        if trustee_call:
            line += ' -- trustee: ' + trustee_call
        lines.append(line)
    address = data.get('address')
    address = address if isinstance(address, dict) else {}
    address_parts = [radio.clean(address.get(key), 80) for key in
                     ('attn', 'line1', 'line2')]
    address_parts = [part for part in address_parts if part]
    if address_parts:
        lines.append('; '.join(address_parts) + '; USA')
    location = data.get('location')
    location = location if isinstance(location, dict) else {}
    grid = radio.clean(location.get('gridsquare'), 8)
    try:
        latitude = float(location['latitude'])
        longitude = float(location['longitude'])
    except (KeyError, TypeError, ValueError):
        latitude = longitude = None
    if grid and latitude is not None and longitude is not None:
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError('Invalid coordinates in public callsign lookup')
        lines.append('%s %.6f, %.6f [src: FCC]' %
                     (grid, latitude, longitude))
    return lines


def hofh(query):
    quotes = radio.table('hofh')
    if not query:
        index = random.randrange(len(quotes))
    elif query.isdigit():
        index = int(query) - 1
        if not 0 <= index < len(quotes):
            raise ValueError('Choose a HoFH entry from 1 to %d.' % len(quotes))
    else:
        matches = [(i, quote) for i, quote in enumerate(quotes) if query.casefold() in quote.casefold()]
        return ['HoFH #%d: %s' % (i + 1, radio.clean(quote, 300))
                for i, quote in matches[:3]] or ['No matching HoFH excuses.']
    return ['HoFH #%d: %s' % (index + 1, radio.clean(quotes[index], 300))]
