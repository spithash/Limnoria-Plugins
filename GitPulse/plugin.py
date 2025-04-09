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
import json
import os
import requests
from threading import Thread
from supybot import callbacks, ircmsgs, ircutils

class GitPulse(callbacks.Plugin):
    """GitHub activity monitor using Events API."""

    def __init__(self, irc):
        super().__init__(irc)
        self.cache_dir = self.get_cache_dir()
        os.makedirs(self.cache_dir, exist_ok=True)
        self.polling_started = False
        self.subscriptions = {}
        
        # Do not start polling immediately in the constructor
        # We'll start it after the plugin is activated

    def get_cache_dir(self):
        """Find the Limnoria bot root folder and construct the cache directory path."""
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        limnoria_root = os.path.dirname(os.path.dirname(plugin_dir))
        return os.path.join(limnoria_root, 'tmp', 'gitpulse_cache')

    def get_subscriptions_file(self, channel):
        """Get the path to the file where subscriptions for a channel are stored."""
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        limnoria_root = os.path.dirname(os.path.dirname(plugin_dir))
        channel_name = ircutils.toLower(channel)
        return os.path.join(limnoria_root, 'tmp', f'gitpulse_subscriptions_{channel_name}.json')

    def load_subscriptions(self, channel):
        """Load the list of repositories for a specific channel from the persistent file."""
        subscriptions_file = self.get_subscriptions_file(channel)
        if os.path.exists(subscriptions_file):
            with open(subscriptions_file, "r") as f:
                return json.load(f)
        return []

    def save_subscriptions(self, channel, subscriptions):
        """Save the list of repositories for a specific channel to the persistent file."""
        subscriptions_file = self.get_subscriptions_file(channel)
        with open(subscriptions_file, "w") as f:
            json.dump(subscriptions, f)

    def activate(self):
        super().activate()
        # Now that the plugin is activated, we can safely start polling
        if not self.polling_started:
            self.polling_started = True
            self.start_polling()

    def start_polling(self):
        """Start the polling thread for checking repository events."""
        def poll():
            while True:
                # Poll each channel individually
                for channel in self.irc.state.channels:
                    # Ensure the channel exists in subscriptions
                    if channel not in self.subscriptions:
                        self.subscriptions[channel] = self.load_subscriptions(channel)
                    for repo in self.subscriptions[channel]:
                        self.fetch_and_announce(repo, self.irc, channel)
                time.sleep(self.registryValue('pollInterval'))

        # Start the polling thread
        Thread(target=poll, daemon=True).start()

    def _get_cache_file(self, repo):
        sanitized = repo.replace('/', '_')
        return os.path.join(self.cache_dir, f"{sanitized}.json")

    def _load_seen_ids(self, repo):
        path = self._get_cache_file(repo)
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
        return []

    def _save_seen_ids(self, repo, ids):
        path = self._get_cache_file(repo)
        # Keep only the latest 20 events
        trimmed = ids[-20:]
        with open(path, "w") as f:
            json.dump(trimmed, f)

    def fetch_and_announce(self, repo, irc, channel):
        token = self.registryValue('githubToken')
        headers = {'Authorization': f'token {token}'} if token else {}
        url = f"https://api.github.com/repos/{repo}/events"
        resp = requests.get(url, headers=headers)

        if resp.status_code != 200:
            self.log.error(f"[GitPulse] Failed to fetch events for {repo}: {resp.status_code}")
            return

        events = resp.json()
        seen_ids = self._load_seen_ids(repo)
        new_ids = []
        for event in reversed(events):
            event_id = event['id']
            if event_id in seen_ids:
                continue
            msg_text = self.format_event(event, repo)
            if msg_text:
                self.announce(msg_text, irc, channel)
            new_ids.append(event_id)

        if new_ids:
            updated = seen_ids + new_ids
            self._save_seen_ids(repo, updated)

    def format_event(self, event, repo):
        etype = event['type']
        actor = event['actor']['login']
        B = '\x02'
        C = '\x03'
        RESET = '\x0f'
        RED, GREEN, CYAN, BLUE = '05', '03', '10', '12'

        if etype == 'PushEvent':
            commits = event['payload'].get('commits', [])
            msgs = []
            for c in commits:
                msg = c['message'].split('\n')[0]
                url = f"https://github.com/{repo}/commit/{c['sha']}"
                msgs.append(f"{B}{actor}{B} pushed: {C}{GREEN}{msg}{RESET} to {B}{repo}{B}: {C}{BLUE}{url}{RESET}")
            return '\n'.join(msgs)

        elif etype == 'IssuesEvent':
            action = event['payload']['action']
            issue = event['payload']['issue']
            title = issue['title']
            url = issue['html_url']
            return f"{B}{actor}{B} {action} issue: {C}{CYAN}{title}{RESET} in {B}{repo}{B}: {C}{BLUE}{url}{RESET}"

        elif etype == 'PullRequestEvent':
            action = event['payload']['action']
            pr = event['payload']['pull_request']
            title = pr['title']
            url = pr['html_url']
            return f"{B}{actor}{B} {action} PR: {C}{CYAN}{title}{RESET} in {B}{repo}{B}: {C}{BLUE}{url}{RESET}"

        return None

    def announce(self, message, irc, channel):
        """Send the formatted message to the specified channel."""
        if not channel:
            self.log.warning("No channel specified for announcement.")
            return
        for line in message.split('\n'):
            irc.sendMsg(ircmsgs.privmsg(channel, line))

    def subscribe(self, irc, msg, args):
        """<owner/repo> -- Subscribe to a GitHub repository."""
        if not args:
            irc.reply("Usage: subscribe owner/repo")
            return
        repo = args[0]
        channel = msg.args[0]
        subscriptions = self.subscriptions.get(channel, [])
        if repo not in subscriptions:
            subscriptions.append(repo)
            self.subscriptions[channel] = subscriptions
            self.save_subscriptions(channel, subscriptions)
            irc.reply(f"Subscribed to {repo} in {channel}.")
        else:
            irc.reply(f"Already subscribed to {repo} in {channel}.")

    def unsubscribe(self, irc, msg, args):
        """<owner/repo> -- Unsubscribe from a GitHub repository."""
        if not args:
            irc.reply("Usage: unsubscribe owner/repo")
            return
        repo = args[0]
        channel = msg.args[0]
        subscriptions = self.subscriptions.get(channel, [])
        if repo in subscriptions:
            subscriptions.remove(repo)
            self.subscriptions[channel] = subscriptions
            self.save_subscriptions(channel, subscriptions)
            irc.reply(f"Unsubscribed from {repo} in {channel}.")
        else:
            irc.reply(f"Not subscribed to {repo} in {channel}.")

    def listgitpulse(self, irc, msg, args):
        """List the GitHub repositories you're subscribed to in the current channel."""
        channel = msg.args[0]
        subscriptions = self.subscriptions.get(channel, [])
        if subscriptions:
            irc.reply(f"Subscribed repositories in {channel}: {', '.join(subscriptions)}")
        else:
            irc.reply(f"You are not subscribed to any repositories in {channel}.")

    def fetchgitpulse(self, irc, msg, args):
        """Manually fetch events from all subscribed repositories in the current channel."""
        channel = msg.args[0]
        subscriptions = self.subscriptions.get(channel, [])
        for repo in subscriptions:
            self.fetch_and_announce(repo, irc, channel)

    def die(self):
        super().die()

Class = GitPulse

