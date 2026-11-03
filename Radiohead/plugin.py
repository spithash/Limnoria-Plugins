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

import json
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, quote

from supybot import utils, callbacks, ircutils, ircmsgs, schedule
from supybot.commands import wrap, optional
from supybot.i18n import PluginInternationalization

from . import reports, radio, extras


_ = PluginInternationalization('Radiohead')


# Thresholds and IRC palette from qrmbot's lib/bands and lib/Colors.pm:
# https://github.com/molo1134/qrmbot/blob/master/lib/bands
# Each boundary is exclusive; the last color applies above all boundaries.
SFI_COLORS = ((85, '05'), (105, '04'), (125, '07'), (145, '08'),
              (165, '03'), (185, '09'), (205, '12'), (225, '10'),
              (235, '11'), (math.inf, '06'))
A_COLORS = ((20, '03'), (30, '08'), (50, '04'), (100, '05'),
            (math.inf, '12'))
K_COLORS = ((4, '03'), (5, '08'), (8, '04'), (math.inf, '12'))


def _color_index(value, scale, maximum=math.inf):
    """Apply qrmbot's color scale; leave unavailable/invalid values neutral."""
    try:
        number = float(value)
    except ValueError:
        return value
    if not math.isfinite(number) or not 0 <= number <= maximum:
        return value
    for upper, color in scale:
        if number < upper:
            return '\x03%s%s\x03' % (color, value)
    return value


class Radiohead(callbacks.Plugin):
    """Amateur radio tools: band conditions, propagation forecasts, reception
    spots, ionosondes, satellite passes and frequencies, launches, aviation
    reports, Sun/Moon positions, QSL activity, grids, contests, and offline
    Morse, Q-code, and coax calculators. Uses public sources without credentials.
    """
    threaded = True

    def __init__(self, irc):
        super().__init__(irc)
        self._bands_cache = None
        self._bands_cached_at = 0
        self._bands_lock = threading.Lock()
        self._report_cache = {}
        self._report_locks = {name: threading.Lock()
                              for name in ('xray', 'kindex', 'forecast')}
        self._radio_cache = {}
        self._radio_locks = {name: threading.Lock() for name in
                             ('iono', 'pota', 'psk', 'activity', 'contests',
                              'tle', 'satinfo', 'launch', 'adsb', 'metar', 'callook',
                              'lotw', 'eqsl', 'fest')}
        self._psk_requested_at = None
        self._launch_requested_at = None
        self._wwv_events = {}
        self._wwv_lock = threading.Lock()

    def _radio_request(self, name, url, ttl, data=None, size=2097152):
        """Cache source payloads, including errors, to avoid hammering services."""
        with self._radio_locks[name]:
            now = time.monotonic()
            key = (name, url, data)
            cached = self._radio_cache.get(key)
            if cached and now - cached[0] < ttl:
                if isinstance(cached[1], Exception):
                    raise cached[1]
                return cached[1]
            if name == 'psk':
                if self._psk_requested_at is not None and now - self._psk_requested_at < 300:
                    raise ValueError('PSK Reporter refreshes are limited to one '
                                     'request per five minutes; try again later.')
                self._psk_requested_at = now
            if name == 'launch':
                if self._launch_requested_at is not None and now - self._launch_requested_at < 300:
                    raise ValueError('Launch Library refreshes are limited to one '
                                     'request per five minutes; cached searches still work.')
                self._launch_requested_at = now
            try:
                options = {'timeout': 20, 'size': size}
                if data is not None:
                    options['data'] = data
                content = utils.web.getUrl(url, **options)
                if len(content) >= size:
                    content = ValueError('%s response exceeded the download limit.' % name)
            except utils.web.Error as exc:
                content = ValueError('%s source is unavailable: %s' % (name, exc))
            if len(self._radio_cache) >= 64:
                oldest, unused = min(list(self._radio_cache.items()),
                                     key=lambda item: item[1][0])
                self._radio_cache.pop(oldest, None)
            self._radio_cache[key] = (time.monotonic(), content)
            if isinstance(content, Exception):
                raise content
            return content

    def _reply_radio(self, irc, generate):
        try:
            lines = generate()
        except (ValueError, TypeError, KeyError, OverflowError, ET.ParseError,
                utils.web.Error) as exc:
            self.log.warning('Radio tool request failed: %s', exc)
            irc.error(radio.clean(str(exc), 260) or 'The radio data source returned invalid data.')
            return
        irc.replies(lines, oneToOne=False)

    def solar(self, irc, msg, args):
        """takes no arguments

        Show the cached solar-index summary from bands, including eSFI/eSSN.
        Uses HamQSL and KC2G rather than requiring account credentials.
        """
        self._reply_radio(irc, lambda: [
            self._get_bands()[0].replace('Bands as of ', 'Solar indices as of ', 1)])

    solar = wrap(solar)

    def launch(self, irc, msg, args, search):
        """[search term]

        Show the next matching future launch from Launch Library 2.
        Example: launch Falcon or launch Rocket Lab. Schedules may change.
        """
        def generate():
            if search and len(search) > 80:
                raise ValueError('Keep launch searches to 80 characters or fewer.')
            query = urlencode({'limit': 10, 'mode': 'normal', 'search': search or ''})
            content = self._radio_request(
                'launch', 'https://ll.thespacedevs.com/2.3.0/launches/upcoming/?' + query, 900)
            return extras.launch(content, time.time())
        self._reply_radio(irc, generate)

    launch = wrap(launch, [optional('text')])

    def spacex(self, irc, msg, args):
        """takes no arguments

        Show the next SpaceX launch using the public Launch Library 2 schedule.
        """
        self.launch(irc, msg, ['SpaceX'])

    spacex = wrap(spacex)

    def _tle(self):
        return self._radio_request(
            'tle', 'https://www.amsat.org/tle/current/nasabare.txt', 3600)

    def satpass(self, irc, msg, args, text):
        """<satellite> <grid|lat,lon> [count 1–3]

        Predict the next complete passes with peak elevation at least 5 degrees
        within 48 hours. Example: satpass ISS KM17 2. Uses AMSAT TLEs and PyEphem.
        """
        def generate():
            extras.pass_options(text)
            return extras.satpass(self._tle(), text, time.time())
        self._reply_radio(irc, generate)

    satpass = wrap(satpass, ['text'])

    def satinfo(self, irc, msg, args, query):
        """<satellite name|NORAD ID>

        Show satellite uplink/downlink frequencies listed active in SatNOGS.
        Examples: satinfo ISS, satinfo SO-50, satinfo 25544.
        Listings do not confirm current radio transmissions.
        """
        def generate():
            if query.isdigit():
                number = int(query)
                if not 1 <= number <= 999999:
                    raise ValueError('Invalid NORAD ID.')
                name = 'Satellite'
            else:
                name, number, unused, unused2 = extras.satellite(self._tle(), query)
            url = 'https://db.satnogs.org/api/transmitters/?' + urlencode(
                {'satellite__norad_cat_id': number, 'format': 'json'})
            content = self._radio_request('satinfo', url, 3600)
            return extras.satinfo(content, name, number)
        self._reply_radio(irc, generate)

    satinfo = wrap(satinfo, ['text'])

    def qsl(self, irc, msg, args, target):
        """<callsign>

        Check public US FCC license details, LoTW activity, and eQSL profile.
        QRZ and Club Log lookups that need credentials are omitted.
        """
        def generate():
            call = radio.callsign(target)
            lines = []
            try:
                content = self._radio_request(
                    'callook', 'https://callook.info/' + quote(call, safe='') + '/json',
                    86400, size=1048576)
                lines.extend(extras.callbook(content, call))
            except (ValueError, TypeError, KeyError) as exc:
                self.log.warning('Callook QSL lookup failed: %s', exc)
            for source, url, ttl, size, parser in (
                ('lotw', 'https://lotw.arrl.org/lotw-user-activity.csv', 86400,
                 16777216, extras.lotw),
                ('eqsl', 'https://www.eqsl.cc/Member.cfm?' + quote(call, safe=''),
                 3600, 1048576, extras.eqsl),
            ):
                try:
                    content = self._radio_request(source, url, ttl, size=size)
                    lines.append(parser(content, call))
                except (ValueError, TypeError, KeyError) as exc:
                    self.log.warning('%s QSL lookup failed: %s', source, exc)
                    lines.append('%s: %s data unavailable.' % (call, source))
            return lines
        self._reply_radio(irc, generate)

    qsl = wrap(qsl, ['somethingWithoutSpaces'])

    def fest(self, irc, msg, args, state):
        """<US state abbreviation>

        Show up to three ARRL-listed hamfests in the next 90 days.
        Example: fest FL. Searches by state, without Google geocoding credentials.
        """
        def generate():
            state_code = state.upper()
            payload = extras.fest_payload(state_code, time.time())
            content = self._radio_request('fest', 'https://www.arrl.org/hamfests/search',
                                          3600, data=payload)
            return extras.fest(content, state_code, time.time())
        self._reply_radio(irc, generate)

    fest = wrap(fest, ['somethingWithoutSpaces'])

    def adsb(self, irc, msg, args, query):
        """<callsign|reg:registration|0xHEX|grid|lat,lon>

        Show up to three recent aircraft reports from adsb.lol (no API key).
        Examples: adsb AAL100, adsb reg:N123AB, adsb 0x469c35, adsb KM17.
        Location searches use a 25 nautical mile radius.
        """
        def generate():
            path, location = extras.adsb_query(query)
            base = 'https://api.adsb.lol/v2/'
            content = self._radio_request('adsb', base + path, 30)
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError('The ADS-B source returned an unexpected response.')
            if path.startswith('callsign/') and ':' not in query and data.get('ac') == []:
                content = self._radio_request('adsb', base + 'reg/' + path.split('/')[1], 30)
            return extras.adsb(content, location, time.time())
        self._reply_radio(irc, generate)

    adsb = wrap(adsb, ['text'])

    def hofh(self, irc, msg, args, query):
        """[entry number|search term]

        Give a humorous excuse for why your radio is broken, from qrmbot's list.
        """
        self._reply_radio(irc, lambda: extras.hofh(query))

    hofh = wrap(hofh, [optional('text')])

    def sun(self, irc, msg, args, location):
        """<grid|lat,lon>

        Show the Sun's azimuth, altitude and next rise/set times in UTC.
        Example: sun KM17. Uses PyEphem at sea level, without refraction.
        """
        self._reply_radio(irc, lambda: extras.sky('Sun', location, time.time()))

    sun = wrap(sun, ['text'])

    def moon(self, irc, msg, args, location):
        """<grid|lat,lon>

        Show Moon position, illumination, distance and next rise/set in UTC.
        Example: moon KM17. Uses PyEphem at sea level, without refraction.
        """
        self._reply_radio(irc, lambda: extras.sky('Moon', location, time.time()))

    moon = wrap(moon, ['text'])

    def metar(self, irc, msg, args, station):
        """<four-letter ICAO airport code>

        Show the raw NOAA METAR and observation time. Example: metar LGAV.
        """
        def generate():
            code = station.upper()
            if not re.fullmatch(r'[A-Z]{4}', code):
                raise ValueError('Use a four-letter ICAO airport code, e.g. LGAV or KJFK.')
            url = 'https://tgftp.nws.noaa.gov/data/observations/metar/stations/%s.TXT' % code
            content = self._radio_request('metar', url, 300)
            return extras.metar(content, code)
        self._reply_radio(irc, generate)

    metar = wrap(metar, ['somethingWithoutSpaces'])

    def qth(self, irc, msg, args, text):
        """<grid|lat,lon> [de <grid|lat,lon>]

        Convert grids/coordinates; optionally calculate distance and bearing from
        the location after 'de'. Example: qth KM17 de FN31. No geocoding service.
        """
        self._reply_radio(irc, lambda: extras.qth(text))

    qth = wrap(qth, ['text'])

    def wwv(self, irc, msg, args):
        """takes no arguments

        In a channel, announce a coming UTC minute and send <beep> at that minute.
        This emulates WWV in text using the host clock; IRC delivery may be delayed.
        Only one pending announcement is allowed per channel/network.
        """
        channel = msg.channel
        if not channel:
            irc.error('WWV announcements are available in channels only.')
            return
        real = irc.getRealIrc()
        key = (real.network, ircutils.toLower(channel))
        with self._wwv_lock:
            if key in self._wwv_events:
                irc.reply('A WWV tone is already scheduled for this channel.')
                return
            now = time.time()
            tone = (math.floor(now / 60) + 1) * 60
            if tone - now < 5:
                tone += 60
            names = ['Radiohead-wwv-%s-%s-%s' % (id(self), tone, part)
                     for part in ('announce-' + real.network + channel,
                                  'beep-' + real.network + channel)]
            self._wwv_events[key] = names
            def announce():
                if channel in real.state.channels:
                    from datetime import datetime, timezone
                    clock = datetime.fromtimestamp(tone, timezone.utc)
                    real.queueMsg(ircmsgs.privmsg(channel, clock.strftime(
                        'At the tone: %H hours %M minutes Coordinated Universal Time:')))
            def beep():
                try:
                    if channel in real.state.channels:
                        real.queueMsg(ircmsgs.privmsg(channel, '<beep>'))
                finally:
                    with self._wwv_lock:
                        self._wwv_events.pop(key, None)
            schedule.addEvent(announce, tone - 4, name=names[0])
            schedule.addEvent(beep, tone, name=names[1])
        irc.reply('WWV text tone scheduled for %s (host clock; IRC delivery may lag).'
                  % radio.utc(tone))

    wwv = wrap(wwv)

    def die(self):
        with self._wwv_lock:
            for names in self._wwv_events.values():
                for name in names:
                    try:
                        schedule.removeEvent(name)
                    except KeyError:
                        pass
            self._wwv_events.clear()
        super().die()

    def iono(self, irc, msg, args, location):
        """<grid>|<latitude>,<longitude>

        Show recent measurements from the nearest usable ionosonde (KC2G).
        Example: iono KM17 or iono 37.98,23.73.
        """
        def generate():
            radio.coordinates(location)  # Validate before contacting the source.
            content = self._radio_request(
                'iono', 'https://prop.kc2g.com/api/stations.json', 300)
            return radio.iono(content, location, time.time())
        self._reply_radio(irc, generate)

    iono = wrap(iono, ['text'])

    def spots(self, irc, msg, args, text):
        """<callsign> [limit]

        Show recent reception/POTA spots for a callsign (default 3, maximum 5).
        PSK Reporter requests are limited to one refresh per five minutes.
        """
        def generate():
            parts = text.split()
            if not 1 <= len(parts) <= 2:
                raise ValueError('Usage: spots <callsign> [limit from 1 to 5].')
            target = radio.callsign(parts[0])
            limit = int(parts[1]) if len(parts) == 2 else 3
            if not 1 <= limit <= 5:
                raise ValueError('Choose a spot limit from 1 to 5.')
            rows, unavailable = [], []
            query = urlencode({'senderCallsign': target, 'flowStartSeconds': -3600,
                               'rronly': 1})
            try:
                content = self._radio_request(
                    'psk', 'https://retrieve.pskreporter.info/query?' + query, 300)
                rows.extend(radio.psk_rows(content, target, time.time()))
            except (ValueError, TypeError, KeyError, ET.ParseError) as exc:
                self.log.warning('PSK Reporter spots unavailable: %s', exc)
                unavailable.append('PSK Reporter')
            try:
                content = self._radio_request(
                    'pota', 'https://api.pota.app/spot/activator', 60)
                rows.extend(row for row in radio.pota_rows(content, time.time())
                            if row['call'] == target)
            except (ValueError, TypeError, KeyError) as exc:
                self.log.warning('POTA spots unavailable: %s', exc)
                unavailable.append('POTA')
            rows.sort(key=lambda row: row['time'], reverse=True)
            seen, lines = set(), []
            for row in rows:
                identity = (row['call'], row['receiver'], row['frequency'],
                            row['time'], row['source'])
                if identity not in seen:
                    lines.append(radio.format_spot(row))
                    seen.add(identity)
                if len(lines) >= limit:
                    break
            if unavailable:
                if len(unavailable) == 2:
                    raise ValueError('PSK Reporter and POTA are both unavailable. Try again later.')
                lines.append('Unavailable sources: %s; results may be incomplete.'
                             % ', '.join(unavailable))
            if not rows:
                lines.insert(0, 'No recent spots for %s from the available sources.' % target)
            return lines
        self._reply_radio(irc, generate)

    spots = wrap(spots, ['text'])

    def potaspots(self, irc, msg, args, options):
        """[band] [mode] [limit]

        Show recent POTA activators and frequencies. Example: potaspots 20m CW 3.
        Modes include PHONE, DIGI, CW, SSB, FT8, FT4, FM, AM, RTTY, and JS8.
        Limit defaults to 3, maximum 5; --20m/--cw style options also work.
        """
        def generate():
            radio.spot_options(options or '')
            content = self._radio_request(
                'pota', 'https://api.pota.app/spot/activator', 60)
            return radio.potaspots(content, options or '', time.time())
        self._reply_radio(irc, generate)

    potaspots = wrap(potaspots, [optional('text')])

    def activity(self, irc, msg, args, text):
        """[mode] <grid>

        Show PSK Reporter's reported activity by band over the last 15 minutes.
        Use a two/four-character grid, e.g. activity FT8 KM17. Counts describe
        reports, not guaranteed propagation or the number of stations.
        """
        def generate():
            mode, grid = radio.activity_query(text)
            query = urlencode({'mode': mode, 'grid': grid, 'time': 90})
            content = self._radio_request(
                'activity', 'https://pskreporter.info/cgi-bin/psk-freq.pl?' + query, 300)
            return radio.activity(content, mode, grid)
        self._reply_radio(irc, generate)

    activity = wrap(activity, ['text'])

    def contests(self, irc, msg, args):
        """takes no arguments

        List up to five current/upcoming contests from the WA7BNM weekly calendar.
        All times are UTC.
        """
        def generate():
            content = self._radio_request(
                'contests', 'https://www.contestcalendar.com/weeklycontcustom.ics', 1800)
            return radio.contests(content, time.time())
        self._reply_radio(irc, generate)

    contests = wrap(contests)

    def coax(self, irc, msg, args, text):
        """<type> [<frequency> MHz [<length> m|ft]] | list

        Estimate matched coax attenuation using nominal tables and interpolation.
        Example: coax LMR-400 145 MHz 20 m. Default length is 100 ft.
        Use coax list for cable types, or coax <type> for its attenuation table.
        """
        self._reply_radio(irc, lambda: radio.coax(text))

    atten = coax = wrap(coax, ['text'])

    def morse(self, irc, msg, args, text):
        """<text>

        Encode text as Morse. Letters are separated by spaces, words by /.
        Includes international punctuation and Greek characters.
        """
        self._reply_radio(irc, lambda: radio.morse(text))

    cw = morse = wrap(morse, ['text'])

    def unmorse(self, irc, msg, args, text):
        """[--greek|--cyrillic|--hebrew|--japanese] <Morse>

        Decode space-separated Morse letters; use / or three spaces for words.
        Underscores may be used instead of dashes. Default alphabet: international.
        """
        self._reply_radio(irc, lambda: radio.unmorse(text))

    demorse = unmorse = wrap(unmorse, ['text'])

    def qcode(self, irc, msg, args, query):
        """<Q-code>|<search term>

        Explain a Q-code, or search the local Q-code definitions (up to 4 matches).
        Example: qcode QRM or qcode interference.
        """
        self._reply_radio(irc, lambda: radio.qcode(query))

    qcode = wrap(qcode, ['text'])

    def _reply_report(self, irc, name, url, ttl, parser):
        try:
            with self._report_locks[name]:
                cached_at, lines = self._report_cache.get(name, (0, None))
                if lines is None or time.monotonic() - cached_at >= ttl:
                    content = utils.web.getUrl(url, timeout=15, size=262144)
                    lines = parser(content)
                    self._report_cache[name] = (time.monotonic(), lines)
        except utils.web.Error as exc:
            self.log.warning('Could not fetch NOAA %s report: %s', name, exc)
            irc.error('Unable to fetch the NOAA %s report. Try again later.' % name)
            return
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            self.log.warning('Invalid NOAA %s report: %s', name, exc)
            irc.error('NOAA returned incomplete or invalid %s data. Try again later.'
                      % name)
            return
        irc.replies(lines, oneToOne=False)

    def xray(self, irc, msg, args):
        """takes no arguments

        Show NOAA GOES X-ray levels and the latest reported flare peak.
        """
        self._reply_report(
            irc, 'xray',
            'https://services.swpc.noaa.gov/json/goes/primary/xray-flares-latest.json',
            60, reports.xray)

    xray = wrap(xray)

    def kindex(self, irc, msg, args):
        """takes no arguments

        Show NOAA's three-day Kp forecast in three-hour UTC intervals.
        """
        self._reply_report(
            irc, 'kindex',
            'https://services.swpc.noaa.gov/text/3-day-forecast.txt',
            900, lambda content: reports.kindex(content, _color_index, K_COLORS))

    ki = kf = kindex = wrap(kindex)

    def forecast(self, irc, msg, args):
        """takes no arguments

        Show NOAA's 27-day solar outlook, starting with today (UTC).
        Each graph block represents one day; forecasts are not observations.
        """
        self._reply_report(
            irc, 'forecast',
            'https://services.swpc.noaa.gov/text/27-day-outlook.txt',
            3600, lambda content: reports.forecast(
                content, _color_index, SFI_COLORS, A_COLORS, K_COLORS))

    forecast = wrap(forecast)

    def _get_effective_indices(self):
        content = utils.web.getUrl(
            'https://prop.kc2g.com/api/essn.json?days=1',
            timeout=10, size=262144)
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError('Invalid effective indices response')
        rows = data.get('24h')
        if not isinstance(rows, list) or not rows:
            raise ValueError('Missing 24-hour effective indices')
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError('Invalid effective indices record')
        latest = max(rows, key=lambda row: float(row['time']))
        timestamp = float(latest['time'])
        sfi, ssn = float(latest['sfi']), float(latest['ssn'])
        if not all(math.isfinite(value) for value in (timestamp, sfi, ssn)):
            raise ValueError('Non-finite effective indices')
        if not -300 <= time.time() - timestamp <= 7200:
            raise ValueError('Effective indices are stale or future-dated')
        if sfi <= 0:
            raise ValueError('Invalid effective solar flux')
        return '%.1f' % sfi, '%.1f' % ssn

    def _get_bands(self):
        with self._bands_lock:
            if (self._bands_cache is not None
                    and time.monotonic() - self._bands_cached_at < 300):
                return self._bands_cache

            content = utils.web.getUrl(
                'https://www.hamqsl.com/solarxml.php',
                timeout=15, size=65536)
            data = ET.fromstring(content).find('solardata')
            if data is None:
                raise ValueError('Missing solar data')

            def field(name):
                value = ' '.join((data.findtext(name) or '').split())
                if not value:
                    raise ValueError('Missing %s' % name)
                return value

            lines = [
                'Bands as of %s: %s=%s %s=%s %s=%s %s=%s'
                % (ircutils.bold(field('updated')),
                   ircutils.bold('SFI'),
                   _color_index(field('solarflux'), SFI_COLORS),
                   ircutils.bold('SN'),
                   field('sunspots'),
                   ircutils.bold('A'),
                   _color_index(field('aindex'), A_COLORS, 400),
                   ircutils.bold('K'),
                   _color_index(field('kindex'), K_COLORS, 9))
            ]
            conditions = {}
            for band in data.findall('calculatedconditions/band'):
                conditions[(band.get('name'), band.get('time'))] = (
                    ' '.join((band.text or '').split()))
            for name in ('12m-10m', '17m-15m', '30m-20m', '80m-40m'):
                day = conditions.get((name, 'day'))
                night = conditions.get((name, 'night'))
                if not day or not night:
                    raise ValueError('Missing conditions for %s' % name)
                colors = {'good': 'green', 'fair': 'yellow', 'poor': 'red'}
                day = ircutils.mircColor(day, colors.get(day.lower()))
                night = ircutils.mircColor(night, colors.get(night.lower()))
                lines.append('| %s | day: %s | night: %s |'
                             % (name, day, night))

            try:
                esfi, essn = self._get_effective_indices()
            except (utils.web.Error, ValueError, TypeError, KeyError) as exc:
                self.log.warning('Could not get KC2G effective indices: %s', exc)
                esfi, essn = 'N/A', 'N/A'
            lines[0] += ' %s=%s %s=%s' % (
                ircutils.bold('eSFI'), _color_index(esfi, SFI_COLORS),
                ircutils.bold('eSSN'), essn)

            self._bands_cache = lines
            self._bands_cached_at = time.monotonic()
            return lines

    def bands(self, irc, msg, args):
        """takes no arguments

        Show HamQSL's general HF day/night band estimates and solar indices.
        """
        try:
            lines = self._get_bands()
        except utils.web.Error as exc:
            self.log.warning('Could not fetch HamQSL band conditions: %s', exc)
            irc.error('Unable to fetch band conditions from HamQSL. Try again later.')
            return
        except (ET.ParseError, ValueError) as exc:
            self.log.warning('Invalid HamQSL band conditions: %s', exc)
            irc.error('HamQSL returned incomplete or invalid band data. Try again later.')
            return
        irc.replies(lines, oneToOne=False)

    bands = wrap(bands)


Class = Radiohead


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
