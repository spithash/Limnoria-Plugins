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
from datetime import datetime, timedelta
from collections import deque
from supybot import callbacks, ircmsgs
from supybot.commands import wrap
from colorama import Fore, Style, init

# Initialize colorama
init(autoreset=True)


class GitPulse(callbacks.Plugin):
    """Plugin that monitors public GitHub repositories for activity using the GitHub API."""

    def __init__(self, irc):
        super().__init__(irc)

        self.irc = irc

        self.polling_started = False
        self.polling_thread = None
        self.stop_polling_event = Event()

        # Conditional request caches
        self.etags = {}
        self.last_modifieds = {}

        # Deduplication caches with timestamps
        self.seen_events = {}  # event_id -> timestamp
        self.seen_commits = {}  # commit_sha -> timestamp

        # Logging colors
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

    # --------------------------------------------------
    # Logging
    # --------------------------------------------------

    def get_timestamp(self):
        return Fore.CYAN + Style.BRIGHT + datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S') + Style.RESET_ALL

    def log_debug(self, message):
        timestamp = self.get_timestamp()
        print(f"{timestamp} {self.TAG} {self.LEVEL_COLORS['DEBUG']}[DEBUG]{self.RESET_COLOR} {message}")

    def log_info(self, message):
        timestamp = self.get_timestamp()
        print(f"{timestamp} {self.TAG} {self.LEVEL_COLORS['INFO']}[INFO]{self.RESET_COLOR} {message}")

    def log_warning(self, message):
        timestamp = self.get_timestamp()
        print(f"{timestamp} {self.TAG} {self.LEVEL_COLORS['WARNING']}[WARNING]{self.RESET_COLOR} {message}")

    def log_error(self, message):
        timestamp = self.get_timestamp()
        print(f"{timestamp} {self.TAG} {self.LEVEL_COLORS['ERROR']}[ERROR]{self.RESET_COLOR} {message}")

    # --------------------------------------------------
    # Polling
    # --------------------------------------------------

    def start_polling(self):
        """Start the GitHub polling thread."""
        if not self.polling_started:
            self.polling_started = True
            self.log_info("Starting polling thread.")
            self.polling_thread = Thread(target=self.poll, daemon=True)
            self.polling_thread.start()

    def stop_polling(self):
        """Stop the GitHub polling thread."""
        self.stop_polling_event.set()

        if self.polling_thread:
            self.polling_thread.join(timeout=5)

        self.log_info("Polling thread stopped.")

    def clean_old_caches(self):
        """Remove items older than 1 hour from caches."""
        now = datetime.utcnow()
        one_hour_ago = now - timedelta(hours=1)
        
        # Clean events cache
        old_events = [eid for eid, ts in self.seen_events.items() if ts < one_hour_ago]
        for eid in old_events:
            del self.seen_events[eid]
        
        # Clean commits cache
        old_commits = [cid for cid, ts in self.seen_commits.items() if ts < one_hour_ago]
        for cid in old_commits:
            del self.seen_commits[cid]
        
        if old_events or old_commits:
            self.log_debug(f"Cleaned {len(old_events)} events and {len(old_commits)} commits from cache")

    def poll(self):
        """Main polling loop."""

        while not self.stop_polling_event.is_set():

            try:
                self.log_info("Polling repositories...")
                
                # Clean old cache entries
                self.clean_old_caches()

                channels = list(self.irc.state.channels.keys()) if self.irc.state.channels else []

                for channel in channels:

                    try:
                        subscriptions = self.registryValue('subscriptions', channel)

                        if isinstance(subscriptions, str):
                            subscriptions = subscriptions.split() if subscriptions else []
                        elif subscriptions is None:
                            subscriptions = []

                        for repo in subscriptions:

                            try:
                                self.fetch_and_announce(repo, self.irc, None, channel)

                            except Exception as e:
                                self.log_error(f"Error processing repo {repo} in {channel}: {e}")

                    except Exception as e:
                        self.log_error(f"Error processing channel {channel}: {e}")

                interval = self.registryValue('pollInterval')

                self.log_info(f"Sleeping for {interval} seconds.")

                self.stop_polling_event.wait(interval)

            except Exception as e:
                self.log_error(f"Unexpected polling loop error: {e}")
                self.stop_polling_event.wait(60)

    # --------------------------------------------------
    # GitHub API
    # --------------------------------------------------

    def github_get(self, url, cache_key):
        """Perform GitHub API request with ETag support."""

        token = self.registryValue('githubToken')

        headers = {
            'Cache-Control': 'no-cache',
            'Accept': 'application/vnd.github+json'
        }

        if token:
            headers['Authorization'] = f'token {token}'

        etag_sent = self.etags.get(cache_key)

        if etag_sent:
            headers['If-None-Match'] = etag_sent

        else:
            last_modified_sent = self.last_modifieds.get(cache_key)

            if last_modified_sent:
                headers['If-Modified-Since'] = last_modified_sent

        try:
            resp = requests.get(
                url,
                headers=headers,
                timeout=20
            )

        except requests.exceptions.Timeout:
            self.log_error(f"Timeout while requesting {url}")
            return None

        except requests.exceptions.RequestException as e:
            self.log_error(f"HTTP request failed for {url}: {e}")
            return None

        etag_received = resp.headers.get('ETag')
        last_modified_received = resp.headers.get('Last-Modified')

        if etag_received:
            self.etags[cache_key] = etag_received

        if last_modified_received:
            self.last_modifieds[cache_key] = last_modified_received

        return resp

    # --------------------------------------------------
    # Main fetcher
    # --------------------------------------------------

    def fetch_and_announce(self, repo, irc, msg, channel):
        """Fetch repository events and commits."""

        self.log_debug(f"Fetching events for repository: {repo}")

        events_url = f"https://api.github.com/repos/{repo}/events"

        resp = self.github_get(
            events_url,
            f"{channel}::{repo}::events"
        )

        if not resp:
            return

        self.log_rate_limit(repo, resp, "Events API")

        if resp.status_code == 304:
            self.log_info(f"No new events for {repo}")

        elif resp.status_code != 200:
            self.log_error(f"Failed fetching events for {repo}: HTTP {resp.status_code}")

        else:

            try:
                events = resp.json()

            except Exception as e:
                self.log_error(f"Failed parsing events JSON for {repo}: {e}")
                events = []

            self.process_events(events, repo, irc, msg, channel)

        self.fetch_and_announce_commits(repo, irc, msg, channel)

    def is_recent(self, timestamp_str):
        """Check if an ISO timestamp is within the last 20 minutes."""
        if not timestamp_str:
            return True
        
        try:
            # Parse GitHub's ISO timestamp format
            if timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str[:-1] + '+00:00'
            
            event_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            # Convert to naive UTC for comparison
            if event_time.tzinfo:
                event_time = event_time.replace(tzinfo=None)
            
            now = datetime.utcnow()
            twenty_minutes_ago = now - timedelta(minutes=20)
            
            return event_time > twenty_minutes_ago
        except Exception as e:
            self.log_debug(f"Failed to parse timestamp {timestamp_str}: {e}")
            return True  # Assume recent if we can't parse

    def process_events(self, events, repo, irc, msg, channel):
        """Process GitHub events."""

        self.log_debug(f"Processing {len(events)} events for {repo}")

        for event in reversed(events):

            try:
                event_id = event.get('id')
                created_at = event.get('created_at')

                if not event_id:
                    continue
                
                # Check if event is recent (within 20 minutes)
                if not self.is_recent(created_at):
                    self.log_debug(f"Skipping old event {event_id} from {created_at}")
                    continue

                # Check if we've already seen this event
                if event_id in self.seen_events:
                    continue

                # Store with timestamp
                self.seen_events[event_id] = datetime.utcnow()

                event_type = event.get('type')

                msg_text = None

                if event_type == 'PullRequestEvent':
                    msg_text = self.format_pull_request_event(event, repo)

                elif event_type == 'IssuesEvent':
                    msg_text = self.format_issues_event(event, repo)

                if msg_text:
                    self.log_info(f"Posting event for {repo}")
                    self.announce(msg_text, irc, channel)

            except Exception as e:
                self.log_error(f"Error processing event for {repo}: {e}")

    # --------------------------------------------------
    # Commits
    # --------------------------------------------------

    def fetch_and_announce_commits(self, repo, irc, msg, channel):
        """Fetch repository commits."""

        self.log_debug(f"Fetching commits for repository: {repo}")

        commits_url = f"https://api.github.com/repos/{repo}/commits"

        resp = self.github_get(
            commits_url,
            f"{channel}::{repo}::commits"
        )

        if not resp:
            return

        self.log_rate_limit(repo, resp, "Commits API")

        if resp.status_code == 304:
            self.log_info(f"No new commits for {repo}")
            return

        if resp.status_code != 200:
            self.log_error(f"Failed fetching commits for {repo}: HTTP {resp.status_code}")
            return

        try:
            commits = resp.json()

        except Exception as e:
            self.log_error(f"Failed parsing commits JSON for {repo}: {e}")
            return

        for commit in reversed(commits):

            try:
                sha = commit.get('sha')
                commit_date = commit.get('commit', {}).get('committer', {}).get('date')

                if not sha:
                    continue
                
                # Check if commit is recent (within 20 minutes)
                if not self.is_recent(commit_date):
                    self.log_debug(f"Skipping old commit {sha} from {commit_date}")
                    continue

                # Check if we've already seen this commit
                if sha in self.seen_commits:
                    continue

                # Store with timestamp
                self.seen_commits[sha] = datetime.utcnow()

                event = {
                    'actor': {
                        'login': commit['commit']['committer']['name']
                    },
                    'payload': {
                        'commits': [{
                            'message': commit['commit']['message'],
                            'sha': sha
                        }],
                        'ref': 'refs/heads/main'
                    }
                }

                msg_text = self.format_push_event(event, repo)

                if msg_text:
                    self.log_info(f"Posting commit for {repo}")
                    self.announce(msg_text, irc, channel)

            except Exception as e:
                self.log_error(f"Error processing commit for {repo}: {e}")

    # --------------------------------------------------
    # Formatting
    # --------------------------------------------------

    def format_push_event(self, event, repo):
        """Format commit push event."""

        actor = event['actor']['login']
        commits = event['payload'].get('commits', [])
        branch = event['payload']['ref'].split('/')[-1]

        if not commits:
            return None

        msgs = []

        for c in commits:

            commit_msg = c['message'].split('\n')[0]

            url = f"https://github.com/{repo}/commit/{c['sha']}"

            msgs.append(
                f"{self.B}{actor}{self.B} pushed "
                f"to {self.C}{self.GREEN}{branch}{self.RESET} "
                f"in {self.B}{repo}{self.B}: "
                f"{commit_msg} "
                f"{self.C}{self.BLUE}{url}{self.RESET}"
            )

        return '\n'.join(msgs)

    def format_pull_request_event(self, event, repo):
        """Format pull request event."""

        actor = event['actor']['login']

        pr = event['payload']['pull_request']

        action = event['payload']['action']

        title = pr['title']

        url = pr['html_url']

        branch = pr['head']['ref']

        return (
            f"{self.B}{actor}{self.B} "
            f"{self.C}{self.GREEN}{action}{self.RESET} "
            f"pull request on "
            f"{self.B}{branch}{self.B} "
            f"in {self.B}{repo}{self.B}: "
            f"{self.C}{self.RED}{title}{self.RESET} "
            f"{self.C}{self.BLUE}{url}{self.RESET}"
        )

    def format_issues_event(self, event, repo):
        """Format issues event."""

        actor = event['actor']['login']

        issue = event['payload']['issue']

        title = issue['title']

        url = issue['html_url']

        action = event['payload']['action']

        return (
            f"{self.B}{actor}{self.B} "
            f"{self.C}{self.YELLOW}{action}{self.RESET} "
            f"issue in "
            f"{self.B}{repo}{self.B}: "
            f"{self.C}{self.RED}{title}{self.RESET} "
            f"{self.C}{self.BLUE}{url}{self.RESET}"
        )

    # --------------------------------------------------
    # IRC
    # --------------------------------------------------

    def announce(self, message, irc, channel):
        """Send IRC announcement."""

        if not channel:
            self.log_warning("No channel specified.")
            return

        for line in message.split('\n'):

            try:
                irc.queueMsg(ircmsgs.privmsg(channel, line))
                self.log_info(f"Posted to {channel}: {line}")

            except Exception as e:
                self.log_error(f"Failed posting to {channel}: {e}")

    # --------------------------------------------------
    # Logging helpers
    # --------------------------------------------------

    def log_rate_limit(self, repo, resp, api_name):
        """Log GitHub API rate limit info."""

        rate_limit = resp.headers.get('X-RateLimit-Limit', 'N/A')
        rate_remaining = resp.headers.get('X-RateLimit-Remaining', 'N/A')
        rate_used = resp.headers.get('X-RateLimit-Used', 'N/A')
        rate_reset = resp.headers.get('X-RateLimit-Reset')

        reset_time = "unknown"

        try:
            if rate_reset:
                reset_time = datetime.utcfromtimestamp(
                    int(rate_reset)
                ).isoformat()

        except Exception:
            pass

        self.log_info(
            f"{repo} | "
            f"{api_name} | "
            f"HTTP {resp.status_code} | "
            f"Used: {rate_used} | "
            f"Remaining: {rate_remaining}/{rate_limit} | "
            f"Reset: {reset_time} UTC"
        )

    # --------------------------------------------------
    # Commands
    # --------------------------------------------------

    def subscribe(self, irc, msg, args, repo):
        """Subscribe the current channel to a GitHub repository."""
        
        channel = msg.args[0]
        
        # Get current subscriptions
        current = self.registryValue('subscriptions', channel)
        
        if isinstance(current, str):
            subscriptions = current.split() if current else []
        elif current is None:
            subscriptions = []
        else:
            subscriptions = current if isinstance(current, list) else []
        
        if repo in subscriptions:
            irc.reply(f"[GitPulse] Already subscribed to {repo}.")
            return
        
        subscriptions.append(repo)
        
        # Save as space-separated string
        self.setRegistryValue('subscriptions', ' '.join(subscriptions), channel)
        
        irc.reply(f"[GitPulse] Subscribed to {repo}.")
        
        self.fetch_and_announce(repo, irc, msg, channel)

    subscribe = wrap(subscribe, ['something'])

    def unsubscribe(self, irc, msg, args, repo):
        """Unsubscribe the current channel from a GitHub repository."""
        
        channel = msg.args[0]
        
        # Get current subscriptions
        current = self.registryValue('subscriptions', channel)
        
        if isinstance(current, str):
            subscriptions = current.split() if current else []
        elif current is None:
            subscriptions = []
        else:
            subscriptions = current if isinstance(current, list) else []
        
        if repo not in subscriptions:
            irc.reply(f"[GitPulse] Not subscribed to {repo}.")
            return
        
        subscriptions.remove(repo)
        
        # Save as space-separated string
        if subscriptions:
            self.setRegistryValue('subscriptions', ' '.join(subscriptions), channel)
        else:
            self.setRegistryValue('subscriptions', '', channel)
        
        irc.reply(f"[GitPulse] Unsubscribed from {repo}.")

    unsubscribe = wrap(unsubscribe, ['something'])

    def listgitpulse(self, irc, msg, args):
        """List subscribed repositories in the current channel."""
        
        channel = msg.args[0]
        
        current = self.registryValue('subscriptions', channel)
        
        if isinstance(current, str):
            subscriptions = current.split() if current else []
        elif current is None:
            subscriptions = []
        else:
            subscriptions = current if isinstance(current, list) else []
        
        if subscriptions:
            irc.reply(
                f"[GitPulse] Subscribed repositories in {channel}: "
                f"{', '.join(subscriptions)}"
            )
        
        else:
            irc.reply(f"[GitPulse] No subscribed repositories in {channel}.")

    listgitpulse = wrap(listgitpulse)

    def fetchgitpulse(self, irc, msg, args):
        """Manually fetch updates for all subscribed repositories."""
        
        channel = msg.args[0]
        
        current = self.registryValue('subscriptions', channel)
        
        if isinstance(current, str):
            subscriptions = current.split() if current else []
        elif current is None:
            subscriptions = []
        else:
            subscriptions = current if isinstance(current, list) else []
        
        if not subscriptions:
            irc.reply(f"[GitPulse] No subscribed repositories in {channel}.")
            return
        
        irc.reply(
            f"[GitPulse] Fetching updates for "
            f"{len(subscriptions)} repositories."
        )
        
        for repo in subscriptions:
            self.fetch_and_announce(repo, irc, msg, channel)

    fetchgitpulse = wrap(fetchgitpulse, ['owner'])

    # --------------------------------------------------
    # Shutdown
    # --------------------------------------------------

    def die(self):
        """Cleanup plugin resources on shutdown."""

        self.stop_polling()

        super().die()


Class = GitPulse
