###
# Copyright (c) 2026, Stathis Xantinidis spithash@Libera
# https://github.com/spithash
#
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
#   * Neither the name of the author of this software nor the names of its
#     contributors may be used to endorse or promote products derived from
#     this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
###

import datetime
import httpx
import json
import os
import re
import threading
import time

from collections import defaultdict

import supybot.callbacks as callbacks
import supybot.conf as conf
import supybot.ircdb as ircdb
import supybot.ircmsgs as ircmsgs
import supybot.ircutils as ircutils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.utils as utils


class GroqAI(callbacks.Plugin):
    """Query Groq Compound Mini from IRC."""

    def __init__(self, irc):
        super(GroqAI, self).__init__(irc)

        # --------------------------------------------------------------
        # CHANNELS
        # --------------------------------------------------------------

        self._enabled_channels = set()

        # --------------------------------------------------------------
        # THROTTLING
        # --------------------------------------------------------------

        self._user_last_request = defaultdict(float)

        # --------------------------------------------------------------
        # DAILY USAGE
        # --------------------------------------------------------------

        self._user_daily_usage = defaultdict(int)
        self._user_daily_tokens = defaultdict(int)

        self._last_reset_date = datetime.datetime.now().date()

        # --------------------------------------------------------------
        # PERSISTENCE
        # --------------------------------------------------------------

        self._data_file = os.path.join(
            self._get_data_dir(),
            "usage_data.json"
        )

        # --------------------------------------------------------------
        # GROQ RATE LIMITS
        # --------------------------------------------------------------

        self._rate_limits = {}

        # --------------------------------------------------------------
        # LOAD DATA
        # --------------------------------------------------------------

        self._load_persisted_data()

    # ==================================================================
    # DATA DIRECTORY
    # ==================================================================

    def _get_data_dir(self):
        """Return the plugin data directory."""

        try:
            data_dir = conf.supybot.directories.data()
            plugin_dir = os.path.join(data_dir, "GroqAI")

            if not os.path.exists(plugin_dir):
                os.makedirs(plugin_dir)

            return plugin_dir

        except Exception:
            return os.path.dirname(os.path.abspath(__file__))

    # ==================================================================
    # PERSISTENCE
    # ==================================================================

    def _load_persisted_data(self):
        """Load usage data from disk."""

        try:
            if not os.path.exists(self._data_file):
                self.log.info(
                    "No persisted GroqAI usage data found."
                )
                return

            with open(self._data_file, "r") as data_file:
                data = json.load(data_file)

            self._user_daily_usage = defaultdict(
                int,
                data.get("user_daily_usage", {})
            )

            self._user_daily_tokens = defaultdict(
                int,
                data.get("user_daily_tokens", {})
            )

            saved_date = data.get("last_reset_date")

            if saved_date:
                self._last_reset_date = datetime.datetime.strptime(
                    saved_date,
                    "%Y-%m-%d"
                ).date()
            else:
                self._last_reset_date = datetime.datetime.now().date()

            self.log.info(
                "Loaded GroqAI usage data from %s",
                self._data_file
            )

        except Exception as e:
            self.log.error(
                "Error loading GroqAI usage data: %s",
                e
            )

            self._user_daily_usage = defaultdict(int)
            self._user_daily_tokens = defaultdict(int)
            self._last_reset_date = datetime.datetime.now().date()

    def _save_persisted_data(self):
        """Save usage data to disk."""

        try:
            data = {
                "user_daily_usage": dict(
                    self._user_daily_usage
                ),
                "user_daily_tokens": dict(
                    self._user_daily_tokens
                ),
                "last_reset_date": (
                    self._last_reset_date.strftime("%Y-%m-%d")
                )
            }

            temp_file = self._data_file + ".tmp"

            with open(temp_file, "w") as data_file:
                json.dump(
                    data,
                    data_file,
                    indent=2
                )

            os.replace(
                temp_file,
                self._data_file
            )

        except Exception as e:
            self.log.error(
                "Error saving GroqAI usage data: %s",
                e
            )

    # ==================================================================
    # DAILY RESET
    # ==================================================================

    def _reset_daily_if_needed(self):
        """Reset daily counters when the date changes."""

        today = datetime.datetime.now().date()

        if today != self._last_reset_date:

            self._user_daily_usage.clear()
            self._user_daily_tokens.clear()

            self._last_reset_date = today

            self._save_persisted_data()

            self.log.info(
                "GroqAI daily usage counters reset."
            )

    # ==================================================================
    # CHANNEL CONFIGURATION
    # ==================================================================

    def _load_enabled_channels(self):
        """Load enabled channels from Limnoria registry."""

        try:
            channels = self.registryValue(
                "enabledChannels"
            )

            if channels:
                self._enabled_channels = set(
                    channel.strip()
                    for channel in channels.split(",")
                    if channel.strip()
                )
            else:
                self._enabled_channels = set()

        except Exception as e:
            self.log.error(
                "Unable to load enabled channels: %s",
                e
            )
            self._enabled_channels = set()

    def _save_enabled_channels(self):
        """Save enabled channels to Limnoria registry."""

        try:
            channels = ",".join(
                sorted(self._enabled_channels)
            )

            self.setRegistryValue(
                "enabledChannels",
                channels
            )

        except Exception as e:
            self.log.error(
                "Unable to save enabled channels: %s",
                e
            )

    def _is_channel_enabled(self, channel):
        """Return True if GroqAI is enabled in a channel."""

        if not channel:
            return False

        self._load_enabled_channels()

        return channel in self._enabled_channels

    # ==================================================================
    # OWNER CHECK
    # ==================================================================

    def _check_owner(self, irc, msg):
        """Check whether the sender has owner capability."""

        try:
            if not ircdb.checkCapability(
                msg.prefix,
                "owner"
            ):
                irc.reply(
                    "Permission denied. Only bot owners can use "
                    "this command.",
                    private=True
                )
                return False

            return True

        except Exception as e:
            self.log.error(
                "Error checking owner capability: %s",
                e
            )

            irc.reply(
                "Error checking permissions.",
                private=True
            )

            return False

    # ==================================================================
    # THROTTLING
    # ==================================================================

    def _check_throttle(self, user):
        """Check per-user request throttling."""

        try:
            throttle_enabled = self.registryValue(
                "throttleEnabled"
            )
        except Exception:
            throttle_enabled = True

        if not throttle_enabled:
            return True, None

        try:
            throttle_seconds = self.registryValue(
                "throttleSeconds"
            )
        except Exception:
            throttle_seconds = 12

        if throttle_seconds <= 0:
            return True, None

        current_time = time.time()

        last_request = self._user_last_request.get(
            user,
            0
        )

        elapsed = current_time - last_request

        if elapsed < throttle_seconds:

            remaining = int(
                throttle_seconds - elapsed
            ) + 1

            return False, remaining

        self._user_last_request[user] = current_time

        return True, None

    # ==================================================================
    # RATE LIMIT HEADERS
    # ==================================================================

    def _parse_rate_limits(self, headers):
        """Parse Groq rate-limit headers."""

        try:
            def get_int(name):
                value = headers.get(name)

                if value is None:
                    return None

                try:
                    return int(value)
                except (ValueError, TypeError):
                    return None

            self._rate_limits = {
                "limit_requests": get_int(
                    "x-ratelimit-limit-requests"
                ),
                "limit_tokens": get_int(
                    "x-ratelimit-limit-tokens"
                ),
                "remaining_requests": get_int(
                    "x-ratelimit-remaining-requests"
                ),
                "remaining_tokens": get_int(
                    "x-ratelimit-remaining-tokens"
                ),
                "reset_requests": headers.get(
                    "x-ratelimit-reset-requests"
                ),
                "reset_tokens": headers.get(
                    "x-ratelimit-reset-tokens"
                ),
                "retry_after": headers.get(
                    "retry-after"
                )
            }

            self.log.info(
                "Groq rate limits: %s",
                self._rate_limits
            )

        except Exception as e:
            self.log.error(
                "Could not parse Groq rate limits: %s",
                e
            )

            self._rate_limits = {}

    # ==================================================================
    # RESPONSE CLEANUP
    # ==================================================================

    def _clean_response(self, text):
        """Clean AI output for IRC."""

        if not text:
            return ""

        text = str(text)

        # Convert real newlines to spaces.
        text = text.replace("\r\n", " ")
        text = text.replace("\n", " ")
        text = text.replace("\r", " ")

        # Convert escaped whitespace.
        text = text.replace("\\n", " ")
        text = text.replace("\\r", " ")
        text = text.replace("\\t", " ")

        # Remove excessive whitespace.
        text = re.sub(r"\s+", " ", text)

        return text.strip()

    # ==================================================================
    # API ERROR EXTRACTION
    # ==================================================================

    def _get_api_error(self, response):
        """Extract a useful error message from Groq."""

        try:
            data = response.json()

            error = data.get(
                "error",
                {}
            )

            if isinstance(error, dict):
                message = error.get("message")

                if message:
                    return message

            if isinstance(error, str):
                return error

        except Exception:
            pass

        try:
            text = response.text.strip()

            if text:
                return text[:500]

        except Exception:
            pass

        return "Unknown Groq API error"

    # ==================================================================
    # REQUEST
    # ==================================================================

    def _process_ask(self, irc, msg, question):
        """Process an AI request."""

        channel = (
            msg.args[0]
            if msg.args
            else None
        )

        # --------------------------------------------------------------
        # CHANNEL ENABLED CHECK
        # --------------------------------------------------------------

        if not self._is_channel_enabled(channel):
            irc.error(
                "GroqAI is not enabled in this channel. "
                "Use {} enable to enable it.".format(
                    self.canonicalName()
                )
            )
            return

        # --------------------------------------------------------------
        # DAILY RESET
        # --------------------------------------------------------------

        self._reset_daily_if_needed()

        # --------------------------------------------------------------
        # USER ID
        # --------------------------------------------------------------

        user = msg.prefix

        # --------------------------------------------------------------
        # THROTTLE
        # --------------------------------------------------------------

        allowed, remaining = self._check_throttle(user)

        if not allowed:

            irc.sendMsg(
                ircmsgs.notice(
                    msg.nick,
                    "You are being throttled. Please wait "
                    "{} seconds before using @ai again.".format(
                        remaining
                    )
                )
            )

            return

        # --------------------------------------------------------------
        # CONFIGURATION
        # --------------------------------------------------------------

        try:
            api_key = self.registryValue(
                "apiKey"
            )
        except Exception:
            api_key = ""

        try:
            model = self.registryValue(
                "model"
            )
        except Exception:
            model = "groq/compound-mini"

        try:
            daily_limit_per_user = self.registryValue(
                "dailyLimitPerUser"
            )
        except Exception:
            daily_limit_per_user = 50

        try:
            global_daily_limit = self.registryValue(
                "globalDailyLimit"
            )
        except Exception:
            global_daily_limit = 950

        # --------------------------------------------------------------
        # API KEY
        # --------------------------------------------------------------

        if not api_key:

            irc.error(
                "The Groq API key is not set. "
                "Please set plugins.GroqAI.apiKey."
            )

            return

        # --------------------------------------------------------------
        # DAILY REQUEST LIMIT - USER
        # --------------------------------------------------------------

        user_used = self._user_daily_usage.get(
            user,
            0
        )

        if (
            daily_limit_per_user > 0
            and user_used >= daily_limit_per_user
        ):

            irc.sendMsg(
                ircmsgs.notice(
                    msg.nick,
                    "You've reached your daily limit of "
                    "{} requests. Try again tomorrow.".format(
                        daily_limit_per_user
                    )
                )
            )

            return

        # --------------------------------------------------------------
        # DAILY REQUEST LIMIT - GLOBAL
        # --------------------------------------------------------------

        total_used = sum(
            self._user_daily_usage.values()
        )

        if (
            global_daily_limit > 0
            and total_used >= global_daily_limit
        ):

            irc.sendMsg(
                ircmsgs.notice(
                    msg.nick,
                    "The bot has reached its global daily "
                    "limit of {} requests. Try again tomorrow.".format(
                        global_daily_limit
                    )
                )
            )

            return

        # --------------------------------------------------------------
        # REQUEST BODY
        #
        # IMPORTANT:
        #
        # Compound Mini's official Quick Start uses only:
        #
        #   model
        #   messages
        #
        # We deliberately do NOT send:
        #
        #   include_reasoning
        #   temperature
        #   max_completion_tokens
        #   stream
        #
        # This avoids the 400/413 problems encountered with
        # Compound Mini.
        # --------------------------------------------------------------

        request_body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": question
                }
            ]
        }

        # --------------------------------------------------------------
        # REQUEST SIZE
        # --------------------------------------------------------------

        try:
            request_bytes = len(
                json.dumps(
                    request_body,
                    ensure_ascii=False,
                    separators=(",", ":")
                ).encode("utf-8")
            )
        except Exception:
            request_bytes = 0

        self.log.info(
            "Groq request: model=%s compound=%s "
            "question_chars=%d request_bytes=%d",
            model,
            model.startswith("groq/compound"),
            len(question),
            request_bytes
        )

        # --------------------------------------------------------------
        # THINKING MESSAGE
        # --------------------------------------------------------------

        thinking_shown = False

        def show_thinking():
            nonlocal thinking_shown

            if not thinking_shown:

                try:
                    irc.reply(
                        "Thinking...",
                        prefixNick=True
                    )

                    thinking_shown = True

                except Exception as e:

                    self.log.error(
                        "Failed to send Thinking message: %s",
                        e
                    )

        timer = threading.Timer(
            3.0,
            show_thinking
        )

        timer.daemon = True
        timer.start()

        # --------------------------------------------------------------
        # API REQUEST
        # --------------------------------------------------------------

        try:

            with httpx.Client(
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=60.0,
                    write=10.0,
                    pool=10.0
                )
            ) as client:

                response = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": (
                            "Bearer {}".format(api_key)
                        ),
                        "Content-Type": "application/json"
                    },
                    json=request_body
                )

            # ----------------------------------------------------------
            # RATE LIMIT HEADERS
            # ----------------------------------------------------------

            self._parse_rate_limits(
                response.headers
            )

            # ----------------------------------------------------------
            # HTTP ERROR
            # ----------------------------------------------------------

            if response.status_code != 200:

                error_message = self._get_api_error(
                    response
                )

                self.log.error(
                    "Groq API error: HTTP %d - %s",
                    response.status_code,
                    error_message
                )

                timer.cancel()

                if response.status_code == 400:

                    irc.error(
                        "Groq rejected the request: {}".format(
                            error_message
                        )
                    )

                elif response.status_code == 413:

                    irc.error(
                        "Groq rejected the request as too large "
                        "(HTTP 413). Request body was {} bytes.".format(
                            request_bytes
                        )
                    )

                elif response.status_code == 429:

                    retry_after = (
                        self._rate_limits.get(
                            "retry_after"
                        )
                    )

                    if retry_after:

                        irc.error(
                            "Groq rate limit reached. "
                            "Retry after {}.".format(
                                retry_after
                            )
                        )
                    else:

                        irc.error(
                            "Groq rate limit reached. "
                            "Please try again later."
                        )

                elif response.status_code == 401:

                    irc.error(
                        "Groq authentication failed. "
                        "Check your API key."
                    )

                elif response.status_code == 403:

                    irc.error(
                        "Groq rejected the request due to "
                        "permissions or account restrictions."
                    )

                else:

                    irc.error(
                        "Groq API error (HTTP {}): {}".format(
                            response.status_code,
                            error_message
                        )
                    )

                return

            # ----------------------------------------------------------
            # PARSE RESPONSE
            # ----------------------------------------------------------

            try:
                data = response.json()

            except Exception as e:

                timer.cancel()

                self.log.error(
                    "Could not decode Groq response: %s",
                    e
                )

                irc.error(
                    "Groq returned an invalid response."
                )

                return

            # ----------------------------------------------------------
            # EXTRACT ANSWER
            # ----------------------------------------------------------

            try:

                answer = (
                    data["choices"][0]
                    ["message"]
                    ["content"]
                )

            except (KeyError, IndexError, TypeError):

                timer.cancel()

                self.log.error(
                    "Unexpected Groq response: %s",
                    data
                )

                irc.error(
                    "Groq returned an unexpected response."
                )

                return

            # ----------------------------------------------------------
            # TOKEN USAGE
            # ----------------------------------------------------------

            usage = data.get(
                "usage",
                {}
            )

            prompt_tokens = usage.get(
                "prompt_tokens",
                0
            ) or 0

            completion_tokens = usage.get(
                "completion_tokens",
                0
            ) or 0

            total_tokens = usage.get(
                "total_tokens",
                0
            ) or 0

            self.log.info(
                "Groq token usage: prompt=%s "
                "completion=%s total=%s",
                prompt_tokens,
                completion_tokens,
                total_tokens
            )

            # ----------------------------------------------------------
            # CLEAN RESPONSE
            # ----------------------------------------------------------

            answer = self._clean_response(
                answer
            )

            if not answer:

                timer.cancel()

                irc.error(
                    "Groq returned an empty response."
                )

                return

            # ----------------------------------------------------------
            # UPDATE USAGE
            # ----------------------------------------------------------

            self._user_daily_usage[user] = (
                self._user_daily_usage.get(
                    user,
                    0
                ) + 1
            )

            self._user_daily_tokens[user] = (
                self._user_daily_tokens.get(
                    user,
                    0
                ) + total_tokens
            )

            self._save_persisted_data()

            # ----------------------------------------------------------
            # CANCEL THINKING TIMER
            # ----------------------------------------------------------

            timer.cancel()

            # ----------------------------------------------------------
            # SEND ANSWER
            # ----------------------------------------------------------

            irc.reply(
                answer,
                prefixNick=True
            )

        # --------------------------------------------------------------
        # HTTPX ERRORS
        # --------------------------------------------------------------

        except httpx.TimeoutException:

            timer.cancel()

            self.log.error(
                "Timeout while connecting to Groq."
            )

            irc.error(
                "Connection timeout to Groq API."
            )

        except httpx.RequestError as e:

            timer.cancel()

            self.log.error(
                "HTTP request error while contacting Groq: %s",
                e
            )

            irc.error(
                "Could not connect to Groq API."
            )

        except Exception as e:

            timer.cancel()

            self.log.error(
                "Unexpected error while querying Groq: %s",
                e
            )

            irc.error(
                "An error occurred while querying Groq: {}".format(
                    e
                )
            )

    # ==================================================================
    # @ASK
    # ==================================================================

    @wrap(["text"])
    def ask(self, irc, msg, args, question):
        """<question>

        Ask Groq Compound Mini a question.
        """

        self._process_ask(
            irc,
            msg,
            question
        )

    # ==================================================================
    # @AI
    # ==================================================================

    @wrap(["text"])
    def ai(self, irc, msg, args, question):
        """<question>

        Alias for @ask.
        """

        self._process_ask(
            irc,
            msg,
            question
        )

    # ==================================================================
    # @ENABLE
    # ==================================================================

    @wrap([])
    def enable(self, irc, msg, args):
        """Enable GroqAI in the current channel.
        Only bot owners can use this command.
        """

        if not self._check_owner(irc, msg):
            return

        channel = (
            msg.args[0]
            if msg.args
            else None
        )

        if not channel:

            irc.error(
                "This command must be used in a channel."
            )

            return

        if not ircutils.isChannel(channel):

            irc.error(
                "This command must be used in a channel."
            )

            return

        self._load_enabled_channels()

        self._enabled_channels.add(
            channel
        )

        self._save_enabled_channels()

        irc.reply(
            "GroqAI has been enabled in {}.".format(
                channel
            ),
            prefixNick=True
        )

    # ==================================================================
    # @DISABLE
    # ==================================================================

    @wrap([])
    def disable(self, irc, msg, args):
        """Disable GroqAI in the current channel.
        Only bot owners can use this command.
        """

        if not self._check_owner(irc, msg):
            return

        channel = (
            msg.args[0]
            if msg.args
            else None
        )

        if not channel:

            irc.error(
                "This command must be used in a channel."
            )

            return

        if not ircutils.isChannel(channel):

            irc.error(
                "This command must be used in a channel."
            )

            return

        self._load_enabled_channels()

        if channel in self._enabled_channels:

            self._enabled_channels.remove(
                channel
            )

            self._save_enabled_channels()

            irc.reply(
                "GroqAI has been disabled in {}.".format(
                    channel
                ),
                prefixNick=True
            )

        else:

            irc.reply(
                "GroqAI was not enabled in {}.".format(
                    channel
                ),
                prefixNick=True
            )

    # ==================================================================
    # @STATUS
    # ==================================================================

    @wrap([])
    def status(self, irc, msg, args):
        """Show GroqAI status for the current channel."""

        channel = (
            msg.args[0]
            if msg.args
            else None
        )

        if not channel:

            irc.error(
                "This command must be used in a channel."
            )

            return

        self._load_enabled_channels()

        if channel in self._enabled_channels:

            irc.reply(
                "GroqAI is currently ENABLED in {}.".format(
                    channel
                ),
                prefixNick=True
            )

        else:

            irc.reply(
                "GroqAI is currently DISABLED in {}.".format(
                    channel
                ),
                prefixNick=True
            )

    # ==================================================================
    # @LIST
    # ==================================================================

    @wrap([])
    def list(self, irc, msg, args):
        """List channels where GroqAI is enabled.
        Only bot owners can use this command.
        """

        if not self._check_owner(irc, msg):
            return

        self._load_enabled_channels()

        if self._enabled_channels:

            channels = ", ".join(
                sorted(self._enabled_channels)
            )

            irc.reply(
                "GroqAI is enabled in: {}.".format(
                    channels
                ),
                prefixNick=True
            )

        else:

            irc.reply(
                "GroqAI is not enabled in any channels.",
                prefixNick=True
            )

    # ==================================================================
    # @AIUSAGE
    # ==================================================================

    @wrap([])
    def aiusage(self, irc, msg, args):
        """Show daily GroqAI usage and Groq rate limits."""

        self._reset_daily_if_needed()

        user = msg.prefix

        user_requests = (
            self._user_daily_usage.get(
                user,
                0
            )
        )

        user_tokens = (
            self._user_daily_tokens.get(
                user,
                0
            )
        )

        try:
            daily_limit = self.registryValue(
                "dailyLimitPerUser"
            )
        except Exception:
            daily_limit = 50

        try:
            global_limit = self.registryValue(
                "globalDailyLimit"
            )
        except Exception:
            global_limit = 950

        total_requests = sum(
            self._user_daily_usage.values()
        )

        total_tokens = sum(
            self._user_daily_tokens.values()
        )

        parts = [
            "You: {}/{} req".format(
                user_requests,
                daily_limit
            ),
            "Global: {}/{} req".format(
                total_requests,
                global_limit
            ),
            "Tokens: user={} global={}".format(
                user_tokens,
                total_tokens
            )
        ]

        # --------------------------------------------------------------
        # GROQ RATE LIMITS
        # --------------------------------------------------------------

        if self._rate_limits:

            remaining_requests = (
                self._rate_limits.get(
                    "remaining_requests"
                )
            )

            limit_requests = (
                self._rate_limits.get(
                    "limit_requests"
                )
            )

            remaining_tokens = (
                self._rate_limits.get(
                    "remaining_tokens"
                )
            )

            limit_tokens = (
                self._rate_limits.get(
                    "limit_tokens"
                )
            )

            reset_requests = (
                self._rate_limits.get(
                    "reset_requests"
                )
            )

            reset_tokens = (
                self._rate_limits.get(
                    "reset_tokens"
                )
            )

            if (
                remaining_requests is not None
                and limit_requests is not None
            ):

                parts.append(
                    "API req: {}/{}".format(
                        remaining_requests,
                        limit_requests
                    )
                )

            if (
                remaining_tokens is not None
                and limit_tokens is not None
            ):

                parts.append(
                    "API tok: {}/{}".format(
                        remaining_tokens,
                        limit_tokens
                    )
                )

            if reset_requests:

                parts.append(
                    "req reset: {}".format(
                        reset_requests
                    )
                )

            if reset_tokens:

                parts.append(
                    "tok reset: {}".format(
                        reset_tokens
                    )
                )

        else:

            parts.append(
                "API rate limits: no data yet"
            )

        irc.reply(
            " | ".join(parts),
            prefixNick=True
        )

    # ==================================================================
    # @RESETUSAGE
    # ==================================================================

    @wrap([])
    def resetusage(self, irc, msg, args):
        """Reset all GroqAI usage statistics.
        Only bot owners can use this command.
        """

        if not self._check_owner(irc, msg):
            return

        self._user_daily_usage.clear()
        self._user_daily_tokens.clear()

        self._last_reset_date = (
            datetime.datetime.now().date()
        )

        self._save_persisted_data()

        irc.reply(
            "All GroqAI usage statistics have been reset.",
            prefixNick=True
        )


Class = GroqAI


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
