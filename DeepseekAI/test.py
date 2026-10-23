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

import os
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
        "plugins.DeepseekAI.webDailyLimit": 15,
        "plugins.DeepseekAI.formatting": True,
        "plugins.DeepseekAI.greyItalics": True,
    }

    def setUp(self):
        super().setUp()
        self.callback = self.irc.getCallback("DeepseekAI")
        self.save = patch.object(self.callback, "_save_usage", return_value=True)
        self.save_mock = self.save.start()
        self.addCleanup(self.save.stop)
        channel_value = self.callback.registryValue("enabled", self.channel, value=False)
        self.originals[channel_value] = channel_value()
        channel_value.setValue(True)
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
        self.assertNotError("deepseekai forget")
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
        self.callback._history[key] = [(time.monotonic() - 90000, "expired", "old")]
        self.assertEqual(self.callback._get_history(key), [])
        self.assertNotIn(key, self.callback._history)
        self.callback._history[key] = [(time.monotonic(), str(i), "answer") for i in range(25)]
        self.assertEqual(len(self.callback._get_history(key)), 40)
        self.assertEqual(self.callback._get_history(key)[0]["content"], "5")

    def testHistoryDisabled(self):
        conf.supybot.plugins.DeepseekAI.historyExchanges.setValue(0)
        self.assertResponse("ai hello", "Hello from DeepSeek.")
        self.assertFalse(self.callback._history)

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

    def _web_responses(self):
        return [httpx.Response(200, json={"results": [{
            "title": "Release", "url": "https://example.org/release",
            "summary": "Current release is 2.0."}]}),
            httpx.Response(200, json={
                "choices": [{"message": {"content": "Version 2.0 [1]."}}],
                "usage": {"total_tokens": 100}})]

    def testWebCommandAndPayload(self):
        self.post.side_effect = self._web_responses()
        self.assertResponse("aiweb latest release", "Version 2.0 [1]. [1] https://example.org/release")
        search, chat = self.post.call_args_list
        self.assertEqual(search.args[0], plugin.DeepseekAI.SEARCH_URL)
        self.assertEqual(search.kwargs["json"], {
            "query": "latest release", "count": 3, "need_content": False})
        self.assertEqual(search.kwargs["headers"]["Authorization"], "Bearer test-secret-key")
        self.assertEqual(chat.args[0], plugin.DeepseekAI.API_URL)
        messages = chat.kwargs["json"]["messages"]
        self.assertIn("untrusted", messages[0]["content"])
        self.assertIn("Current release is 2.0.", messages[-2]["content"])
        self.assertEqual(messages[-1]["content"], "latest release")
        self.assertEqual(self.callback._web_attempts, 1)
        self.assertEqual(self.callback._attempts, 1)
        self.assertEqual(self.callback._tokens, 100)

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
        self.assertRegexp("aiweb " + "a" * 129, "at most 128")
        self.post.assert_not_called()
        self.assertEqual(self.callback._web_attempts, 0)
        self.callback._last_notice.clear()
        conf.supybot.plugins.DeepseekAI.webDailyLimit.setValue(0)
        self.assertRegexp("aiweb news", "disabled")
        self.post.assert_not_called()

    def testWebEmptyResultsAndFailuresConsumeAttempt(self):
        self.post.return_value = httpx.Response(200, json={"results": []})
        self.assertRegexp("aiweb news", "No usable web results")
        self.assertEqual(self.post.call_count, 1)
        self.assertFalse(self.callback._active_users)
        self.callback._last_notice.clear()
        self.post.return_value = httpx.Response(429)
        self.assertRegexp("aiweb news", "rate limit reached")
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(self.callback._web_attempts, 2)
        self.assertFalse(self.callback._active_users)

    def testWebMalformedResultsReleaseSlot(self):
        self.post.return_value = httpx.Response(200, json={"results": "invalid"})
        self.assertRegexp("aiweb news", "invalid response")
        self.assertEqual(self.post.call_count, 1)
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


class DeepseekAIConfigTestCase(ChannelPluginTestCase):
    plugins = ("DeepseekAI", "Config", "Utilities", "Aka")
    config = {
        "plugins.DeepseekAI.enabled": True,
        "plugins.DeepseekAI.replyToNick": True,
        "reply.whenAddressedBy.nick": True,
    }

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
        self.assertRegexp("deepseekai status", "personality custom")

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
