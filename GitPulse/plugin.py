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

class GitPulse(callbacks.Plugin):
    """GitHub activity monitor using Events API."""

    def __init__(self, irc):
        super().__init__(irc)
        self.irc = irc
        self.polling_started = False
        self.polling_thread = None
        self.stop_polling_event = Event()
        self.etags = {}

        self.B = '\x02'  # Bold
        self.C = '\x03'  # Color prefix
        self.RESET = '\x0f'  # Reset
        self.GREEN = '03'
        self.BLUE = '12'
        self.RED = '04'
        self.YELLOW = '08'

        self.start_polling()

    def start_polling(self):
        if not self.polling_started:
            self.polling_started = True
            self.log.info("[GitPulse] Starting polling thread for GitHub events.")
            self.polling_thread = Thread(target=self.poll, daemon=True)
            self.polling_thread.start()

    def stop_polling(self):
        self.stop_polling_event.set()
        if self.polling_thread:
            self.polling_thread.join()
        self.log.info("[GitPulse] Polling thread stopped.")

    def poll(self):
        while not self.stop_polling_event.is_set():
            self.log.info("[GitPulse] Polling for events...")

            for channel in self.irc.state.channels:
                subscriptions = self.registryValue('subscriptions', channel)
                if isinstance(subscriptions, str):
                    subscriptions = subscriptions.split()

                for repo in subscriptions:
                    self.fetch_and_announce(repo, self.irc, None, channel)

            interval = self.registryValue('pollInterval')
            self.log.info(f"[GitPulse] Waiting for {interval} seconds before next poll.")
            self.stop_polling_event.wait(interval)

    def fetch_and_announce(self, repo, irc, msg, channel):
        self.log.debug(f"[GitPulse] Fetching events for repository: {repo}")
        token = self.registryValue('githubToken')
        headers = {'Cache-Control': 'no-cache'}
        if token:
            headers['Authorization'] = f'token {token}'

        # Add If-None-Match header if we have a stored ETag for this repo
        etag = self.etags.get(repo)
        if etag:
            headers['If-None-Match'] = etag

        url = f"https://api.github.com/repos/{repo}/events"
        resp = requests.get(url, headers=headers)

        rate_limit = resp.headers.get('X-RateLimit-Limit')
        rate_remaining = resp.headers.get('X-RateLimit-Remaining')
        rate_used = resp.headers.get('X-RateLimit-Used')
        rate_reset = resp.headers.get('X-RateLimit-Reset')

        reset_time_str = "unknown"
        try:
            if rate_reset:
                reset_time_str = datetime.utcfromtimestamp(int(rate_reset)).isoformat()
        except Exception as e:
            self.log.warning(f"[GitPulse] Could not parse rate reset time: {e}")

        self.log.info(
            f"[GitPulse] Repo: {repo} | Status: {resp.status_code} | Rate: Used {rate_used}, Remaining {rate_remaining}/{rate_limit} | Resets at {reset_time_str} UTC"
        )

        # Auto-throttle if rate limit is too low
        if rate_remaining is not None and int(rate_remaining) < 100:
            self.log.warning(f"[GitPulse] Rate limit nearly exhausted ({rate_remaining} remaining). Skipping fetch for {repo}. Resets at {reset_time_str} UTC")
            return

        if resp.status_code == 304:
            self.log.info(f"[GitPulse] No new data (304 Not Modified) for {repo}")
            return

        if resp.status_code != 200:
            self.log.error(f"[GitPulse] Failed to fetch events for {repo}: HTTP {resp.status_code}")
            return

        # Save ETag for next request
        new_etag = resp.headers.get('ETag')
        if new_etag:
            self.etags[repo] = new_etag

        try:
            events = resp.json()
        except Exception as e:
            self.log.error(f"[GitPulse] Failed to parse JSON response: {e}")
            return

        self.log.debug(f"[GitPulse] Fetched {len(events)} events for {repo}")

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=30)

        for event in reversed(events):
            try:
                event_timestamp = datetime.strptime(event['created_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            except Exception as e:
                self.log.warning(f"[GitPulse] Skipping event due to invalid timestamp: {e}")
                continue

            if event_timestamp < cutoff:
                continue

            if event['type'] == 'PushEvent':
                msg_text = self.format_push_event(event, repo)
                if msg_text:
                    self.log.info(f"[GitPulse] Posting PushEvent: {msg_text}")
                    self.announce(msg_text, irc, msg, channel)

            elif event['type'] == 'PullRequestEvent':
                msg_text = self.format_pull_request_event(event, repo)
                if msg_text:
                    self.log.info(f"[GitPulse] Posting PullRequestEvent: {msg_text}")
                    self.announce(msg_text, irc, msg, channel)

            elif event['type'] == 'IssuesEvent':
                msg_text = self.format_issues_event(event, repo)
                if msg_text:
                    self.log.info(f"[GitPulse] Posting IssuesEvent: {msg_text}")
                    self.announce(msg_text, irc, msg, channel)

    def format_push_event(self, event, repo):
        actor = event['actor']['login']
        commits = event['payload'].get('commits', [])
        branch = event['payload']['ref'].split('/')[-1]

        if commits:
            msgs = []
            for c in commits:
                msg = c['message'].split('\n')[0]
                url = f"https://github.com/{repo}/commit/{c['sha']}"
                msgs.append(f"{self.B}{actor}{self.B} pushed: {self.C}{self.GREEN}branch: {self.B}{branch}{self.RESET} {msg}{self.RESET} to {self.B}{repo}{self.B}: {self.C}{self.BLUE}{url}{self.RESET}")
            return '\n'.join(msgs)
        return None

    def format_pull_request_event(self, event, repo):
        actor = event['actor']['login']
        pr = event['payload']['pull_request']
        pr_url = pr['html_url']
        pr_title = pr['title']
        action = event['payload']['action']
        branch = pr['head']['ref']

        action_text = f"{self.C}{self.GREEN}opened{self.RESET}" if action == 'opened' else f"{self.C}{self.BLUE}{action}{self.RESET}"
        return f"{self.B}{actor}{self.B} {action_text} pull request: {self.C}{self.RED}{pr_title}{self.RESET} on branch {self.B}{branch}{self.B}: {self.C}{self.BLUE}{pr_url}{self.RESET}"

    def format_issues_event(self, event, repo):
        actor = event['actor']['login']
        issue = event['payload']['issue']
        issue_url = issue['html_url']
        issue_title = issue['title']
        action = event['payload']['action']
        issue_state = issue['state']

        state_text = f"{self.C}{self.RED}opened{self.RESET}" if issue_state == 'open' else f"{self.C}{self.GREEN}closed{self.RESET}"
        return f"{self.B}{actor}{self.B} {self.C}{self.RED}issue{self.RESET} {state_text}: {self.C}{self.RED}{issue_title}{self.RESET} {self.C}{self.BLUE}{issue_url}{self.RESET}"

    def announce(self, message, irc, msg, channel):
        if not channel:
            self.log.warning("[GitPulse] No channel specified for announcement.")
            return
        for line in message.split('\n'):
            irc.sendMsg(ircmsgs.privmsg(channel, line))
            self.log.info(f"[GitPulse] Posted message to channel {channel}: {line}")

    def subscribe(self, irc, msg, args):
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
            self.fetch_and_announce(repo, irc, msg, channel)
        else:
            irc.reply(f"[GitPulse] Already subscribed to {repo} in channel {channel}.")

    def unsubscribe(self, irc, msg, args):
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
        self.setRegistryValue('subscriptions', ' '.join(subscriptions), channel)

    def listgitpulse(self, irc, msg, args):
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
        """Manually fetch recent GitHub events for all subscribed repositories in the current channel."""
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
        self.stop_polling()
        super().die()

Class = GitPulse

