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
    """GitHub activity monitor using Events API.

    When you subscribe to a repository using the subscribe command, the bot will immediately
    fetch and post the latest event. Thereafter, it polls GitHub every pollInterval seconds,
    and if new events (e.g. commits) are found, they are announced in the channel.
    The global event history (up to 50 event IDs) is stored in the configuration to avoid reposts.
    """

    def __init__(self, irc):
        super().__init__(irc)
        self.irc = irc                           # store the IRC connection
        self.polling_active = True               # flag to control polling
        self.start_polling()                     # start the background polling thread

    def get_cache_dir(self):
        """Determine the bot's tmp directory for caching event IDs."""
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        limnoria_root = os.path.dirname(os.path.dirname(plugin_dir))
        cache_dir = os.path.join(limnoria_root, 'tmp', 'gitpulse_cache')
        return cache_dir

    def _get_cache_file(self, repo):
        """Get a sanitized path for caching events for a repo."""
        sanitized = repo.replace('/', '_')
        return os.path.join(self.get_cache_dir(), f"{sanitized}.json")

    def _load_seen_ids(self, repo):
        """Load the event IDs that have already been seen for this repository."""
        path = self._get_cache_file(repo)
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
        return []

    def _save_seen_ids(self, repo, ids):
        """Save the event IDs that have been seen for this repository."""
        path = self._get_cache_file(repo)
        trimmed = ids[-50:]  # keep only the last 50 for safety
        with open(path, "w") as f:
            json.dump(trimmed, f)

    def fetch_and_announce(self, repo, irc, msg, channel):
        """Fetch events from GitHub for a repository and announce new events."""
        token = self.registryValue('githubToken')
        headers = {'Authorization': f'token {token}'} if token else {}
        url = f"https://api.github.com/repos/{repo}/events"
        resp = requests.get(url, headers=headers)

        if resp.status_code != 200:
            self.log.error(f"[GitPulse] Failed to fetch events for {repo}: {resp.status_code}")
            return

        events = resp.json()
        seen_ids = self.load_global_seen_ids()
        new_ids = []
        for event in reversed(events):
            event_id = event['id']
            if event_id in seen_ids:
                continue
            # We only process PushEvent events (commits)
            if event['type'] == 'PushEvent':
                msg_text = self.format_push_event(event, repo)
                if msg_text:
                    self.announce(msg_text, irc, msg, channel)
            new_ids.append(event_id)

        if new_ids:
            updated = seen_ids + new_ids
            self._save_seen_ids(repo, updated)
            self.save_global_seen_ids(new_ids)

    def format_push_event(self, event, repo):
        """Format PushEvent data into a human-readable message."""
        actor = event['actor']['login']
        commits = event['payload'].get('commits', [])
        B = '\x02'
        C = '\x03'
        RESET = '\x0f'
        GREEN = '03'
        BLUE = '12'

        if commits:
            msgs = []
            for c in commits:
                message_line = c['message'].split('\n')[0]
                url = f"https://github.com/{repo}/commit/{c['sha']}"
                msgs.append(f"{B}{actor}{B} pushed: {C}{GREEN}{message_line}{RESET} to {B}{repo}{B}: {C}{BLUE}{url}{RESET}")
            return '\n'.join(msgs)
        return None

    def announce(self, message, irc, msg, channel):
        """Announce a message in a specific channel."""
        if not channel:
            self.log.warning("No channel specified for announcement.")
            return
        for line in message.split('\n'):
            irc.sendMsg(ircmsgs.privmsg(channel, line))

    def subscribe(self, irc, msg, args):
        """<owner/repo> -- Subscribe to a GitHub repository and immediately fetch its latest event."""
        if not args:
            irc.reply("Usage: subscribe owner/repo")
            return
        repo = args[0]
        channel = msg.args[0]

        # Load current subscriptions (stored as a space-separated string)
        subscriptions = self.registryValue('subscriptions', channel)
        if isinstance(subscriptions, str):
            subscriptions = subscriptions.split()

        if repo not in subscriptions:
            subscriptions.append(repo)
            self.save_subscriptions(channel, subscriptions)
            irc.reply(f"Subscribed to {repo} in channel {channel}.")
            # Immediately fetch and announce the latest event for the new repo
            self.fetch_and_announce(repo, irc, msg, channel)
        else:
            irc.reply(f"Already subscribed to {repo} in channel {channel}.")

    def unsubscribe(self, irc, msg, args):
        """<owner/repo> -- Unsubscribe from a GitHub repository."""
        if not args:
            irc.reply("Usage: unsubscribe owner/repo")
            return
        repo = args[0]
        channel = msg.args[0]

        subscriptions = self.registryValue('subscriptions', channel)
        if isinstance(subscriptions, str):
            subscriptions = subscriptions.split()

        if repo in subscriptions:
            subscriptions.remove(repo)
            self.save_subscriptions(channel, subscriptions)
            irc.reply(f"Unsubscribed from {repo} in channel {channel}.")
        else:
            irc.reply(f"Not subscribed to {repo} in channel {channel}.")

    def save_subscriptions(self, channel, subscriptions):
        """Save the subscriptions (space-separated) for a channel into the configuration."""
        self.setRegistryValue('subscriptions', ' '.join(subscriptions), channel)

    def load_global_seen_ids(self):
        """Load the global history of event IDs from configuration."""
        history = self.registryValue('history')
        return history.split() if history else []

    def save_global_seen_ids(self, event_ids):
        """Save global event history to configuration (maximum 50 events)."""
        history = self.load_global_seen_ids()
        history.extend(event_ids)
        history = history[-50:]
        self.setRegistryValue('history', ' '.join(history))

    def listgitpulse(self, irc, msg, args):
        """List the GitHub repositories you're subscribed to in this channel."""
        channel = msg.args[0]
        subscriptions = self.registryValue('subscriptions', channel)
        if subscriptions:
            irc.reply(f"Subscribed repositories in {channel}: {subscriptions}")
        else:
            irc.reply(f"No repositories subscribed to in {channel}.")

    def poll(self):
        """Poll GitHub for events and announce them."""
        while self.polling_active:
            self.log.info("Polling for events...")
            for channel in self.irc.state.channels:
                subscriptions = self.registryValue('subscriptions', channel)
                if isinstance(subscriptions, str):
                    subscriptions = subscriptions.split()
                for repo in subscriptions:
                    self.fetch_and_announce(repo, self.irc, None, channel)
            self.log.info(f"Waiting for {self.registryValue('pollInterval')} seconds before next poll.")
            time.sleep(self.registryValue('pollInterval'))

    def start_polling(self):
        """Start the polling thread."""
        self.polling_active = True
        Thread(target=self.poll, daemon=True).start()

    def die(self):
        """Stop polling when the plugin is unloaded."""
        self.polling_active = False
        super().die()

Class = GitPulse

