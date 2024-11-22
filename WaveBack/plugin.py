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

import supybot.utils as utils
from supybot.commands import *
import supybot.callbacks as callbacks
import supybot.conf as conf
import supybot.registry as registry
import random
import re

class WaveBack(callbacks.Plugin):
    """A friendly bot that responds to greetings."""

    def __init__(self, irc):
        self.__parent = super(WaveBack, self)
        self.__parent.__init__(irc)
        self.enabled_channels = conf.supybot.plugins.WaveBack.enabledChannels()

        # Enriched greetings list (doubled size)
        self.greetings_keywords = [
            'hello', 'hi', 'hey', 'howdy', 'hola', 'greetings', 'sup',
            'yo', 'what\'s up', 'hiya', 'good day', 'morning', 'evening',
            'afternoon', 'salutations', 'bonjour', 'hallo', 'namaste',
            'shalom', 'ciao', 'aloha', 'hey there', 'hi there', 'ahoy',
            'wassup', 'whazzup', 'how are you', 'how\'s it going', 'what\'s new',
            'what\'s happening', 'how\'s life', 'peace', 'hiii', 'heya',
            'hiya there', 'how are ya', 'yo yo', 'hey yo', 'wassup yo',
            'how\'s everyone', 'what\'s good', 'how\'s your day', 'how\'s your night',
            'good evening', 'good morning', 'good afternoon', 'aloha friends',
            'cheerio', 'howdy y\'all', 'what\'s crackin', 'holler', 'what\'s poppin'
        ]

        # List of dynamic replies (doubled size)
        self.dynamic_replies = [
            "Hello, how can I help you today?",
            "Hi there! What’s up?",
            "Greetings! How may I assist?",
            "Hey! Need any help?",
            "Howdy! How’s everything?",
            "Hola! How can I assist you?",
            "Ahoy! What can I do for you?",
            "Hi! What’s on your mind?",
            "Hello there! How can I support you?",
            "Yo! What’s happening?",
            "Good day! How can I help?",
            "Namaste! What can I do for you?",
            "Bonjour! How may I assist you?",
            "Hey there! Anything I can do?",
            "Hi there! Need a hand?",
            "What’s up? How can I assist?",
            "Hey! Got something to discuss?",
            "Aloha! How can I help you?",
            "Hiya! What’s the task?",
            "Hello! Anything I can assist with?",
            "Howdy partner! Need some help?",
            "Hi there, friend! How’s life?",
            "Hello, how are things?",
            "Greetings, traveler! What’s your query?",
            "Hey, how’s it going?",
            "Hola, amigo! Anything on your mind?",
            "Good day, friend! Need something?",
            "Hey hey! What’s new?",
            "Ahoy, matey! What’s the task?",
            "Yo yo! How can I help?",
            "Hey, how’s your day?",
            "Namaste, my friend! Need a hand?",
            "Howdy-do! How can I assist?",
            "Hey, what’s cooking?",
            "Hi! How’s everything?",
            "Good morning! How can I help?",
            "Good evening! Need anything?",
            "Hello again! What’s up?",
            "Hey there, buddy! What’s on your mind?",
            "Hi hi! Got any questions?",
            "What’s new today? How can I help?",
            "Greetings, earthling! Need help?",
            "Aloha! What can I do for you?",
            "Hello, dear friend! How’s it going?",
            "Yo yo! Got something to share?",
            "Hiya, mate! How’s everything?",
            "Hey, cool cat! What’s shaking?",
            "Howdy, champ! Need a hand?"
        ]

    def doPrivmsg(self, irc, msg):
        """Respond to greetings."""
        try:
            # Log incoming message details for debugging
            self.log.debug(f"WaveBack: Received message in {msg.args[0]}: {msg.args[1]}")

            # Validate `msg.args` structure to avoid errors
            if not hasattr(msg, 'args') or len(msg.args) < 2:
                self.log.debug(f"WaveBack: Malformed msg.args: {getattr(msg, 'args', None)}")
                return  # Exit gracefully

            channel = msg.args[0]
            message_content = msg.args[1]

            # Log the channel and message content
            self.log.debug(f"WaveBack: Channel: {channel}, Message: {message_content}")

            # Ensure the channel is in the enabled list
            if channel in self.enabled_channels:
                self.log.debug(f"WaveBack: Channel {channel} is enabled")

                # Check if message content is a string
                if isinstance(message_content, str):
                    self.log.debug(f"WaveBack: Checking message content for greetings...")

                    # Tokenize message into words (case-insensitive)
                    words = re.findall(r'\b\w+\b', message_content.lower())

                    # Check if any greeting keyword matches
                    for keyword in self.greetings_keywords:
                        if keyword in words:  # Match as whole word only
                            self.log.debug(f"WaveBack: Found keyword '{keyword}' in message.")
                            reply = random.choice(self.dynamic_replies)
                            irc.reply(reply)
                            return  # Exit once a reply is sent

                    self.log.debug("WaveBack: No matching greeting found.")
                else:
                    self.log.debug("WaveBack: Message content is not a string.")
            else:
                self.log.debug(f"WaveBack: Channel {channel} is not in enabledChannels.")

        except Exception as e:
            self.log.error(f"WaveBack: Error in doPrivmsg: {e}")

# Register the plugin configuration
conf.registerPlugin('WaveBack')

conf.registerGlobalValue(
    conf.supybot.plugins.WaveBack,
    'enabledChannels',
    registry.SpaceSeparatedSetOfStrings(
        '',  # Default value (empty string means no channels initially)
        """A list of channels where the WaveBack plugin will respond to greetings."""
    )
)

Class = WaveBack

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:

