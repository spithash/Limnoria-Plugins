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
import supybot.ircdb as ircdb
import groq
import threading
import time
import re
import datetime
import json
import os
import httpx
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
        # Store daily usage per user
        self._user_daily_usage = defaultdict(int)
        # Store daily tokens per user (exact from API)
        self._user_daily_tokens = defaultdict(int)
        # Track date for reset
        self._last_reset_date = datetime.datetime.now().date()
        # Data file path for persistence
        self._data_file = os.path.join(self._get_data_dir(), 'usage_data.json')
        # Store latest rate limit info from API
        self._rate_limits = {}
        # Load persisted data
        self._load_persisted_data()

    def _get_data_dir(self):
        """Get the data directory for the plugin."""
        # Use the bot's data directory
        try:
            import supybot.conf as conf
            data_dir = conf.supybot.directories.data()
            plugin_dir = os.path.join(data_dir, 'GroqAI')
            if not os.path.exists(plugin_dir):
                os.makedirs(plugin_dir)
            return plugin_dir
        except:
            # Fallback to current directory
            return os.path.dirname(os.path.abspath(__file__))

    def _load_persisted_data(self):
        """Load usage data from file."""
        try:
            if os.path.exists(self._data_file):
                with open(self._data_file, 'r') as f:
                    data = json.load(f)
                    
                # Load the data
                self._user_daily_usage = defaultdict(int, data.get('user_daily_usage', {}))
                self._user_daily_tokens = defaultdict(int, data.get('user_daily_tokens', {}))
                
                # Parse the saved date
                saved_date = data.get('last_reset_date')
                if saved_date:
                    self._last_reset_date = datetime.datetime.strptime(saved_date, '%Y-%m-%d').date()
                else:
                    self._last_reset_date = datetime.datetime.now().date()
                    
                self.log.info(f"Loaded persisted usage data from {self._data_file}")
            else:
                self.log.info("No persisted usage data found, starting fresh")
        except Exception as e:
            self.log.error(f"Error loading persisted data: {e}")
            # Start fresh on error
            self._user_daily_usage = defaultdict(int)
            self._user_daily_tokens = defaultdict(int)
            self._last_reset_date = datetime.datetime.now().date()

    def _save_persisted_data(self):
        """Save usage data to file."""
        try:
            data = {
                'user_daily_usage': dict(self._user_daily_usage),
                'user_daily_tokens': dict(self._user_daily_tokens),
                'last_reset_date': self._last_reset_date.strftime('%Y-%m-%d')
            }
            
            with open(self._data_file, 'w') as f:
                json.dump(data, f, indent=2)
                
            self.log.debug(f"Saved usage data to {self._data_file}")
        except Exception as e:
            self.log.error(f"Error saving persisted data: {e}")

    def _check_owner(self, irc, msg):
        """Check if user has owner capability."""
        try:
            if not ircdb.checkCapability(msg.prefix, 'owner'):
                irc.reply("Permission denied. Only bot owners can use this command.", private=True)
                return False
            return True
        except Exception as e:
            self.log.error(f"Error checking owner capability: {e}")
            irc.reply("Error checking permissions.", private=True)
            return False

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

    def _reset_daily_if_needed(self):
        """Reset daily counters if it's a new day."""
        today = datetime.datetime.now().date()
        if today != self._last_reset_date:
            self._user_daily_usage.clear()
            self._user_daily_tokens.clear()
            self._last_reset_date = today
            # Save the reset state
            self._save_persisted_data()
            self.log.info("Daily request and token counters reset")

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

    def _progress_bar(self, percent, width=10):
        """Create a simple progress bar."""
        filled = int(percent / 100 * width)
        empty = width - filled
        bar = "█" * filled + "░" * empty
        return f"[{bar}] {percent}%"

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

    def _parse_rate_limits(self, headers):
        """Parse rate limit headers from the response."""
        try:
            # Get rate limit headers (case-insensitive)
            limit_requests = headers.get('x-ratelimit-limit-requests')
            limit_tokens = headers.get('x-ratelimit-limit-tokens')
            remaining_requests = headers.get('x-ratelimit-remaining-requests')
            remaining_tokens = headers.get('x-ratelimit-remaining-tokens')
            reset_requests = headers.get('x-ratelimit-reset-requests')
            reset_tokens = headers.get('x-ratelimit-reset-tokens')
            
            # Store them
            self._rate_limits = {
                'limit_requests': int(limit_requests) if limit_requests else None,
                'limit_tokens': int(limit_tokens) if limit_tokens else None,
                'remaining_requests': int(remaining_requests) if remaining_requests else None,
                'remaining_tokens': int(remaining_tokens) if remaining_tokens else None,
                'reset_requests': reset_requests,
                'reset_tokens': reset_tokens,
            }
            
            self.log.info(f"Rate limits parsed: {self._rate_limits}")
            
            # Log warnings if we're running low
            if remaining_requests and int(remaining_requests) < 50:
                self.log.warning(f"Low on requests: {remaining_requests} remaining")
            if remaining_tokens and int(remaining_tokens) < 1000:
                self.log.warning(f"Low on tokens: {remaining_tokens} remaining")
                
        except Exception as e:
            self.log.error(f"Could not parse rate limits: {e}")
            self._rate_limits = {}

    def _process_ask(self, irc, msg, question):
        """Internal method to process the ask command."""
        # Check if this channel is enabled
        channel = msg.args[0] if msg.args else None
        if not self._is_channel_enabled(channel):
            irc.error(f"GroqAI is not enabled in this channel. Use {self.canonicalName()} enable to enable it.")
            return

        # Reset daily counters if new day
        self._reset_daily_if_needed()

        # Check throttling for the user (use hostmask or nick)
        user = msg.prefix  # Full hostmask for unique identification
        throttle_result = self._check_throttle(user)
        
        if not throttle_result[0]:
            remaining = throttle_result[1]
            # Send a notice to the user with the remaining time
            irc.sendMsg(ircmsgs.notice(msg.nick, 
                f"You are being throttled. Please wait {remaining} seconds before using @ask again."))
            return

        # Get limits from config
        try:
            daily_limit_per_user = self.registryValue('dailyLimitPerUser')
        except:
            daily_limit_per_user = 50  # Default: 50 requests per user per day
            
        try:
            global_daily_limit = self.registryValue('globalDailyLimit')
        except:
            global_daily_limit = 950  # Default: 950 total requests per day
            
        try:
            daily_tokens_per_user = self.registryValue('dailyTokensPerUser')
        except:
            daily_tokens_per_user = 10000  # Default: 10000 tokens per user per day
            
        try:
            global_daily_tokens = self.registryValue('globalDailyTokens')
        except:
            global_daily_tokens = 90000  # Default: 90000 total tokens per day

        # Check per-user daily request limit
        if daily_limit_per_user > 0:
            user_used = self._user_daily_usage.get(user, 0)
            if user_used >= daily_limit_per_user:
                irc.sendMsg(ircmsgs.notice(msg.nick, 
                    f"You've reached your daily limit of {daily_limit_per_user} requests. Try again tomorrow."))
                return

        # Check global daily request limit
        if global_daily_limit > 0:
            total_used = sum(self._user_daily_usage.values())
            if total_used >= global_daily_limit:
                irc.sendMsg(ircmsgs.notice(msg.nick, 
                    f"The bot has reached its global daily limit of {global_daily_limit} requests. Try again tomorrow."))
                return

        # Check per-user daily token limit
        if daily_tokens_per_user > 0:
            user_tokens_used = self._user_daily_tokens.get(user, 0)
            if user_tokens_used >= daily_tokens_per_user:
                irc.sendMsg(ircmsgs.notice(msg.nick,
                    f"You've reached your daily token limit of {daily_tokens_per_user} tokens. Try again tomorrow."))
                return

        # Check global daily token limit
        if global_daily_tokens > 0:
            total_tokens_used = sum(self._user_daily_tokens.values())
            if total_tokens_used >= global_daily_tokens:
                irc.sendMsg(ircmsgs.notice(msg.nick,
                    f"The bot has reached its global daily token limit of {global_daily_tokens} tokens. Try again tomorrow."))
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
            
        try:
            max_tokens = self.registryValue('maxTokens')
        except:
            max_tokens = 1024
            
        try:
            temperature = self.registryValue('temperature')
        except:
            temperature = 0.7
        
        # Validate API key is set
        if not api_key:
            irc.error("The Groq API key is not set. Please set plugins.GroqAI.apiKey.")
            return

        # Track when the request starts
        thinking_shown = False

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
            # Use httpx to make the request directly (so we can get headers)
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": question}],
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    }
                )
                
                # Parse rate limit headers
                self._parse_rate_limits(response.headers)
                
                # Check if request was successful
                if response.status_code != 200:
                    error_msg = response.json().get('error', {}).get('message', 'Unknown error')
                    irc.error(f"Groq API error: {error_msg}")
                    return
                
                # Parse the response
                data = response.json()
                answer = data['choices'][0]['message']['content']
                
                # Get token usage
                usage = data.get('usage', {})
                prompt_tokens = usage.get('prompt_tokens', 0)
                completion_tokens = usage.get('completion_tokens', 0)
                total_tokens = usage.get('total_tokens', 0)

            # Cancel the timer if it hasn't fired yet
            timer.cancel()

            # Clean up the response
            answer = self._clean_response(answer)
            
            # Log the token usage for debugging
            self.log.info(f"Token usage - Prompt: {prompt_tokens}, Completion: {completion_tokens}, Total: {total_tokens}")
            
            # Increment daily request counters
            self._user_daily_usage[user] = self._user_daily_usage.get(user, 0) + 1
            
            # Increment daily token counters with EXACT values from API
            self._user_daily_tokens[user] = self._user_daily_tokens.get(user, 0) + total_tokens
            
            # Save the updated data
            self._save_persisted_data()
            
            # Send the response - Limnoria will automatically handle truncation
            # and provide the @more functionality
            if thinking_shown:
                # If we showed "Thinking...", we need to send as a separate message
                irc.reply(answer, prefixNick=True)
            else:
                # Otherwise, reply normally (Limnoria will handle @more)
                irc.reply(answer, prefixNick=True)

        except httpx.TimeoutException:
            timer.cancel()
            irc.error("Connection timeout to Groq API")
        except httpx.HTTPStatusError as e:
            timer.cancel()
            irc.error(f"HTTP error: {e}")
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

    @wrap([])
    def enable(self, irc, msg, args):
        """Enable GroqAI in the current channel. Only bot owners can use this."""
        # Check if user is a bot owner
        if not self._check_owner(irc, msg):
            return
        
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
        """Disable GroqAI in the current channel. Only bot owners can use this."""
        # Check if user is a bot owner
        if not self._check_owner(irc, msg):
            return
        
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
        """List all channels where GroqAI is enabled. Only bot owners can use this."""
        # Check if user is a bot owner
        if not self._check_owner(irc, msg):
            return
        
        self._load_enabled_channels()
        if self._enabled_channels:
            channels = ', '.join(sorted(self._enabled_channels))
            irc.reply(f"GroqAI is enabled in: {channels}", prefixNick=True)
        else:
            irc.reply("GroqAI is not enabled in any channels.", prefixNick=True)

    @wrap([])
    def aiusage(self, irc, msg, args):
        """Show your daily AI usage and current rate limits."""
        # Reset daily counters if new day
        self._reset_daily_if_needed()
        
        user = msg.prefix
        used = self._user_daily_usage.get(user, 0)
        tokens_used = self._user_daily_tokens.get(user, 0)
        
        try:
            daily_limit = self.registryValue('dailyLimitPerUser')
        except:
            daily_limit = 50
            
        try:
            daily_tokens = self.registryValue('dailyTokensPerUser')
        except:
            daily_tokens = 10000
            
        total_used = sum(self._user_daily_usage.values())
        total_tokens = sum(self._user_daily_tokens.values())
        
        try:
            global_limit = self.registryValue('globalDailyLimit')
        except:
            global_limit = 950
            
        try:
            global_tokens = self.registryValue('globalDailyTokens')
        except:
            global_tokens = 90000
        
        # Build a user-friendly single-line response
        response_parts = []
        
        # Your personal usage
        response_parts.append(f"You: {used}/{daily_limit} req, {tokens_used}/{daily_tokens} tok")
        
        # Global usage
        response_parts.append(f"Global: {total_used}/{global_limit} req, {total_tokens}/{global_tokens} tok")
        
        # Groq API rate limits (from headers)
        if self._rate_limits:
            remaining_req = self._rate_limits.get('remaining_requests')
            remaining_tok = self._rate_limits.get('remaining_tokens')
            limit_req = self._rate_limits.get('limit_requests')
            limit_tok = self._rate_limits.get('limit_tokens')
            reset_req = self._rate_limits.get('reset_requests')
            reset_tok = self._rate_limits.get('reset_tokens')
            
            if remaining_req is not None and limit_req is not None:
                req_percent = int((remaining_req / limit_req) * 100)
                req_bar = self._progress_bar(req_percent)
                reset_msg = f" reset {reset_req}" if reset_req else ""
                response_parts.append(f"API Req: {remaining_req}/{limit_req} {req_bar}{reset_msg}")
                
            if remaining_tok is not None and limit_tok is not None:
                tok_percent = int((remaining_tok / limit_tok) * 100)
                tok_bar = self._progress_bar(tok_percent)
                reset_msg = f" reset {reset_tok}" if reset_tok else ""
                response_parts.append(f"API Tok: {remaining_tok}/{limit_tok} {tok_bar}{reset_msg}")
        else:
            response_parts.append("API: No data (make an @ask request)")
        
        # Join with | separator
        full_message = " | ".join(response_parts)
        irc.reply(full_message, prefixNick=True)

    @wrap([])
    def resetusage(self, irc, msg, args):
        """Reset all usage statistics. Only bot owners can use this."""
        # Check if user is a bot owner
        if not self._check_owner(irc, msg):
            return
        
        # Clear all usage data
        self._user_daily_usage.clear()
        self._user_daily_tokens.clear()
        self._last_reset_date = datetime.datetime.now().date()
        self._save_persisted_data()
        
        irc.reply("All usage statistics have been reset.", prefixNick=True)

Class = GroqAI

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
