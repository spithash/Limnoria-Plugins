###
# Copyright (c) 2026, Stathis Xantinidis
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

import datetime
import json
import os
import stat
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import httpx
from supybot import ircdb, ircmsgs
from supybot.test import ChannelPluginTestCase, conf
from . import plugin


class DeepseekAITestCase(ChannelPluginTestCase):
    plugins = ("DeepseekAI",)
    config = {
        "plugins.DeepseekAI.apiKey": "test-secret-key",
        "plugins.DeepseekAI.enabled": True,
        "plugins.DeepseekAI.throttleEnabled": False,
        "plugins.DeepseekAI.localRpmLimit": 30,
        "plugins.DeepseekAI.globalDailyLimit": 250,
        "plugins.DeepseekAI.dailyLimitPerUser": 25,
        "plugins.DeepseekAI.dailyTokensPerUser": 0,
        "plugins.DeepseekAI.historyExchanges": 20,
        "plugins.DeepseekAI.maxQuestionChars": 1000,
        "plugins.DeepseekAI.maxResponseChars": 800,
        "plugins.DeepseekAI.webDailyLimit": 15,
        "plugins.DeepseekAI.formatting": True,
        "plugins.DeepseekAI.greyItalics": True,
        "plugins.DeepseekAI.autoWeb": True,
        "plugins.DeepseekAI.webShowUrls": True,
        "plugins.DeepseekAI.historyMode": "user",
        "plugins.DeepseekAI.historyEnabled": True,
        "plugins.DeepseekAI.profilesEnabled": False,
        "plugins.DeepseekAI.allowPrivate": False,
    }

    def setUp(self):
        super().setUp()
        self.callback = self.irc.getCallback("DeepseekAI")
        self.save = patch.object(self.callback, "_save_usage", return_value=True)
        self.save_mock = self.save.start()
        self.addCleanup(self.save.stop)
        self.history_save = patch.object(self.callback, "_save_history", return_value=True)
        self.history_save_mock = self.history_save.start()
        self.addCleanup(self.history_save.stop)
        channel_value = self.callback.registryValue("enabled", self.channel, value=False)
        self.originals[channel_value] = channel_value()
        channel_value.setValue(True)
        for name in ("historyEnabled", "profilesEnabled"):
            scoped = self.callback.registryValue(name, self.channel, network=self.irc.network, value=False)
            self.originals.setdefault(scoped, scoped())
            scoped.setValue(self.config["plugins.DeepseekAI." + name])
        self.client_patch = patch.object(plugin.httpx, "Client")
        self.client = self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.post = self.client.return_value.__enter__.return_value.post
        self.post.return_value = httpx.Response(200, json={
            "choices": [{"message": {"content": "Hello from DeepSeek."}}],
            "usage": {"total_tokens": 42},
        })

    def testCommandAndPayload(self):
        self.assertResponse("ai hello", "Hello from DeepSeek.")
        args, kwargs = self.post.call_args
        self.assertEqual(args[0], plugin.DeepseekAI.API_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-secret-key")
        self.assertEqual(kwargs["json"]["messages"][-1], {"role": "user", "content": "hello"})
        self.assertEqual(kwargs["json"]["max_tokens"], 512)
        self.assertEqual(kwargs["json"]["thinking"], {"type": "disabled"})
        self.assertEqual(self.callback._tokens, 42)
        self.assertEqual(self.callback._attempts, 1)

    def _enable_nick_prompts(self):
        value = self.callback.registryValue("replyToNick", self.channel, value=False)
        self.originals.setdefault(value, value())
        value.setValue(True)
        value = conf.supybot.reply.whenAddressedBy.nick
        self.originals.setdefault(value, value())
        value.setValue(True)

    def testNickPromptPreservesTextAndHistory(self):
        self._enable_nick_prompts()
        question = 'explain "IRC bouncers", please?'
        self.assertResponse(self.irc.nick + ": " + question,
                            "Hello from DeepSeek.", usePrefixChar=False)
        self.assertEqual(self.post.call_args.kwargs["json"]["messages"][-1],
                         {"role": "user", "content": question})
        self.assertResponse("ai continue", "Hello from DeepSeek.")
        messages = self.post.call_args.kwargs["json"]["messages"]
        self.assertEqual(messages[1]["content"], question)
        self.assertEqual(len(messages), 4)

    def testNickCommandsKeepPriority(self):
        self._enable_nick_prompts()
        self.assertRegexp(self.irc.nick + ": deepseekai status", "enabled",
                          usePrefixChar=False)
        self.assertResponse(self.irc.nick + " ai hello", "Hello from DeepSeek.",
                            usePrefixChar=False)
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(self.post.call_args.kwargs["json"]["messages"][-1]["content"],
                         "hello")
        self.assertError(self.irc.nick + ": ai", usePrefixChar=False)
        self.assertEqual(self.post.call_count, 1)

    def testNickPromptNeedsOptInAndIgnoresOtherTraffic(self):
        msg = ircmsgs.privmsg(self.channel, self.irc.nick + ": explain IRC",
                              prefix=self.prefix)
        msg.channel = self.channel
        self.assertIs(self.callback.inFilter(self.irc, msg), msg)
        self.assertIsNone(msg.tagged("deepseekai.question"))
        self._enable_nick_prompts()
        for text in ("explain IRC", "@unknowncommand IRC",
                     self.irc.nick + "Else: explain IRC"):
            other = ircmsgs.privmsg(self.channel, text, prefix=self.prefix)
            other.channel = self.channel
            self.callback.inFilter(self.irc, other)
            self.assertIsNone(other.tagged("deepseekai.question"))
        self.callback.setRegistryValue("enabled", False, self.channel)
        self.callback.inFilter(self.irc, msg)
        self.assertIsNone(msg.tagged("deepseekai.question"))
        self.post.assert_not_called()

    def testNickPromptRejectionIsHandled(self):
        self._enable_nick_prompts()
        conf.supybot.plugins.DeepseekAI.throttleEnabled.setValue(True)
        self.assertResponse(self.irc.nick + ": explain IRC", "Hello from DeepSeek.",
                            usePrefixChar=False)
        self.assertRegexp(self.irc.nick + ": explain more", "Please wait",
                          usePrefixChar=False)
        self.assertEqual(self.post.call_count, 1)

    def testNickPromptWithoutCharacterPrefix(self):
        self._enable_nick_prompts()
        value = conf.supybot.reply.whenAddressedBy.chars.get(self.channel)
        self.originals.setdefault(value, value())
        value.setValue("")
        self.assertResponse(self.irc.nick + ", explain IRC", "Hello from DeepSeek.",
                            usePrefixChar=False)

    def testNetworkScopedPromptsAndRequests(self):
        self._enable_nick_prompts()
        for network, enabled in (("dalnet", True), ("grnet", False)):
            for setting in ("enabled", "replyToNick"):
                value = conf.supybot.plugins.DeepseekAI.get(setting).get(
                    ":" + network).get(self.channel)
                self.originals.setdefault(value, value())
                value.setValue(enabled)
        msg = ircmsgs.privmsg(self.channel, self.irc.nick + ": explain IRC",
                              prefix=self.prefix)
        msg.channel = self.channel
        proxy = Mock(network="grnet", nick=self.irc.nick, callbacks=self.irc.callbacks)
        # Registry lookup validates networks against live IRC connections.
        with patch("supybot.world.getIrc", return_value=proxy):
            self.callback.inFilter(proxy, msg)
            self.assertIsNone(msg.tagged("deepseekai.question"))
            proxy.network = "dalnet"
            self.callback.inFilter(proxy, msg)
            self.assertEqual(msg.tagged("deepseekai.question"), "explain IRC")
        proxy.network = "grnet"
        with patch("supybot.world.getIrc", return_value=proxy):
            self.callback._query(proxy, msg, "explain IRC")
        self.post.assert_not_called()
        self.assertIn("disabled", proxy.queueMsg.call_args.args[0].args[1])

    def testNickPromptsTreatSyntaxAsLiteralText(self):
        self._enable_nick_prompts()
        for question in ('explain [deepseekai forget]', 'explain [unfinished',
                         'explain "unfinished', 'explain x | deepseekai forget',
                         'explain arr[0] and \\n literally', 'εξήγησε [α]',
                         '[deepseekai forget] explain this'):
            with self.subTest(question=question):
                self.assertResponse(self.irc.nick + ": " + question,
                                    "Hello from DeepSeek.", usePrefixChar=False)
                self.assertEqual(self.post.call_args.kwargs["json"]["messages"][-1],
                                 {"role": "user", "content": question})
        self.assertEqual(self.post.call_count, 7)
        self.assertEqual(len(next(iter(self.callback._history.values()))), 7)

    def testKnownCommandSyntaxAndPermissionsStayCommands(self):
        self._enable_nick_prompts()
        self.assertError(self.irc.nick + ': deepseekai forget [unfinished',
                         usePrefixChar=False)
        with patch("supybot.ircdb.checkCapability", return_value=False):
            self.assertError(self.irc.nick + ': deepseekai enable', usePrefixChar=False)
        self.post.assert_not_called()

    def testNickPromptsRespectIgnoresAndNickAddressing(self):
        self._enable_nick_prompts()
        with patch("supybot.ircdb.checkIgnored", return_value=True):
            self.assertNoResponse(self.irc.nick + ': explain [anything]',
                                  usePrefixChar=False, timeout=0.2)
        conf.supybot.reply.whenAddressedBy.nick.setValue(False)
        self.assertNoResponse(self.irc.nick + ': explain anything',
                              usePrefixChar=False, timeout=0.2)
        self.post.assert_not_called()

    def testNickReplyOwnerCommandAndStatus(self):
        value = conf.supybot.plugins.DeepseekAI.replyToNick.get(
            ":" + self.irc.network).get(self.channel)
        self.originals.setdefault(value, value())
        with patch("supybot.ircdb.checkCapability", return_value=False):
            self.assertError("deepseekai nickreply on")
        original = ircdb.checkCapability
        def check(prefix, capability, *args, **kwargs):
            return capability == "owner" or original(prefix, capability, *args, **kwargs)
        with patch("supybot.ircdb.checkCapability", side_effect=check):
            self.assertNotError("deepseekai nickreply on")
            self.assertTrue(value())
            self.assertFalse(self.callback.registryValue("replyToNick", self.channel))
            self.assertRegexp("deepseekai status",
                              self.irc.network + ".*" + self.channel + ".*nick replies enabled")
            self.assertNotError("deepseekai nickreply off")
            self.assertFalse(value())
            self.assertError("deepseekai nickreply maybe")
        self.post.assert_not_called()

    def testPromptCommandSetsScopedInstructionsAndShowsPrivately(self):
        value = conf.supybot.plugins.DeepseekAI.systemPrompt.get(
            ":" + self.irc.network).get(self.channel)
        self.originals.setdefault(value, value())
        default = self.callback.registryValue("systemPrompt")
        with patch("supybot.ircdb.checkCapability", return_value=False):
            self.assertError("deepseekai prompt Answer in English.")
            self.assertError("deepseekai prompt")
        self.assertEqual(value(), default)
        original = ircdb.checkCapability
        def check(prefix, capability, *args, **kwargs):
            return capability == "owner" or original(prefix, capability, *args, **kwargs)
        with patch("supybot.ircdb.checkCapability", side_effect=check):
            self.assertNotError("deepseekai prompt Answer only in English with dry irony.")
            response = self.assertResponse("deepseekai prompt",
                "Prompt for %s %s: Answer only in English with dry irony." % (
                    self.irc.network, self.channel))
            self.assertEqual(response.args[0], self.nick)
            self.assertError('deepseekai prompt " "')
        self.assertEqual(value(), "Answer only in English with dry irony.")
        self.assertEqual(self.callback.registryValue("systemPrompt", self.channel), default)
        self.assertEqual(self.callback.registryValue("systemPrompt", "#elsewhere",
                         network=self.irc.network), default)
        with patch("supybot.world.getIrc", return_value=self.irc):
            self.assertEqual(self.callback.registryValue("systemPrompt", self.channel,
                             network="anothernetwork"), default)
        self.post.assert_not_called()
        self.assertResponse("ai hello", "Hello from DeepSeek.")
        self.assertTrue(self.post.call_args.kwargs["json"]["messages"][0]["content"].startswith(value()))

    def testPromptRemoteChannelAndLongPromptChunks(self):
        value = conf.supybot.plugins.DeepseekAI.systemPrompt.get(
            ":" + self.irc.network).get(self.channel)
        self.originals.setdefault(value, value())
        default = self.callback.registryValue("systemPrompt")
        with patch("supybot.ircdb.checkCapability", return_value=False):
            self.assertError("deepseekai promptappend " + self.channel + " Extra instructions.")
        original = ircdb.checkCapability
        def check(prefix, capability, *args, **kwargs):
            return capability == "owner" or original(prefix, capability, *args, **kwargs)
        chunks = ["Reply in English. " + "Useful instructions. " * 9,
                  "Use dry irony. " + "More instructions. " * 9,
                  "No forced jokes. " + "Final instructions. " * 9]
        with patch("supybot.ircdb.checkCapability", side_effect=check):
            self.assertNotError("deepseekai prompt " + self.channel + " " + chunks[0],
                                to="#elsewhere")
            for chunk in chunks[1:]:
                self.assertNotError("deepseekai promptappend " + self.channel + " " + chunk,
                                    private=True)
            self.assertEqual(value(), " ".join(chunk.strip() for chunk in chunks))
            self.assertGreater(len(value()), 512)
            self.assertError('deepseekai promptappend ' + self.channel + ' " "')
            self.assertError("deepseekai promptappend " + self.channel)
            for setting, number in ((conf.supybot.reply.mores.length, 200),
                                    (conf.supybot.reply.mores.instant, 1),
                                    (conf.supybot.reply.mores.instant.whenPrivate, 1)):
                self.originals.setdefault(setting, setting())
                setting.setValue(number)
            response = self.assertRegexp("deepseekai prompt " + self.channel,
                                         "more messages", to="#elsewhere")
            self.assertEqual(response.args[0], self.nick)
            response = self.assertNotError("more", private=True)
            self.assertEqual(response.args[0], self.nick)
        self.assertEqual(self.callback.registryValue("systemPrompt", self.channel), default)
        self.assertEqual(self.callback.registryValue("systemPrompt", "#elsewhere",
                         network=self.irc.network), default)
        with tempfile.TemporaryDirectory() as folder:
            from supybot import registry
            filename = os.path.join(folder, "settings.conf")
            registry.close(conf.supybot.plugins.DeepseekAI, filename)
            with open(filename, encoding="utf-8") as stream:
                self.assertIn(value(), stream.read())
        self.post.assert_not_called()

    def testSystemPromptDefaultsAndNetworkChannelOverrides(self):
        default = conf.supybot.plugins.DeepseekAI.systemPrompt()
        self.assertResponse("ai default", "Hello from DeepSeek.")
        self.assertTrue(self.post.call_args.kwargs["json"]["messages"][0]["content"].startswith(default))
        value = conf.supybot.plugins.DeepseekAI.systemPrompt.get(
            ":" + self.irc.network).get(self.channel)
        self.originals.setdefault(value, value())
        value.setValue("Answer only in English, with dry irony.")
        self.assertResponse("ai custom", "Hello from DeepSeek.")
        self.assertTrue(self.post.call_args.kwargs["json"]["messages"][0]["content"].startswith(value()))
        self.assertEqual(conf.supybot.plugins.DeepseekAI.systemPrompt(), default)
        self.assertEqual(self.callback.registryValue("systemPrompt", "#another",
                         network=self.irc.network), default)
        with patch("supybot.world.getIrc", return_value=self.irc):
            self.assertEqual(self.callback.registryValue("systemPrompt", self.channel,
                             network="anothernetwork"), default)
        with tempfile.TemporaryDirectory() as folder:
            from supybot import registry
            filename = os.path.join(folder, "settings.conf")
            registry.close(conf.supybot.plugins.DeepseekAI, filename)
            with open(filename, encoding="utf-8") as stream:
                self.assertIn("Answer only in English, with dry irony.", stream.read())

    def testUsageRepliesPrivatelyAsNotice(self):
        with patch.object(conf.supybot.reply.withNoticeWhenPrivate, "value", True):
            response = self.assertResponse("deepseekai aiusage",
                "You: 0 attempts, 0 replies, 0 tokens. "
                "Global: 0 attempts, 0 replies, 0 tokens. Web: 0/15 daily attempts.")
        self.assertEqual(response.command, "NOTICE")
        self.assertEqual(response.args[0], self.nick)
        self.post.assert_not_called()

    def testHistoryAndForget(self):
        self.assertResponse("ai first", "Hello from DeepSeek.")
        self.assertResponse("ai second", "Hello from DeepSeek.")
        messages = self.post.call_args.kwargs["json"]["messages"]
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self._forget_ok()
        self.assertResponse("ai third", "Hello from DeepSeek.")
        self.assertEqual(len(self.post.call_args.kwargs["json"]["messages"]), 2)

    def testChannelDisabled(self):
        self.callback.setRegistryValue("enabled", False, self.channel)
        self.assertRegexp("ai hello", "disabled")
        self.post.assert_not_called()

    def testMissingKey(self):
        conf.supybot.plugins.DeepseekAI.apiKey.setValue("")
        self.assertRegexp("ai hello", "API key is not set")
        self.post.assert_not_called()

    def testQuestionLimit(self):
        conf.supybot.plugins.DeepseekAI.maxQuestionChars.setValue(5)
        self.assertRegexp("ai too long", "at most 5")
        self.post.assert_not_called()
        self.assertEqual(self.callback._attempts, 0)

    def testThrottle(self):
        conf.supybot.plugins.DeepseekAI.throttleEnabled.setValue(True)
        self.assertResponse("ai first", "Hello from DeepSeek.")
        self.assertRegexp("ai second", "Please wait")
        self.assertEqual(self.post.call_count, 1)

    def testErrorsCountTowardsDailyLimit(self):
        conf.supybot.plugins.DeepseekAI.dailyLimitPerUser.setValue(1)
        self.post.return_value = httpx.Response(402, json={"error": "test-secret-key"})
        self.assertRegexp("ai hello", "balance is insufficient")
        self.callback._last_notice.clear()
        self.assertRegexp("ai again", "daily request limit")
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(self.callback._attempts, 1)
        self.assertFalse(self.callback._active_users)
        self.assertFalse(self.callback._history)

    def testBackoff(self):
        self.post.return_value = httpx.Response(429, headers={"retry-after": "120"})
        self.assertRegexp("ai hello", "rate limit reached")
        self.assertGreater(self.callback._backoff_until, time.monotonic() + 100)
        self.callback._last_notice.clear()
        self.assertRegexp("ai again", "temporarily unavailable")
        self.assertEqual(self.post.call_count, 1)

    def testTimeoutReleasesSlot(self):
        self.post.side_effect = httpx.ReadTimeout("secret request information")
        self.assertRegexp("ai hello", "timed out")
        self.assertFalse(self.callback._active_users)
        self.assertEqual(self.callback._attempts, 1)

    def testMalformedResponseReleasesSlot(self):
        self.post.return_value = httpx.Response(200, json={"choices": []})
        self.assertRegexp("ai hello", "invalid response")
        self.assertFalse(self.callback._active_users)

    def testOwnerCommands(self):
        with patch("supybot.ircdb.checkCapability", return_value=False):
            self.assertError("deepseekai enable")
            self.assertError("deepseekai disable")
            self.assertError("deepseekai resetusage")

    def testConcurrentLimit(self):
        barrier = threading.Barrier(8)
        def reserve(index):
            barrier.wait(timeout=5)
            with self.callback._lock:
                return self.callback._reserve("user-%s" % index)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(8)))
        self.assertEqual(results.count(None), 2)
        self.assertEqual(self.callback._attempts, 2)
        self.assertEqual(len(self.callback._active_users), 2)

    def testUserCannotQueueSecondRequest(self):
        with self.callback._lock:
            self.assertIsNone(self.callback._reserve("user"))
            self.assertIn("still running", self.callback._reserve("user"))
        self.assertEqual(self.callback._attempts, 1)

    def testRpmAndGlobalDailyLimit(self):
        conf.supybot.plugins.DeepseekAI.localRpmLimit.setValue(1)
        with self.callback._lock:
            self.assertIsNone(self.callback._reserve("one"))
            self.callback._active_users.clear()
            self.assertIn("minute", self.callback._reserve("two"))
            self.callback._requests[0] -= 61
            conf.supybot.plugins.DeepseekAI.globalDailyLimit.setValue(1)
            self.assertIn("daily request", self.callback._reserve("two"))

    def testTokenLimit(self):
        conf.supybot.plugins.DeepseekAI.dailyTokensPerUser.setValue(1)
        self.assertResponse("ai first", "Hello from DeepSeek.")
        self.assertRegexp("ai second", "daily token limit")
        self.assertEqual(self.post.call_count, 1)

    def testIdentityAndIsolation(self):
        irc = Mock(network="network-one")
        first = ircmsgs.privmsg("#one", "@ai hello", prefix="nick!u@host")
        renamed = ircmsgs.privmsg("#one", "@ai hello", prefix="othernick!u@host")
        other_channel = ircmsgs.privmsg("#two", "@ai hello", prefix="nick!u@host")
        private = ircmsgs.privmsg("bot", "@ai hello", prefix="nick!u@host")
        one = self.callback._keys(irc, first)
        self.assertEqual(one, self.callback._keys(irc, renamed))
        self.assertEqual(one[0], self.callback._keys(irc, other_channel)[0])
        self.assertNotEqual(one[1], self.callback._keys(irc, other_channel)[1])
        self.assertNotEqual(one[1], self.callback._keys(irc, private)[1])
        irc.network = "network-two"
        self.assertNotEqual(one, self.callback._keys(irc, first))

    def testHistoryExpiryAndLimit(self):
        key = ("user", "#channel")
        self.callback._history[key] = [(time.time() - 90000, "expired", "old")]
        self.assertEqual(self.callback._get_history(key), [])
        self.assertNotIn(key, self.callback._history)
        self.callback._history[key] = [(time.time(), str(i), "answer") for i in range(25)]
        self.assertEqual(len(self.callback._get_history(key)), 40)
        self.assertEqual(self.callback._get_history(key)[0]["content"], "5")

    def testHistoryDisabled(self):
        conf.supybot.plugins.DeepseekAI.historyExchanges.setValue(0)
        self.assertResponse("ai hello", "Hello from DeepSeek.")
        self.assertFalse(self.callback._history)

    def testHistoryRoundTripExpiresAndKeepsIdentityIsolation(self):
        now = time.time()
        first = ("network-one:user@host", "#one")
        others = [(first[0], "#two"), ("network-two:user@host", "#one"),
                  ("network-one:other@host", "#one"), (first[0], "private")]
        with tempfile.TemporaryDirectory() as folder:
            self.callback._history_file = os.path.join(folder, "history.json")
            self.callback._history = {key: [(now, "question " + str(i), "answer")]
                                      for i, key in enumerate([first] + others)}
            self.callback._history[first].insert(0, (now - 90000, "expired", "answer"))
            self.assertTrue(plugin.DeepseekAI._save_history(self.callback))
            self.assertEqual(stat.S_IMODE(os.stat(self.callback._history_file).st_mode), 0o640)
            self.callback._history.clear()
            # A new process has a different monotonic clock; history uses UTC epochs.
            with patch.object(plugin.time, "monotonic", return_value=1):
                self.callback._load_history()
            self.assertEqual(len(self.callback._history), 5)
            self.assertEqual(self.callback._get_history(first)[0]["content"], "question 0")
            for i, key in enumerate(others, 1):
                self.assertEqual(self.callback._get_history(key)[0]["content"], "question " + str(i))

    def testHistoryMalformedFileDoesNotLoadOrExposeContents(self):
        with tempfile.TemporaryDirectory() as folder:
            self.callback._history_file = os.path.join(folder, "history.json")
            for payload in ("not JSON private contents", json.dumps({"version": 99}),
                            json.dumps({"version": 1, "conversations": [{"user": "u",
                                "target": "#c", "exchanges": [["bad time", "private", "reply"]]}]})):
                with open(self.callback._history_file, "w") as stream:
                    stream.write(payload)
                with patch.object(self.callback.log, "error") as logged:
                    self.callback._load_history()
                self.assertFalse(self.callback._history)
                self.assertEqual(logged.call_args.args, (
                    "Unable to load DeepseekAI conversation history.",))

    def testHistoryAtomicFailurePreservesSavedFile(self):
        with tempfile.TemporaryDirectory() as folder:
            self.callback._history_file = os.path.join(folder, "history.json")
            self.callback._history = {("user", "#one"): [(time.time(), "old", "answer")]}
            self.assertTrue(plugin.DeepseekAI._save_history(self.callback))
            with open(self.callback._history_file, "rb") as stream:
                saved = stream.read()
            self.callback._history.clear()
            with patch.object(plugin.os, "replace", side_effect=PermissionError):
                self.assertFalse(plugin.DeepseekAI._save_history(self.callback))
            with open(self.callback._history_file, "rb") as stream:
                self.assertEqual(stream.read(), saved)
            self.assertEqual(os.listdir(folder), ["history.json"])

    def testHistoryWriteFailureStillAnswersAndWarnsPrivately(self):
        self.history_save_mock.return_value = False
        irc = Mock(network=self.irc.network)
        msg = ircmsgs.privmsg(self.channel, "@ai hello", prefix=self.prefix)
        msg.channel = self.channel
        self.callback._query(irc, msg, "hello")
        irc.reply.assert_called_once_with("Hello from DeepSeek.", prefixNick=True)
        warning = irc.queueMsg.call_args.args[0]
        self.assertEqual(warning.command, "NOTICE")
        self.assertEqual(warning.args[0], self.nick)
        self.assertIn("could not be saved", warning.args[1])
        self.assertFalse(self.callback._active_users)

    def testForgetPersistsAndFailureRestoresHistory(self):
        self.assertResponse("ai remember this", "Hello from DeepSeek.")
        saved_history = dict(self.callback._history)
        self.history_save_mock.return_value = False
        self._forget_match("Could not save cleared history")
        self.assertEqual(self.callback._history, saved_history)
        with tempfile.TemporaryDirectory() as folder:
            self.callback._history_file = os.path.join(folder, "history.json")
            self.history_save_mock.side_effect = lambda: plugin.DeepseekAI._save_history(self.callback)
            self._forget_ok()
            self.callback._history = saved_history
            self.callback._load_history()
            self.assertFalse(self.callback._history)

    def testSuccessfulExchangeReallyRestoresAfterReload(self):
        with tempfile.TemporaryDirectory() as folder:
            self.callback._history_file = os.path.join(folder, "history.json")
            self.history_save_mock.side_effect = lambda: plugin.DeepseekAI._save_history(self.callback)
            self.assertResponse("ai before restart", "Hello from DeepSeek.")
            self.callback._history.clear()
            self.callback._load_history()
            self.assertResponse("ai after restart", "Hello from DeepSeek.")
            messages = self.post.call_args.kwargs["json"]["messages"]
            self.assertEqual([message["content"] for message in messages[1:]],
                             ["before restart", "Hello from DeepSeek.", "after restart"])

    def testBalanceIsOwnerOnlyPrivateAndFromApi(self):
        get = self.client.return_value.__enter__.return_value.get
        get.return_value = httpx.Response(200, json={"is_available": True,
            "balance_infos": [{"currency": "USD", "total_balance": "12.50",
                "granted_balance": "2.50", "topped_up_balance": "10.00"}]})
        with patch("supybot.ircdb.checkCapability", return_value=False):
            self.assertError("deepseekai balance")
        get.assert_not_called()
        original = ircdb.checkCapability
        def check(prefix, capability, *args, **kwargs):
            return capability == "owner" or original(prefix, capability, *args, **kwargs)
        with patch("supybot.ircdb.checkCapability", side_effect=check):
            response = self.assertResponse("deepseekai balance",
                "DeepSeek account balance: USD total 12.50 (granted 2.50; topped up 10.00). "
                "Available for API calls: yes.")
            self.assertEqual(response.args[0], self.nick)
            self.assertEqual(get.call_args.args[0], plugin.DeepseekAI.BALANCE_URL)
            self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer test-secret-key")
            get.return_value = httpx.Response(200, json={"is_available": True,
                "balance_infos": [{"currency": "USD", "total_balance": "NaN",
                    "granted_balance": "0", "topped_up_balance": "0"}]})
            response = self.assertRegexp("deepseekai balance", "invalid balance response")
            self.assertEqual(response.args[0], self.nick)
            get.side_effect = httpx.ReadTimeout("private authentication details")
            response = self.assertRegexp("deepseekai balance", "timed out")
            self.assertEqual(response.args[0], self.nick)
        self.assertEqual(self.callback._attempts, 0)
        self.post.assert_not_called()

    def testSafeFormattingAndLength(self):
        text = self.callback._format_answer("\x01ACTION test\x01\r\n**bold** `code`\x00")
        self.assertEqual(text, "ACTION test \x02bold\x02 code\x0f")
        self.assertEqual(len(self.callback._format_answer("α" * 1000)), 800)

    def testEmphasisAndTruncatedFormatting(self):
        text = self.callback._format_answer("**Important** and *emphasis* 123")
        self.assertIn("\x02Important\x02", text)
        self.assertIn("\x0393emphasis\x03\x02\x02 123", text)
        self.assertTrue(text.endswith("\x0f"))
        text = self.callback._format_answer("*" + "α" * 1000 + "*")
        self.assertLessEqual(len(text), 800)
        self.assertTrue(text.endswith("…\x0f"))
        conf.supybot.plugins.DeepseekAI.greyItalics.setValue(False)
        self.assertEqual(self.callback._format_answer("*hello*"), "\x1dhello\x1d\x0f")
        conf.supybot.plugins.DeepseekAI.formatting.setValue(False)
        self.assertEqual(self.callback._format_answer("**bold** *italic* \x0304red"),
                         "bold italic 04red")
        self.assertEqual(self.callback._format_answer("`**literal**`"), "literal")

    def testFormattedHistoryIsPlainText(self):
        self.post.return_value = httpx.Response(200, json={
            "choices": [{"message": {"content": "**Hello** *there*"}}]})
        self.assertNotError("ai first")
        self.assertNotError("ai second")
        messages = self.post.call_args.kwargs["json"]["messages"]
        self.assertEqual(messages[-2]["content"], "Hello there")

    def _search_plan(self, query="latest release", language="en", after=None):
        arguments = {"query": query, "language": language}
        if after:
            arguments["after"] = after
        return httpx.Response(200, json={"choices": [{"message": {
            "content": None, "tool_calls": [{"id": "search-1", "type": "function",
                "function": {"name": "web_search", "arguments": json.dumps(arguments)}}]}}],
            "usage": {"total_tokens": 20}})

    def _web_responses(self):
        return [self._search_plan(), httpx.Response(200, json={"results": [{
            "title": "Release", "url": "https://example.org/release",
            "summary": "Current release is 2.0."}]}),
            httpx.Response(200, json={
                "choices": [{"message": {"content": "Version 2.0 [1]."}}],
                "usage": {"total_tokens": 100}})]

    def testWebCommandAndPayload(self):
        self.post.side_effect = self._web_responses()
        self.assertResponse("aiweb latest release", "Version 2.0.")
        plan, search, chat = self.post.call_args_list
        self.assertEqual(plan.kwargs["json"]["tool_choice"]["function"]["name"], "web_search")
        self.assertEqual(search.args[0], plugin.DeepseekAI.SEARCH_URL)
        self.assertEqual(search.kwargs["json"], {
            "query": "latest release lang:en", "count": 8, "need_content": True})
        self.assertEqual(search.kwargs["headers"]["Authorization"], "Bearer test-secret-key")
        self.assertEqual(chat.args[0], plugin.DeepseekAI.API_URL)
        messages = chat.kwargs["json"]["messages"]
        self.assertIn("untrusted", messages[0]["content"])
        self.assertIn("Current release is 2.0.", messages[-1]["content"])
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(messages[-3]["content"], "latest release")
        self.assertEqual(chat.kwargs["json"]["tool_choice"], "none")
        self.assertIn(datetime.date.today().isoformat(), messages[0]["content"])
        self.assertEqual(self.callback._web_attempts, 1)
        self.assertEqual(self.callback._attempts, 1)
        self.assertEqual(self.callback._tokens, 120)

    def testWebAllowanceSharedAndRegularAiStillWorks(self):
        for index in range(15):
            with self.callback._lock:
                self.assertIsNone(self.callback._reserve("web-%s" % index, web=True))
                self.callback._active_users.clear()
        self.assertRegexp("aiweb news", "daily web search allowance")
        self.post.assert_not_called()
        self.assertEqual(self.callback._web_attempts, 15)
        self.assertResponse("ai explain something", "Hello from DeepSeek.")

    def testWebAllowanceConcurrentReservation(self):
        conf.supybot.plugins.DeepseekAI.webDailyLimit.setValue(3)
        def reserve(index):
            with self.callback._lock:
                result = self.callback._reserve("web-%s" % index, web=True)
                self.callback._active_users.clear()
                return result
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(20)))
        self.assertEqual(results.count(None), 3)
        self.assertEqual(self.callback._web_attempts, 3)

    def testWebPersistenceAndRollover(self):
        with tempfile.TemporaryDirectory() as folder:
            self.callback._data_file = os.path.join(folder, "usage.json")
            self.callback._web_attempts = 14
            self.assertTrue(plugin.DeepseekAI._save_usage(self.callback))
            self.callback._web_attempts = 0
            self.callback._load_usage()
            self.assertEqual(self.callback._web_attempts, 14)
            self.callback._day = "2000-01-01"
            self.callback._reset_day()
            self.assertEqual(self.callback._web_attempts, 0)

    def testWebPersistenceFailurePreventsSearch(self):
        self.save_mock.return_value = False
        self.assertRegexp("aiweb news", "Usage tracking is unavailable")
        self.post.assert_not_called()
        self.assertEqual(self.callback._web_attempts, 0)
        self.assertEqual(self.callback._attempts, 0)

    def testWebDisabledAndLengthValidation(self):
        self.assertRegexp("aiweb " + "a" * 1001, "at most 1000")
        self.post.assert_not_called()
        self.assertEqual(self.callback._web_attempts, 0)
        self.callback._last_notice.clear()
        conf.supybot.plugins.DeepseekAI.webDailyLimit.setValue(0)
        self.assertRegexp("aiweb news", "disabled")
        self.post.assert_not_called()

    def testWebEmptyResultsAndFailuresConsumeAttempt(self):
        self.post.side_effect = [self._search_plan(), httpx.Response(200, json={"results": []}),
                                 self._search_plan(), httpx.Response(429)]
        self.assertRegexp("aiweb news", "No usable web results")
        self.assertEqual(self.post.call_count, 2)
        self.assertFalse(self.callback._active_users)
        self.callback._last_notice.clear()
        self.post.return_value = httpx.Response(429)
        self.assertRegexp("aiweb news", "rate limit reached")
        self.assertEqual(self.post.call_count, 4)
        self.assertEqual(self.callback._web_attempts, 2)
        self.assertFalse(self.callback._active_users)

    def testWebMalformedResultsReleaseSlot(self):
        self.post.side_effect = [self._search_plan(), httpx.Response(200, json={"results": "invalid"})]
        self.assertRegexp("aiweb news", "invalid response")
        self.assertEqual(self.post.call_count, 2)
        self.assertFalse(self.callback._active_users)

    def testWebContextBoundedAndUnsafeUrlsExcluded(self):
        self.post.return_value = httpx.Response(200, json={"results": [
            {"title": "bad", "url": "javascript:alert(1)", "summary": "bad"},
            {"title": "ok", "url": "https://example.org/", "summary": "a" * 5000}]})
        sources, error = self.callback._search(
            self.client.return_value.__enter__.return_value, {}, "news")
        self.assertIsNone(error)
        self.assertEqual(len(sources), 1)
        self.assertEqual(len(sources[0]["summary"]), 1000)

    def testAutomaticWebSearchUsesContextAndCountsAllTokens(self):
        self.assertResponse("ai explain elections", "Hello from DeepSeek.")
        self.post.reset_mock()
        responses = self._web_responses()
        responses[0] = self._search_plan("US Senate election results 2026", after="2026-11-03")
        self.post.side_effect = responses
        self.assertResponse("ai and who won the Senate?", "Version 2.0.")
        plan, search, final = self.post.call_args_list
        self.assertEqual(plan.kwargs["json"]["tool_choice"], "auto")
        self.assertIn("explain elections", [m["content"] for m in plan.kwargs["json"]["messages"]])
        self.assertEqual(search.kwargs["json"]["query"],
                         "US Senate election results 2026 lang:en after:2026-11-03")
        self.assertEqual(final.kwargs["json"]["tool_choice"], "none")
        self.assertEqual(self.callback._attempts, 2)
        self.assertEqual(self.callback._web_attempts, 1)
        self.assertEqual(self.callback._tokens, 162)

    def testAutomaticWebQuotaDoesNotFallBackToMemory(self):
        self.callback._web_attempts = 15
        self.post.return_value = self._search_plan()
        self.assertRegexp("ai who won yesterday?", "cannot verify current information")
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(self.callback._web_attempts, 15)
        self.assertEqual(self.callback._tokens, 20)
        self.assertEqual(self.callback._successes, 0)
        self.assertFalse(self.callback._active_users)

    def testAutomaticWebReservationPersistenceAndConcurrency(self):
        conf.supybot.plugins.DeepseekAI.webDailyLimit.setValue(3)
        def reserve(index):
            with self.callback._lock:
                return self.callback._reserve_auto_web()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(20)))
        self.assertEqual(results.count(None), 3)
        self.assertEqual(self.callback._web_attempts, 3)
        self.callback._web_attempts = 0
        self.save_mock.return_value = False
        self.assertIn("Usage tracking", self.callback._reserve_auto_web())
        self.assertEqual(self.callback._web_attempts, 0)

    def testAutoWebCanBeDisabledWithoutDisablingForcedWeb(self):
        conf.supybot.plugins.DeepseekAI.autoWeb.setValue(False)
        self.assertResponse("ai hello", "Hello from DeepSeek.")
        self.assertNotIn("tools", self.post.call_args.kwargs["json"])
        self.post.side_effect = self._web_responses()
        self.assertResponse("aiweb latest release", "Version 2.0.")

    def testWebLongQuestionBecomesShortGreekQuery(self):
        responses = self._web_responses()
        responses[0] = self._search_plan("νέα έκδοση Limnoria", "el")
        self.post.side_effect = responses
        self.assertNotError("aiweb " + "Ποια είναι η τελευταία έκδοση; " * 8)
        self.assertEqual(self.post.call_args_list[1].kwargs["json"]["query"],
                         "νέα έκδοση Limnoria lang:el")

    def testSearchRanksDiversifiesAndPreservesDates(self):
        self.post.return_value = httpx.Response(200, json={"results": [
            {"title": "weak", "url": "https://weak.example/a", "summary": "old", "rerank_score": 0.2},
            {"title": "official", "url": "https://official.example/a", "summary": "results",
             "rerank_score": 2.8, "authority": 4, "date": "2026-11-04T01:00:00Z", "content": "x" * 9000},
            {"title": "duplicate host", "url": "https://www.official.example/b", "summary": "same", "rerank_score": 2.7},
            {"title": "other", "url": "https://other.example/a", "summary": "independent", "rerank_score": 2.6}]})
        sources, error = self.callback._search(self.client.return_value.__enter__.return_value, {}, "results")
        self.assertIsNone(error)
        self.assertEqual([s["title"] for s in sources], ["official", "other", "weak"])
        self.assertEqual(sources[0]["date"], "2026-11-04T01:00:00Z")
        self.assertEqual(len(sources[0]["content"]), 4000)

    def testInvalidSearchArgumentsNeverReachSearchApi(self):
        client = self.client.return_value.__enter__.return_value
        for query, language, after in [("", "en", None), ("news", "en OR lang:ru", None),
                                       ("news", "en", "2026-02-30"), ("lang:ru", "en", None)]:
            with self.assertRaises(ValueError):
                self.callback._search(client, {}, query, language, after)
        self.post.assert_not_called()

    def testSearchNormalizesLongQueriesAndProviderOperators(self):
        client = self.client.return_value.__enter__.return_value
        self.post.return_value = httpx.Response(200, json={"results": []})
        self.callback._search(client, {}, "x" * 91, "en")
        self.assertEqual(self.post.call_args.kwargs["json"]["query"], "x" * 90 + " lang:en")
        self.callback._search(client, {}, "news lang:ru after:2020-01-01", "en")
        self.assertEqual(self.post.call_args.kwargs["json"]["query"], "news lang:en")

    def testWebEmptyResultsPreservePlanningTokens(self):
        self.post.side_effect = [self._search_plan(), httpx.Response(200, json={"results": []})]
        self.assertRegexp("ai news today", "cannot verify")
        self.assertEqual(self.callback._tokens, 20)
        self.assertEqual(self.callback._web_attempts, 1)
        self.assertEqual(self.callback._successes, 0)

    def testWebRefusesMoreThanOneSearchRound(self):
        responses = self._web_responses()
        responses[-1] = self._search_plan()
        self.post.side_effect = responses
        self.assertRegexp("aiweb news", "invalid response")
        self.assertEqual(self.post.call_count, 3)
        self.assertEqual(self.callback._web_attempts, 1)
        self.assertFalse(self.callback._active_users)

    def testUrlsAreRemovedWithoutSearchOrExplicitRequest(self):
        self.post.return_value = httpx.Response(200, json={"choices": [{"message": {
            "content": "Read [the docs](https://invented.example/docs) or https://bad.example/path and www.bad.example. [1]"}}]})
        self.assertResponse("ai explain this", "Read the docs or and")
        self.assertIn("without URLs", self.post.call_args.kwargs["json"]["messages"][0]["content"])
        rendered, footer = self.callback._render_sources(
            "Visit example.org/docs or <ftp://bad.example/file>.", [])
        self.assertNotIn("example", rendered)
        self.assertEqual(footer, "")
        self.assertEqual(self.callback._strip_urls("Use os.path.join in plugin.py."),
                         "Use os.path.join in plugin.py.")

    def testLinkOptInUsesCurrentExplicitRequest(self):
        for question in ("sources?", "give me a link", "show URLs please", "cite your sources",
                         "δώσε μου τις πηγές", "στείλε συνδέσμους", "where are your sources?"):
            self.assertTrue(self.callback._links_requested(question), question)
        for question in ("latest news", "explain https://example.org", "show source code",
                         "no links please", "do not include URLs", "χωρίς πηγές", "why are links broken?"):
            self.assertFalse(self.callback._links_requested(question), question)

    def testCitationsOnlyIncludeRequestedTrustedSourcesAndRespectLength(self):
        sources = [{"url": "https://one.example/a", "authority": 1},
                   {"url": "https://two.example/b", "authority": 4},
                   {"url": "https://unused.example/c", "authority": 3}]
        rendered, footer = self.callback._render_sources("Answer [2].", sources)
        self.assertEqual((rendered, footer), ("Answer.", ""))
        rendered, footer = self.callback._render_sources(
            "Answer [1] [2]. Bad [99].", sources, links_requested=True)
        self.assertEqual(footer, "\n[2] https://two.example/b")
        self.assertNotIn("[1]", rendered)
        self.assertNotIn("[99]", rendered)
        self.assertNotIn("unused", footer)
        conf.supybot.plugins.DeepseekAI.webShowUrls.setValue(False)
        rendered, footer = self.callback._render_sources(
            "Answer [2].", sources, links_requested=True)
        self.assertEqual(footer, "")
        self.assertIn("No suitable source links", rendered)

    def testRequestedUrlsAreNeverTruncatedOrSplit(self):
        sources = [{"url": "https://two.example/" + "x" * 400, "authority": 4}]
        rendered, footer = self.callback._render_sources(
            "Answer [1].", sources, links_requested=True, line_limit=300)
        self.assertEqual(footer, "")
        self.assertIn("No suitable source links", rendered)
        sources = [{"url": "https://two.example/" + "α" * 100, "authority": 4}]
        rendered, footer = self.callback._render_sources(
            "Answer [1].", sources, links_requested=True, line_limit=150)
        self.assertEqual(footer, "")
        sources = [{"url": "https://two.example/b", "authority": 4}]
        rendered, footer = self.callback._render_sources(
            "Answer " + "x" * 1000 + " [1]", sources, links_requested=True)
        self.assertEqual(footer, "")
        self.assertLessEqual(len(rendered + footer), 800)
        rendered, footer = self.callback._render_sources(
            "Answer [1].", sources, limit=80, links_requested=True)
        self.assertLessEqual(len(rendered + footer), 80)

    def testRequestedLinksAreSeparateIrcMessages(self):
        responses = self._web_responses()
        responses[1] = httpx.Response(200, json={"results": [
            {"title": "Official", "url": "https://official.example/release", "authority": 4},
            {"title": "Reporting", "url": "https://news.example/release", "authority": 3}]})
        responses[-1] = httpx.Response(200, json={"choices": [{"message": {
            "content": "Version 2.0 [1] [2]. https://invented.example/"}}]})
        self.post.side_effect = responses
        self.assertResponse("ai give me links for the latest release", "Version 2.0 [1] [2].")
        first, second = self.irc.takeMsg(), self.irc.takeMsg()
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.args[1], "[1] https://official.example/release")
        self.assertEqual(second.args[1], "[2] https://news.example/release")
        self.assertEqual(self.post.call_args_list[0].kwargs["json"]["tool_choice"]["function"]["name"], "web_search")
        self.post.side_effect = None
        self.post.return_value = httpx.Response(200, json={"choices": [{"message": {"content": "Next answer."}}]})
        self.assertResponse("ai explain further", "Next answer.")
        self.assertIn("without URLs", self.post.call_args.kwargs["json"]["messages"][0]["content"])

    def testWarningFloodSuppression(self):
        irc = Mock()
        msg = ircmsgs.privmsg("#one", "@ai", prefix="nick!u@host")
        for _ in range(10):
            self.callback._notice(irc, msg, "user", "Please wait")
        self.assertEqual(irc.queueMsg.call_count, 1)

    def testPersistenceAndRollover(self):
        with tempfile.TemporaryDirectory() as folder:
            self.callback._data_file = os.path.join(folder, "DeepseekAI", "usage.json")
            self.callback._users = {"user": {"attempts": 3, "successes": 2, "tokens": 99}}
            self.assertTrue(plugin.DeepseekAI._save_usage(self.callback))
            self.callback._users = {}
            plugin.DeepseekAI._load_usage(self.callback)
            self.assertEqual(self.callback._attempts, 3)
            self.assertEqual(self.callback._tokens, 99)
            self.callback._day = "2000-01-01"
            self.callback._reset_day()
            self.assertEqual(self.callback._attempts, 0)
            self.assertFalse(self.callback._users)

    def testPersistenceFailurePreventsCall(self):
        self.save_mock.return_value = False
        self.assertRegexp("ai hello", "Usage tracking is unavailable")
        self.post.assert_not_called()
        self.assertFalse(self.callback._active_users)
        self.assertEqual(self.callback._attempts, 0)

    def testOwnerCanEnableAndDisable(self):
        value = self.callback.registryValue(
            "enabled", self.channel, network=self.irc.network, value=False)
        self.originals.setdefault(value, value())
        original = ircdb.checkCapability
        def check(prefix, capability, *args, **kwargs):
            return capability == "owner" or original(prefix, capability, *args, **kwargs)
        with patch("supybot.ircdb.checkCapability", side_effect=check):
            self.assertNotError("deepseekai disable")
            self.assertFalse(self.callback.registryValue(
                "enabled", self.channel, network=self.irc.network))
            self.assertTrue(self.callback.registryValue("enabled", self.channel))
            self.assertNotError("deepseekai enable")
            self.assertTrue(self.callback.registryValue(
                "enabled", self.channel, network=self.irc.network))

    def testInvalidConfigRejected(self):
        from supybot import registry
        with self.assertRaises(registry.InvalidRegistryValue):
            conf.supybot.plugins.DeepseekAI.maxConcurrentRequests.setValue(0)
        with self.assertRaises(registry.InvalidRegistryValue):
            conf.supybot.plugins.DeepseekAI.historyExchanges.setValue(-1)

    def testRegistrySaveDefaults(self):
        from supybot import registry
        # Exercise the actual save path, which reconstructs each setting
        # with setDefault=False before rendering its default-value comment.
        with tempfile.TemporaryDirectory() as folder:
            filename = os.path.join(folder, "deepseekai.conf")
            with patch.object(registry, "exception") as log_exception:
                registry.close(conf.supybot.plugins.DeepseekAI, filename)
            log_exception.assert_not_called()
            with open(filename, encoding="utf-8") as stream:
                saved = stream.read()
            self.assertIn("# Default value: 25", saved)
            self.assertIn("# Default value: 512", saved)

    def testRegistryConstructorOptions(self):
        from .config import BoundedInteger
        value = BoundedInteger(2, "Test setting.", 1, 10,
                               setDefault=False, showDefault=False,
                               private=True)
        self.assertFalse(hasattr(value, "value"))
        self.assertFalse(value._showDefault)
        self.assertTrue(value._private)
        value.setValue(3)
        self.assertEqual(value(), 3)

    def testUpgradeGlobalPromptOnReloadPreservesConfiguration(self):
        from importlib import reload
        from . import config
        value = conf.supybot.plugins.DeepseekAI.systemPrompt
        self.originals.setdefault(value, value())
        value.setValue("An existing customized global prompt.")
        value._supplyDefault = False
        value._networkValue = False
        value._channelValue = False
        reload(config)
        self.assertIs(conf.supybot.plugins.DeepseekAI.systemPrompt, value)
        self.assertEqual(value(), "An existing customized global prompt.")
        self.assertTrue(value._supplyDefault)
        self.assertTrue(value._networkValue)
        self.assertTrue(value._channelValue)

    def testScopedGuidanceKeepsPersonalityAndOtherScopes(self):
        setting = conf.supybot.plugins.DeepseekAI.webSearchPrompt
        value = setting.get(":" + self.irc.network).get(self.channel)
        self.originals.setdefault(value, value())
        value.setValue("Search only when external verification is useful.")
        personality = self.callback.registryValue("systemPrompt", self.channel, network=self.irc.network)
        self.assertResponse("ai hello", "Hello from DeepSeek.")
        prompt = self.post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertTrue(prompt.startswith(personality))
        self.assertIn(value(), prompt)
        self.assertNotIn("Use web_search for changing facts", prompt)
        self.assertNotIn("about 533 characters", prompt)
        self.assertEqual(self.callback.registryValue("webSearchPrompt", "#elsewhere", network=self.irc.network), setting())
        with patch("supybot.world.getIrc", return_value=self.irc):
            self.assertEqual(self.callback.registryValue("webSearchPrompt", self.channel, network="othernetwork"), setting())
        self.assertEqual(self.callback.registryValue("systemPrompt", self.channel, network=self.irc.network), personality)
        value.setValue("")
        self.assertResponse("ai hello again", "Hello from DeepSeek.")
        self.assertNotIn("Browsing guidance", self.post.call_args.kwargs["json"]["messages"][0]["content"])

    def testScopedResponseLimitIncludesFormattingAndWebFooter(self):
        setting = conf.supybot.plugins.DeepseekAI.maxResponseChars
        value = setting.get(":" + self.irc.network).get(self.channel)
        self.originals.setdefault(value, value())
        value.setValue(215)
        irc = Mock(network=self.irc.network)
        irc._replyOverhead.return_value = 80
        msg = ircmsgs.privmsg(self.channel, "@aiweb news", prefix=self.prefix)
        msg.channel = self.channel
        responses = self._web_responses()
        responses[1] = httpx.Response(200, json={"results": [{
            "title": "Release", "url": "https://example.org/release", "authority": 4}]})
        responses[-1] = httpx.Response(200, json={"choices": [{"message": {
            "content": "**Current result** [1]. " + "Long explanation. " * 100}}]})
        self.post.side_effect = responses
        self.callback._query(irc, msg, "news, give me links", web=True)
        answer = "\n".join(call.args[0] for call in irc.reply.call_args_list)
        self.assertLessEqual(len(answer), 215)
        self.assertIn("[1] https://example.org/release", answer)
        self.assertIn("\x02", answer)
        self.assertIn("within 215 characters", self.post.call_args.kwargs["json"]["messages"][0]["content"])
        self.assertEqual(self.callback.registryValue("maxResponseChars", "#elsewhere", network=self.irc.network), 800)
        with patch("supybot.world.getIrc", return_value=self.irc):
            self.assertEqual(self.callback.registryValue("maxResponseChars", self.channel, network="othernetwork"), 800)
        for invalid in (0, 79, 2001):
            with self.assertRaises(Exception):
                value.setValue(invalid)

    def testScopedResponseLimitAlsoAppliesWithoutWeb(self):
        value = conf.supybot.plugins.DeepseekAI.maxResponseChars.get(":" + self.irc.network).get(self.channel)
        self.originals.setdefault(value, value())
        value.setValue(600)
        self.post.return_value = httpx.Response(200, json={"choices": [{"message": {"content": "x" * 1000}}]})
        irc = Mock(network=self.irc.network)
        msg = ircmsgs.privmsg(self.channel, "@ai hello", prefix=self.prefix)
        msg.channel = self.channel
        self.callback._query(irc, msg, "hello")
        self.assertEqual(len(irc.reply.call_args.args[0]), 600)

    def testUpgradeScopedLimitsAndGuidancePreservesExistingValues(self):
        from importlib import reload
        from . import config
        for name, customized in (("maxResponseChars", 611), ("webSearchPrompt", "Custom research guidance.")):
            value = conf.supybot.plugins.DeepseekAI.get(name)
            self.originals.setdefault(value, value())
            value.setValue(customized)
            if name == "maxResponseChars":
                # Simulate the original global integer from an older plugin.
                value.__class__ = config.BoundedInteger
            value._supplyDefault = value._networkValue = value._channelValue = False
            reload(config)
            self.assertIs(conf.supybot.plugins.DeepseekAI.get(name), value)
            self.assertEqual(value(), customized)
            self.assertTrue(value._supplyDefault)
            self.assertTrue(value._networkValue)
            self.assertTrue(value._channelValue)


    def _owner(self):
        original = ircdb.checkCapability
        def owner(prefix, capability, *args, **kwargs):
            return capability == "owner" or original(prefix, capability, *args, **kwargs)
        return patch("supybot.ircdb.checkCapability", side_effect=owner)

    def _forget_ok(self):
        with self._owner():
            self.assertNotError("deepseekai forget")

    def _forget_match(self, pattern):
        with self._owner():
            self.assertRegexp("deepseekai forget", pattern)

    def _shared_turn(self, nick, question, identity=None, web=False):
        irc = Mock(network=self.irc.network)
        msg = ircmsgs.privmsg(self.channel, "@ai " + question,
                             prefix=nick + "!" + (identity or nick.lower()) + "@test.host")
        msg.channel = self.channel
        self.callback._query(irc, msg, question, web=web)
        return irc, msg

    def _memory_flag(self, name, value):
        node = self.callback.registryValue(name, self.channel, network=self.irc.network, value=False)
        self.originals.setdefault(node, node())
        node.setValue(value)

    def _enable_shared(self, profiles=False):
        self._memory_flag("historyEnabled", True)
        conf.supybot.plugins.DeepseekAI.historyMode.setValue("channel")
        self._memory_flag("profilesEnabled", profiles)

    def testSharedContextHasSpeakersAndBotRepliesWithoutHosts(self):
        self._enable_shared()
        self._shared_turn("Bob", "My site uses WordPress.")
        self._shared_turn("John", "Would Redis help his site?")
        messages = self.post.call_args.kwargs["json"]["messages"]
        context = json.loads(messages[-2]["content"].split("\n", 1)[1])
        self.assertEqual(context["current_requester"], "John")
        self.assertEqual(context["exchanges"][0]["speaker"], "Bob")
        self.assertEqual(context["exchanges"][0]["bot_reply"], "Hello from DeepSeek.")
        self.assertNotIn("test.host", json.dumps(messages))
        self.assertEqual(messages[-1]["content"], "Would Redis help his site?")
        self.assertFalse(self.callback._history)

    def testSharedModeDoesNotExposeLegacyPersonalHistory(self):
        self._shared_turn("Bob", "Old personal context")
        self._enable_shared()
        self._shared_turn("John", "New shared question")
        self.assertNotIn("Old personal context", json.dumps(self.post.call_args.kwargs["json"]["messages"]))

    def testSharedMemoryDoesNotIncludeOrdinaryChat(self):
        self._enable_shared()
        self.irc.feedMsg(ircmsgs.privmsg(self.channel, "ordinary-channel-message", prefix=self.prefix))
        self._shared_turn("John", "What did Bob say?")
        self.assertNotIn("ordinary-channel-message", json.dumps(self.post.call_args.kwargs["json"]["messages"]))

    def testSharedRequestsRejectOverlapWithoutChargingAndReleaseAfterFailure(self):
        self._enable_shared()
        scope = (plugin.ircutils.toLower(self.irc.network), plugin.ircutils.toLower(self.channel))
        self.callback._channel_requests["someone"] = scope
        irc, _ = self._shared_turn("Bob", "hello")
        self.assertIn("already answering", irc.queueMsg.call_args.args[0].args[1])
        self.post.assert_not_called()
        self.assertEqual(self.callback._attempts, 0)
        self.callback._channel_requests.clear()
        self.post.side_effect = httpx.ReadTimeout("timeout")
        self._shared_turn("Bob", "hello")
        self.assertFalse(self.callback._channel_requests)
        self.assertFalse(self.callback._active_users)

    def testSharedPersistenceAndVersionOneMigration(self):
        self._enable_shared(profiles=True)
        self._shared_turn("Bob", "A question")
        with tempfile.TemporaryDirectory() as folder:
            self.callback._history_file = os.path.join(folder, "history.json")
            self.assertTrue(plugin.DeepseekAI._save_history(self.callback))
            expected = self.callback._memory.dump()
            self.callback._memory = plugin.ConversationMemory()
            self.callback._load_history()
            self.assertEqual(self.callback._memory.dump(), expected)
            with open(self.callback._history_file, "w") as stream:
                json.dump({"version": 1, "conversations": [{"user": "net:u@h", "target": "#old",
                           "exchanges": [[time.time(), "old question", "old answer"]]}]}, stream)
            self.callback._load_history()
            self.assertEqual(self.callback._history[("net:u@h", "#old")][0][1], "old question")
            self.assertFalse(self.callback._memory.channels)
            self.assertFalse(self.callback._memory.profiles)

    def testSharedRetentionBudgetAndScopeIsolation(self):
        memory = plugin.ConversationMemory()
        now = time.time()
        def entry(nick, question, age=0):
            return {"time": now - age, "user": "net:" + nick, "nick": nick,
                    "question": question, "answer": "response"}
        memory.channels[("net", "#one")] = [entry("Bob", "expired", 3700), entry("Bob", "x" * 800), entry("John", "latest")]
        memory.channels[("other", "#one")] = [dict(entry("Alice", "other network"), user="other:Alice")]
        memory.prune(20, 60, 1000)
        key = ("net:john", "#one")
        context = memory.context(("net", "#one"), key, "John", True, 1000)
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False)), 1000)
        self.assertEqual(len(context["exchanges"]), 1)
        self.assertEqual(context["exchanges"][0]["question"], "latest")
        self.assertNotIn("other network", json.dumps(context))
        self.assertEqual(memory.context(("net", "#two"), key, "John", True)["exchanges"], [])
        memory.prune(0, 60, 1000)
        self.assertFalse(memory.channels)

    def testPrivateContextRemainsPerUserInChannelMode(self):
        self._enable_shared()
        conf.supybot.plugins.DeepseekAI.allowPrivate.setValue(True)
        for nick, question in (("Bob", "private secret"), ("John", "other question")):
            irc = Mock(network=self.irc.network)
            msg = ircmsgs.privmsg(self.irc.nick, "@ai " + question, prefix=nick + "!" + nick + "@host")
            msg.channel = None
            self.callback._query(irc, msg, question)
        self.assertNotIn("private secret", json.dumps(self.post.call_args.kwargs["json"]["messages"]))
        self.assertFalse(self.callback._memory.channels)

    def testSharedForgetRemovesOnlyOwnExchangesAndProfile(self):
        self._enable_shared(profiles=True)
        self.assertResponse("ai my question", "Hello from DeepSeek.")
        self._shared_turn("Other", "someone else quoting my question")
        self.assertNotError("deepseekai profile set tone direct")
        self._forget_ok()
        entries = next(iter(self.callback._memory.channels.values()))
        self.assertEqual([e["nick"] for e in entries], ["Other"])
        self.assertFalse(any(k[0].endswith(self.prefix.split("!", 1)[1].lower()) for k in self.callback._memory.profiles))

    def testSharedForgetAndProfileChangesRollbackTogether(self):
        self._enable_shared(profiles=True)
        self.assertResponse("ai remember", "Hello from DeepSeek.")
        self.assertNotError("deepseekai profile set tone direct")
        expected = self.callback._memory.dump()
        self.history_save_mock.return_value = False
        self._forget_match("Could not save")
        self.assertEqual(self.callback._memory.dump(), expected)
        self.assertRegexp("deepseekai profile off", "Could not save")
        self.assertEqual(self.callback._memory.dump(), expected)

    def testOwnerCanClearChannelAndCannotClearWhileBusy(self):
        self._enable_shared(profiles=True)
        self.assertResponse("ai remember", "Hello from DeepSeek.")
        with patch("supybot.ircdb.checkCapability", return_value=False):
            self.assertError("deepseekai forgetchannel")
        original = ircdb.checkCapability
        def owner(prefix, capability, *args, **kwargs):
            return capability == "owner" or original(prefix, capability, *args, **kwargs)
        with patch("supybot.ircdb.checkCapability", side_effect=owner):
            scope = next(iter(self.callback._memory.channels))
            self.callback._channel_requests["someone"] = scope
            self.assertRegexp("deepseekai forgetchannel", "Wait until")
            self.callback._channel_requests.clear()
            self.assertNotError("deepseekai forgetchannel")
        self.assertFalse(self.callback._memory.channels)
        self.assertTrue(all(not p["samples"] and not p["observations"] for p in self.callback._memory.profiles.values()))

    def testProfileCommandsArePrivateValidatedAndCallNoApi(self):
        response = self.assertRegexp("deepseekai profile", "no preferences")
        self.assertEqual(response.args[0], self.nick)
        self.assertNotError("deepseekai profile set reply_length short")
        self.assertRegexp("deepseekai profile show", "reply_length=short")
        self.assertError("deepseekai profile set diagnosis happy")
        self.assertError("deepseekai profile set tone hostile")
        self.assertNotError("deepseekai profile unset reply_length")
        self.assertRegexp("deepseekai profile show", "Preferences: none")
        self.assertNotError("deepseekai profile off")
        self.assertRegexp("deepseekai profile show", "here: off")
        self.assertNotError("deepseekai profile reset")
        self.assertRegexp("deepseekai profile show", "here: off")
        self.assertNotError("deepseekai profile on")
        self.post.assert_not_called()

    def testProfileOptOutPreventsLearningAndSurvivesForget(self):
        self._enable_shared(profiles=True)
        self.assertNotError("deepseekai profile off")
        self.assertResponse("ai hello", "Hello from DeepSeek.")
        profile = next(iter(self.callback._memory.profiles.values()))
        self.assertFalse(profile["enabled"])
        self.assertFalse(profile["samples"])
        self.assertNotIn("requester_profile", self.post.call_args.kwargs["json"]["messages"][-2]["content"])
        self._forget_ok()
        self.assertFalse(next(iter(self.callback._memory.profiles.values()))["enabled"])

    def testProfileReviewUsesOnlyOwnMessagesAcrossDaysAndAccountsTokens(self):
        self._enable_shared(profiles=True)
        irc = Mock(network=self.irc.network)
        msg = ircmsgs.privmsg(self.channel, "@ai thanks!", prefix=self.prefix)
        msg.channel = self.channel
        _, key = self.callback._keys(irc, msg)
        profile = self.callback._memory.profile(key, self.nick)
        profile["pending"] = 7
        profile["samples"] = [{"time": time.time() - 86400, "text": "own upbeat message " + str(i)} for i in range(7)]
        self._shared_turn("Other", "never use other person's words for this profile")
        self.post.reset_mock()
        self.post.side_effect = [httpx.Response(200, json={"choices": [{"message": {"content": "You're welcome."}}], "usage": {"total_tokens": 42}}),
            httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"observations": [{"style": "upbeat", "evidence": [0, 2, 7]}]})}}],
                                     "usage": {"total_tokens": 15}})]
        self.callback._query(irc, msg, "thanks!")
        irc.reply.assert_called_once_with("You're welcome.", prefixNick=True)
        self.assertEqual(self.post.call_count, 2)
        review = self.post.call_args.kwargs["json"]
        self.assertEqual(review["response_format"], {"type": "json_object"})
        self.assertNotIn("never use other person's words", json.dumps(review))
        self.assertNotIn(self.prefix.split("!", 1)[1], json.dumps(review))
        self.assertEqual(profile["observations"][0]["style"], "upbeat")
        self.assertEqual(profile["observations"][0]["supporting_exchanges"], 3)
        self.assertEqual(profile["pending"], 0)
        self.assertEqual(self.callback._tokens, 99)
        self.assertFalse(self.callback._channel_requests)

    def testBadProfileReviewDoesNotSuppressDeliveredAnswer(self):
        self._enable_shared(profiles=True)
        irc = Mock(network=self.irc.network)
        msg = ircmsgs.privmsg(self.channel, "@ai hi", prefix=self.prefix)
        msg.channel = self.channel
        _, key = self.callback._keys(irc, msg)
        profile = self.callback._memory.profile(key)
        profile["pending"] = 7
        profile["samples"] = [{"time": time.time(), "text": "hello " + str(i)} for i in range(7)]
        self.post.side_effect = [httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]}), httpx.ReadTimeout("failed")]
        self.callback._query(irc, msg, "hi")
        irc.reply.assert_called_once_with("hello", prefixNick=True)
        irc.queueMsg.assert_not_called()
        self.assertFalse(profile["observations"])
        self.assertFalse(self.callback._active_users)

    def testProfileEvidenceValidationExpiryAndReset(self):
        memory = plugin.ConversationMemory()
        key = ("net:bob@host", "#one")
        profile = memory.profile(key)
        for observation in ({"style": "upbeat", "evidence": [0, 0, 0]},
                            {"style": "upbeat", "evidence": [0, 1, 99]},
                            {"style": "diagnosis", "evidence": [0, 1, 2]}):
            with self.assertRaises(ValueError):
                memory.accept_review(key, {"observations": [observation]}, 8, 30)
        memory.accept_review(key, {"observations": [{"style": "playful", "evidence": [0, 1, 2]}]}, 8, 30)
        profile["observations"][0]["expires_at"] = time.time() - 1
        profile["samples"] = [{"time": time.time() - 8 * 86400, "text": "old"}]
        memory.prune(20, 60, 12000)
        self.assertEqual(profile["observations"], [])
        self.assertEqual(profile["samples"], [])
        profile["preferences"]["tone"] = "direct"
        self.assertIn("requester_profile", memory.context(("net", "#one"), key, "Bob", True))
        self.assertNotIn("requester_profile", memory.context(("net", "#two"), (key[0], "#two"), "Bob", True))
        memory.reset_profile(key)
        self.assertFalse(memory.profiles[key]["preferences"])

    def testDisabledHistoryAndProfilingUseNoMemoryAndStoreNothing(self):
        self._memory_flag("historyEnabled", False)
        self._shared_turn("Bob", "first")
        self._shared_turn("Bob", "second")
        messages = self.post.call_args.kwargs["json"]["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["content"], "second")
        self.assertFalse(self.callback._history)
        self.assertFalse(self.callback._memory.channels)
        self.assertFalse(self.callback._memory.profiles)
        self.assertEqual(self.post.call_count, 2)

    def testProfilesCanBeUsedWithoutConversationHistory(self):
        self._memory_flag("historyEnabled", False)
        self._memory_flag("profilesEnabled", True)
        self.assertNotError("deepseekai profile set tone direct")
        self.assertResponse("ai first", "Hello from DeepSeek.")
        self.assertResponse("ai second", "Hello from DeepSeek.")
        messages = self.post.call_args.kwargs["json"]["messages"]
        self.assertNotIn('"content": "first"', json.dumps(messages))
        self.assertIn('"tone": "direct"', messages[-2]["content"])
        self.assertFalse(self.callback._history)
        self.assertFalse(self.callback._memory.channels)
        self.assertEqual(len(next(iter(self.callback._memory.profiles.values()))["samples"]), 2)

    def testOwnerCanForgetAnotherNickButOrdinaryUsersCannot(self):
        self._enable_shared(profiles=True)
        self._shared_turn("Bob", "Bob's question")
        self._shared_turn("John", "John's question")
        with patch("supybot.ircdb.checkCapability", return_value=False):
            self.assertError("deepseekai forget Bob")
            self.assertError("deepseekai forget")
        with self._owner():
            self.assertNotError("deepseekai forget Bob")
        entries = next(iter(self.callback._memory.channels.values()))
        self.assertEqual([e["nick"] for e in entries], ["John"])
        self.assertFalse(any(p["last_nick"] == "Bob" for p in self.callback._memory.profiles.values()))

    def testOwnerForgetRefusesAmbiguousHistoricalNick(self):
        self._enable_shared(profiles=True)
        self._shared_turn("Bob", "one identity", identity="first")
        self._shared_turn("Bob", "another identity", identity="second")
        before = self.callback._memory.dump()
        with self._owner():
            self.assertRegexp("deepseekai forget Bob", "Cannot uniquely identify")
        self.assertEqual(self.callback._memory.dump(), before)

    def testTurningSwitchesOffDuringRequestPreventsRecording(self):
        self._enable_shared(profiles=True)
        def answer(*args, **kwargs):
            self._memory_flag("historyEnabled", False)
            self._memory_flag("profilesEnabled", False)
            return httpx.Response(200, json={"choices": [{"message": {"content": "done"}}]})
        self.post.side_effect = answer
        irc, _ = self._shared_turn("Bob", "please answer")
        irc.reply.assert_called_once_with("done", prefixNick=True)
        self.assertFalse(self.callback._history)
        self.assertFalse(self.callback._memory.channels)
        self.assertFalse(self.callback._memory.profiles)
        self.assertEqual(self.post.call_count, 1)

    def testTurningHistoryOffOmitsAlreadyRetainedContext(self):
        self._enable_shared(profiles=True)
        self._shared_turn("Bob", "old retained question")
        self._memory_flag("historyEnabled", False)
        self._memory_flag("profilesEnabled", False)
        self._shared_turn("John", "fresh question")
        messages = self.post.call_args.kwargs["json"]["messages"]
        self.assertEqual(len(messages), 2)
        self.assertNotIn("old retained question", json.dumps(messages))
        self.assertEqual(len(next(iter(self.callback._memory.channels.values()))), 1)

    def testProfileContextFollowsIdentityAcrossNickChangesAndOnlyRequester(self):
        self._enable_shared(profiles=True)
        self._shared_turn("Bob", "first question")
        key = next(iter(self.callback._memory.profiles))
        self.callback._memory.profiles[key]["preferences"]["tone"] = "direct"
        self._shared_turn("Robert", "second question", identity="bob")
        context = self.post.call_args.kwargs["json"]["messages"][-2]["content"]
        self.assertIn('"tone": "direct"', context)
        self._shared_turn("John", "another question")
        context = self.post.call_args.kwargs["json"]["messages"][-2]["content"]
        self.assertNotIn('"tone": "direct"', context)

    def testMalformedSharedStateDoesNotReplaceExistingMemory(self):
        self._enable_shared(profiles=True)
        self._shared_turn("Bob", "valid state")
        original = self.callback._memory.dump()
        with tempfile.TemporaryDirectory() as folder:
            self.callback._history_file = os.path.join(folder, "history.json")
            payload = json.loads(json.dumps({"version": 2, "conversations": [], **original}))
            payload["channels"][0]["exchanges"][0] = {"time": "invalid"}
            with open(self.callback._history_file, "w") as stream:
                json.dump(payload, stream)
            before = self.callback._memory.dump()
            self.callback._load_history()
            self.assertEqual(self.callback._memory.dump(), before)

    def _remote_memory_target(self):
        target = "#memory-target"
        self.irc.state.addMsg(self.irc, ircmsgs.join(target, prefix=self.irc.prefix))
        for setting in ("historyEnabled", "profilesEnabled"):
            node = self.callback.registryValue(setting, target, network=self.irc.network, value=False)
            self.originals.setdefault(node, node())
            node.setValue(False)
        return target

    def testRemoteStatusShowsTargetHistoryAndProfilingInDebugChannel(self):
        target = self._remote_memory_target()
        self._memory_flag("historyEnabled", False)
        self._memory_flag("profilesEnabled", False)
        conf.supybot.plugins.DeepseekAI.historyMode.setValue("channel")
        with self._owner():
            self.assertNotError("deepseekai history %s on" % target)
            self.assertNotError("deepseekai profile %s on" % target)
        response = self.assertRegexp("deepseekai status " + target,
                                     target + r".*history on \(channel\); profiling on")
        self.assertEqual(response.args[0], self.channel)
        self.assertRegexp("deepseekai status", r"history off; profiling off")
        self.post.assert_not_called()

    def testRemoteStatusShowsDisabledTargetRegardlessOfCurrentChannel(self):
        target = self._remote_memory_target()
        self._memory_flag("profilesEnabled", True)
        self.assertRegexp("deepseekai status " + target, target + r".*history off; profiling off")
        self.assertRegexp("deepseekai status", r"history on \(user\); profiling on")

    def testStatusSupportsPrivateRequestsAndPrivateLocation(self):
        target = self._remote_memory_target()
        response = self.assertRegexp("deepseekai status " + target,
                                     target + r".*history off; profiling off", to=self.irc.nick)
        self.assertEqual(response.args[0], self.nick)
        response = self.assertRegexp("deepseekai status", r"private AI disabled; history on \(user\); profiling off",
                                     to=self.irc.nick)
        self.assertEqual(response.args[0], self.nick)
        self.post.assert_not_called()

    def testRemoteHistoryAndProfileSwitchesReplyInDebugChannel(self):
        target = self._remote_memory_target()
        self._memory_flag("historyEnabled", False)
        self._memory_flag("profilesEnabled", False)
        with self._owner():
            for command in ("history", "profiling", "profile"):
                setting = "historyEnabled" if command == "history" else "profilesEnabled"
                original_global = self.callback.registryValue(setting)
                response = self.assertRegexp("deepseekai %s %s on" % (command, target), target + ": on")
                self.assertEqual(response.args[0], self.channel)
                self.assertTrue(self.callback.registryValue(setting, target, network=self.irc.network))
                self.assertFalse(self.callback.registryValue(setting, self.channel, network=self.irc.network))
                self.assertEqual(self.callback.registryValue(setting), original_global)
                with patch("supybot.world.getIrc", return_value=self.irc):
                    self.assertEqual(self.callback.registryValue(setting, target, network="anothernetwork"), original_global)
                self.assertNotError("deepseekai %s %s off" % (command, target))
        self.assertFalse(self.callback._memory.profiles)
        self.post.assert_not_called()

    def testRemoteMemoryCommandsRemainOwnerOnly(self):
        target = self._remote_memory_target()
        with patch("supybot.ircdb.checkCapability", return_value=False):
            for command in ("history %s on", "profiling %s on", "profile %s on",
                            "forget %s Bob", "forgetchannel %s"):
                self.assertError("deepseekai " + command % target)
        self.assertFalse(self.callback.registryValue("historyEnabled", target, network=self.irc.network))
        self.assertFalse(self.callback.registryValue("profilesEnabled", target, network=self.irc.network))
        self.post.assert_not_called()

    def testRemoteSwitchesCanBeIssuedInPrivateMessages(self):
        target = self._remote_memory_target()
        with self._owner():
            for command in ("history", "profiling", "profile"):
                response = self.assertRegexp("deepseekai %s %s on" % (command, target), target + ": on", to=self.irc.nick)
                self.assertEqual(response.args[0], self.nick)

    def testRemoteForgetOnlyDeletesTargetChannelAndRepliesInDebug(self):
        target = self._remote_memory_target()
        self._enable_shared(profiles=True)
        self._shared_turn("Bob", "debug context")
        debug_scope = next(iter(self.callback._memory.channels))
        target_scope = (debug_scope[0], target)
        debug_key = next(iter(self.callback._memory.profiles))
        target_key = (debug_key[0], target)
        self.callback._memory.channels[target_scope] = json.loads(json.dumps(self.callback._memory.channels[debug_scope]))
        self.callback._memory.profiles[target_key] = json.loads(json.dumps(self.callback._memory.profiles[debug_key]))
        with self._owner():
            response = self.assertRegexp("deepseekai forget %s Bob" % target, "Forgotten.*Bob.*" + target)
        self.assertEqual(response.args[0], self.channel)
        self.assertNotIn(target_scope, self.callback._memory.channels)
        self.assertNotIn(target_key, self.callback._memory.profiles)
        self.assertIn(debug_scope, self.callback._memory.channels)
        self.assertIn(debug_key, self.callback._memory.profiles)

    def testRemoteForgetFromPrivateAndForgetChannelPreserveDebugMemory(self):
        target = self._remote_memory_target()
        self._enable_shared(profiles=True)
        self._shared_turn("Bob", "debug context")
        debug_scope = next(iter(self.callback._memory.channels))
        target_scope = (debug_scope[0], target)
        entries = json.loads(json.dumps(self.callback._memory.channels[debug_scope]))
        self.callback._memory.channels[target_scope] = entries
        with self._owner():
            response = self.assertRegexp("deepseekai forget %s Bob" % target, "Forgotten.*Bob", to=self.irc.nick)
            self.assertEqual(response.args[0], self.nick)
            self.callback._memory.channels[target_scope] = entries
            response = self.assertRegexp("deepseekai forgetchannel " + target, "Cleared.*" + target)
        self.assertEqual(response.args[0], self.channel)
        self.assertNotIn(target_scope, self.callback._memory.channels)
        self.assertIn(debug_scope, self.callback._memory.channels)

    def testRemoteForgetChecksTargetChannelForActiveRequests(self):
        target = self._remote_memory_target()
        self._enable_shared()
        self._shared_turn("Bob", "debug context")
        debug_scope = next(iter(self.callback._memory.channels))
        target_scope = (debug_scope[0], target)
        entries = json.loads(json.dumps(self.callback._memory.channels[debug_scope]))
        self.callback._memory.channels[target_scope] = entries
        with self._owner():
            self.callback._channel_requests["someone"] = target_scope
            self.assertRegexp("deepseekai forget %s Bob" % target, "Wait for")
            self.assertIn(target_scope, self.callback._memory.channels)
            self.callback._channel_requests["someone"] = debug_scope
            self.assertNotError("deepseekai forget %s Bob" % target)
        self.callback._channel_requests.clear()
        self.assertNotIn(target_scope, self.callback._memory.channels)


class DeepseekAIConfigTestCase(ChannelPluginTestCase):
    plugins = ("DeepseekAI", "Config", "Utilities", "Aka")
    config = {
        "plugins.DeepseekAI.enabled": True,
        "plugins.DeepseekAI.replyToNick": True,
        "reply.whenAddressedBy.nick": True,
        "plugins.DeepseekAI.historyEnabled": False,
        "plugins.DeepseekAI.profilesEnabled": False,
    }

    def _owner(self):
        original = ircdb.checkCapability
        def owner(prefix, capability, *args, **kwargs):
            return capability == "owner" or original(prefix, capability, *args, **kwargs)
        return patch("supybot.ircdb.checkCapability", side_effect=owner)

    def testResponseCharsCreatesFreshScopeAndEnablesPlainConfig(self):
        callback = self.irc.getCallback("DeepseekAI")
        setting = conf.supybot.plugins.DeepseekAI.maxResponseChars
        target = "#fresh-response-limit"
        name = "plugins.DeepseekAI.maxResponseChars.:%s.%s" % (self.irc.network, target)
        with self._owner():
            self.assertError("config " + name + " 600")
            response = self.assertRegexp("deepseekai responsechars %s %s 600" % (self.irc.network, target),
                                         target + ": 600 characters")
            self.assertEqual(response.args[0], self.channel)
            node = setting.get(":" + self.irc.network).get(target)
            self.originals.setdefault(node, setting())
            self.assertNotError("config " + name + " 215")
            self.assertRegexp("deepseekai responsechars " + target, target + ": 215 characters")
        self.assertEqual(callback.registryValue("maxResponseChars", target, network=self.irc.network), 215)
        self.assertEqual(setting(), 800)
        self.assertFalse(setting.get(target)._wasSet)

    def testResponseCharsSupportsAnotherNetworkWithoutChannelFallback(self):
        callback = self.irc.getCallback("DeepseekAI")
        other = Mock(network="other-limit-network")
        target = "#fresh-cross-network-limit"
        setting = conf.supybot.plugins.DeepseekAI.maxResponseChars
        with patch("supybot.world.ircs", [self.irc, other]), self._owner():
            response = self.assertRegexp("deepseekai responsechars other-limit-network %s 215" % target,
                                         "other-limit-network " + target + ": 215 characters")
            self.assertEqual(response.args[0], self.channel)
            node = setting.get(":other-limit-network").get(target)
            self.originals.setdefault(node, setting())
            self.assertEqual(callback.registryValue("maxResponseChars", target, network=other.network), 215)
            self.assertEqual(callback.registryValue("maxResponseChars", target, network=self.irc.network), 800)
            self.assertEqual(setting(), 800)
            self.assertFalse(setting.get(target)._wasSet)

    def testResponseCharsRequiresOwnerAndEnforcesBounds(self):
        target = "#invalid-response-limit"
        setting = conf.supybot.plugins.DeepseekAI.maxResponseChars
        with patch("supybot.ircdb.checkCapability", return_value=False):
            self.assertError("deepseekai responsechars %s 600" % target)
        with self._owner():
            self.assertRegexp("deepseekai responsechars %s 79" % target, "between 80 and 2000")
            self.assertRegexp("deepseekai responsechars %s 2001" % target, "between 80 and 2000")
            self.assertRegexp("deepseekai responsechars " + target, target + ": 800 characters")
        self.assertEqual(setting(), 800)

    def testResponseCharsCanBeSetFromPrivateMessage(self):
        target = "#private-response-limit"
        setting = conf.supybot.plugins.DeepseekAI.maxResponseChars
        with self._owner():
            response = self.assertRegexp("deepseekai responsechars %s 600" % target,
                                         target + ": 600 characters", to=self.irc.nick)
            self.assertEqual(response.args[0], self.nick)
        node = setting.get(":" + self.irc.network).get(target)
        self.originals.setdefault(node, setting())

    def testMemorySwitchesRequireOwnerAndOnlyChangeCurrentNetworkChannel(self):
        callback = self.irc.getCallback("DeepseekAI")
        original = ircdb.checkCapability
        def owner(prefix, capability, *args, **kwargs):
            return capability == "owner" or original(prefix, capability, *args, **kwargs)
        for command, setting in (("history", "historyEnabled"), ("profiling", "profilesEnabled")):
            node = conf.supybot.plugins.DeepseekAI.get(setting).get(":" + self.irc.network).get(self.channel)
            self.originals.setdefault(node, node())
            with patch("supybot.ircdb.checkCapability", return_value=False):
                self.assertError("deepseekai " + command + " on")
            self.assertFalse(callback.registryValue(setting, self.channel, network=self.irc.network))
            with patch("supybot.ircdb.checkCapability", side_effect=owner):
                self.assertNotError("deepseekai " + command + " on")
            self.assertTrue(callback.registryValue(setting, self.channel, network=self.irc.network))
            self.assertFalse(callback.registryValue(setting, "#elsewhere", network=self.irc.network))
            with patch("supybot.world.getIrc", return_value=self.irc):
                self.assertFalse(callback.registryValue(setting, self.channel, network="anothernetwork"))
            self.assertFalse(conf.supybot.plugins.DeepseekAI.get(setting)())
            name = "plugins.DeepseekAI.%s.:%s.%s" % (setting, self.irc.network, self.channel)
            with patch("supybot.ircdb.checkCapability", side_effect=owner):
                self.assertNotError("config " + name + " False")
            self.assertFalse(callback.registryValue(setting, self.channel, network=self.irc.network))
            self.assertFalse(conf.supybot.plugins.DeepseekAI.get(setting)._opSettable)

    def testConfigurePersonalityWithoutAffectingOtherChannelsOrNetworks(self):
        callback = self.irc.getCallback("DeepseekAI")
        default = callback.registryValue("systemPrompt")
        self.assertNotError("deepseekai status")
        value = conf.supybot.plugins.DeepseekAI.systemPrompt.get(
            ":" + self.irc.network).get(self.channel)
        self.originals.setdefault(value, value())
        original = ircdb.checkCapability
        def check(prefix, capability, *args, **kwargs):
            return capability == "owner" or original(prefix, capability, *args, **kwargs)
        name = "plugins.DeepseekAI.systemPrompt.:%s.%s" % (self.irc.network, self.channel)
        with patch("supybot.ircdb.checkCapability", side_effect=check):
            self.assertNotError("config " + name + " Always answer in English with dry irony.")
            self.assertResponse("config " + name, "Always answer in English with dry irony.")
        self.assertEqual(value(), "Always answer in English with dry irony.")
        self.assertEqual(callback.registryValue("systemPrompt", self.channel), default)
        self.assertEqual(callback.registryValue("systemPrompt", "#elsewhere",
                         network=self.irc.network), default)
        self.assertRegexp("deepseekai status", "custom personality on")

    def testKnownCommandsAndAliasesRetainNormalParsing(self):
        self.assertResponse(self.irc.nick + ': echo [echo nested]', "nested",
                            usePrefixChar=False)
        self.assertResponse(self.irc.nick + ': echo[echo nested]', "nested",
                            usePrefixChar=False)
        self.assertError(self.irc.nick + ': echo[unfinished', usePrefixChar=False)
        self.assertNotError('aka add testgreeting "echo Hello there"')
        self.assertResponse(self.irc.nick + ': testgreeting', "Hello there",
                            usePrefixChar=False)
        self.assertNotError("aka remove testgreeting")
