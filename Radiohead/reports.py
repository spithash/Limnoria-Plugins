"""Parse and format NOAA radio propagation reports for IRC.

Commands and data sources inspired by https://github.com/molo1134/qrmbot.
"""

from datetime import datetime, timezone
import json
import math
import re

from supybot import ircutils


def _utc(value):
    if not isinstance(value, str):
        raise ValueError('Missing or invalid observation time')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _flare(value):
    if not isinstance(value, str) or not re.fullmatch(r'[ABCMX]\d+(?:\.\d+)?', value):
        raise ValueError('Missing or invalid X-ray class')
    color = 'green'
    if value.startswith('M'):
        color = 'yellow'
    elif value.startswith('X'):
        color = 'purple' if float(value[1:]) >= 10 else 'red'
    return ircutils.mircColor(value, color)


def xray(content):
    data = json.loads(content)
    if not isinstance(data, list) or not data:
        raise ValueError('Missing X-ray observations')
    if any(not isinstance(row, dict) for row in data):
        raise ValueError('Invalid X-ray observations')
    latest = max(data, key=lambda row: _utc(row['time_tag']))
    observed = _utc(latest['time_tag']).strftime('%d %b %Y %H:%M UTC')
    line = 'X-ray status: %s as of %s' % (
        _flare(latest['current_class']), observed)
    if latest.get('max_class') and latest.get('max_time'):
        peak = _utc(latest['max_time']).strftime('%d %b %Y %H:%M UTC')
        line += '; latest reported flare peak: %s at %s' % (
            _flare(latest['max_class']), peak)
    else:
        line += '; latest flare peak: unavailable'
    return [line]


def _text(content):
    return content.decode('utf-8') if isinstance(content, bytes) else content


def _issued(text):
    match = re.search(r'^:Issued:\s*(.+)$', text, re.MULTILINE)
    if not match:
        raise ValueError('Missing forecast issue time')
    return match.group(1).strip()


def _number(value, maximum):
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= maximum:
        raise ValueError('Invalid forecast value')
    return number


def _colored_spark(values, scale, color_index, base=0, span=9):
    # Get the palette from the numeric value, then replace only its text.
    chars = '▁▂▃▄▅▆▇█'
    result = []
    for value in values:
        char = chars[min(7, max(0, int((value - base) * 7 / span)))]
        colored = color_index(str(value), scale)
        if colored.startswith('\x03'):
            result.append(colored[:3] + char + '\x03')
        else:
            result.append(char)
    return ''.join(result)


def kindex(content, color_index, k_colors):
    text = _text(content)
    issued = _issued(text)
    section = text.split('NOAA Kp index breakdown', 1)
    if len(section) != 2:
        raise ValueError('Missing Kp forecast')
    section = section[1].split('Rationale:', 1)[0]
    dates = re.search(
        r'^\s+([A-Z][a-z]{2}\s+\d{2})\s+'
        r'([A-Z][a-z]{2}\s+\d{2})\s+([A-Z][a-z]{2}\s+\d{2})\s*$',
        section, re.MULTILINE)
    if not dates:
        raise ValueError('Missing Kp forecast dates')
    slots, columns = [], [[], [], []]
    for slot, values in re.findall(r'^(\d{2}-\d{2})UT\s+([^\n]+)',
                                   section, re.MULTILINE):
        values = re.sub(r'\(G[1-5]\)', '', values).split()
        if len(values) != 3:
            raise ValueError('Incomplete Kp forecast row')
        slots.append(slot)
        for column, value in zip(columns, values):
            column.append(_number(value, 9))
    expected = ['%02d-%02d' % (hour, (hour + 3) % 24)
                for hour in range(0, 24, 3)]
    if slots != expected:
        raise ValueError('Incomplete Kp forecast intervals')
    lines = ['Kp forecast issued %s; UTC slots: 00,03,06,09,12,15,18,21'
             % issued]
    for date, values in zip(dates.groups(), columns):
        graph = _colored_spark(values, k_colors, color_index)
        numbers = ' '.join(color_index('%g' % value, k_colors)
                           for value in values)
        lines.append('%s: %s | Kp: %s' % (date, graph, numbers))
    return lines


def forecast(content, color_index, sfi_colors, a_colors, k_colors, today=None):
    text = _text(content)
    issued = _issued(text)
    if today is None:
        today = datetime.now(timezone.utc).date()
    rows = []
    for line in text.splitlines():
        if not re.match(r'^\d{4}\s', line):
            continue
        fields = line.split()
        if len(fields) != 6:
            raise ValueError('Invalid outlook row')
        date = datetime.strptime(' '.join(fields[:3]), '%Y %b %d').date()
        sfi, ap, kp = (_number(value, maximum) for value, maximum
                       in zip(fields[3:], (1000, 400, 9)))
        if date >= today:
            rows.append((date, sfi, ap, kp))
    if not rows:
        raise ValueError('No current or upcoming outlook data')
    rows.sort()
    if len(rows) > 27 or any((right[0] - left[0]).days != 1
                            for left, right in zip(rows, rows[1:])):
        raise ValueError('Invalid outlook date sequence')
    lines = [
        '27-day outlook issued %s; %s to %s UTC (one block/day)' % (
            issued, rows[0][0].isoformat(), rows[-1][0].isoformat())
    ]
    for label, index, scale, base, span in (
        ('Kp max', 3, k_colors, 0, 9),
        ('Ap', 2, a_colors, 0, 100),
        ('SFI', 1, sfi_colors, 75, 160),
    ):
        values = [row[index] for row in rows]
        graph = _colored_spark(values, scale, color_index, base, span)
        lines.append('%s: %s | range %g–%g'
                     % (label, graph, min(values), max(values)))
    return lines
