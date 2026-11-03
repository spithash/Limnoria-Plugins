Radiohead provides amateur radio conditions, reception spots, satellite passes
and frequencies, launch schedules, aviation reports, Sun/Moon positions,
public QSL activity, grid calculations, and offline radio utilities for Limnoria.

Install dependencies in the bot's Python environment: `ephem` and
`beautifulsoup4` (declared in `pyproject.toml`). Both are already available
in this workspace's Limnoria environment.

Load with `load Radiohead`, then use `bands` (for example, `!bands` if your
bot's command prefix is `!`).

The command shows HamQSL/N0NBH's update time, solar flux, sunspot number,
A/K indices, and four groups of HF bands with day/night ratings. These are
general propagation estimates, not location-specific reception reports.
Ratings use IRC colors: green for Good, yellow for Fair, and red for Poor.
The timestamp and index labels are bold. Index color thresholds match
[qrmbot's implementation](https://github.com/molo1134/qrmbot/blob/master/lib/bands)
and its IRC palette in `lib/Colors.pm`:

| SFI/eSFI range | Color |
| --- | --- |
| Below 85 | Dark red (05) |
| 85 to below 105 | Red (04) |
| 105 to below 125 | Dark yellow/orange (07) |
| 125 to below 145 | Yellow (08) |
| 145 to below 165 | Green (03) |
| 165 to below 185 | Light green (09) |
| 185 to below 205 | Light blue (12) |
| 205 to below 225 | Cyan (10) |
| 225 to below 235 | Light cyan (11) |
| 235 and above | Magenta (06) |

A uses green below 20, yellow from 20 to below 30, red from 30 to below 50,
dark red from 50 to below 100, and light blue from 100.
K uses green below 4, yellow from 4 to below 5, red from 5 to below 8,
and light blue from 8. SN and eSSN values remain neutral.
These colors are display conventions, not local propagation forecasts.
Unavailable or invalid index values remain neutral.

The header also includes eSFI and eSSN from KC2G's latest 24-hour estimate.
Both use one decimal place; eSFI uses the same colors as SFI.
KC2G observations have their own timestamps, separate from the HamQSL timestamp
shown in the header. If KC2G is unavailable, returns invalid data, or its latest
estimate is over two hours old, both values display as `N/A` and the HamQSL
report still works.

Source: https://www.hamqsl.com/solarxml.php
Effective indices: https://prop.kc2g.com/essn/

Successful results are cached for five minutes. Requests time out after
15 seconds for HamQSL and 10 seconds for KC2G; unavailable or invalid HamQSL
feeds produce a short error message.

## More radio reports

All commands use the bot's configured prefix and require no API keys.

| Command | Output | Cache |
| --- | --- | --- |
| `xray` | Current GOES X-ray class with observation time and latest reported flare peak | 1 minute |
| `kindex`, `ki`, `kf` | Three-day Kp forecast, with a graph and decimal values for eight three-hour UTC intervals per day | 15 minutes |
| `forecast` | Remaining days in NOAA's 27-day outlook: daily maximum Kp, planetary Ap, and solar flux | 1 hour |

`xray` uses green for A/B/C, yellow for M, red for X below X10, and magenta
for X10 and above, following qrmbot. Its current timestamp comes from the
observation, not the end time of the earlier flare. X-ray levels describe
solar activity; they are not a location-specific HF blackout report.

Forecast graphs use the same numeric color thresholds as `bands`. The Kp
graph has one block per three-hour interval; the outlook has one block per
UTC day, starting today or the first available future day. Each outlook
line also shows its minimum and maximum value. These are forecasts, not
confirmed band openings. No past data is silently substituted for a failed
fetch. All three commands have a 15-second request timeout.

NOAA sources:

- https://services.swpc.noaa.gov/json/goes/primary/xray-flares-latest.json
- https://services.swpc.noaa.gov/text/3-day-forecast.txt
- https://services.swpc.noaa.gov/text/27-day-outlook.txt

Inspired by [qrmbot's radio commands](https://github.com/molo1134/qrmbot/blob/master/botcommands.md),
implemented in Python for Limnoria.

## Community radio commands

Examples below omit your configured command prefix.

| Command | Example | Purpose |
| --- | --- | --- |
| `iono` | `iono KM17` or `iono 37.98,23.73` | Nearest recent ionosonde with usable confidence and foF2/MUF(D) measurements |
| `spots` | `spots SV1ABC/P 3` | Latest callsign reports from PSK Reporter and POTA, with kHz, mode, reporter and UTC time |
| `potaspots` | `potaspots 20m CW 3` | Filter POTA activators by band/mode; accepts `--20m --cw` too |
| `activity` | `activity FT8 KM17` | PSK Reporter activity totals by band for a two/four-character grid over 15 minutes |
| `contests` | `contests` | Up to five current/upcoming contests, with UTC times and detail links |
| `coax` / `atten` | `coax LMR-400 145 MHz 20 m` | Estimate matched cable attenuation and percentage of power remaining |
| `morse` / `cw` | `morse CQ TEST` | Encode letters, digits, punctuation, and Greek characters |
| `unmorse` / `demorse` | `unmorse -.-. --.- / - . ... -` | Decode Morse; spaces between letters and `/` between words |
| `qcode` | `qcode QRM` or `qcode interference` | Exact Q-code lookup or bounded definition search |

`iono` accepts 2/4/6/8-character Maidenhead grids and decimal `latitude,longitude`.
It excludes measurements over one hour old, confidence scores of 20 or below,
and stations lacking usable foF2/MUF(D). The nearest reporting station can be
far away. A station's MUF(D) is not a prediction for every path from your location.
Its source payload is cached for five minutes; age is checked on every command.

`spots` and `potaspots` default to three results, maximum five, and exclude
spots over one hour old. POTA is cached for one minute and shared between
the commands. PSK Reporter results are cached for five minutes; refreshes
across all callsigns are also limited to one PSK query per five minutes.
If one source fails, `spots` explicitly labels its partial results.
`activity` caches each mode/grid query for five minutes. Activity is reported
reception, not a propagation score or a count of unique stations.

PSK Reporter returned HTTP 403 during development from this environment.
Its reception and activity parsers were tested with fixtures, but live PSK
retrieval could not be verified here. These commands report source failures
without interpreting a blocked response as zero activity. `spots` can still
return matching POTA reports while PSK Reporter is unavailable.

`contests` caches the WA7BNM weekly iCalendar feed for 30 minutes and filters
finished events on every invocation. It is limited to the events published
in that feed. Network requests have a 20-second timeout and cache failures
for the source's cache interval to avoid repeated requests during outages.

The offline tools need no network or API keys. `coax list` lists 28 cable
types; `coax <type>` shows its nominal attenuation table. Frequency units
are Hz/kHz/MHz/GHz; length units are metres or feet, default 100 ft. Estimates
use linear interpolation within the published table's frequency range,
with no extrapolation. Tables are generic nominal values imported from
qrmbot, not current manufacturer guarantees; mismatch and connectors are
excluded. `RG58` is an alias for `RG-58/U`.

`unmorse` supports `--greek`, `--cyrillic`/`--russian`, `--hebrew`, and
`--japanese`/`--wabun`; default is international. Unsupported characters
and unknown Morse symbols produce a clear error. `qcode` includes 290
definitions from qrmbot and caps search output at four matches plus a count.

Additional sources:

- Ionosondes: https://prop.kc2g.com/api/stations.json
- POTA: https://api.pota.app/spot/activator
- PSK reception reports: https://retrieve.pskreporter.info/query
- PSK activity: https://pskreporter.info/cgi-bin/psk-freq.pl
- Contest calendar: https://www.contestcalendar.com/weeklycontcustom.ics
- Offline tables: https://github.com/molo1134/qrmbot (`lib/morse`, `lib/unmorse`,
  `lib/qcode`, and `lib/atten`); see [third-party notices](THIRD_PARTY_NOTICES.md).

## Space, aviation, astronomy, and public QSL tools

| Command | Example | Output |
| --- | --- | --- |
| `solar` | `solar` | One-line solar-index summary, sharing the `bands` cache |
| `launch` | `launch` or `launch Rocket Lab` | Next matching future launch, NET time, precision, status, provider and site |
| `spacex` | `spacex` | Shortcut for `launch SpaceX` |
| `satpass` | `satpass ISS KM17 2` | Up to three complete satellite passes within 48 hours, with AOS, peak, LOS, azimuth and elevation |
| `satinfo` | `satinfo ISS` or `satinfo 25544` | Up to three transmitter listings with uplink/downlink frequencies and modes |
| `qsl` | `qsl W1AW` | Public US FCC callbook details, LoTW activity, and eQSL profile/AG information |
| `fest` | `fest FL` | Up to three ARRL hamfests in a US state in the next 90 days |
| `adsb` | `adsb AAL100` | Current reported aircraft matching a flight callsign |
| `adsb` | `adsb reg:N123AB` or `adsb 0x469c35` | Aircraft by registration or six-digit ICAO hex identifier |
| `adsb` | `adsb KM17` or `adsb 37.98,23.73` | Up to three nearest reported aircraft within 25 nautical miles |
| `hofh` | `hofh`, `hofh 1`, or `hofh antenna` | Random, numbered, or searched humorous radio-failure excuses |
| `sun` | `sun KM17` | Solar azimuth/altitude and next rise/set times |
| `moon` | `moon KM17` | Lunar azimuth/altitude, illuminated percentage, distance, and next rise/set |
| `wwv` | `wwv` in a channel | A text announcement four seconds before a coming UTC minute, followed by `<beep>` |
| `metar` | `metar LGAV` or `metar KJFK` | Raw airport METAR with its observation timestamp |
| `qth` | `qth KM17` or `qth 37.98,23.73` | Coordinates and six-character Maidenhead locator |
| `qth` | `qth KM17 de FN31` | Distance and initial great-circle bearing from FN31 to KM17 |

Satellite passes use PyEphem and AMSAT orbital elements. TLEs are cached for
one hour; pass prediction rejects epochs older than 14 days or over one day
in the future. Rise/set times are geometric horizon crossings at sea level,
and only passes reaching at least 5° elevation are included. TLEs, terrain,
and station altitude affect accuracy. Satellite visibility is not proof of
an active transmitter. SatNOGS frequency listings are cached for one hour;
"listed active" is a database status, not a live reception confirmation.

Sun/Moon calculations use PyEphem at sea level with refraction disabled.
Locations accept Maidenhead grids or latitude/longitude. Place-name geocoding
is omitted because the original Google service requires a key. The `qth`
command likewise supports grids and coordinates without geographic name/address
lookup; `fest` uses explicit US state abbreviations instead of geocoding.

Launch Library results are cached for 15 minutes, with at most one upstream
refresh across searches every five minutes to respect the anonymous service's
quota. Cached searches remain available during that cooldown. NET means
"no earlier than"; the precision field may indicate a day/month rather than
a firm launch minute. Launch schedules change.

ADS-B data is cached for 30 seconds. Source snapshots and aircraft reports
over five minutes old are rejected; coverage is volunteer-dependent. Altitudes
are the source's barometric feet and speeds are knots. Registration lookups
use `reg:` explicitly; a bare callsign lookup falls back to registration when
no callsign match is returned. No adsb.fi or FlightAware credentials are used.
METAR reports are cached for five minutes and retain the observation timestamp.

`qsl` uses public Callook/FCC, LoTW, and eQSL information. Callook data is
cached for 24 hours, the LoTW CSV for 24 hours, and eQSL pages for one hour.
Callbook data is currently available for US amateur licenses. Missing source data is labelled unavailable, not
"not registered." eQSL profiles can have historical QTH date ranges; finding
a profile does not guarantee a station currently accepts eQSL confirmations.
QRZ lookups and Club Log's authenticated checks are omitted. ARRL hamfest
searches are cached for one hour and use the public search form.

`wwv` is a playful text emulation, not a calibrated time signal. It uses the
host clock and IRC delivery can lag. It allows one pending tone per network
and channel, does not repeat automatically, and cancels pending events when
Radiohead unloads/reloads. Requests arriving too close to the next minute
use the following minute so the four-second announcement can be sent.
The offline HoFH command uses 292 entries from qrmbot's excuse list.

Skipped credential-requiring services: **QRZ**, **APRS.fi**, authenticated
**Club Log**, **Google Maps geocoding**, and optional **adsb.fi/FlightAware**
enrichment. No credentials were requested, read, stored, or added to configuration.

Additional source URLs:

- Launches: https://ll.thespacedevs.com/2.3.0/launches/upcoming/
- Orbital elements: https://www.amsat.org/tle/current/nasabare.txt
- Satellite transmitters: https://db.satnogs.org/api/transmitters/
- Aircraft: https://api.adsb.lol/v2/
- LoTW activity: https://lotw.arrl.org/lotw-user-activity.csv
- US amateur callbook: https://callook.info/W1AW/json (FCC license records)
- eQSL profiles: https://www.eqsl.cc/Member.cfm?W1AW
- Hamfests: https://www.arrl.org/hamfests/search
- METAR: https://tgftp.nws.noaa.gov/data/observations/metar/stations/LGAV.TXT

Run the offline test suite with:

```text
limnoria-test --no-network /path/to/Radiohead
```
