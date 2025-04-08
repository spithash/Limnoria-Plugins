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
import json
import time
from threading import Thread
from supybot import callbacks, ircutils

class GitPulse(callbacks.Plugin):
    """Subscribe to GitHub repositories and output activity in the channel."""

    def __init__(self, irc):
        super().__init__(irc)
        self.subscriptions = {}  # Store subscribed repositories
        self.start_polling()

    def start_polling(self):
        """Start polling GitHub for events in a separate thread."""
        def poll():
            while True:
                for repo in self.subscriptions:
                    self.fetch_and_announce(repo)
                time.sleep(self.registryValue('pollInterval'))  # Use polling interval from config

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
            for event in events:
                message = self.format_event(event)
                self.announce(message)
        else:
            self.log.info(f"Failed to fetch events for {repo}: {response.status_code}")

    def format_event(self, event):
        """Format a GitHub event into a readable message."""
        event_type = event['type']
        repo_name = event['repo']['name']
        actor = event['actor']['login']
        created_at = event['created_at']

        # You can format this as per your needs. Here's an example format:
        return f"New event from {actor} in {repo_name} ({event_type}): {created_at}"

    def announce(self, message):
        """Announce the formatted message to the channel where the command was triggered."""
        # The channel will be the one the command was invoked in
        channel = self.ircutils.get_channel_from_message(self.ircmsgs)
        if channel:
            self.irc.queueMsg(ircutils.privmsg(channel, message))

    def subscribe(self, irc, msg, args):
        """<owner/repo>
        Subscribe to a GitHub repository to monitor its activity.
        """
        self.log.info(f"Received args in subscribe: {args}")  # Debugging log
        if len(args) < 1:
            irc.reply("Please provide the GitHub repository in the format 'owner/repo'.")
            return

        repo = args[0]

        if repo in self.subscriptions:
            irc.reply(f"Already subscribed to {repo}.")
        else:
            self.subscriptions[repo] = None  # Add repo to subscriptions list
            irc.reply(f"Subscribed to {repo}.")

    def unsubscribe(self, irc, msg, args):
        """<owner/repo>
        Unsubscribe from a GitHub repository.
        """
        self.log.info(f"Received args in unsubscribe: {args}")  # Debugging log
        if len(args) < 1:
            irc.reply("Please provide the GitHub repository in the format 'owner/repo'.")
            return

        repo = args[0]

        if repo in self.subscriptions:
            del self.subscriptions[repo]
            irc.reply(f"Unsubscribed from {repo}.")
        else:
            irc.reply(f"Not subscribed to {repo}.")

    def die(self):
        """Handle cleanup when the bot shuts down."""
        super().die()
        # You can add any cleanup code here, if needed.

Class = GitPulse

