###
# Copyright (c) 2026, Stathis Xantinidis spithash@Libera https://github.com/spithash
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
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.ircmsgs as ircmsgs
import supybot.callbacks as callbacks
import supybot.registry as registry
import groq
import threading
import time
import re
from collections import defaultdict

class GroqAI(callbacks.Plugin):
    """Query Groq's AI models from IRC."""
    
    def __init__(self, irc):
        self.__parent = super(GroqAI, self)
        self.__parent.__init__(irc)
        # Store enabled channels in memory for quick access
        self._enabled_channels = set()
        # Store user request timestamps for throttling
        self._user_last_request = defaultdict(float)

    def _load_enabled_channels(self):
        """Load enabled channels from registry."""
        try:
            # Get the stored list of enabled channels
            channels_str = self.registryValue('enabledChannels')
            if channels_str:
                self._enabled_channels = set(channels_str.split(','))
            else:
                self._enabled_channels = set()
        except:
            self._enabled_channels = set()

    def _save_enabled_channels(self):
        """Save enabled channels to registry."""
        try:
            # Convert set to comma-separated string
            channels_str = ','.join(self._enabled_channels)
            # Store it using the registry
            self.setRegistryValue('enabledChannels', channels_str)
        except Exception as e:
            self.log.error(f"Failed to save enabled channels: {e}")

    def _is_channel_enabled(self, channel):
        """Check if a channel is enabled for AI responses."""
        if not channel:
            return False
        # Always reload from registry to keep in sync across restarts
        self._load_enabled_channels()
        return channel in self._enabled_channels

    def _check_throttle(self, user):
        """Check if the user is being throttled."""
        # Get throttle settings from registry
        try:
            throttle_seconds = self.registryValue('throttleSeconds')
        except:
            throttle_seconds = 12  # Default: 12 seconds between requests
            
        try:
            throttle_enabled = self.registryValue('throttleEnabled')
        except:
            throttle_enabled = True  # Default: throttling enabled
            
        # If throttling is disabled, allow all requests
        if not throttle_enabled:
            return True, None
            
        # Get the last request time for this user
        last_request = self._user_last_request.get(user, 0)
        current_time = time.time()
        
        # Check if enough time has passed
        if current_time - last_request < throttle_seconds:
            remaining = int(throttle_seconds - (current_time - last_request))
            return False, remaining
        else:
            # Update the last request time
            self._user_last_request[user] = current_time
            return True, None

    def _clean_response(self, text):
        """Clean up the AI response for IRC."""
        # First, convert actual newlines to spaces (for single line responses)
        text = text.replace('\n', ' ')
        
        # Now replace literal \n with spaces (if any remain)
        text = text.replace('\\n', ' ')
        
        # Replace literal \t with spaces
        text = text.replace('\\t', ' ')
        
        # Remove multiple spaces
        text = re.sub(r' +', ' ', text)
        
        # Remove leading/trailing whitespace
        text = text.strip()
        
        return text

    def _format_response(self, text, use_formatting=True):
        """Optionally format the response with IRC formatting."""
        if not use_formatting:
            return text
        
        # You can add formatting here if desired
        return text

    def _process_ask(self, irc, msg, question):
        """Internal method to process the ask command."""
        # Check if this channel is enabled
        channel = msg.args[0] if msg.args else None
        if not self._is_channel_enabled(channel):
            irc.error(f"GroqAI is not enabled in this channel. Use {self.canonicalName()} enable to enable it.")
            return

        # Check throttling for the user (use hostmask or nick)
        user = msg.prefix  # Full hostmask for unique identification
        throttle_result = self._check_throttle(user)
        
        if not throttle_result[0]:
            remaining = throttle_result[1]
            # Send a notice to the user with the remaining time
            irc.sendMsg(ircmsgs.notice(msg.nick, 
                f"You are being throttled. Please wait {remaining} seconds before using @ask again."))
            return

        # Get configuration values
        try:
            api_key = self.registryValue('apiKey')
        except:
            api_key = ''
            
        try:
            model = self.registryValue('model')
        except:
            model = 'llama-3.1-8b-instant'
            
        # Hardcoded values (or use registry if available)
        max_tokens = 1024
        temperature = 0.7
        
        # Validate API key is set
        if not api_key:
            irc.error("The Groq API key is not set. Please set plugins.GroqAI.apiKey.")
            return

        # Track when the request starts
        thinking_shown = False
        thinking_timer = None

        # Function to show "Thinking..." after 3 seconds
        def show_thinking():
            nonlocal thinking_shown
            if not thinking_shown:
                irc.reply("Thinking...", prefixNick=True)
                thinking_shown = True

        # Start a timer that will show "Thinking..." after 3 seconds
        timer = threading.Timer(3.0, show_thinking)
        timer.daemon = True
        timer.start()

        try:
            # Initialize the Groq client with the API key
            client = groq.Groq(api_key=api_key)

            # Send the question to Groq with all configurable parameters
            chat_completion = client.chat.completions.create(
                messages=[
                    {
                        "role": "user",
                        "content": question,
                    }
                ],
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            # Cancel the timer if it hasn't fired yet
            timer.cancel()

            # Get the answer and clean it up
            answer = chat_completion.choices[0].message.content
            
            # Clean up the response
            answer = self._clean_response(answer)
            
            # Send the response - Limnoria will automatically handle truncation
            # and provide the @more functionality
            if thinking_shown:
                # If we showed "Thinking...", we need to send as a separate message
                irc.reply(answer, prefixNick=True)
            else:
                # Otherwise, reply normally (Limnoria will handle @more)
                irc.reply(answer, prefixNick=True)

        except groq.APIConnectionError as e:
            timer.cancel()
            irc.error(f"Connection error to Groq API: {e}")
        except groq.RateLimitError as e:
            timer.cancel()
            irc.error(f"Rate limit exceeded. Please wait a moment and try again. Error: {e}")
        except groq.APIStatusError as e:
            timer.cancel()
            irc.error(f"Groq API error: {e}")
        except Exception as e:
            timer.cancel()
            irc.error(f"An error occurred while querying Groq: {e}")

    @wrap(['text'])
    def ask(self, irc, msg, args, question):
        """<question>

        Asks Groq's AI a question and replies with the answer.
        """
        self._process_ask(irc, msg, question)

    @wrap(['text'])
    def ai(self, irc, msg, args, question):
        """<question>

        Alias for ask. Asks Groq's AI a question and replies with the answer.
        """
        self._process_ask(irc, msg, question)

    # Note: The commands below are added to the plugin's command set
    # They will be available as @groq enable, @groq disable, etc.
    
    @wrap([])
    def enable(self, irc, msg, args):
        """Enable GroqAI in the current channel."""
        channel = msg.args[0] if msg.args else None
        if not channel:
            irc.error("This command must be used in a channel.")
            return
        
        # Check if user has permission (requires op or admin)
        if not ircutils.isChannel(channel):
            irc.error("This command must be used in a channel.")
            return
        
        # Load current enabled channels
        self._load_enabled_channels()
        
        # Add current channel
        self._enabled_channels.add(channel)
        self._save_enabled_channels()
        
        irc.reply(f"GroqAI has been enabled in {channel}.", prefixNick=True)

    @wrap([])
    def disable(self, irc, msg, args):
        """Disable GroqAI in the current channel."""
        channel = msg.args[0] if msg.args else None
        if not channel:
            irc.error("This command must be used in a channel.")
            return
        
        # Check if user has permission (requires op or admin)
        if not ircutils.isChannel(channel):
            irc.error("This command must be used in a channel.")
            return
        
        # Load current enabled channels
        self._load_enabled_channels()
        
        # Remove current channel
        if channel in self._enabled_channels:
            self._enabled_channels.remove(channel)
            self._save_enabled_channels()
            irc.reply(f"GroqAI has been disabled in {channel}.", prefixNick=True)
        else:
            irc.reply(f"GroqAI was not enabled in {channel}.", prefixNick=True)

    @wrap([])
    def status(self, irc, msg, args):
        """Show the status of GroqAI in the current channel."""
        channel = msg.args[0] if msg.args else None
        if not channel:
            irc.error("This command must be used in a channel.")
            return
        
        # Load current enabled channels
        self._load_enabled_channels()
        
        if channel in self._enabled_channels:
            irc.reply(f"GroqAI is currently ENABLED in {channel}.", prefixNick=True)
        else:
            irc.reply(f"GroqAI is currently DISABLED in {channel}.", prefixNick=True)

    @wrap([])
    def list(self, irc, msg, args):
        """List all channels where GroqAI is enabled."""
        self._load_enabled_channels()
        if self._enabled_channels:
            channels = ', '.join(sorted(self._enabled_channels))
            irc.reply(f"GroqAI is enabled in: {channels}", prefixNick=True)
        else:
            irc.reply("GroqAI is not enabled in any channels.", prefixNick=True)

Class = GroqAI

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
