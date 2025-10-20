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

import os
import json
import random
import time
from datetime import datetime
from supybot import utils, plugins, ircutils, callbacks, conf
from supybot.commands import *
from supybot.i18n import PluginInternationalization

_ = PluginInternationalization('PublicQuotes')


class PublicQuotes(callbacks.Plugin):
    """A channel-based quote management system for Limnoria with flood protection."""

    def __init__(self, irc):
        super().__init__(irc)
        # Plugin data directory
        self.data_dir = os.path.join(conf.supybot.directories.data(), 'PublicQuotes')
        os.makedirs(self.data_dir, exist_ok=True)
        # Flood tracking: {(nick, channel, command): last_time}
        self.flood_times = {}

    # --- Helper Methods ---
    def _get_channel_file(self, network, channel):
        safe_network = network.replace(':', '_')
        safe_channel = channel.replace('#', '')
        return os.path.join(self.data_dir, f"{safe_network}_{safe_channel}.json")

    def _load_quotes(self, network, channel):
        filename = self._get_channel_file(network, channel)
        if not os.path.isfile(filename):
            return []
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _save_quotes(self, network, channel, quotes):
        filename = self._get_channel_file(network, channel)
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(quotes, f, indent=2, ensure_ascii=False)

    def _flood_check(self, nick, channel, command):
        now = time.time()
        key = (nick, channel, command)
        last_time = self.flood_times.get(key, 0)
        if now - last_time < 20:  # 20s flood protection per command
            return True
        self.flood_times[key] = now
        return False

    def _format_quote(self, quote, idx, total):
        # Colors:
        # 02 = bold
        # 00 = white (quote)
        # 14 = grey (author/timestamp)
        quote_text = quote['text']
        author = quote['author']
        timestamp = quote.get('timestamp', '')  # fallback for older quotes
        if timestamp:
            try:
                dt = datetime.fromtimestamp(timestamp)
                date_str = dt.strftime('%d %b %Y')  # e.g., 18 Oct 2025
            except Exception:
                date_str = 'unknown date'
        else:
            date_str = 'unknown date'

        return (
            f"\x02[{idx}] of {total}\x02 "
            f"\x0300{quote_text}\x03 "
            f"(\x0314added by {author} on {date_str}\x03)"
        )

    # --- Commands ---
    def quote(self, irc, msg, args, quote_id):
        """[<id>]
        Shows a random quote, or a specific quote if <id> is given.
        """
        nick = msg.nick
        channel = msg.args[0]
        network = irc.network

        if self._flood_check(nick, channel, 'quote'):
            return

        quotes = self._load_quotes(network, channel)
        total = len(quotes)
        if not total:
            irc.reply("No quotes in this channel yet.")
            return

        if quote_id is None:
            quote_idx = random.randint(0, total - 1)
        else:
            if not quote_id.isdigit() or int(quote_id) < 1 or int(quote_id) > total:
                irc.reply(f"Invalid quote ID. Use 1-{total}.")
                return
            quote_idx = int(quote_id) - 1

        quote = quotes[quote_idx]
        irc.reply(self._format_quote(quote, quote_idx + 1, total))

    quote = wrap(quote, [optional('text')])

    def addquote(self, irc, msg, args, text):
        """<quote>
        Adds a quote to the channel.
        """
        nick = msg.nick
        channel = msg.args[0]
        network = irc.network

        if self._flood_check(nick, channel, 'addquote'):
            return

        timestamp = int(time.time())
        quotes = self._load_quotes(network, channel)
        quotes.append({'text': text, 'author': nick, 'timestamp': timestamp})
        self._save_quotes(network, channel, quotes)
        irc.reply(f"Quote added! Total quotes now: {len(quotes)}")

    addquote = wrap(addquote, ['text'])

    def removequote(self, irc, msg, args, quote_id):
        """<id>
        Removes a quote by its ID.
        """
        nick = msg.nick
        channel = msg.args[0]
        network = irc.network

        if self._flood_check(nick, channel, 'removequote'):
            return

        quotes = self._load_quotes(network, channel)
        total = len(quotes)
        if not total:
            irc.reply("No quotes to remove.")
            return

        if not quote_id.isdigit() or int(quote_id) < 1 or int(quote_id) > total:
            irc.reply(f"Invalid quote ID. Use 1-{total}.")
            return

        removed = quotes.pop(int(quote_id) - 1)
        self._save_quotes(network, channel, quotes)
        irc.reply(f"Removed quote {quote_id}: {removed['text']} (added by {removed['author']})")

    removequote = wrap(removequote, ['text'])

    def totalquotes(self, irc, msg, args):
        """Shows the total number of quotes in this channel."""
        nick = msg.nick
        channel = msg.args[0]
        network = irc.network

        if self._flood_check(nick, channel, 'totalquotes'):
            return

        quotes = self._load_quotes(network, channel)
        irc.reply(f"Total quotes in {channel}: {len(quotes)}")

    totalquotes = wrap(totalquotes)


Class = PublicQuotes

