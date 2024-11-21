###
# Copyright (c) 2024, spithash@Libera
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

from supybot import ircutils, callbacks, conf, registry
from supybot.commands import wrap, optional
from supybot.i18n import PluginInternationalization
import re

_ = PluginInternationalization('Greeter')


class Greeter(callbacks.Plugin):
    """Greets users who send specific greeting messages."""
    threaded = True

    def __init__(self, irc):
        super().__init__(irc)
        # Expanded default greeting keywords in various languages and styles
        self.greetings = [
            # English greetings
            'hi', 'hello', 'hey', 'howdy', 'hiya', 'yo', 'sup', 'what\'s up', 'greetings', 
            'morning', 'good morning', 'evening', 'good evening', 'afternoon', 'good afternoon',
            # Informal and regional
            'g\'day', 'howzit', 'aloha',
            # Spanish
            'hola', 'buenos días', 'buenas tardes', 'buenas noches',
            # French
            'bonjour', 'salut', 'bonsoir',
            # German
            'hallo', 'guten tag', 'guten morgen', 'servus',
            # Italian
            'ciao', 'buongiorno', 'buonasera',
            # Hebrew
            'shalom',
            # Arabic
            'salaam', 'marhaba',
            # Hindi
            'namaste', 'namaskar',
            # Japanese
            'konnichiwa', 'ohayo', 'konbanwa',
            # Korean
            'annyeong', 'annyeonghaseyo',
            # Chinese
            'ni hao',
            # Portuguese
            'olá', 'bom dia', 'boa tarde', 'boa noite',
            # Russian
            'privet', 'zdravstvuyte',
            # Other
            'hei', 'hej', 'hallo', 'tere', 'czesc'
        ]
        # Suffixes for group greetings
        self.group_suffixes = ['everyone', 'all']

    def greet_message(self, msg):
        """Craft the greeting message."""
        return f"Hello, {msg.nick}!"

    def doPrivmsg(self, irc, msg):
        """Listen for messages and greet if enabled and matching."""
        if not irc.isChannel(msg.channel):
            return

        channel = msg.channel
        network = irc.network

        # Check if greeter is enabled for this channel
        if not self.registryValue('enabled', channel, network):
            return

        # Extract the message text
        text = msg.args[1].strip().lower()

        # Check for exact greetings or group-based greetings
        if text in self.greetings:
            irc.reply(self.greet_message(msg))
        else:
            # Check for group greetings like "hello everyone" or "hi all"
            for greeting in self.greetings:
                for suffix in self.group_suffixes:
                    if text == f"{greeting} {suffix}":
                        irc.reply(self.greet_message(msg))
                        return

    @wrap(['channel', optional('text')])
    def setgreetings(self, irc, msg, args, channel, new_greetings):
        """[<greetings>]
        Set or display the list of greetings. Provide a comma-separated list to set new greetings.
        Example: 'hi, hello, hey, hola'."""
        if new_greetings:
            self.greetings = [g.strip().lower() for g in new_greetings.split(',') if g.strip()]
            irc.reply(f"Greetings set to: {', '.join(self.greetings)}")
        else:
            irc.reply(f"Current greetings: {', '.join(self.greetings)}")

    @wrap(['channel'])
    def enablegreeter(self, irc, msg, args, channel):
        """Enable the greeter in the current channel."""
        self.setRegistryValue('enabled', True, channel=channel, network=irc.network)
        irc.reply("Greeter enabled for this channel.")

    @wrap(['channel'])
    def disablegreeter(self, irc, msg, args, channel):
        """Disable the greeter in the current channel."""
        self.setRegistryValue('enabled', False, channel=channel, network=irc.network)
        irc.reply("Greeter disabled for this channel.")


Class = Greeter

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:

