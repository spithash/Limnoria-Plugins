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
from supybot import callbacks, ircmsgs


class GitPulse(callbacks.Plugin):
    """GitHub activity monitor using Events API."""

    def __init__(self, irc):
        super().__init__(irc)
        self.irc = irc
        self.polling_started = False
        self.polling_thread = None
        self.stop_polling_event = Event()  # Used to stop polling when the plugin is unloaded
        self.start_polling()

        # Define color codes and styles
        self.B = '\x02'   # Bold
        self.C = '\x03'   # Color
        self.RESET = '\x0f'  # Reset color
        self.GREEN = '03'    # Green
        self.RED = '04'      # Red
        self.YELLOW = '08'   # Yellow
        self.CYAN = '11'     # Cyan
        self.BLUE = '12'     # Blue

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

        for event in reversed(events):  # Reverse to get the latest events first
            event_id = event['id']
            self.log.debug(f"Checking event ID {event_id}")

            if event_id in seen_ids:
                self.log.debug(f"Skipping event {event_id} (already seen)")
                continue  # Skip events that have already been posted

            msg_text = None
            if event['type'] == 'PushEvent':
                msg_text = self.format_push_event(event, repo)
            elif event['type'] == 'PullRequestEvent':
                msg_text = self.format_pull_request_event(event, repo)
            elif event['type'] == 'IssuesEvent':
                msg_text = self.format_issue_event(event, repo)

            if msg_text:
                self.log.info(f"Posting new event for {repo}: {msg_text}")
                # First, post the event to the channel
                self.announce(msg_text, irc, msg, channel)
                # After posting the event, save the event ID
                new_ids.append(event_id)
            else:
                self.log.debug(f"No relevant event found for {event_id}")

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
        msg = f"{self.B}{actor}{self.B} pushed: {self.C}{self.GREEN}branch: {self.B}{branch}{self.RESET} {self.C}{self.CYAN}{event['payload']['commits'][0]['message'].splitlines()[0]}{self.RESET} to {self.B}{repo}{self.B}: {self.C}{self.BLUE}https://github.com/{repo}/commit/{event['payload']['commits'][0]['sha']}{self.RESET}"
        return msg

    def format_pull_request_event(self, event, repo):
        """Formats the PullRequestEvent into a human-readable string."""
        action = event['payload']['action']
        pr_title = event['payload']['pull_request']['title']
        pr_branch = event['payload']['pull_request']['head']['ref']
        pr_id = event['payload']['pull_request']['id']
        pr_state = event['payload']['pull_request']['state']
        action_color = self.GREEN if action == 'opened' else self.RED
        state_color = self.GREEN if pr_state == 'open' else self.BLUE
        return f"{self.B}{event['actor']['login']}{self.B} {action_color}{action}{self.RESET} pull request {self.C}{self.RED}#{pr_id}{self.RESET} {self.C}{self.CYAN}{pr_title}{self.RESET} ({self.C}{self.YELLOW}branch: {self.CYAN}{pr_branch}{self.RESET}) to {self.B}{repo}{self.B}: {self.C}{self.BLUE}https://github.com/{repo}/pull/{pr_id}{self.RESET}"

    def format_issue_event(self, event, repo):
        """Formats the IssueEvent into a human-readable string."""
        action = event['payload']['action']
        issue_title = event['payload']['issue']['title']
        issue_id = event['payload']['issue']['number']
        issue_state = event['payload']['issue']['state']
        action_color = self.RED if action == 'opened' else self.GREEN
        state_color = self.RED if issue_state == 'open' else self.GREEN
        return f"{self.B}{event['actor']['login']}{self.B} {action_color}{action}{self.RESET} {self.C}{self.RED}issue#{issue_id}{self.RESET} {self.C}{self.CYAN}{issue_title}{self.RESET} ({self.C}{state_color}{issue_state}{self.RESET}) in {self.B}{repo}{self.B}: {self.C}{self.BLUE}https://github.com/{repo}/issues/{issue_id}{self.RESET}"

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

