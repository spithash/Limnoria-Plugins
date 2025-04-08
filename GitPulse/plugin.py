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
        self.subscriptions = []  # List of repositories to track
        self.last_checked = {}  # Track last fetch time per repo
        self.start_polling()

    def start_polling(self):
        """Start polling GitHub for events in a separate thread."""
        def poll():
            while True:
                for repo in self.subscriptions:
                    self.fetch_and_announce(repo)
                time.sleep(self.registryValue('pollInterval'))  # Default 600s

        Thread(target=poll, daemon=True).start()

    def fetch_and_announce(self, repo):
        """Fetch GitHub events for a repository and announce them."""
        github_token = self.registryValue('githubToken')
        headers = {}
        if github_token:
            headers['Authorization'] = f'token {github_token}'

        url = f"https://api.github.com/repos/{repo}/events"
        params = {'per_page': 100}  # Adjust as necessary
        all_events = []

        # Handle paginated results
        while url:
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 200:
                events = response.json()
                all_events.extend(events)
                # Get next page URL from the response headers
                url = response.links.get('next', {}).get('url', None)
            else:
                self.log.error(f"Failed to fetch events for {repo}: {response.status_code}")
                return

        if all_events:
            new_events = [event for event in all_events if self.is_new_event(event, repo)]
            if new_events:
                for event in new_events:
                    message = self.format_event(event)
                    self.announce(message)
            else:
                self.log.info(f"No new events for {repo}.")
        else:
            self.log.info(f"No events found for {repo}.")

    def is_new_event(self, event, repo):
        """Check if the event is new based on the timestamp."""
        created_at = event['created_at']
        last_checked = self.last_checked.get(repo)
        if last_checked and created_at <= last_checked:
            return False  # Already fetched this event
        self.last_checked[repo] = created_at
        return True

    def format_event(self, event):
        """Format a GitHub event into a readable message."""
        event_type = event['type']
        repo_name = event['repo']['name']
        actor = event['actor']['login']
        created_at = event['created_at']

        # Example of colored output using IRC color codes
        return f"\x02{actor}\x02 performed \x03,04{event_type}\x03 on \x02{repo_name}\x02 at {created_at}"

    def announce(self, message):
        """Announce the formatted message to the channel."""
        channel = self.ircutils.get_channel_from_message(self.ircmsgs)
        if channel:
            self.irc.queueMsg(ircutils.privmsg(channel, message))

    def subscribe(self, irc, msg, args):
        """<owner/repo>
        Subscribe to a GitHub repository to monitor its activity.
        """
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
        """<owner/repo>
        Unsubscribe from a GitHub repository."""
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
        """Manually fetch events from all subscribed repositories."""
        for repo in self.subscriptions:
            self.fetch_and_announce(repo)
        irc.reply("Fetched updates for all subscribed repositories.")

    def die(self):
        """Handle cleanup when the bot shuts down."""
        super().die()

Class = GitPulse

