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
import time
from collections import defaultdict, deque

import supybot.callbacks as callbacks
import supybot.conf as conf
import supybot.ircdb as ircdb
import supybot.ircmsgs as ircmsgs
import supybot.ircutils as ircutils
from supybot.commands import *
import supybot.plugins as plugins


class GroqAI(callbacks.Plugin):
    """Query Groq Compound Mini from IRC."""

    # IRC formatting control codes.
    IRC_BOLD = "\x02"
    IRC_UNDERLINE = "\x1f"

    def __init__(self, irc):
        super(GroqAI, self).__init__(irc)

        self._enabled_channels = set()

        # Per-user throttle.
        self._user_last_request = defaultdict(float)

        # Daily successful requests.
        self._user_daily_usage = defaultdict(int)

        # Daily API attempts.
        self._user_daily_attempts = defaultdict(int)

        # Daily successful token usage.
        self._user_daily_tokens = defaultdict(int)

        # Global API attempts.
        self._daily_attempts = 0

        # Global successful token usage.
        self._daily_tokens = 0

        # Rolling local RPM tracking.
        self._request_timestamps = deque()

        # Latest Groq rate-limit headers.
        self._rate_limits = {}

        self._last_reset_date = datetime.datetime.now().date()

        self._data_file = os.path.join(
            self._get_data_dir(),
            "usage_data.json"
        )

        self._load_persisted_data()

    # ------------------------------------------------------------------
    # DATA / PERSISTENCE
    # ------------------------------------------------------------------

    def _get_data_dir(self):
        try:
            data_dir = conf.supybot.directories.data()
            plugin_dir = os.path.join(data_dir, "GroqAI")

            if not os.path.exists(plugin_dir):
                os.makedirs(plugin_dir)

            return plugin_dir

        except Exception:
            return os.path.dirname(os.path.abspath(__file__))

    def _load_persisted_data(self):
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

            self._user_daily_attempts = defaultdict(
                int,
                data.get("user_daily_attempts", {})
            )

            self._user_daily_tokens = defaultdict(
                int,
                data.get("user_daily_tokens", {})
            )

            self._daily_attempts = int(
                data.get("daily_attempts", 0)
            )

            self._daily_tokens = int(
                data.get("daily_tokens", 0)
            )

            saved_date = data.get("last_reset_date")

            if saved_date:
                self._last_reset_date = (
                    datetime.datetime.strptime(
                        saved_date,
                        "%Y-%m-%d"
                    ).date()
                )

            today = datetime.datetime.now().date()

            if self._last_reset_date != today:
                self._user_daily_usage.clear()
                self._user_daily_attempts.clear()
                self._user_daily_tokens.clear()

                self._daily_attempts = 0
                self._daily_tokens = 0

                self._last_reset_date = today

                self._save_persisted_data()

            self.log.info(
                f"Loaded GroqAI usage data from "
                f"{self._data_file}"
            )

        except Exception as e:
            self.log.error(
                f"Error loading GroqAI usage data: {e}"
            )

            self._user_daily_usage = defaultdict(int)
            self._user_daily_attempts = defaultdict(int)
            self._user_daily_tokens = defaultdict(int)

            self._daily_attempts = 0
            self._daily_tokens = 0

            self._last_reset_date = (
                datetime.datetime.now().date()
            )

    def _save_persisted_data(self):
        try:
            data = {
                "user_daily_usage":
                    dict(self._user_daily_usage),

                "user_daily_attempts":
                    dict(self._user_daily_attempts),

                "user_daily_tokens":
                    dict(self._user_daily_tokens),

                "daily_attempts":
                    self._daily_attempts,

                "daily_tokens":
                    self._daily_tokens,

                "last_reset_date":
                    self._last_reset_date.strftime("%Y-%m-%d")
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
                f"Error saving GroqAI usage data: {e}"
            )

    def _reset_daily_if_needed(self):
        today = datetime.datetime.now().date()

        if today != self._last_reset_date:
            self._user_daily_usage.clear()
            self._user_daily_attempts.clear()
            self._user_daily_tokens.clear()

            self._daily_attempts = 0
            self._daily_tokens = 0

            self._last_reset_date = today

            self._save_persisted_data()

            self.log.info(
                "GroqAI daily usage counters reset."
            )

    # ------------------------------------------------------------------
    # CHANNELS
    # ------------------------------------------------------------------

    def _load_enabled_channels(self):
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
                f"Unable to load enabled channels: {e}"
            )
            self._enabled_channels = set()

    def _save_enabled_channels(self):
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
                f"Unable to save enabled channels: {e}"
            )

    def _is_channel_enabled(self, channel):
        if not channel:
            return False

        self._load_enabled_channels()

        return channel in self._enabled_channels

    # ------------------------------------------------------------------
    # OWNER
    # ------------------------------------------------------------------

    def _check_owner(self, irc, msg):
        try:
            if not ircdb.checkCapability(
                msg.prefix,
                "owner"
            ):
                irc.reply(
                    "Permission denied. "
                    "Only bot owners can use this command.",
                    private=True
                )
                return False

            return True

        except Exception as e:
            self.log.error(
                f"Error checking owner capability: {e}"
            )

            irc.reply(
                "Error checking permissions.",
                private=True
            )

            return False

    # ------------------------------------------------------------------
    # PER-USER THROTTLE
    # ------------------------------------------------------------------

    def _check_throttle(self, user):
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

        now = time.time()

        last_request = self._user_last_request.get(
            user,
            0
        )

        elapsed = now - last_request

        if elapsed < throttle_seconds:
            remaining = int(
                throttle_seconds - elapsed
            ) + 1

            return False, remaining

        self._user_last_request[user] = now

        return True, None

    # ------------------------------------------------------------------
    # LOCAL RPM
    # ------------------------------------------------------------------

    def _cleanup_rpm(self):
        now = time.time()

        while self._request_timestamps:
            if now - self._request_timestamps[0] >= 60:
                self._request_timestamps.popleft()
            else:
                break

    def _check_local_rpm(self):
        try:
            rpm_limit = self.registryValue(
                "localRpmLimit"
            )
        except Exception:
            rpm_limit = 30

        if rpm_limit <= 0:
            return True, None

        self._cleanup_rpm()

        if len(self._request_timestamps) >= rpm_limit:
            oldest = self._request_timestamps[0]

            wait_seconds = int(
                60 - (time.time() - oldest)
            ) + 1

            return False, max(
                1,
                wait_seconds
            )

        return True, None

    def _record_api_attempt(self):
        self._cleanup_rpm()
        self._request_timestamps.append(
            time.time()
        )

    # ------------------------------------------------------------------
    # GROQ RATE LIMIT HEADERS
    # ------------------------------------------------------------------

    def _parse_rate_limits(self, headers):
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
                f"Groq rate limits: "
                f"{self._rate_limits}"
            )

        except Exception as e:
            self.log.error(
                f"Could not parse Groq rate limits: {e}"
            )

            self._rate_limits = {}

    # ------------------------------------------------------------------
    # API ERROR
    # ------------------------------------------------------------------

    def _get_api_error(self, response):
        try:
            data = response.json()

            error = data.get(
                "error",
                {}
            )

            if isinstance(error, dict):
                message = error.get("message")

                if message:
                    return str(message)

            if isinstance(error, str):
                return error

        except Exception:
            pass

        try:
            text = response.text.strip()

            if text:
                return text[:1000]

        except Exception:
            pass

        return "Unknown Groq API error"

    # ------------------------------------------------------------------
    # 413 DIAGNOSTICS
    # ------------------------------------------------------------------

    def _log_413_diagnostic(
        self,
        response,
        request_body,
        request_bytes
    ):
        try:
            error_message = self._get_api_error(
                response
            )

            user_question = ""

            messages = request_body.get(
                "messages",
                []
            )

            if messages:
                user_question = messages[-1].get(
                    "content",
                    ""
                )

            self.log.error(
                "Groq HTTP 413 diagnostic: "
                f"question_chars={len(user_question)} "
                f"request_bytes={request_bytes} "
                f"error_message={error_message}"
            )

            # Do not expose Authorization headers.
            safe_headers = {}

            for key, value in response.headers.items():
                lower_key = key.lower()

                if lower_key in (
                    "authorization",
                    "cookie",
                    "set-cookie"
                ):
                    continue

                safe_headers[key] = value

            self.log.error(
                "Groq HTTP 413 response headers: "
                f"{safe_headers}"
            )

            self.log.error(
                "Groq HTTP 413 request body: "
                f"{json.dumps(request_body, ensure_ascii=False)}"
            )

            self.log.error(
                "Groq HTTP 413 rate-limit state: "
                f"{self._rate_limits}"
            )

        except Exception as e:
            self.log.error(
                f"Unable to log 413 diagnostics: {e}"
            )

    # ------------------------------------------------------------------
    # MARKDOWN / IRC FORMATTING
    # ------------------------------------------------------------------

    def _strip_bold_from_heading(self, line):
        """
        Headings should remain normal text.

        Example:
        **Key points**
        -> Key points

        But:
        The **important** point is...
        keeps its bold formatting.
        """

        stripped = line.strip()

        # Markdown heading.
        heading_match = re.fullmatch(
            r"#{1,6}\s+\*\*(.+?)\*\*",
            stripped
        )

        if heading_match:
            return heading_match.group(1).strip()

        # Standalone bold line.
        standalone_bold = re.fullmatch(
            r"\*\*(.+?)\*\*",
            stripped
        )

        if standalone_bold:
            return standalone_bold.group(1).strip()

        return line

    def _clean_markdown_tables(self, text):
        lines = text.splitlines()
        output = []

        table_rows = []

        def flush_table():
            nonlocal table_rows

            if not table_rows:
                return

            for row in table_rows:
                cells = [
                    cell.strip()
                    for cell in row.strip().strip("|").split("|")
                ]

                cells = [
                    cell
                    for cell in cells
                    if cell
                ]

                if not cells:
                    continue

                # Ignore separator rows:
                # |--------|--------|
                if all(
                    re.fullmatch(
                        r":?-{2,}:?",
                        cell.replace(" ", "")
                    )
                    for cell in cells
                ):
                    continue

                if len(cells) == 1:
                    output.append(
                        cells[0]
                    )
                else:
                    output.append(
                        " — ".join(cells)
                    )

            table_rows = []

        for line in lines:
            stripped = line.strip()

            # Markdown table row.
            if (
                stripped.startswith("|")
                and stripped.endswith("|")
                and stripped.count("|") >= 2
            ):
                table_rows.append(
                    stripped
                )
                continue

            # Markdown separator row.
            if re.fullmatch(
                r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?",
                stripped
            ):
                continue

            flush_table()

            output.append(
                self._strip_bold_from_heading(line)
            )

        flush_table()

        return "\n".join(output)

    def _convert_markdown_formatting(self, text):
        """
        Convert Markdown emphasis to IRC formatting.

        **text** -> IRC bold
        __text__ -> IRC underline

        Existing whitespace is deliberately preserved.
        """

        # Preserve whitespace outside the formatting markers.
        text = re.sub(
            r"\*\*(?!\s)(.+?)(?<!\s)\*\*",
            lambda match:
                self.IRC_BOLD
                + match.group(1)
                + self.IRC_BOLD,
            text,
            flags=re.DOTALL
        )

        text = re.sub(
            r"__(?!\s)(.+?)(?<!\s)__",
            lambda match:
                self.IRC_UNDERLINE
                + match.group(1)
                + self.IRC_UNDERLINE,
            text,
            flags=re.DOTALL
        )

        # Markdown headings.
        text = re.sub(
            r"(?m)^\s*#{1,6}\s+",
            "",
            text
        )

        # Markdown bullets.
        text = re.sub(
            r"(?m)^\s*[-*+]\s+",
            "• ",
            text
        )

        # Markdown links.
        text = re.sub(
            r"\[([^\]]+)\]\((https?://[^)]+)\)",
            r"\1 (\2)",
            text
        )

        # Inline code.
        text = re.sub(
            r"`([^`]+)`",
            r"\1",
            text
        )

        return text

    def _clean_response(self, text):
        if not text:
            return ""

        text = str(text)

        # Normalize line endings.
        text = text.replace(
            "\r\n",
            "\n"
        )

        text = text.replace(
            "\r",
            "\n"
        )

        # First convert Markdown tables.
        text = self._clean_markdown_tables(
            text
        )

        # Then convert emphasis.
        text = self._convert_markdown_formatting(
            text
        )

        # Remove Markdown horizontal rules.
        text = re.sub(
            r"(?m)^\s*([-*_])(?:\s*\1){2,}\s*$",
            "",
            text
        )

        # Remove any remaining literal pipe characters.
        text = text.replace(
            "|",
            " "
        )

        # Clean escaped line breaks.
        text = text.replace(
            "\\\n",
            " "
        )

        # Collapse whitespace but DO NOT remove whitespace
        # around IRC formatting codes.
        text = re.sub(
            r"[ \t]+",
            " ",
            text
        )

        # Collapse blank lines.
        text = re.sub(
            r"\n{2,}",
            "\n",
            text
        )

        # IRC response is sent as one message.
        text = re.sub(
            r"\s*\n\s*",
            " ",
            text
        )

        # Do NOT perform the old cleanup that stripped spaces
        # immediately next to \x02 / \x1f.
        #
        # That was the reason for:
        # "text" + BOLD + "next"
        # instead of:
        # "text " + BOLD + "bold" + BOLD + " next"

        text = re.sub(
            r"\s+",
            " ",
            text
        )

        # Avoid accidental repeated separators.
        text = re.sub(
            r"\s{2,}",
            " ",
            text
        )

        return text.strip()

    # ------------------------------------------------------------------
    # DAILY LIMITS
    # ------------------------------------------------------------------

    def _check_daily_limits(
        self,
        user,
        daily_limit_per_user,
        global_daily_limit,
        daily_tokens_per_user,
        global_daily_tokens
    ):
        user_requests = (
            self._user_daily_usage.get(
                user,
                0
            )
        )

        total_requests = sum(
            self._user_daily_usage.values()
        )

        if (
            daily_limit_per_user > 0
            and user_requests >= daily_limit_per_user
        ):
            return False, (
                f"You've reached your daily limit of "
                f"{daily_limit_per_user} successful requests. "
                "Try again tomorrow."
            )

        if (
            global_daily_limit > 0
            and total_requests >= global_daily_limit
        ):
            return False, (
                "The bot has reached its global daily "
                f"limit of {global_daily_limit} successful requests."
            )

        user_tokens = (
            self._user_daily_tokens.get(
                user,
                0
            )
        )

        if (
            daily_tokens_per_user > 0
            and user_tokens >= daily_tokens_per_user
        ):
            return False, (
                f"You've reached your daily token limit "
                f"of {daily_tokens_per_user} tokens."
            )

        if (
            global_daily_tokens > 0
            and self._daily_tokens >= global_daily_tokens
        ):
            return False, (
                f"The bot has reached its global daily "
                f"token limit of {global_daily_tokens}."
            )

        return True, None

    # ------------------------------------------------------------------
    # MAIN REQUEST
    # ------------------------------------------------------------------

    def _process_ask(self, irc, msg, question):
        channel = (
            msg.args[0]
            if msg.args
            else None
        )

        if not self._is_channel_enabled(channel):
            irc.error(
                "GroqAI is not enabled in this channel. "
                f"Use {self.canonicalName()} enable "
                "to enable it."
            )
            return

        self._reset_daily_if_needed()

        user = msg.prefix

        # --------------------------------------------------------------
        # Per-user throttle.
        # --------------------------------------------------------------

        allowed, remaining = self._check_throttle(
            user
        )

        if not allowed:
            irc.sendMsg(
                ircmsgs.notice(
                    msg.nick,
                    "You are being throttled. "
                    f"Please wait {remaining} seconds "
                    "before using @ai again."
                )
            )
            return

        # --------------------------------------------------------------
        # Config.
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
            daily_limit_per_user = 25

        try:
            global_daily_limit = self.registryValue(
                "globalDailyLimit"
            )
        except Exception:
            global_daily_limit = 250

        try:
            daily_tokens_per_user = self.registryValue(
                "dailyTokensPerUser"
            )
        except Exception:
            daily_tokens_per_user = 0

        try:
            global_daily_tokens = self.registryValue(
                "globalDailyTokens"
            )
        except Exception:
            global_daily_tokens = 0

        try:
            max_question_chars = self.registryValue(
                "maxQuestionChars"
            )
        except Exception:
            max_question_chars = 220

        try:
            max_response_chars = self.registryValue(
                "maxResponseChars"
            )
        except Exception:
            max_response_chars = 1200

        try:
            request_timeout = self.registryValue(
                "requestTimeout"
            )
        except Exception:
            request_timeout = 45

        # --------------------------------------------------------------
        # Question validation.
        # --------------------------------------------------------------

        question = (
            question.strip()
            if question
            else ""
        )

        question_length = len(question)

        if not question:
            irc.error(
                "Please provide a question."
            )
            return

        if (
            max_question_chars > 0
            and question_length > max_question_chars
        ):
            irc.sendMsg(
                ircmsgs.notice(
                    msg.nick,
                    f"Your question is too long "
                    f"({question_length} chars). "
                    f"Maximum is {max_question_chars} characters."
                )
            )
            return

        if not api_key:
            irc.error(
                "The Groq API key is not set. "
                "Please set plugins.GroqAI.apiKey."
            )
            return

        # --------------------------------------------------------------
        # Local daily limits.
        # --------------------------------------------------------------

        allowed, reason = self._check_daily_limits(
            user,
            daily_limit_per_user,
            global_daily_limit,
            daily_tokens_per_user,
            global_daily_tokens
        )

        if not allowed:
            irc.sendMsg(
                ircmsgs.notice(
                    msg.nick,
                    reason
                )
            )
            return

        # --------------------------------------------------------------
        # Local RPM.
        # --------------------------------------------------------------

        rpm_allowed, rpm_wait = (
            self._check_local_rpm()
        )

        if not rpm_allowed:
            irc.sendMsg(
                ircmsgs.notice(
                    msg.nick,
                    "The bot is temporarily rate-limited locally. "
                    f"Please wait about {rpm_wait} seconds."
                )
            )
            return

        # --------------------------------------------------------------
        # Request body.
        #
        # IMPORTANT:
        #
        # We intentionally keep this very close to Groq's documented
        # Compound Mini request. Groq's current Compound Mini quickstart
        # uses a normal messages array with the compound-mini model.
        #
        # We handle IRC formatting locally, so we don't need a huge
        # system prompt just to tell the model how IRC works.
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

        try:
            request_json = json.dumps(
                request_body,
                ensure_ascii=False,
                separators=(",", ":")
            )

            request_bytes = len(
                request_json.encode("utf-8")
            )

        except Exception:
            request_json = json.dumps(
                request_body
            )

            request_bytes = len(
                request_json.encode("utf-8")
            )

        self.log.info(
            "Groq request: "
            f"model={model} "
            f"compound={model.startswith('groq/compound')} "
            f"question_chars={question_length} "
            f"request_bytes={request_bytes}"
        )

        # --------------------------------------------------------------
        # API request.
        # --------------------------------------------------------------

        try:
            timeout = httpx.Timeout(
                connect=10.0,
                read=float(request_timeout),
                write=10.0,
                pool=10.0
            )

            with httpx.Client(
                timeout=timeout
            ) as client:

                self._record_api_attempt()

                self._user_daily_attempts[user] += 1
                self._daily_attempts += 1

                self._save_persisted_data()

                response = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    content=request_json.encode("utf-8")
                )

            self._parse_rate_limits(
                response.headers
            )

            # ----------------------------------------------------------
            # Errors.
            # ----------------------------------------------------------

            if response.status_code != 200:

                error_message = self._get_api_error(
                    response
                )

                self.log.error(
                    "Groq API error: "
                    f"HTTP {response.status_code} - "
                    f"{error_message}"
                )

                # ------------------------------------------------------
                # 413
                # ------------------------------------------------------
                #
                # Official Groq meaning:
                # Request body is too large.
                #
                # Do NOT call this TPM.
                # Do NOT retry.
                # ------------------------------------------------------

                if response.status_code == 413:

                    self._log_413_diagnostic(
                        response=response,
                        request_body=request_body,
                        request_bytes=request_bytes
                    )

                    irc.error(
                        "Groq rejected the request with "
                        "HTTP 413 (Request Entity Too Large). "
                        f"The outbound JSON was "
                        f"{request_bytes} bytes. "
                        "Groq rejected the request before "
                        "a normal completion was returned."
                    )

                    return

                # ------------------------------------------------------
                # 429
                # ------------------------------------------------------

                if response.status_code == 429:

                    retry_after = (
                        self._rate_limits.get(
                            "retry_after"
                        )
                    )

                    if retry_after:
                        irc.error(
                            "Groq rate limit reached. "
                            f"Retry after {retry_after}."
                        )
                    else:
                        reset_requests = (
                            self._rate_limits.get(
                                "reset_requests"
                            )
                        )

                        irc.error(
                            "Groq rate limit reached. "
                            f"Request reset: "
                            f"{reset_requests or 'unknown'}."
                        )

                    return

                # ------------------------------------------------------
                # 422
                # ------------------------------------------------------

                if response.status_code == 422:
                    irc.error(
                        "Groq could not process the request: "
                        f"{error_message}"
                    )
                    return

                # ------------------------------------------------------
                # Authentication.
                # ------------------------------------------------------

                if response.status_code == 401:
                    irc.error(
                        "Groq authentication failed. "
                        "Check your API key."
                    )
                    return

                if response.status_code == 403:
                    irc.error(
                        "Groq rejected the request due "
                        "to permissions or account restrictions."
                    )
                    return

                # ------------------------------------------------------
                # 5xx
                #
                # Groq documents these as server-side errors.
                # We retry ONLY these transient server errors once.
                # ------------------------------------------------------

                if response.status_code in (
                    500,
                    502,
                    503
                ):
                    irc.error(
                        f"Groq server error "
                        f"(HTTP {response.status_code}). "
                        "Please try again."
                    )
                    return

                irc.error(
                    f"Groq API error "
                    f"(HTTP {response.status_code}): "
                    f"{error_message}"
                )

                return

            # ----------------------------------------------------------
            # Parse response.
            # ----------------------------------------------------------

            try:
                data = response.json()

            except Exception as e:
                self.log.error(
                    f"Could not decode Groq response: {e}"
                )

                irc.error(
                    "Groq returned an invalid response."
                )

                return

            # ----------------------------------------------------------
            # Extract answer.
            # ----------------------------------------------------------

            try:
                answer = (
                    data["choices"][0]
                    ["message"]["content"]
                )

            except (
                KeyError,
                IndexError,
                TypeError
            ):
                self.log.error(
                    f"Unexpected Groq response: {data}"
                )

                irc.error(
                    "Groq returned an unexpected response."
                )

                return

            # ----------------------------------------------------------
            # Token usage.
            # ----------------------------------------------------------

            usage = data.get(
                "usage",
                {}
            )

            prompt_tokens = (
                usage.get(
                    "prompt_tokens",
                    0
                ) or 0
            )

            completion_tokens = (
                usage.get(
                    "completion_tokens",
                    0
                ) or 0
            )

            total_tokens = (
                usage.get(
                    "total_tokens",
                    0
                ) or 0
            )

            self.log.info(
                "Groq token usage: "
                f"prompt={prompt_tokens} "
                f"completion={completion_tokens} "
                f"total={total_tokens}"
            )

            # ----------------------------------------------------------
            # Successful usage.
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

            self._daily_tokens += total_tokens

            self._save_persisted_data()

            # ----------------------------------------------------------
            # Clean response.
            # ----------------------------------------------------------

            answer = self._clean_response(
                answer
            )

            if not answer:
                irc.error(
                    "Groq returned an empty response."
                )
                return

            # ----------------------------------------------------------
            # IRC response-length protection.
            # ----------------------------------------------------------

            if (
                max_response_chars > 0
                and len(answer) > max_response_chars
            ):
                answer = (
                    answer[
                        :max_response_chars
                    ].rstrip()
                    + " …"
                )

                self.log.info(
                    "Groq answer was truncated to "
                    f"{max_response_chars} characters "
                    "for IRC."
                )

            # ----------------------------------------------------------
            # Send.
            # ----------------------------------------------------------

            irc.reply(
                answer,
                prefixNick=True
            )

        except httpx.TimeoutException:
            self.log.error(
                "Timeout while connecting to Groq."
            )

            irc.error(
                "Connection timeout to Groq API."
            )

        except httpx.RequestError as e:
            self.log.error(
                f"HTTP request error while "
                f"contacting Groq: {e}"
            )

            irc.error(
                "Could not connect to Groq API."
            )

        except Exception as e:
            self.log.error(
                f"Unexpected error while querying Groq: {e}",
                exc_info=True
            )

            irc.error(
                f"An error occurred: {str(e)[:200]}"
            )

    # ------------------------------------------------------------------
    # COMMANDS
    # ------------------------------------------------------------------

    @wrap(["text"])
    def ask(self, irc, msg, args, question):
        """<question> Ask Groq Compound Mini a question."""
        self._process_ask(
            irc,
            msg,
            question
        )

    @wrap(["text"])
    def ai(self, irc, msg, args, question):
        """<question> Alias for @ask."""
        self._process_ask(
            irc,
            msg,
            question
        )

    @wrap([])
    def enable(self, irc, msg, args):
        """Enable GroqAI in the current channel. Only bot owners can use this command."""

        if not self._check_owner(
            irc,
            msg
        ):
            return

        channel = (
            msg.args[0]
            if msg.args
            else None
        )

        if (
            not channel
            or not ircutils.isChannel(channel)
        ):
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
            f"GroqAI has been enabled in {channel}.",
            prefixNick=True
        )

    @wrap([])
    def disable(self, irc, msg, args):
        """Disable GroqAI in the current channel. Only bot owners can use this command."""

        if not self._check_owner(
            irc,
            msg
        ):
            return

        channel = (
            msg.args[0]
            if msg.args
            else None
        )

        if (
            not channel
            or not ircutils.isChannel(channel)
        ):
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
                f"GroqAI has been disabled in {channel}.",
                prefixNick=True
            )

        else:
            irc.reply(
                f"GroqAI was not enabled in {channel}.",
                prefixNick=True
            )

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
                f"GroqAI is currently ENABLED in {channel}.",
                prefixNick=True
            )
        else:
            irc.reply(
                f"GroqAI is currently DISABLED in {channel}.",
                prefixNick=True
            )

    @wrap([])
    def list(self, irc, msg, args):
        """List channels where GroqAI is enabled. Only bot owners can use this command."""

        if not self._check_owner(
            irc,
            msg
        ):
            return

        self._load_enabled_channels()

        if self._enabled_channels:
            channels = ", ".join(
                sorted(
                    self._enabled_channels
                )
            )

            irc.reply(
                f"GroqAI is enabled in: {channels}.",
                prefixNick=True
            )

        else:
            irc.reply(
                "GroqAI is not enabled in any channels.",
                prefixNick=True
            )

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

        user_attempts = (
            self._user_daily_attempts.get(
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
            daily_limit = 25

        try:
            global_limit = self.registryValue(
                "globalDailyLimit"
            )
        except Exception:
            global_limit = 250

        total_requests = sum(
            self._user_daily_usage.values()
        )

        total_attempts = self._daily_attempts

        total_tokens = self._daily_tokens

        if daily_limit > 0:
            user_part = (
                f"You: {user_requests}/{daily_limit} req"
            )
        else:
            user_part = (
                f"You: {user_requests}/unlimited req"
            )

        if global_limit > 0:
            global_part = (
                f"Global: {total_requests}/{global_limit} req"
            )
        else:
            global_part = (
                f"Global: {total_requests}/unlimited req"
            )

        parts = [
            user_part,
            global_part,
            f"Attempts: user={user_attempts} "
            f"global={total_attempts}",
            f"Tokens: user={user_tokens} "
            f"global={total_tokens}"
        ]

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
                    f"API req: "
                    f"{remaining_requests}/"
                    f"{limit_requests}"
                )

            if (
                remaining_tokens is not None
                and limit_tokens is not None
            ):
                parts.append(
                    f"API tok: "
                    f"{remaining_tokens}/"
                    f"{limit_tokens}"
                )

            if reset_requests:
                parts.append(
                    f"req reset: {reset_requests}"
                )

            if reset_tokens:
                parts.append(
                    f"tok reset: {reset_tokens}"
                )

        else:
            parts.append(
                "API rate limits: no data yet"
            )

        self._cleanup_rpm()

        try:
            local_rpm_limit = self.registryValue(
                "localRpmLimit"
            )
        except Exception:
            local_rpm_limit = 30

        if local_rpm_limit > 0:
            parts.append(
                f"Local RPM: "
                f"{len(self._request_timestamps)}/"
                f"{local_rpm_limit}"
            )
        else:
            parts.append(
                "Local RPM: unlimited"
            )

        irc.reply(
            " | ".join(parts),
            prefixNick=True
        )

    @wrap([])
    def resetusage(self, irc, msg, args):
        """Reset all GroqAI usage statistics. Only bot owners can use this command."""

        if not self._check_owner(
            irc,
            msg
        ):
            return

        self._user_daily_usage.clear()
        self._user_daily_attempts.clear()
        self._user_daily_tokens.clear()

        self._daily_attempts = 0
        self._daily_tokens = 0

        self._request_timestamps.clear()

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
