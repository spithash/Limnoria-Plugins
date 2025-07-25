###
# Copyright (c) 2025, Stathis Xantinidis @ https://github.com/spithash
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

from supybot import utils, plugins, ircutils, callbacks
from supybot.commands import *
from supybot.i18n import PluginInternationalization
import os
from pathlib import Path
from collections import Counter
import datetime
import re

_ = PluginInternationalization('GraphStats')


class GraphStats(callbacks.Plugin):
    """Displays channel statistics as text-based graphs using ChannelLogger logs."""

    def _parse_logs(self, log_files, exclude_nick):
        counts = Counter()
        display_names = {}
        msg_re = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}  <([^>]+)>')

        for log_file in log_files:
            try:
                with open(log_file, encoding='utf-8') as f:
                    for line in f:
                        m = msg_re.match(line)
                        if m:
                            full_nick = m.group(1)
                            if '!' not in full_nick:
                                hostmask = f"{full_nick}!*@*"
                            else:
                                hostmask = full_nick

                            nick = full_nick.split('!')[0]
                            if nick.lower() == exclude_nick.lower():
                                continue

                            nick_lc = nick.lower()
                            counts[nick_lc] += 1

                            # Save the first-seen display variant (preserve capitalization)
                            if nick_lc not in display_names:
                                display_names[nick_lc] = nick
            except Exception:
                pass  # quietly ignore bad files or decoding issues

        # Convert lowercase-counts back to display names
        final_counts = Counter()
        for nick_lc, count in counts.items():
            final_counts[display_names[nick_lc]] = count
        return final_counts

    def _scale_bar(self, count, max_count, width=24):
        if max_count == 0:
            return ''
        bar_len = int((count / max_count) * width)
        return '━' * bar_len  # Sleek, thin and readable graph character

    def _get_log_files(self, base_path, network, channel, timeframe):
        channel_path = base_path / network / channel
        if not channel_path.exists():
            return []

        log_files = []
        today = datetime.date.today()

        # Rolling 12-month window
        if timeframe == 'yearly':
            cutoff = today - datetime.timedelta(days=365)
        for f in channel_path.iterdir():
            if f.is_file() and f.name.startswith(channel):
                try:
                    date_str = f.name.split('.', 1)[1].rsplit('.', 1)[0]
                    dt = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
                except Exception:
                    continue

                if timeframe == 'daily':
                    if dt == today:
                        log_files.append(f)
                elif timeframe == 'monthly':
                    if dt.year == today.year and dt.month == today.month:
                        log_files.append(f)
                elif timeframe == 'yearly':
                    if dt >= cutoff:
                        log_files.append(f)
                else:
                    log_files.append(f)
        return log_files

    def _format_stats(self, counts, timeframe):
        if not counts:
            return "No messages found for this timeframe."

        # Title line with color and bold
        title = f"📊 \x02\x0310Top Chatters ({timeframe.title()}, by number of lines):\x0F"
        max_nick_len = max(len(nick) for nick in counts)
        max_count = max(counts.values())
        lines = [title]

        for nick, count in counts.most_common(10):
            bar = self._scale_bar(count, max_count)
            line = f"\x0309{nick.ljust(max_nick_len)}\x0F  \x02\x0300{bar}\x0F \x0314{count}\x0F"
            lines.append(line)
        return '\n'.join(lines)

    def graphstats(self, irc, msg, args, timeframe=None):
        """
        Display channel message statistics from ChannelLogger logs.

        Optionally specify a timeframe: 'graphstats', 'graphstats monthly', or 'graphstats yearly'.
        Defaults to daily if no timeframe is given.
        """
        if timeframe is None:
            timeframe = 'daily'
        timeframe = timeframe.lower()
        valid_timeframes = ('daily', 'monthly', 'yearly')
        if timeframe not in valid_timeframes:
            irc.reply(f"Invalid timeframe '{timeframe}'. Use one of {valid_timeframes}.")
            return

        channel = msg.args[0]
        network = irc.network.lower()

        # Logs should be in logs/limnoria/ChannelLogger/<network>/<channel>/
        base_path = Path(os.getcwd()) / 'logs' / 'limnoria' / 'ChannelLogger'
        log_files = self._get_log_files(base_path, network, channel, timeframe)

        if not log_files:
            irc.reply(f"No logs found for {channel} on {network} in {timeframe} timeframe.")
            return

        bot_nick = irc.nick
        counts = self._parse_logs(log_files, bot_nick)

        reply = self._format_stats(counts, timeframe)

        # Send each line as a separate IRC message (better formatting)
        for line in reply.split('\n'):
            irc.reply(line)

    graphstats = wrap(graphstats, [optional('something')])


Class = GraphStats

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:

