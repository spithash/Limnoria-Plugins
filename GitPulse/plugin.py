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

import time
import requests
from threading import Thread
from supybot import callbacks, ircutils, ircmsgs


class GitPulse(callbacks.Plugin):
    """Subscribe to GitHub repositories and output activity in the channel."""

    def __init__(self, irc):
        super().__init__(irc)
        self.subscriptions = []
        self.last_checked = {}
        self.polling_started = False  # Flag to avoid multiple threads

    def activate(self):
        super().activate()
        if not self.polling_started:
            self.polling_started = True
            self.start_polling()

    def start_polling(self):
        def poll():
            while True:
                for repo in self.subscriptions:
                    self.fetch_and_announce(repo, self.irc, None)
                time.sleep(self.registryValue('pollInterval'))  # default: 600

        Thread(target=poll, daemon=True).start()

    def fetch_and_announce(self, repo, irc, msg):
        github_token = self.registryValue('githubToken')
        headers = {'Authorization': f'token {github_token}'} if github_token else {}

        url = f"https://api.github.com/repos/{repo}/commits"
        params = {'per_page': 100}
        all_events = []

        while url:
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 200:
                events = response.json()
                all_events.extend(events)
                url = response.links.get('next', {}).get('url', None)
            else:
                self.log.error(f"Failed to fetch events for {repo}: {response.status_code}")
                return

        if all_events:
            new_events = [event for event in all_events if self.is_new_event(event, repo)]
            if new_events:
                for event in reversed(new_events):
                    message = self.format_event(event, repo)
                    self.announce(message, irc, msg)

    def is_new_event(self, event, repo):
        created_at = event['commit']['committer']['date']
        last_checked = self.last_checked.get(repo)
        if last_checked and created_at <= last_checked:
            return False
        self.last_checked[repo] = created_at
        return True

    def format_event(self, event, repo):
        commit_message = event['commit']['message'].split('\n')[0]
        author = event['commit']['author']['name']
        commit_url = event['html_url']

        BOLD = '\x02'
        COLOR = '\x03'
        RESET = '\x0f'
        GREEN = '03'
        BLUE = '12'

        return (
            f"{BOLD}{author}{BOLD} committed: "
            f"{COLOR}{GREEN}{commit_message}{RESET} to "
            f"{BOLD}{repo}{BOLD}: "
            f"{COLOR}{BLUE}{commit_url}{RESET}"
        )

    def announce(self, message, irc, msg):
        if msg is not None:
            channel = msg.args[0]
            if channel:
                irc.sendMsg(ircmsgs.privmsg(channel, message))

    def subscribe(self, irc, msg, args):
        """<owner/repo> -- Subscribe to a GitHub repository."""
        if len(args) < 1:
            irc.reply("Usage: subscribe owner/repo")
            return

        repo = args[0]
        if repo not in self.subscriptions:
            self.subscriptions.append(repo)
            irc.reply(f"Subscribed to {repo}.")
        else:
            irc.reply(f"Already subscribed to {repo}.")

    def unsubscribe(self, irc, msg, args):
        """<owner/repo> -- Unsubscribe from a GitHub repository."""
        if len(args) < 1:
            irc.reply("Usage: unsubscribe owner/repo")
            return

        repo = args[0]
        if repo in self.subscriptions:
            self.subscriptions.remove(repo)
            irc.reply(f"Unsubscribed from {repo}.")
        else:
            irc.reply(f"Not subscribed to {repo}.")

    def fetchgitpulse(self, irc, msg, args):
        """Manually fetch events from all subscribed repositories."""
        for repo in self.subscriptions:
            self.fetch_and_announce(repo, irc, msg)

    def die(self):
        super().die()


Class = GitPulse

