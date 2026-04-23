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

"""
Plugin that monitors public GitHub repositories for activity using the GitHub Events & Commits API. Available commands are 'subscribe', 'unsubscribe', 'listgitpulse' and 'fetchgitpulse'
"""

import requests
from threading import Thread, Event
from datetime import datetime, timedelta, timezone
import time

from supybot import callbacks, ircmsgs
from supybot.commands import wrap
from colorama import Fore, Style, init

init(autoreset=True)


class GitPulse(callbacks.Plugin):

    def __init__(self, irc):
        super().__init__(irc)
        self.irc = irc

        self.polling_started = False
        self.polling_thread = None
        self.stop_polling_event = Event()

        self.etags = {}
        self.last_modifieds = {}

        self.TAG = Style.BRIGHT + Fore.WHITE + "[GitPulse]" + Style.RESET_ALL

        self.LEVEL_COLORS = {
            "DEBUG": Fore.MAGENTA + Style.BRIGHT,
            "INFO": Fore.GREEN + Style.BRIGHT,
            "WARNING": Fore.YELLOW + Style.BRIGHT,
            "ERROR": Fore.RED + Style.BRIGHT,
        }
        self.RESET_COLOR = Style.RESET_ALL

        # IRC formatting
        self.B = '\x02'
        self.C = '\x03'
        self.RESET = '\x0f'
        self.GREEN = '03'
        self.BLUE = '12'
        self.RED = '04'
        self.YELLOW = '08'

        self.start_polling()

    # ---------------- LOGGING (fixed + stable) ----------------

    def get_timestamp(self):
        return Fore.CYAN + datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S') + Style.RESET_ALL

    def _log(self, level, message):
        ts = self.get_timestamp()
        print(f"{ts} {self.TAG} {self.LEVEL_COLORS[level]}[{level}]{self.RESET_COLOR} {message}")

    def log_debug(self, m): self._log("DEBUG", m)
    def log_info(self, m): self._log("INFO", m)
    def log_warning(self, m): self._log("WARNING", m)
    def log_error(self, m): self._log("ERROR", m)

    # ---------------- THREAD CONTROL ----------------

    def start_polling(self):
        if not self.polling_started:
            self.polling_started = True
            self.log_info("Starting polling thread for GitHub events.")

            self.polling_thread = Thread(target=self.poll, daemon=True)
            self.polling_thread.start()

            Thread(target=self._watchdog, daemon=True).start()

    def stop_polling(self):
        self.stop_polling_event.set()
        if self.polling_thread:
            self.polling_thread.join()
        self.log_info("Polling thread stopped.")

    def _watchdog(self):
        while not self.stop_polling_event.is_set():
            if self.polling_thread and not self.polling_thread.is_alive():
                self.log_error("Polling thread died — restarting")
                self.polling_started = False
                self.start_polling()
            time.sleep(30)

    # ---------------- POLLING LOOP ----------------

    def poll(self):
        while not self.stop_polling_event.is_set():
            try:
                self.log_info("Polling for GitHub updates...")

                for channel in list(self.irc.state.channels):
                    try:
                        subs = self.registryValue('subscriptions', channel)
                        if isinstance(subs, str):
                            subs = subs.split()

                        for repo in subs:
                            try:
                                self.fetch_and_announce(repo, self.irc, None, channel)
                            except Exception as e:
                                self.log_error(f"{repo}: {e}")

                    except Exception as e:
                        self.log_error(f"Channel error {channel}: {e}")

                interval = self.registryValue('pollInterval')
                self.stop_polling_event.wait(interval)

            except Exception as e:
                self.log_error(f"FATAL poll error: {e}")
                self.stop_polling_event.wait(10)

    # ---------------- HTTP ----------------

    def _request(self, url, headers):
        try:
            return requests.get(url, headers=headers, timeout=15)
        except Exception as e:
            self.log_error(f"HTTP error: {e}")
            return None

    # ---------------- EVENTS ----------------

    def fetch_and_announce(self, repo, irc, msg, channel):
        token = self.registryValue('githubToken')
        headers = {'Cache-Control': 'no-cache'}
        if token:
            headers['Authorization'] = f'token {token}'

        key = f"{channel}::{repo}::events"

        if key in self.etags:
            headers['If-None-Match'] = self.etags[key]
        elif key in self.last_modifieds:
            headers['If-Modified-Since'] = self.last_modifieds[key]

        resp = self._request(f"https://api.github.com/repos/{repo}/events", headers)
        if not resp:
            return

        if resp.status_code == 304:
            self.fetch_commits(repo, irc, msg, channel)
            return

        if resp.status_code != 200:
            self.log_error(f"{repo} HTTP {resp.status_code}")
            return

        self.etags[key] = resp.headers.get('ETag')
        self.last_modifieds[key] = resp.headers.get('Last-Modified')

        try:
            events = resp.json()
        except Exception as e:
            self.log_error(f"JSON error: {e}")
            return

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)

        for e in reversed(events):
            try:
                t = datetime.strptime(e['created_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                if t < cutoff:
                    continue

                if e['type'] == 'PullRequestEvent':
                    msg_text = self.format_pr(e, repo)
                elif e['type'] == 'IssuesEvent':
                    msg_text = self.format_issue(e, repo)
                else:
                    continue

                if msg_text:
                    self.announce(msg_text, irc, msg, channel)

            except Exception as ex:
                self.log_warning(f"Event parse error: {ex}")

        self.fetch_commits(repo, irc, msg, channel)

    # ---------------- COMMITS ----------------

    def fetch_commits(self, repo, irc, msg, channel):
        headers = {'Cache-Control': 'no-cache'}

        token = self.registryValue('githubToken')
        if token:
            headers['Authorization'] = f'token {token}'

        key = f"{channel}::{repo}::commits"

        if key in self.etags:
            headers['If-None-Match'] = self.etags[key]

        resp = self._request(f"https://api.github.com/repos/{repo}/commits", headers)
        if not resp:
            return

        if resp.status_code != 200:
            return

        self.etags[key] = resp.headers.get('ETag')

        try:
            commits = resp.json()
        except:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)

        for c in reversed(commits):
            try:
                t = datetime.strptime(c['commit']['committer']['date'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                if t < cutoff:
                    continue

                event = {
                    'actor': {'login': c['commit']['committer']['name']},
                    'payload': {
                        'commits': [{'message': c['commit']['message'], 'sha': c['sha']}],
                        'ref': 'refs/heads/main'
                    }
                }

                msg_text = self.format_push(event, repo)
                if msg_text:
                    self.announce(msg_text, irc, msg, channel)

            except Exception as e:
                self.log_warning(f"Commit parse error: {e}")

    # ---------------- FORMATTERS ----------------

    def format_push(self, e, repo):
        actor = e['actor']['login']
        msgs = []
        for c in e['payload']['commits']:
            msg = c['message'].split('\n')[0]
            url = f"https://github.com/{repo}/commit/{c['sha']}"
            msgs.append(f"{self.B}{actor}{self.B} pushed {msg} → {url}")
        return "\n".join(msgs)

    def format_pr(self, e, repo):
        pr = e['payload']['pull_request']
        return f"{self.B}{e['actor']['login']}{self.B} PR: {pr['title']} → {pr['html_url']}"

    def format_issue(self, e, repo):
        issue = e['payload']['issue']
        return f"{self.B}{e['actor']['login']}{self.B} Issue: {issue['title']} → {issue['html_url']}"

    # ---------------- IRC ----------------

    def announce(self, message, irc, msg, channel):
        for line in message.split("\n"):
            irc.sendMsg(ircmsgs.privmsg(channel, line))

    # ---------------- COMMANDS ----------------

    def subscribe(self, irc, msg, args):
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
        channel = msg.args[0]
        subs = self.registryValue('subscriptions', channel)
        if isinstance(subs, str):
            subs = subs.split()

        irc.reply(f"Repos: {', '.join(subs) if subs else 'none'}")

    @wrap(['owner'])
    def fetchgitpulse(self, irc, msg, args):
        """Manually trigger fetching GitHub events for all repos subscribed in the current channel."""
        channel = msg.args[0]
        subs = self.registryValue('subscriptions', channel)
        if isinstance(subs, str):
            subs = subs.split()

        for repo in subs:
            self.fetch_and_announce(repo, irc, msg, channel)

    def die(self):
        self.stop_polling()
        super().die()


Class = GitPulse
