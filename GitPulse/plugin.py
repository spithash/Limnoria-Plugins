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
from supybot import callbacks, ircmsgs
from supybot.commands import wrap
from colorama import Fore, Style, init

# Initialize colorama to support colored terminal output and auto reset styles after each print
init(autoreset=True)

class GitPulse(callbacks.Plugin):
    """Plugin that monitors GitHub repositories for activity using the GitHub Events API."""

    def __init__(self, irc):
        super().__init__(irc)
        self.irc = irc
        self.polling_started = False
        self.polling_thread = None
        self.stop_polling_event = Event()  # Used to signal polling thread to stop
        self.etags = {}  # Store ETag headers per repo to optimize GitHub API usage

        # Styles for coloring logs
        self.TAG = Style.BRIGHT + Fore.WHITE + "[GitPulse]" + Style.RESET_ALL

        # Different colors for log levels to make output easy to scan
        self.LEVEL_COLORS = {
            "DEBUG": Fore.MAGENTA + Style.BRIGHT,
            "INFO": Fore.GREEN + Style.BRIGHT,
            "WARNING": Fore.YELLOW + Style.BRIGHT,
            "ERROR": Fore.RED + Style.BRIGHT,
        }
        self.RESET_COLOR = Style.RESET_ALL

        # Start the background polling loop
        self.start_polling()

        # IRC formatting codes for messages
        self.B = '\x02'  # Bold
        self.C = '\x03'  # IRC color code prefix
        self.RESET = '\x0f'  # Reset IRC formatting
        self.GREEN = '03'
        self.BLUE = '12'
        self.RED = '04'
        self.YELLOW = '08'

    def get_timestamp(self):
        """Get current UTC time in ISO8601 format with bright cyan color for console logs."""
        now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
        return Fore.CYAN + Style.BRIGHT + now + Style.RESET_ALL

    def log_debug(self, message):
        """Log a debug-level message with timestamp and colors."""
        timestamp = self.get_timestamp()
        level = "DEBUG"
        print(f"{timestamp} {self.TAG} {self.LEVEL_COLORS[level]}[{level}]{self.RESET_COLOR} {message}")

    def log_info(self, message):
        """Log an info-level message with timestamp and colors."""
        timestamp = self.get_timestamp()
        level = "INFO"
        print(f"{timestamp} {self.TAG} {self.LEVEL_COLORS[level]}[{level}]{self.RESET_COLOR} {message}")

    def log_warning(self, message):
        """Log a warning-level message with timestamp and colors."""
        timestamp = self.get_timestamp()
        level = "WARNING"
        print(f"{timestamp} {self.TAG} {self.LEVEL_COLORS[level]}[{level}]{self.RESET_COLOR} {message}")

    def log_error(self, message):
        """Log an error-level message with timestamp and colors."""
        timestamp = self.get_timestamp()
        level = "ERROR"
        print(f"{timestamp} {self.TAG} {self.LEVEL_COLORS[level]}[{level}]{self.RESET_COLOR} {message}")

    def start_polling(self):
        """Starts the background thread that polls GitHub for repository events."""
        if not self.polling_started:
            self.polling_started = True
            self.log_info("Starting polling thread for GitHub events.")
            self.polling_thread = Thread(target=self.poll, daemon=True)
            self.polling_thread.start()

    def stop_polling(self):
        """Signals the polling thread to stop and waits for it to finish."""
        self.stop_polling_event.set()
        if self.polling_thread:
            self.polling_thread.join()
        self.log_info("Polling thread stopped.")

    def poll(self):
        """Background loop that polls subscribed repos at configured intervals."""
        while not self.stop_polling_event.is_set():
            self.log_info("Polling for events...")

            # Iterate through all channels and their repo subscriptions
            for channel in self.irc.state.channels:
                subscriptions = self.registryValue('subscriptions', channel)
                if isinstance(subscriptions, str):
                    subscriptions = subscriptions.split()

                for repo in subscriptions:
                    self.fetch_and_announce(repo, self.irc, None, channel)

            interval = self.registryValue('pollInterval')
            self.log_info(f"Waiting for {interval} seconds before next poll.")
            # Wait for interval seconds or until stop signal
            self.stop_polling_event.wait(interval)

    def fetch_and_announce(self, repo, irc, msg, channel):
        """Fetch latest events for a given repo and announce new ones to the IRC channel."""
        self.log_debug(f"Fetching events for repository: {repo}")
        token = self.registryValue('githubToken')
        headers = {'Cache-Control': 'no-cache'}
        if token:
            headers['Authorization'] = f'token {token}'

        # Send ETag to use conditional requests and reduce API rate limit usage
        etag_sent = self.etags.get(repo)
        if etag_sent:
            headers['If-None-Match'] = etag_sent

        url = f"https://api.github.com/repos/{repo}/events"
        try:
            resp = requests.get(url, headers=headers)
        except Exception as e:
            self.log_error(f"HTTP request failed for {repo}: {e}")
            return

        etag_received = resp.headers.get('ETag')

        # Extract rate limit headers for logging and safety checks
        rate_limit = resp.headers.get('X-RateLimit-Limit')
        rate_remaining = resp.headers.get('X-RateLimit-Remaining')
        rate_used = resp.headers.get('X-RateLimit-Used')
        rate_reset = resp.headers.get('X-RateLimit-Reset')

        reset_time_str = "unknown"
        try:
            if rate_reset:
                reset_time_str = datetime.utcfromtimestamp(int(rate_reset)).isoformat()
        except Exception as e:
            self.log_warning(f"Could not parse rate reset time: {e}")

        # Add color to key values for easy reading in logs
        repo_c = Fore.CYAN + repo + Style.RESET_ALL
        rate_used_c = Fore.MAGENTA + (rate_used if rate_used else "N/A") + Style.RESET_ALL
        rate_remaining_c = Fore.GREEN + (rate_remaining if rate_remaining else "N/A") + Style.RESET_ALL
        rate_limit_c = Fore.YELLOW + (rate_limit if rate_limit else "N/A") + Style.RESET_ALL
        etag_sent_c = Fore.BLUE + (etag_sent if etag_sent else "None") + Style.RESET_ALL
        etag_received_c = Fore.BLUE + (etag_received if etag_received else "None") + Style.RESET_ALL
        reset_time_c = Fore.CYAN + reset_time_str + Style.RESET_ALL

        self.log_info(
            f"Repo: {repo_c} | Status: {resp.status_code} | "
            f"Rate: Used {rate_used_c}, Remaining {rate_remaining_c}/{rate_limit_c} | "
            f"Resets at {reset_time_c} UTC | "
            f"ETag sent: {etag_sent_c} | ETag received: {etag_received_c}"
        )

        # Avoid making calls if close to exhausting rate limit
        if rate_remaining is not None and int(rate_remaining) < 100:
            self.log_warning(f"Rate limit nearly exhausted ({rate_remaining} remaining). Skipping fetch for {repo_c}. Resets at {reset_time_c} UTC")
            return

        # If content not modified since last poll, no need to parse further
        if resp.status_code == 304:
            self.log_info(f"No new data (304 Not Modified) for {repo_c}")
            return

        # Log an error if request failed
        if resp.status_code != 200:
            self.log_error(f"Failed to fetch events for {repo_c}: HTTP {resp.status_code}")
            return

        # Update stored ETag for next request
        if etag_received:
            self.etags[repo] = etag_received

        # Try parsing JSON response
        try:
            events = resp.json()
        except Exception as e:
            self.log_error(f"Failed to parse JSON response: {e}")
            return

        self.log_debug(f"Fetched {len(events)} events for {repo_c}")

        now = datetime.now(timezone.utc)
        # Ignore events older than 12 hours
        cutoff = now - timedelta(hours=12)

        # Process events in reverse order (oldest first)
        for event in reversed(events):
            try:
                event_timestamp = datetime.strptime(event['created_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            except Exception as e:
                self.log_warning(f"Skipping event due to invalid timestamp: {e}")
                continue

            if event_timestamp < cutoff:
                # Skip older events
                continue

            # Only announce certain event types for now
            if event['type'] == 'PullRequestEvent':
                msg_text = self.format_pull_request_event(event, repo)
                if msg_text:
                    self.log_info(f"Posting PullRequestEvent: {msg_text}")
                    self.announce(msg_text, irc, msg, channel)

            elif event['type'] == 'IssuesEvent':
                msg_text = self.format_issues_event(event, repo)
                if msg_text:
                    self.log_info(f"Posting IssuesEvent: {msg_text}")
                    self.announce(msg_text, irc, msg, channel)

        # Fetch and announce commits separately from the commits API
        self.fetch_and_announce_commits(repo, irc, msg, channel)

    def fetch_and_announce_commits(self, repo, irc, msg, channel):
        """Fetch latest commits separately from the GitHub commits API and announce."""
        self.log_debug(f"Fetching commits for repository: {repo}")
        token = self.registryValue('githubToken')
        headers = {'Cache-Control': 'no-cache'}
        if token:
            headers['Authorization'] = f'token {token}'

        url = f"https://api.github.com/repos/{repo}/commits"
        try:
            resp = requests.get(url, headers=headers)
            if resp.status_code != 200:
                self.log_error(f"Failed to fetch commits for {repo}: HTTP {resp.status_code}")
                return
            commits = resp.json()
        except Exception as e:
            self.log_error(f"Error fetching commits: {e}")
            return

        now = datetime.now(timezone.utc)

        # # Ignore events older than 1 hour
        cutoff = now - timedelta(hours=1)

        for commit in reversed(commits):
            try:
                commit_time = datetime.strptime(commit['commit']['committer']['date'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                if commit_time < cutoff:
                    continue
                event = {
                    'actor': {'login': commit['commit']['committer']['name']},
                    'payload': {
                        'commits': [{'message': commit['commit']['message'], 'sha': commit['sha']}],
                        'ref': 'refs/heads/main'
                    }
                }
                msg_text = self.format_push_event(event, repo)
                if msg_text:
                    self.log_info(f"Posting commit: {msg_text}")
                    self.announce(msg_text, irc, msg, channel)
            except Exception as e:
                self.log_warning(f"Error parsing commit: {e}")

    def format_push_event(self, event, repo):
        """Format a push event into a nicely colored IRC message."""
        actor = event['actor']['login']
        commits = event['payload'].get('commits', [])
        branch = event['payload']['ref'].split('/')[-1]

        if commits:
            msgs = []
            for c in commits:
                # Only use the first line of commit message for brevity
                msg = c['message'].split('\n')[0]
                url = f"https://github.com/{repo}/commit/{c['sha']}"
                # IRC color codes for emphasis
                msgs.append(f"{self.B}{actor}{self.B} pushed: {self.C}{self.GREEN}branch: {self.B}{branch}{self.RESET} {msg}{self.RESET} to {self.B}{repo}{self.B}: {self.C}{self.BLUE}{url}{self.RESET}")
            return '\n'.join(msgs)
        return None

    def format_pull_request_event(self, event, repo):
        """Format a pull request event into a colored IRC message."""
        actor = event['actor']['login']
        pr = event['payload']['pull_request']
        pr_url = pr['html_url']
        pr_title = pr['title']
        action = event['payload']['action']
        branch = pr['head']['ref']

        # Highlight action differently if newly opened
        action_text = f"{self.C}{self.GREEN}opened{self.RESET}" if action == 'opened' else f"{self.C}{self.BLUE}{action}{self.RESET}"
        return f"{self.B}{actor}{self.B} {action_text} pull request: {self.C}{self.RED}{pr_title}{self.RESET} on branch {self.B}{branch}{self.B}: {self.C}{self.BLUE}{pr_url}{self.RESET}"

    def format_issues_event(self, event, repo):
        """Format an issue event into a colored IRC message."""
        actor = event['actor']['login']
        issue = event['payload']['issue']
        issue_url = issue['html_url']
        issue_title = issue['title']
        action = event['payload']['action']
        issue_state = issue['state']

        # Different color for opened vs closed issues
        state_text = f"{self.C}{self.RED}opened{self.RESET}" if issue_state == 'open' else f"{self.C}{self.GREEN}closed{self.RESET}"
        return f"{self.B}{actor}{self.B} {self.C}{self.RED}issue{self.RESET} {state_text}: {self.C}{self.RED}{issue_title}{self.RESET} {self.C}{self.BLUE}{issue_url}{self.RESET}"

    def announce(self, message, irc, msg, channel):
        """Send message lines to the specified IRC channel."""
        if not channel:
            self.log_warning("No channel specified for announcement.")
            return
        for line in message.split('\n'):
            irc.sendMsg(ircmsgs.privmsg(channel, line))
            self.log_info(f"Posted message to channel {channel}: {line}")

    def subscribe(self, irc, msg, args):
        """IRC command to subscribe current channel to a repository."""
        if not args:
            irc.reply("[GitPulse] Usage: subscribe owner/repo")
            return

        repo = args[0]
        channel = msg.args[0]
        subscriptions = self.registryValue('subscriptions', channel)
        if isinstance(subscriptions, str):
            subscriptions = subscriptions.split()

        if repo not in subscriptions:
            subscriptions.append(repo)
            self.save_subscriptions(channel, subscriptions)
            irc.reply(f"[GitPulse] Subscribed to {repo} in channel {channel}.")
            # Fetch immediately after subscribing
            self.fetch_and_announce(repo, irc, msg, channel)
        else:
            irc.reply(f"[GitPulse] Already subscribed to {repo} in channel {channel}.")

    def unsubscribe(self, irc, msg, args):
        """IRC command to unsubscribe current channel from a repository."""
        if not args:
            irc.reply("[GitPulse] Usage: unsubscribe owner/repo")
            return

        repo = args[0]
        channel = msg.args[0]
        subscriptions = self.registryValue('subscriptions', channel)
        if isinstance(subscriptions, str):
            subscriptions = subscriptions.split()

        if repo in subscriptions:
            subscriptions.remove(repo)
            self.save_subscriptions(channel, subscriptions)
            irc.reply(f"[GitPulse] Unsubscribed from {repo} in channel {channel}.")
        else:
            irc.reply(f"[GitPulse] Not subscribed to {repo} in channel {channel}.")

    def save_subscriptions(self, channel, subscriptions):
        """Save updated subscription list back to the registry."""
        self.setRegistryValue('subscriptions', ' '.join(subscriptions), channel)

    def listgitpulse(self, irc, msg, args):
        """IRC command to list all repos subscribed in the current channel."""
        channel = msg.args[0]
        subscriptions = self.registryValue('subscriptions', channel)
        if isinstance(subscriptions, str):
            subscriptions = subscriptions.split()
        if subscriptions:
            irc.reply(f"[GitPulse] Subscribed to the following repositories in {channel}: {', '.join(subscriptions)}")
        else:
            irc.reply(f"[GitPulse] No repositories subscribed to in {channel}.")

    @wrap(['owner'])
    def fetchgitpulse(self, irc, msg, args):
        """Manually trigger fetching GitHub events for all repos subscribed in the channel."""
        channel = msg.args[0]
        subscriptions = self.registryValue('subscriptions', channel)
        if isinstance(subscriptions, str):
            subscriptions = subscriptions.split()
        if not subscriptions:
            irc.reply(f"[GitPulse] No repositories subscribed to in {channel}.")
            return

        irc.reply(f"[GitPulse] Manually fetching updates for {len(subscriptions)} repositories...")
        for repo in subscriptions:
            self.fetch_and_announce(repo, irc, msg, channel)

    def die(self):
        """Cleanup on plugin shutdown."""
        self.stop_polling()
        super().die()

Class = GitPulse

