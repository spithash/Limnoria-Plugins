###
# Copyright (c) 2025, Stathis Xantinidis @spithash
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

import requests
from threading import Thread, Event
from datetime import datetime, timedelta, timezone
import time

from supybot import callbacks, ircmsgs
from supybot.commands import wrap
from colorama import Fore, Style, init

# Enable colored terminal output
init(autoreset=True)


class GitPulse(callbacks.Plugin):
    """Plugin that monitors public GitHub repositories for activity using the GitHub Events & Commits API. Available commands are 'subscribe', 'unsubscribe', 'listgitpulse' and 'fetchgitpulse'"""

    def __init__(self, irc):
        super().__init__(irc)
        self.irc = irc

        # Thread lifecycle control
        self.polling_started = False
        self.polling_thread = None
        self.stop_polling_event = Event()

        # Cache headers (ETag / Last-Modified) to reduce API usage
        self.etags = {}
        self.last_modifieds = {}

        # Pretty logging prefix
        self.TAG = Style.BRIGHT + Fore.WHITE + "[GitPulse]" + Style.RESET_ALL

        # Color per log level for readability
        self.LEVEL_COLORS = {
            "DEBUG": Fore.MAGENTA + Style.BRIGHT,
            "INFO": Fore.GREEN + Style.BRIGHT,
            "WARNING": Fore.YELLOW + Style.BRIGHT,
            "ERROR": Fore.RED + Style.BRIGHT,
        }

        # IRC formatting helpers
        self.B = '\x02'
        self.C = '\x03'
        self.RESET = '\x0f'
        self.GREEN = '03'
        self.BLUE = '12'
        self.RED = '04'
        self.YELLOW = '08'

        # Start background polling when plugin loads
        self.start_polling()

    # ---------------- LOGGING ----------------

    def ts(self):
        """Return a colored UTC timestamp for logs."""
        return Fore.CYAN + datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S') + Style.RESET_ALL

    def _log(self, level, msg):
        """Internal logger (avoid clashing with Supybot's self.log)."""
        print(f"{self.ts()} {self.TAG} {self.LEVEL_COLORS[level]}[{level}]{Style.RESET_ALL} {msg}")

    def log_debug(self, m): self._log("DEBUG", m)
    def log_info(self, m): self._log("INFO", m)
    def log_warning(self, m): self._log("WARNING", m)
    def log_error(self, m): self._log("ERROR", m)

    # ---------------- THREAD CONTROL ----------------

    def start_polling(self):
        """Start polling thread (only once)."""
        if not self.polling_started:
            self.polling_started = True
            self.log_info("Starting polling thread")

            # Main worker thread
            self.polling_thread = Thread(target=self.poll, daemon=True)
            self.polling_thread.start()

            # Watchdog to restart thread if it dies unexpectedly
            Thread(target=self._watchdog, daemon=True).start()

    def stop_polling(self):
        """Stop polling thread cleanly."""
        self.stop_polling_event.set()
        if self.polling_thread:
            self.polling_thread.join()
        self.log_info("Polling stopped")

    def _watchdog(self):
        """Ensure polling thread is always alive."""
        while not self.stop_polling_event.is_set():
            if self.polling_thread and not self.polling_thread.is_alive():
                self.log_error("Polling thread died — restarting")
                self.polling_started = False
                self.start_polling()
            time.sleep(30)

    # ---------------- POLL LOOP ----------------

    def poll(self):
        """
        Main loop:
        - iterates channels
        - fetches repo updates
        - sleeps between cycles

        Wrapped in try/except so it never silently dies.
        """
        while not self.stop_polling_event.is_set():
            try:
                self.log_info("Polling...")

                # Copy channels list to avoid runtime mutation issues
                for channel in list(self.irc.state.channels):
                    try:
                        subscriptions = self.registryValue('subscriptions', channel)

                        # Registry may return string instead of list
                        if isinstance(subscriptions, str):
                            subscriptions = subscriptions.split()

                        for repo in subscriptions:
                            try:
                                self.fetch_and_announce(repo, channel)
                            except Exception as e:
                                self.log_error(f"{repo} error: {e}")

                    except Exception as e:
                        self.log_error(f"Channel loop error: {e}")

                # Wait for next cycle (interruptible)
                interval = self.registryValue('pollInterval')
                self.stop_polling_event.wait(interval)

            except Exception as e:
                self.log_error(f"FATAL poll error: {e}")
                self.stop_polling_event.wait(10)

    # ---------------- HTTP ----------------

    def _request(self, url, headers):
        """HTTP GET wrapper with timeout to avoid hanging."""
        try:
            return requests.get(url, headers=headers, timeout=15)
        except Exception as e:
            self.log_error(f"HTTP error: {e}")
            return None

    # ---------------- FETCH EVENTS ----------------

    def fetch_and_announce(self, repo, channel):
        """Fetch GitHub events and announce new ones."""
        headers = {'Cache-Control': 'no-cache'}

        token = self.registryValue('githubToken')
        if token:
            headers['Authorization'] = f'token {token}'

        key = f"{channel}::{repo}::events"

        # Use caching headers if available
        if key in self.etags:
            headers['If-None-Match'] = self.etags[key]
        elif key in self.last_modifieds:
            headers['If-Modified-Since'] = self.last_modifieds[key]

        resp = self._request(f"https://api.github.com/repos/{repo}/events", headers)
        if not resp:
            return

        # No changes
        if resp.status_code == 304:
            self.fetch_commits(repo, channel)
            return

        if resp.status_code != 200:
            self.log_error(f"{repo} HTTP {resp.status_code}")
            return

        # Save cache headers
        self.etags[key] = resp.headers.get('ETag')
        self.last_modifieds[key] = resp.headers.get('Last-Modified')

        try:
            events = resp.json()
        except Exception as e:
            self.log_error(f"JSON error: {e}")
            return

        # Only consider recent activity
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)

        for e in reversed(events):
            try:
                t = datetime.strptime(e['created_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                if t < cutoff:
                    continue

                if e['type'] == 'PullRequestEvent':
                    msg = self.format_pr(e, repo)
                elif e['type'] == 'IssuesEvent':
                    msg = self.format_issue(e, repo)
                else:
                    continue

                if msg:
                    self.announce(msg, channel)

            except Exception as ex:
                self.log_warning(f"Event parse error: {ex}")

        self.fetch_commits(repo, channel)

    # ---------------- FETCH COMMITS ----------------

    def fetch_commits(self, repo, channel):
        """Fetch recent commits and announce them."""
        headers = {'Cache-Control': 'no-cache'}

        token = self.registryValue('githubToken')
        if token:
            headers['Authorization'] = f'token {token}'

        key = f"{channel}::{repo}::commits"

        if key in self.etags:
            headers['If-None-Match'] = self.etags[key]
        elif key in self.last_modifieds:
            headers['If-Modified-Since'] = self.last_modifieds[key]

        resp = self._request(f"https://api.github.com/repos/{repo}/commits", headers)
        if not resp:
            return

        if resp.status_code == 304:
            return

        if resp.status_code != 200:
            return

        self.etags[key] = resp.headers.get('ETag')
        self.last_modifieds[key] = resp.headers.get('Last-Modified')

        try:
            commits = resp.json()
        except:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)

        for c in reversed(commits):
            try:
                t = datetime.strptime(
                    c['commit']['committer']['date'],
                    '%Y-%m-%dT%H:%M:%SZ'
                ).replace(tzinfo=timezone.utc)

                if t < cutoff:
                    continue

                # Normalize commit into pseudo event format
                event = {
                    'actor': {'login': c['commit']['committer']['name']},
                    'payload': {
                        'commits': [{'message': c['commit']['message'], 'sha': c['sha']}],
                        'ref': 'refs/heads/main'
                    }
                }

                msg = self.format_push(event, repo)
                if msg:
                    self.announce(msg, channel)

            except Exception as e:
                self.log_warning(f"Commit parse error: {e}")

    # ---------------- FORMAT ----------------

    def format_push(self, e, repo):
        """Format commit message."""
        actor = e['actor']['login']

        msgs = []
        for c in e['payload']['commits']:
            msg = c['message'].split('\n')[0]
            url = f"https://github.com/{repo}/commit/{c['sha']}"
            msgs.append(f"{self.B}{actor}{self.B} pushed {msg} → {url}")
        return "\n".join(msgs)

    def format_pr(self, e, repo):
        """Format pull request event."""
        pr = e['payload']['pull_request']
        return f"{self.B}{e['actor']['login']}{self.B} PR: {pr['title']} → {pr['html_url']}"

    def format_issue(self, e, repo):
        """Format issue event."""
        issue = e['payload']['issue']
        return f"{self.B}{e['actor']['login']}{self.B} Issue: {issue['title']} → {issue['html_url']}"

    # ---------------- IRC ----------------

    def announce(self, message, channel):
        """Send message to IRC channel."""
        for line in message.split("\n"):
            self.irc.sendMsg(ircmsgs.privmsg(channel, line))

    # ---------------- COMMANDS ----------------

    def subscribe(self, irc, msg, args):
        """subscribe owner/repo"""
        repo = args[0]
        channel = msg.args[0]

        subs = self.registryValue('subscriptions', channel)
        if isinstance(subs, str):
            subs = subs.split()

        if repo not in subs:
            subs.append(repo)
            self.setRegistryValue('subscriptions', ' '.join(subs), channel)
            irc.reply(f"Subscribed to {repo}")

    def unsubscribe(self, irc, msg, args):
        """unsubscribe owner/repo"""
        repo = args[0]
        channel = msg.args[0]

        subs = self.registryValue('subscriptions', channel)
        if isinstance(subs, str):
            subs = subs.split()

        if repo in subs:
            subs.remove(repo)
            self.setRegistryValue('subscriptions', ' '.join(subs), channel)
            irc.reply(f"Unsubscribed from {repo}")

    def listgitpulse(self, irc, msg, args):
        """List subscribed repositories."""
        channel = msg.args[0]
        subs = self.registryValue('subscriptions', channel)

        if isinstance(subs, str):
            subs = subs.split()

        if subs:
            irc.reply(f"Subscribed repos: {', '.join(subs)}")
        else:
            irc.reply("No subscriptions.")

    @wrap(['owner'])
    def fetchgitpulse(self, irc, msg, args):
        """Manually trigger fetch."""
        channel = msg.args[0]
        subs = self.registryValue('subscriptions', channel)

        if isinstance(subs, str):
            subs = subs.split()

        for repo in subs:
            self.fetch_and_announce(repo, channel)

    def die(self):
        """Cleanup when plugin is unloaded."""
        self.stop_polling()
        super().die()


Class = GitPulse
