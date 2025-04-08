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
import time
from threading import Thread
from supybot import callbacks, ircutils

class GitPulse(callbacks.Plugin):
    """Subscribe to GitHub repositories and output activity in the channel."""

    def __init__(self, irc):
        super().__init__(irc)
        self.subscriptions = []  # List of repositories
        self.poll_interval = self.registryValue('pollInterval')  # Get the polling interval from the configuration
        self.start_polling()

    def start_polling(self):
        """Start polling GitHub for events in a separate thread."""
        def poll():
            while True:
                for repo in self.subscriptions:
                    self.fetch_and_announce(repo)
                time.sleep(self.poll_interval)

        Thread(target=poll, daemon=True).start()

    def fetch_and_announce(self, repo):
        """Fetch GitHub events for a repository and announce them."""
        github_token = self.registryValue('githubToken')  # Get the token if available
        headers = {}
        if github_token:
            headers['Authorization'] = f'token {github_token}'

        url = f"https://api.github.com/repos/{repo}/events"
        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            events = response.json()
            if events:
                for event in events:
                    message = self.format_event(event)
                    self.announce(message)
            else:
                self.log.info(f"No new events for {repo}.")
        else:
            self.log.info(f"Failed to fetch events for {repo}: {response.status_code}")

    def format_event(self, event):
        """Format a GitHub event into a readable and colorful message."""
        event_type = event['type']
        repo_name = event['repo']['name']
        actor = event['actor']['login']
        created_at = event['created_at']
        
        # Assign color based on event type
        if event_type == 'PushEvent':
            color = ircutils.mircColor('green')  # Green for commits
        elif event_type == 'IssuesEvent':
            color = ircutils.mircColor('yellow')  # Yellow for issues
        else:
            color = ircutils.mircColor('blue')  # Blue for other events

        return f"{color}New event: {event_type} by {actor} in {repo_name} on {created_at}"

    def announce(self, message):
        """Announce the formatted message to the channel."""
        channel = self.ircutils.get_channel_from_message(self.ircmsgs)
        if channel:
            self.irc.queueMsg(ircutils.privmsg(channel, message))

    def subscribe(self, irc, msg, args):
        """Subscribe to a GitHub repository to monitor its activity."""
        if len(args) < 1:
            irc.reply("Please provide the GitHub repository in the format 'owner/repo'.")
            return

        repo = args[0]

        if repo not in self.subscriptions:
            self.subscriptions.append(repo)
            irc.reply(f"Subscribed to {repo}.")
        else:
            irc.reply(f"Already subscribed to {repo}.")

    def unsubscribe(self, irc, msg, args):
        """Unsubscribe from a GitHub repository."""
        if len(args) < 1:
            irc.reply("Please provide the GitHub repository in the format 'owner/repo'.")
            return

        repo = args[0]

        if repo in self.subscriptions:
            self.subscriptions.remove(repo)
            irc.reply(f"Unsubscribed from {repo}.")
        else:
            irc.reply(f"Not subscribed to {repo}.")

    def fetchgitpulse(self, irc, msg, args):
        """Manually fetch updates for all subscribed repositories."""
        for repo in self.subscriptions:
            irc.reply(f"Fetching updates for {repo}...")
            self.fetch_and_announce(repo)

    def die(self):
        """Handle cleanup when the bot shuts down."""
        super().die()

Class = GitPulse

