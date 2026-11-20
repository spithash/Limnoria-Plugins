###
# Copyright (c) 2026, spithash @ Libera
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

import builtins
import json
import re
import threading
import time
from datetime import datetime, timezone

import requests

from supybot import callbacks, ircmsgs, ircutils, log, world
from supybot.commands import *
from supybot.i18n import PluginInternationalization


_ = PluginInternationalization('SubReddit')


class SubReddit(callbacks.Plugin):
    """Subscribe for subreddit channel announcements via redlisten API."""
    threaded = True

    API_ROOT = 'https://api.redlisten.com'
    SUBREDDIT_RE = re.compile(r'^[A-Za-z0-9_]{2,21}$')
    MAX_SEEN_PER_SUBREDDIT = 200
    _state_lock = threading.RLock()
    _api_lock = threading.Lock()

    def __init__(self, irc):
        super().__init__(irc)
        self.irc = irc
        self._stop = threading.Event()
        self._wake = threading.Event()
        # Shared by callback instances on every configured IRC network.
        self._lock = self._state_lock
        self._next_poll = {}
        self._thread = threading.Thread(target=self._poll_loop,
                                        name='SubReddit-poller', daemon=True)
        self._thread.start()

    def die(self):
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        super().die()

    @staticmethod
    def _month_key(now=None):
        now = now or datetime.now(timezone.utc)
        return now.strftime('%Y-%m')

    @staticmethod
    def _seconds_to_next_month(now=None):
        now = now or datetime.now(timezone.utc)
        if now.month == 12:
            then = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            then = datetime(now.year, now.month + 1, 1,
                            tzinfo=timezone.utc)
        return max(1, int((then - now).total_seconds()))

    def _load_json(self, name, default):
        raw = self.registryValue(name)
        if not raw:
            return default
        try:
            value = json.loads(raw)
            return value
        except (TypeError, ValueError):
            log.warning('SubReddit: invalid %s; resetting it', name)
            return default

    def _save_json(self, name, value):
        self.setRegistryValue(name, json.dumps(value, separators=(',', ':')))

    def _request_count(self):
        state = self._load_json('requestState', {})
        month = self._month_key()
        if state.get('month') != month:
            state = {'month': month, 'count': 0}
            self._save_json('requestState', state)
        return state

    def _record_request(self):
        with self._lock:
            state = self._request_count()
            state['count'] = int(state.get('count', 0)) + 1
            self._save_json('requestState', state)

    def _subscriptions(self):
        result = {}
        state = self._load_json('subscriptionState', {})
        for target_irc in list(world.ircs):
            network_state = self._network_state(state, target_irc.network)
            joined = {ircutils.toLower(channel): channel for channel in
                      getattr(target_irc.state, 'channels', {})}
            for channel, values in network_state.items():
                actual_channel = joined.get(ircutils.toLower(channel))
                if actual_channel is None:
                    continue
                for subreddit in values:
                    destination = (target_irc, actual_channel)
                    result.setdefault(subreddit.lower(), []).append(destination)
        return result

    @staticmethod
    def _named_state(state, name):
        wanted = name.lower()
        for stored_name, value in state.items():
            if stored_name.lower() == wanted:
                return stored_name, value
        return None, None

    def _network_state(self, state, network, create=False):
        unused, value = self._named_state(state, network)
        if value is not None:
            return value
        if create:
            state[network] = {}
            return state[network]
        return {}

    def _channel_subscriptions(self, network, channel):
        state = self._load_json('subscriptionState', {})
        network_state = self._network_state(state, network)
        unused, values = self._named_state(network_state, channel)
        return list(values or [])

    def _set_channel_subscriptions(self, network, channel, values):
        with self._lock:
            state = self._load_json('subscriptionState', {})
            network_state = self._network_state(state, network, create=True)
            stored, unused = self._named_state(network_state, channel)
            key = stored or channel
            if values:
                network_state[key] = values
            elif stored is not None:
                del network_state[stored]
            self._save_json('subscriptionState', state)

    def _poll_interval(self, feed_count):
        state = self._request_count()
        budget = self.registryValue('monthlyRequestBudget')
        remaining = max(0, budget - int(state.get('count', 0)))
        if not remaining:
            return None
        # A full round costs feed_count requests. This spreads those rounds
        # evenly over the rest of the UTC month.
        fair = (self._seconds_to_next_month() * max(1, feed_count)) / remaining
        return max(self.registryValue('minimumPollInterval'), int(fair) + 1)

    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                feeds = self._subscriptions()
                now = time.monotonic()
                interval = self._poll_interval(len(feeds)) if feeds else None
                active = set(feeds)
                self._next_poll = {k: v for k, v in self._next_poll.items()
                                   if k in active}
                if interval is not None:
                    for subreddit, destinations in feeds.items():
                        if self._next_poll.get(subreddit, 0) <= now:
                            self._fetch(subreddit, destinations)
                            self._next_poll[subreddit] = (
                                time.monotonic() + interval)
                            # Recalculate after every charged request.
                            break
                wait = 60
                if self._next_poll:
                    wait = max(1, min(60, min(self._next_poll.values()) -
                                      time.monotonic()))
                self._wake.wait(wait)
                self._wake.clear()
            except Exception:
                log.exception('SubReddit: unexpected polling error')
                self._stop.wait(60)

    def _fetch(self, subreddit, destinations):
        """Serializes automatic and manual pulls across all IRC networks."""
        with self._api_lock:
            return self._fetch_unlocked(subreddit, destinations)

    def _fetch_unlocked(self, subreddit, destinations):
        key = self.registryValue('apiKey')
        if not key:
            return None
        self._record_request()
        try:
            response = requests.get(
                '%s/r/%s/new' % (self.API_ROOT, subreddit),
                params={'limit': 50},
                headers={'Authorization': 'Bearer ' + key,
                         'Accept': 'application/json',
                         'Accept-Encoding': 'gzip',
                         'User-Agent': 'Limnoria-SubReddit/1.0'},
                timeout=30)
            response.raise_for_status()
            children = response.json().get('data', {}).get('children', [])
        except requests.RequestException as error:
            log.warning('SubReddit: RedListen request for r/%s failed: %s',
                        subreddit, error)
            return None
        except (TypeError, ValueError, AttributeError) as error:
            log.warning('SubReddit: invalid RedListen response for r/%s: %s',
                        subreddit, error)
            return None

        with self._lock:
            state = self._load_json('seenState', {})
            feed_state = state.setdefault('feeds', {})
            previous = list(feed_state.get(subreddit, []))
            previous_set = set(previous)
            posts = [item.get('data', {}) for item in children
                     if isinstance(item, dict)]
            current_ids = [post.get('name') or post.get('id') for post in posts]
            current_ids = [post_id for post_id in current_ids if post_id]
            # No previous state means this is the baseline, not 50 new alerts.
            new_posts = [] if subreddit not in feed_state else [
                post for post in reversed(posts)
                if (post.get('name') or post.get('id')) not in previous_set]
            merged = current_ids + previous
            feed_state[subreddit] = list(dict.fromkeys(merged))[
                :self.MAX_SEEN_PER_SUBREDDIT]
            self._save_json('seenState', state)

        for post in new_posts:
            title = ' '.join(str(post.get('title') or '(untitled)').split())
            title = ircutils.stripFormatting(title)
            title = ''.join(c for c in title if ord(c) >= 32)[:300]
            permalink = post.get('permalink') or post.get('url') or ''
            if permalink.startswith('/'):
                permalink = 'https://www.reddit.com' + permalink
            text = '[r/%s] %s %s' % (subreddit, title, permalink)
            for target_irc, channel in destinations:
                target_irc.queueMsg(ircmsgs.privmsg(channel, text))
        return len(new_posts)

    def reddit(self, irc, msg, args, text):
        """<setkey|subscribe|unsubscribe|list|status|pull> [arguments]

        Manages RedListen subreddit announcements. All actions are owner-only.
        Use ``reddit subscribe <channel> <subreddit>`` to subscribe a channel.
        """
        words = text.split()
        if not words:
            irc.error('Use: reddit setkey, subscribe, unsubscribe, list, or status.',
                      private=True)
            return
        action = words.pop(0).lower()
        if action == 'setkey' and len(words) == 1:
            self._setkey(irc, msg, words[0])
        elif action == 'subscribe' and len(words) == 2:
            self._subscribe(irc, irc.network, words[0], words[1])
        elif action == 'subscribe' and len(words) == 3:
            self._subscribe(irc, words[0], words[1], words[2])
        elif action == 'unsubscribe' and len(words) == 2:
            self._unsubscribe(irc, irc.network, words[0], words[1])
        elif action == 'unsubscribe' and len(words) == 3:
            self._unsubscribe(irc, words[0], words[1], words[2])
        elif action == 'list' and len(words) == 1:
            self._list(irc, irc.network, words[0])
        elif action == 'list' and len(words) == 2:
            self._list(irc, words[0], words[1])
        elif action == 'status' and not words:
            self._status(irc)
        elif action == 'pull' and not words:
            self._pull(irc)
        else:
            irc.error('Invalid arguments. See help reddit. Actions: setkey, '
                      'subscribe, unsubscribe, list, status, pull.',
                      private=True)
    reddit = wrap(reddit, ['owner', 'text'])

    def _setkey(self, irc, msg, key):
        if ircutils.isChannel(msg.args[0]):
            irc.error('For safety, send reddit setkey in a private message.',
                      private=True)
            return
        key = key.strip()
        if (not key.startswith('rl_live_') or
                builtins.any(c.isspace() for c in key)):
            irc.error('That does not look like a RedListen rl_live_ key.',
                      private=True)
            return
        self.setRegistryValue('apiKey', key)
        self._wake.set()
        irc.reply('RedListen API key saved privately.', private=True)

    def _subscribe(self, irc, network, channel, subreddit):
        if not ircutils.isChannel(channel):
            irc.error('Invalid IRC channel.', private=True)
            return
        subreddit = subreddit.strip()
        if subreddit.lower().startswith('r/'):
            subreddit = subreddit[2:]
        if not self.SUBREDDIT_RE.match(subreddit):
            irc.error('Invalid subreddit name.', private=True)
            return
        values = self._channel_subscriptions(network, channel)
        if subreddit.lower() in [value.lower() for value in values]:
            irc.reply('%s/%s already follows r/%s.' %
                      (network, channel, subreddit), private=True)
            return
        values.append(subreddit)
        self._set_channel_subscriptions(network, channel, values)
        self._next_poll[subreddit.lower()] = 0
        self._wake.set()
        irc.reply('%s/%s will announce new posts from r/%s.' %
                  (network, channel, subreddit), private=True)

    def _unsubscribe(self, irc, network, channel, subreddit):
        if not ircutils.isChannel(channel):
            irc.error('Invalid IRC channel.', private=True)
            return
        subreddit = subreddit.lower().removeprefix('r/')
        values = self._channel_subscriptions(network, channel)
        kept = [value for value in values if value.lower() != subreddit]
        if len(kept) == len(values):
            irc.error('%s/%s is not subscribed to r/%s.' %
                      (network, channel, subreddit), private=True)
            return
        self._set_channel_subscriptions(network, channel, kept)
        self._wake.set()
        irc.reply('Removed r/%s from %s/%s.' %
                  (subreddit, network, channel), private=True)

    def _list(self, irc, network, channel):
        if not ircutils.isChannel(channel):
            irc.error('Invalid IRC channel.', private=True)
            return
        values = self._channel_subscriptions(network, channel)
        text = ', '.join('r/' + value for value in values) if values else 'none'
        irc.reply('%s/%s subscriptions: %s' %
                  (network, channel, text), private=True)

    def _status(self, irc):
        feeds = self._subscriptions()
        state = self._request_count()
        interval = self._poll_interval(len(feeds)) if feeds else None
        interval_text = ('stopped (budget exhausted)' if interval is None and feeds
                         else ('n/a' if not feeds else '%d minutes' %
                               max(1, interval // 60)))
        irc.reply('%d/%d requests used in %s; %d unique feed(s); poll interval: %s.' %
                  (state.get('count', 0), self.registryValue('monthlyRequestBudget'),
                   state.get('month'), len(feeds), interval_text), private=True)

    def _pull(self, irc):
        feeds = self._subscriptions()
        if not feeds:
            irc.error('There are no active feeds to pull. The bot must be '
                      'joined to each subscribed channel.', private=True)
            return
        state = self._request_count()
        budget = self.registryValue('monthlyRequestBudget')
        remaining = max(0, budget - int(state.get('count', 0)))
        if not remaining:
            irc.error('The monthly SubReddit request budget is exhausted.',
                      private=True)
            return

        attempted = min(len(feeds), remaining)
        succeeded = 0
        new_posts = 0
        for subreddit, destinations in list(feeds.items())[:attempted]:
            result = self._fetch(subreddit, destinations)
            if result is not None:
                succeeded += 1
                new_posts += result

        interval = self._poll_interval(len(feeds))
        if interval is not None:
            next_time = time.monotonic() + interval
            for subreddit in list(feeds)[:attempted]:
                self._next_poll[subreddit] = next_time
        self._wake.set()

        failed = attempted - succeeded
        parts = ['manual pull checked %d feed(s)' % attempted,
                 '%d succeeded' % succeeded,
                 '%d new post(s) announced' % new_posts]
        if failed:
            parts.append('%d failed; check the bot log' % failed)
        if attempted < len(feeds):
            parts.append('%d skipped because the budget is nearly exhausted' %
                         (len(feeds) - attempted))
        irc.reply('; '.join(parts) + '.', private=True)


Class = SubReddit


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
