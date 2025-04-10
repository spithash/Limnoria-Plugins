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
from threading import Thread, Event
from datetime import datetime, timedelta
from supybot import callbacks, ircmsgs

class GitPulse(callbacks.Plugin):
    """GitHub activity monitor using Events API."""

    def __init__(self, irc):
        super().__init__(irc)
        self.irc = irc
        self.polling_started = False
        self.polling_thread = None
        self.stop_polling_event = Event()  # Used to stop polling when the plugin is unloaded
        
        # Define colors once to use globally
        self.B = '\x02'  # Bold
        self.C = '\x03'  # Color prefix
        self.RESET = '\x0f'  # Reset
        self.GREEN = '03'  # Green color
        self.BLUE = '12'  # Blue color
        self.RED = '04'   # Red color
        self.YELLOW = '08'  # Yellow color
        
        self.start_polling()

    def start_polling(self):
        """Start the polling process when the plugin is initialized."""
        if not self.polling_started:
            self.polling_started = True
            self.log.info("Starting polling thread for GitHub events.")
            self.polling_thread = Thread(target=self.poll, daemon=True)
            self.polling_thread.start()

    def stop_polling(self):
        """Stop the polling process immediately when the plugin is unloaded."""
        self.stop_polling_event.set()  # Trigger the stop event
        if self.polling_thread:
            self.polling_thread.join()  # Ensure that the thread stops gracefully
        self.log.info("Polling thread stopped.")

    def poll(self):
        """Polls GitHub for events based on the repositories in the configuration."""
        while not self.stop_polling_event.is_set():
            self.log.info("Polling for events...")

            # Fetch subscribed repositories for all channels
            for channel in self.irc.state.channels:
                subscriptions = self.registryValue('subscriptions', channel)
                if isinstance(subscriptions, str):
                    subscriptions = subscriptions.split()

                # Poll each subscribed repository
                for repo in subscriptions:
                    self.fetch_and_announce(repo, self.irc, None, channel)

            # Wait for the configured poll interval before checking again
            self.log.info(f"Waiting for {self.registryValue('pollInterval')} seconds before next poll.")
            self.stop_polling_event.wait(self.registryValue('pollInterval'))  # Use wait to respect the stop event

    def fetch_and_announce(self, repo, irc, msg, channel):
        """Fetch events from GitHub and announce them in the channel."""
        self.log.debug(f"Fetching events for repository: {repo}")
        token = self.registryValue('githubToken')
        headers = {'Authorization': f'token {token}'} if token else {}
        url = f"https://api.github.com/repos/{repo}/events"
        resp = requests.get(url, headers=headers)

        if resp.status_code != 200:
            self.log.error(f"[GitPulse] Failed to fetch events for {repo}: {resp.status_code}")
            return

        events = resp.json()
        self.log.debug(f"Fetched {len(events)} events for {repo}")

        seen_ids = self.load_global_seen_ids()
        new_ids = []

        # Get the current time
        now = datetime.utcnow()

        for event in reversed(events):  # Reverse to get the latest events first
            event_id = event['id']
            event_timestamp = datetime.strptime(event['created_at'], '%Y-%m-%dT%H:%M:%SZ')

            # Check if the event occurred within the last 2 hours
            if now - event_timestamp > timedelta(hours=2):
                continue  # Skip events older than 2 hours

            self.log.debug(f"Checking event ID {event_id} (created at {event_timestamp})")

            if event_id in seen_ids:
                self.log.debug(f"Skipping event {event_id} (already seen)")
                continue  # Skip events that have already been posted

            # Process PushEvent
            if event['type'] == 'PushEvent':
                msg_text = self.format_push_event(event, repo)
                if msg_text:
                    self.log.info(f"Posting new event for {repo}: {msg_text}")
                    # First, post the event to the channel
                    self.announce(msg_text, irc, msg, channel)
                    # After posting the event, save the event ID
                    new_ids.append(event_id)
                else:
                    self.log.debug(f"No commit message found for event {event_id}")

            # Process PullRequestEvent
            elif event['type'] == 'PullRequestEvent':
                msg_text = self.format_pull_request_event(event, repo)
                if msg_text:
                    self.log.info(f"Posting new event for {repo}: {msg_text}")
                    # First, post the event to the channel
                    self.announce(msg_text, irc, msg, channel)
                    new_ids.append(event_id)

            # Process Issues
            elif event['type'] == 'IssuesEvent':
                msg_text = self.format_issues_event(event, repo)
                if msg_text:
                    self.log.info(f"Posting new event for {repo}: {msg_text}")
                    self.announce(msg_text, irc, msg, channel)
                    new_ids.append(event_id)

        if new_ids:
            # Save event IDs after posting the events
            self.save_global_seen_ids(new_ids)
        else:
            self.log.debug(f"No new events to post for {repo}")

    def format_push_event(self, event, repo):
        """Formats the PushEvent into a human-readable string."""
        actor = event['actor']['login']
        commits = event['payload'].get('commits', [])
        branch = event['payload']['ref'].split('/')[-1]  # Extract branch name

        if commits:
            msgs = []
            for c in commits:
                msg = c['message'].split('\n')[0]  # Only the first line of the commit message
                url = f"https://github.com/{repo}/commit/{c['sha']}"
                msgs.append(f"{self.B}{actor}{self.B} pushed: {self.C}{self.GREEN}branch: {self.B}{branch}{self.RESET} {msg}{self.RESET} to {self.B}{repo}{self.B}: {self.C}{self.BLUE}{url}{self.RESET}")
            return '\n'.join(msgs)
        return None

    def format_pull_request_event(self, event, repo):
        """Formats the PullRequestEvent into a human-readable string."""
        actor = event['actor']['login']
        pr_url = event['payload']['pull_request']['html_url']
        pr_title = event['payload']['pull_request']['title']
        action = event['payload']['action']
        branch = event['payload']['pull_request']['head']['ref']

        action_text = f"{self.C}{self.GREEN}{'opened'}{self.RESET}" if action == 'opened' else f"{self.C}{self.BLUE}{'closed'}{self.RESET}"

        return f"{self.B}{actor}{self.B} {action_text} pull request: {self.C}{self.RED}{pr_title}{self.RESET} on branch {self.B}{branch}{self.B}: {self.C}{self.BLUE}{pr_url}{self.RESET}"

    def format_issues_event(self, event, repo):
        """Formats the IssuesEvent into a human-readable string."""
        actor = event['actor']['login']
        issue_url = event['payload']['issue']['html_url']
        issue_title = event['payload']['issue']['title']
        action = event['payload']['action']
        issue_state = event['payload']['issue']['state']

        state_text = f"{self.C}{self.RED}{'opened'}{self.RESET}" if issue_state == 'open' else f"{self.C}{self.GREEN}{'closed'}{self.RESET}"

        return f"{self.B}{actor}{self.B} {self.C}{self.RED}{'issue'}{self.RESET} {state_text}: {self.C}{self.RED}{issue_title}{self.RESET} {self.C}{self.BLUE}{issue_url}{self.RESET}"

    def announce(self, message, irc, msg, channel):
        """Announce the formatted message in the channel."""
        if not channel:
            self.log.warning("No channel specified for announcement.")
            return
        for line in message.split('\n'):
            irc.sendMsg(ircmsgs.privmsg(channel, line))
            self.log.info(f"Posted message to channel {channel}: {line}")

    def subscribe(self, irc, msg, args):
        """Subscribe to a GitHub repository and immediately show the latest event."""
        if not args:
            irc.reply("Usage: subscribe owner/repo")
            return

        repo = args[0]
        channel = msg.args[0]

        # Fetch current subscriptions and ensure it's a list
        subscriptions = self.registryValue('subscriptions', channel)
        if isinstance(subscriptions, str):
            subscriptions = subscriptions.split()

        # If repo isn't already in the list, append it
        if repo not in subscriptions:
            subscriptions.append(repo)
            self.save_subscriptions(channel, subscriptions)
            irc.reply(f"Subscribed to {repo} in channel {channel}.")

            # Fetch the latest event for this newly subscribed repo and show it immediately
            self.fetch_and_announce(repo, irc, msg, channel)
        else:
            irc.reply(f"Already subscribed to {repo} in channel {channel}.")

    def unsubscribe(self, irc, msg, args):
        """Unsubscribe from a GitHub repository."""
        if not args:
            irc.reply("Usage: unsubscribe owner/repo")
            return

        repo = args[0]
        channel = msg.args[0]

        # Fetch current subscriptions and ensure it's a list
        subscriptions = self.registryValue('subscriptions', channel)
        if isinstance(subscriptions, str):
            subscriptions = subscriptions.split()

        # Remove the repository if it exists in the list
        if repo in subscriptions:
            subscriptions.remove(repo)
            self.save_subscriptions(channel, subscriptions)
            irc.reply(f"Unsubscribed from {repo} in channel {channel}.")
        else:
            irc.reply(f"Not subscribed to {repo} in channel {channel}.")

    def save_subscriptions(self, channel, subscriptions):
        """Save subscriptions for the channel in the configuration."""
        self.setRegistryValue('subscriptions', ' '.join(subscriptions), channel)

    def load_global_seen_ids(self):
        """Load the global seen event IDs."""
        history = self.registryValue('history')
        return history.split() if history else []

    def save_global_seen_ids(self, event_ids):
        """Save global event history."""
        history = self.load_global_seen_ids()
        history.extend(event_ids)
        # Ensure the history length doesn't exceed 50 event IDs
        history = history[-50:]
        self.setRegistryValue('history', ' '.join(history))

    def die(self):
        """This method is called when the plugin is unloaded."""
        self.stop_polling()  # Stop the polling thread when the plugin is unloaded
        super().die()

    def listgitpulse(self, irc, msg, args):
        """Lists all repositories currently subscribed to in the channel."""
        channel = msg.args[0]
        
        # Fetch current subscriptions
        subscriptions = self.registryValue('subscriptions', channel)
        
        if isinstance(subscriptions, str):
            subscriptions = subscriptions.split()

        if subscriptions:
            irc.reply(f"Subscribed to the following repositories in {channel}: {', '.join(subscriptions)}")
        else:
            irc.reply(f"No repositories subscribed to in {channel}.")


Class = GitPulse

