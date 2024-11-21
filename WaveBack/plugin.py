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

class WaveBack(callbacks.Plugin):
    """A friendly bot that responds to greetings."""

    def __init__(self, irc):
        self.__parent = super(WaveBack, self)
        self.__parent.__init__(irc)
        self.enabled_channels = conf.supybot.plugins.WaveBack.enabledChannels()
        # Enriched greetings list
        self.greetings_keywords = [
            'hello', 'hi', 'hey', 'howdy', 'hola', 'greetings', 'sup',
            'yo', 'what\'s up', 'hiya', 'good day', 'morning', 'evening',
            'afternoon', 'salutations', 'bonjour', 'hallo', 'namaste',
            'shalom', 'ciao', 'aloha', 'hey there', 'hi there', 'ahoy',
            'wassup', 'whazzup', 'how are you', 'how\'s it going', 'what\'s new'
        ]
        # List of dynamic replies
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
            "Hello! Anything I can assist with?"
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

                # Check if message content is a string and contains a greeting keyword
                if isinstance(message_content, str):
                    self.log.debug(f"WaveBack: Checking message content for greetings...")

                    # Check against greetings_keywords
                    for keyword in self.greetings_keywords:
                        if keyword in message_content.lower():
                            self.log.debug(f"WaveBack: Found keyword '{keyword}' in message.")
                            reply = random.choice(self.dynamic_replies)
                            irc.reply(reply)
                            return  # Exit once a reply is sent
                else:
                    self.log.debug("WaveBack: Message content is not a string.")
            else:
                self.log.debug(f"WaveBack: Channel {channel} is not in enabledChannels.")
                
            # If no greeting is found or not in enabled channel
            self.log.debug(f"WaveBack: No reply triggered for message.")

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

