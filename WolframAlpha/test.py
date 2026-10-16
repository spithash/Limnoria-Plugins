###
# Copyright (c) 2014, spline
# Copyright (c) 2020, oddluck <oddluck@riseup.net>
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

from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ElementTree

from supybot.test import ChannelPluginTestCase, PluginTestCase, conf
from . import plugin


def pod(title, text, ident="Result", position="200", primary=False):
    node = ElementTree.Element("pod", title=title, id=ident, position=position,
                               primary=str(primary).lower())
    ElementTree.SubElement(ElementTree.SubElement(node, "subpod"), "plaintext").text = text
    return ElementTree.tostring(node, encoding="unicode")


def result(*pods):
    return ('<queryresult success="true" error="false">' + ''.join(pods) + '</queryresult>').encode()


class WolframAlphaTestCase(PluginTestCase):
    plugins = ("WolframAlpha",)
    config = {
        "plugins.WolframAlpha.apiKey": "test-secret-key",
        "plugins.WolframAlpha.disableANSI": True,
        "plugins.WolframAlpha.outputStyle": "detailed",
    }

    def setUp(self):
        super().setUp()
        self.callback = self.irc.getCallback("WolframAlpha")
        self.network = patch.object(plugin.utils.web, "getUrl")
        self.fetch = self.network.start()
        self.addCleanup(self.network.stop)
        self.fetch.return_value = result(
            pod("Input", "2+2", "Input", "100"), pod("Result", "4", primary=True))

    def testShortest(self):
        self.assertResponse("wolframalpha --shortest 2+2", "2+2 :: 4")

    def testSinglePod(self):
        self.fetch.return_value = result(pod("Result", "4"))
        self.assertResponse("wolframalpha --shortest 2+2", "2+2 :: 4")

    def testPrimaryAnswer(self):
        self.fetch.return_value = result(pod("Input", "question", "Input", "100"),
            pod("Other", "secondary", "Other", "200"),
            pod("Answer", "main", "Answer", "300", True))
        self.assertResponse("wolframalpha --shortest question",
                            "question :: main")

    def testRequestOptions(self):
        self.assertRegexp("wolframalpha --num 1 --usemetric --reinterpret 2+2", r"Input :: 2\+2.*more message")
        args, kwargs = self.fetch.call_args
        url = urlsplit(args[0])
        self.assertEqual(url.scheme, "https")
        params = parse_qs(url.query)
        self.assertEqual(params["input"], ["2+2"])
        self.assertEqual(params["units"], ["metric"])
        self.assertEqual(params["reinterpret"], ["true"])
        self.assertEqual(kwargs["timeout"], 20)

    def testInvalidOptions(self):
        self.assertRegexp("wolframalpha --num 0 test", "at least 1")
        self.assertRegexp("wolframalpha --shortest --fulloutput test", "Choose either")
        self.fetch.assert_not_called()

    def testMissingKey(self):
        conf.supybot.plugins.WolframAlpha.apiKey.setValue("")
        self.assertRegexp("wolframalpha test", "API key is not set")
        self.fetch.assert_not_called()

    def testNetworkFailureDoesNotLeakKey(self):
        self.fetch.side_effect = RuntimeError("https://example/?appid=test-secret-key")
        with patch.object(self.callback.log, "warning") as warning:
            self.assertResponse("wolframalpha test", "Could not reach WolframAlpha. Please try again later.")
            self.assertNotIn("test-secret-key", str(warning.call_args))

    def testMalformedResponse(self):
        for data in (b"<broken", b"<html/>"):
            self.fetch.return_value = data
            self.assertRegexp("wolframalpha test", "unreadable response")

    def testApiError(self):
        self.fetch.return_value = b'<queryresult error="true"><error><code>1</code><msg>bad key</msg></error></queryresult>'
        self.assertRegexp("wolframalpha test", "rejected the API key")

    def testSuggestionsAndTips(self):
        self.fetch.return_value = b'<queryresult success="false"><didyoumeans><didyoumean>France</didyoumean></didyoumeans></queryresult>'
        self.assertResponse("wolframalpha frnce", "No result. Did you mean: France?")
        self.fetch.return_value = b'<queryresult><tips><tip text="Try another spelling."/></tips></queryresult>'
        self.assertResponse("wolframalpha test", "No text result from WolframAlpha. Try another spelling.")

    def testNoPlaintext(self):
        self.fetch.return_value = b'<queryresult success="true"><pod title="Plot"/></queryresult>'
        self.assertRegexp("wolframalpha --shortest plot", "No text result")

    def testPodOrderingAndDuplicates(self):
        data = result(pod("Same", "later", position="200"),
                      pod("Same", "first", position="100"),
                      pod("Empty", "", position="50"),
                      pod("Bad position", "kept", position="invalid"))
        pods = self.callback._pods(ElementTree.fromstring(data))
        self.assertEqual([p["text"] for p in pods], ["kept", "first", "later"])

    def testOutputLimits(self):
        conf.supybot.plugins.WolframAlpha.outputLimit.setValue(2)
        self.addCleanup(conf.supybot.plugins.WolframAlpha.outputLimit.setValue, 10)
        self.fetch.return_value = result(*(pod("Result", str(n)) for n in range(5)))
        for options in ("--fulloutput", "--num 999"):
            irc, msg = self._conversation(channel=None)
            self.callback.wolframalpha(irc, msg, options.split() + ["test"])
            self.assertEqual(irc.reply.call_count, 2)
            self.assertEqual(irc.reply.call_args_list[0].args[0], "Result :: 0")
            self.assertIn("Result :: 1", irc.reply.call_args.args[0])


    def testBitcoinPrecision(self):
        self.fetch.return_value = result(pod("Result", "0.001 USD", primary=True))
        self.assertResponse("btc 0.00000001 USD", "0.00000001 BTC = 0.001 USD")
        params = parse_qs(urlsplit(self.fetch.call_args.args[0]).query)
        self.assertEqual(params["input"], ["0.00000001 BTC to USD"])

    def testInvalidBitcoinAmount(self):
        for amount in ("nan", "inf", "-1", "oops", "1e999999"):
            self.assertRegexp("btc " + amount + " EUR", "finite, nonnegative")
        self.fetch.assert_not_called()

    def testBitcoinNoResult(self):
        self.fetch.return_value = result(pod("Input", "BTC", "Input"))
        self.assertRegexp("btc 1 XYZ", "No text result")

    def testCacheHitAndExpiry(self):
        with patch.object(plugin, "monotonic", return_value=100) as clock:
            self.assertResponse("wolframalpha --shortest 2+2", "2+2 :: 4")
            clock.return_value = 110
            self.assertResponse("wolframalpha --shortest 2+2", "2+2 :: 4")
            self.assertEqual(self.fetch.call_count, 1)
            clock.return_value = 161
            self.assertResponse("wolframalpha --shortest 2+2", "2+2 :: 4")
            self.assertEqual(self.fetch.call_count, 2)

    def testCacheIsolation(self):
        irc = Mock()
        self.callback._request(irc, "test")
        self.callback._request(irc, "test", metric=True)
        conf.supybot.plugins.WolframAlpha.apiKey.setValue("another-test-key")
        self.callback._request(irc, "test")
        self.assertEqual(self.fetch.call_count, 3)

    def testCacheBoundAndDisable(self):
        irc = Mock()
        for n in range(20):
            self.callback._request(irc, str(n))
        self.assertEqual(len(self.callback._cache), 16)
        conf.supybot.plugins.WolframAlpha.cacheSeconds.setValue(0)
        self.addCleanup(conf.supybot.plugins.WolframAlpha.cacheSeconds.setValue, 60)
        self.callback._request(irc, "19")
        self.callback._request(irc, "19")
        self.assertEqual(self.fetch.call_count, 22)
        self.assertEqual(len(self.callback._cache), 0)

    def testBitcoinBypassesCache(self):
        irc = Mock()
        self.callback._request(irc, "normal")
        self.callback._request(irc, "BTC", cache_allowed=False)
        self.callback._request(irc, "BTC", cache_allowed=False)
        self.callback._request(irc, "normal")
        self.assertEqual(self.fetch.call_count, 3)

    def testFailuresAreNotCached(self):
        self.fetch.return_value = b'<queryresult success="false"/>'
        self.callback._request(Mock(), "test")
        self.callback._request(Mock(), "test")
        self.assertEqual(self.fetch.call_count, 2)

    def testSectionFilter(self):
        self.assertResponse("wolframalpha --section result 2+2", "Result :: 4")
        self.assertRegexp("wolframalpha --section unknown 2+2", "Available: Input, Result")
        self.fetch.return_value = result(pod("Decimal approximation", "3.14", "Decimal", primary=True))
        self.assertResponse('wolframalpha --section "decimal approximation" pi', "Decimal approximation :: 3.14")

    def testTableRows(self):
        data = result(pod("Table", "name | value\nalpha | 1\n\nbeta | 2"))
        pods = self.callback._pods(ElementTree.fromstring(data))
        self.assertEqual(pods[0]["text"], "name | value ; alpha | 1 ; beta | 2")

    def testFormattingAndPlainMode(self):
        settings = conf.supybot.plugins.WolframAlpha
        settings.disableANSI.setValue(False)
        self.addCleanup(settings.disableANSI.setValue, True)
        self.assertEqual(self.callback._style("4", "answer", None), "\x024\x02")
        settings.answerColor.setValue("white")
        self.addCleanup(settings.answerColor.setValue, "default")
        self.assertEqual(self.callback._style("4", "answer", None), "\x02\x03004\x03\x02")
        self.assertIn("\x0315", self.callback._style("note", "detail", None))
        settings.disableANSI.get("#plain").setValue(True)
        self.assertEqual(self.callback._style("4", "answer", "#plain"), "4")
        settings.disableANSI.setValue(True)
        irc = Mock()
        self.callback._reply(irc, "x" * 500)
        self.assertNotIn("\x0f", irc.reply.call_args.args[0])


    def testSubpodTitles(self):
        data = b'<queryresult><pod title="Solutions"><subpod title="Real"><plaintext>x = 1</plaintext></subpod><subpod title="Complex"><plaintext>x = i</plaintext></subpod></pod></queryresult>'
        pods = self.callback._pods(ElementTree.fromstring(data))
        self.assertEqual(pods[0]["text"], "Real: x = 1 | Complex: x = i")

    def _contextResult(self):
        return (b'<queryresult success="true" error="false">'
            + pod("Result", "answer", primary=True).encode()
            + b'<assumptions><assumption word="mercury" type="Clash">'
              b'<value desc="a planet" input="planet-token"/>'
              b'<value desc="a chemical element" input="element-token"/>'
              b'</assumption></assumptions>'
              b'<sources><source text="Reference" url="https://example.org/reference"/></sources>'
              b'</queryresult>')

    def testAssumptionNoteAndListing(self):
        self.fetch.return_value = self._contextResult()
        self.assertResponse("wolframalpha --shortest mercury", "mercury :: Assuming mercury → a planet | answer")
        self.assertResponse("wolframalpha --assumptions mercury", "Interpretation 1 :: mercury → a planet (current)")
        choices = self.callback._assumptions(ElementTree.fromstring(self._contextResult()))
        self.assertEqual(choices[1], ("mercury → a chemical element", "element-token"))

    def testApplyAssumption(self):
        self.fetch.side_effect = [self._contextResult(), result(pod("Result", "Hg", primary=True))]
        self.assertResponse("wolframalpha --assume 2 --shortest mercury", "mercury :: Hg")
        params = parse_qs(urlsplit(self.fetch.call_args.args[0]).query)
        self.assertEqual(params["assumption"], ["element-token"])
        self.assertEqual(self.fetch.call_count, 2)

    def testStatesAndApplyingView(self):
        data = b'<queryresult success="true"><pod title="Decimal"><subpod><plaintext>3.14</plaintext><states><state name="More digits" input="more-digits"/></states></subpod></pod></queryresult>'
        self.fetch.return_value = data
        self.assertResponse("wolframalpha --states pi", "View 1 :: Decimal → More digits")
        self.fetch.return_value = result(pod("Decimal", "3.14159", primary=True))
        self.assertResponse("wolframalpha --state 1 --shortest pi", "pi :: 3.14159")
        params = parse_qs(urlsplit(self.fetch.call_args.args[0]).query)
        self.assertEqual(params["podstate"], ["more-digits"])

    def testChoiceValidation(self):
        self.assertRegexp("wolframalpha --assume 0 pi", "at least 1")
        self.assertRegexp("wolframalpha --state -1 pi", "at least 1")
        self.fetch.assert_not_called()
        self.assertRegexp("wolframalpha --assume 99 pi", "unavailable")
        self.assertRegexp("wolframalpha --state 99 pi", "unavailable")
        self.assertEqual(self.fetch.call_count, 1)

    def testSources(self):
        self.fetch.return_value = self._contextResult()
        self.assertResponse("wolframalpha --sources mercury", "Reference :: https://example.org/reference")
        self.fetch.return_value = result(pod("Result", "4"))
        self.assertRegexp("wolframalpha --sources 2+2", "No source links")

    def testUnsafeAndLongSources(self):
        document = ElementTree.fromstring(b'<queryresult><sources><source text="Bad" url="javascript:alert(1)"/><source text="Secret" url="https://user:password@example.org/"/><source text="Broken" url="https://[broken"/></sources></queryresult>')
        irc, msg = self._conversation(channel=None)
        self.callback._inspect(irc, msg, document, {"sources": True})
        self.assertEqual(irc.reply.call_args.args[0], "No source links were supplied for this query.")
        source = ElementTree.SubElement(document.find("sources"), "source", text="Long", url="https://example.org/" + "x" * 500)
        self.callback._inspect(irc, msg, document, {"sources": True})
        self.assertEqual(irc.reply.call_args.args[0], "Long (source link too long for IRC)")

    def testWarnings(self):
        document = ElementTree.fromstring(b'<queryresult><warnings><spellcheck word="Chicag" suggestion="Chicago"/><reinterpret new="kitty"/><translation trans="weather today"/><delimiters text="Fixed parentheses."/><unknown/></warnings></queryresult>')
        notes = self.callback._notes(document, None)
        self.assertEqual(notes, ["Corrected Chicag → Chicago", "Interpreted as kitty", "Translated to weather today", "Fixed parentheses."])
        conf.supybot.plugins.WolframAlpha.showWarnings.setValue(False)
        self.addCleanup(conf.supybot.plugins.WolframAlpha.showWarnings.setValue, True)
        self.assertEqual(self.callback._notes(document, None), [])

    def testPartialResultsNotCached(self):
        data = result(pod("Result", "4", primary=True)).replace(b'<queryresult ', b'<queryresult timedout="Other" ')
        self.fetch.return_value = data
        self.assertResponse("wolframalpha --shortest test", "test :: Partial result: some calculations are unfinished. | 4")
        self.assertResponse("wolframalpha --shortest test", "test :: Partial result: some calculations are unfinished. | 4")
        self.assertEqual(self.fetch.call_count, 2)
        self.fetch.return_value = b'<queryresult parsetimedout="true"/>'
        self.assertRegexp("wolframalpha unknown", "Partial result.*No text result")


    def testReloadWithLegacyCommandGlobals(self):
        # importlib.reload retains names from the old wildcard command import.
        # A fresh-process test does not have those leftover module globals.
        from supybot import commands
        with patch.object(plugin, "any", commands.any, create=True):
            self.assertResponse("wolframalpha --shortest 2+2", "2+2 :: 4")
            data = ElementTree.fromstring(b'<queryresult timedout="Other"/>')
            self.assertTrue(self.callback._partial(data))
            self.assertRegexp("wolframalpha --sources 2+2", "No source links")


    def testMultiSectionLinkPreference(self):
        self.fetch.return_value = result(pod("First", "x" * 700), pod("Second", "y" * 700))
        conf.supybot.plugins.WolframAlpha.showLink.setValue(False)
        self.addCleanup(conf.supybot.plugins.WolframAlpha.showLink.setValue, True)
        irc, msg = self._conversation(channel=None)
        self.callback.wolframalpha(irc, msg, ["test"])
        self.assertTrue(all("https://" not in call.args[0] for call in irc.reply.call_args_list))
        irc.reset_mock()
        self.callback.wolframalpha(irc, msg, ["--link", "test"])
        from supybot import callbacks
        replies = [call.args[0] for call in irc.reply.call_args_list]
        replies += [message.args[1] for message in reversed(callbacks.ReplyIrcProxy._mores["user@host"])]
        self.assertEqual(sum(text.count("https://") for text in replies), 1)
        self.assertIn("https://", replies[-1])

    def _conversation(self, prefix="alice!user@host", channel="#test", network="testnet"):
        from supybot import ircmsgs
        irc = Mock()
        irc.network = network
        irc.getRealIrc.return_value.network = network
        irc._replyOverhead.return_value = 80
        irc.isChannel.side_effect = lambda target: str(target).startswith("#")
        irc.stripChannelPrefix.side_effect = lambda target: target
        irc.state.capabilities_ack = set()
        msg = ircmsgs.privmsg(channel or "bot", "query", prefix=prefix)
        msg.channel = channel
        return irc, msg

    def testSummaryAndPreferredSections(self):
        data = result(pod("Input", "q", "Input", "100"),
                      pod("Members", "Io | Europa", "Members", "200", True),
                      pod("Orbital properties", "long " * 200, "Orbit", "300"),
                      pod("Count", "95", "Count", "400"),
                      pod("Physical properties", "mass " * 200, "Physical", "500"))
        pods = self.callback._pods(ElementTree.fromstring(data))
        self.assertEqual([pod["title"] for pod in self.callback._summary_order(pods, None)],
                         ["Members", "Count", "Orbital properties", "Physical properties", "Input"])
        settings = conf.supybot.plugins.WolframAlpha
        settings.preferredSections.get("#preferred").setValue(["Physical properties"])
        self.assertEqual(self.callback._summary_order(pods, "#preferred")[1]["title"], "Physical properties")
        settings.outputStyle.setValue("summary")
        self.addCleanup(settings.outputStyle.setValue, "detailed")
        self.fetch.return_value = data
        self.assertResponse("wolframalpha moons", "Members :: Io | Europa")

    def testLocationAndImageRequestsUseSeparateCacheEntries(self):
        settings = conf.supybot.plugins.WolframAlpha
        settings.defaultLocation.setValue("Athens, Greece")
        self.addCleanup(settings.defaultLocation.setValue, "")
        self.assertResponse("wolframalpha --shortest 2+2", "2+2 :: 4")
        params = parse_qs(urlsplit(self.fetch.call_args.args[0]).query)
        self.assertEqual(params["location"], ["Athens, Greece"])
        self.assertResponse('wolframalpha --location "Tokyo, Japan" --shortest 2+2', "2+2 :: 4")
        self.assertEqual(parse_qs(urlsplit(self.fetch.call_args.args[0]).query)["location"], ["Tokyo, Japan"])
        self.assertResponse("wolframalpha --images 2+2", "No image links were supplied for this query.")
        self.assertEqual(parse_qs(urlsplit(self.fetch.call_args.args[0]).query)["format"], ["plaintext,image"])
        self.assertEqual(self.fetch.call_count, 3)

    def testUnitInformationAndImages(self):
        data = b'<queryresult success="true"><pod title="Distance"><subpod><plaintext>5 au</plaintext><img src="https://example.org/plot.png"/></subpod><infos><info text="Distance varies."><units><unit short="au" long="astronomical units"/></units><link text="Definition" url="https://example.org/units"/></info></infos></pod></queryresult>'
        self.fetch.return_value = data
        irc, msg = self._conversation()
        self.callback.wolframalpha(irc, msg, ["--units", "distance"])
        self.assertEqual(irc.reply.call_args.args[0], "Units — Distance :: au = astronomical units")
        irc.reset_mock()
        self.callback.wolframalpha(irc, msg, ["--info", "distance"])
        self.assertEqual(irc.reply.call_args_list[0].args[0], "Info — Distance :: Distance varies.")
        self.assertIn("Definition :: https://example.org/units", irc.reply.call_args.args[0])
        self.assertEqual(irc.reply.call_count, 2)
        irc.reset_mock()
        self.callback.wolframalpha(irc, msg, ["--images", "distance"])
        self.assertEqual(irc.reply.call_args.args[0], "Distance :: https://example.org/plot.png")
        document = ElementTree.fromstring(data)
        notes = self.callback._information(document)
        self.assertEqual(notes[1]["text"], "Info — Distance :: Definition :: https://example.org/units")
        self.assertTrue(self.callback._partial(ElementTree.fromstring(b'<queryresult><pod async="https://example.org/later"/></queryresult>')))


    def testUpgradeGlobalSettingsWithoutRestart(self):
        from importlib import reload
        from . import config as plugin_config
        settings = conf.supybot.plugins.WolframAlpha
        node = settings.maxOutput
        node.setValue(6)
        self.addCleanup(node.setValue, 4)
        # Recreate the flags on the legacy global setting in the running bot.
        node._supplyDefault = False
        node._channelValue = False
        node._networkValue = False
        reload(plugin_config)
        self.assertIs(settings.maxOutput, node)
        self.assertEqual(node(), 6)
        self.assertTrue(node._channelValue)
        self.assertTrue(node._networkValue)
        self.assertTrue(node._supplyDefault)
        self.assertEqual(node.get("#upgraded")(), 6)
        node.get("#upgraded").setValue(2)
        self.assertEqual(self.callback._section_count("#upgraded"), 2)
        self.assertEqual(node(), 6)

    def testLabeledLinesAndOneLink(self):
        irc, msg = self._conversation()
        text = "distribution " * 200
        records = [self.callback._record("Orbit", text), self.callback._record("Mass", text)]
        self.callback._send_result(irc, msg, records, "moons of Jupiter")
        self.assertEqual(irc.reply.call_count, 4)
        from supybot import callbacks
        saved = callbacks.ReplyIrcProxy._mores["user@host"]
        responses = [call.args[0] for call in irc.reply.call_args_list]
        responses += [message.args[1] for message in reversed(saved)]
        response = " ".join(responses)
        self.assertEqual(response.count("distribution"), 400)
        self.assertEqual(response.count("https://www.wolframalpha.com/"), 1)
        self.assertNotIn("…", response)
        self.assertNotIn("wamore", response)
        self.assertIn("more messages", responses[3])

    def _assertNativeMore(self):
        settings = conf.supybot.reply.mores
        old_length, old_maximum = settings.length(), settings.maximum()
        settings.length.setValue(150)
        settings.maximum.setValue(100)
        self.addCleanup(settings.length.setValue, old_length)
        self.addCleanup(settings.maximum.setValue, old_maximum)
        more_count = conf.supybot.plugins.Misc.mores
        old_more_count = more_count()
        more_count.setValue(2)
        self.addCleanup(more_count.setValue, old_more_count)
        self.fetch.return_value = result(
            pod("Orbit", " ; ".join("orbit%03d distribution" % n for n in range(40)), "Orbit", "100"),
            pod("Mass", " ; ".join("mass%03d distribution" % n for n in range(40)), "Mass", "200"))
        response = self._feedMsg("wolframalpha moons of Jupiter")
        self.assertIsNotNone(response)
        responses = [response.args[1]]
        for _ in range(3):
            response = self.irc.takeMsg()
            self.assertIsNotNone(response)
            responses.append(response.args[1])
        self.assertIsNone(self.irc.takeMsg())
        self.assertIn("more messages", responses[-1])
        from supybot import callbacks
        pending = callbacks.ReplyIrcProxy._mores
        for _ in range(100):
            remaining = len(pending.get("user@host.domain.tld", []))
            if not remaining:
                break
            response = self._feedMsg("more")
            self.assertIsNotNone(response)
            responses.append(response.args[1])
            if remaining >= 2:
                response = self.irc.takeMsg()
                self.assertIsNotNone(response)
                responses.append(response.args[1])
            self.assertIsNone(self.irc.takeMsg())
            self.assertEqual(len(pending["user@host.domain.tld"]), max(0, remaining - 2))
        else:
            self.fail("Native more did not finish")
        text = " ".join(responses)
        import re
        self.assertEqual(re.findall(r"orbit\d+", text), ["orbit%03d" % n for n in range(40)])
        self.assertEqual(re.findall(r"mass\d+", text), ["mass%03d" % n for n in range(40)])
        self.assertEqual(text.count("distribution"), 80)
        self.assertEqual(text.count("https://www.wolframalpha.com/"), 1)
        self.assertNotIn("wamore", text)
        self.assertNotIn("use more", text)
        self.assertEqual(self.fetch.call_count, 1)

    def testUsesNativeMoreSettingsAndNoCustomCommand(self):
        self.assertFalse(self.callback.isCommand("wamore"))
        self.assertFalse(self.callback.isCommand("more"))
        self.assertFalse(hasattr(self.callback, "_pending"))
        irc, msg = self._conversation()
        conf.supybot.plugins.WolframAlpha.showLink.setValue(False)
        self.addCleanup(conf.supybot.plugins.WolframAlpha.showLink.setValue, True)
        self.callback._send_result(irc, msg, [self.callback._record("", "é" * 2000)])
        from supybot import callbacks
        saved = callbacks.ReplyIrcProxy._mores["user@host"]
        text = " ".join([call.args[0] for call in irc.reply.call_args_list] + [message.args[1] for message in saved])
        self.assertEqual(text.count("é"), 2000)

    def testChannelUnitsAndLocationDefaults(self):
        settings = conf.supybot.plugins.WolframAlpha
        settings.maxOutput.get("#small").setValue(1)
        settings.useImperial.get("#small").setValue(False)
        settings.defaultLocation.get("#small").setValue("Athens")
        settings.showUnits.get("#small").setValue(True)
        self.assertEqual(self.callback._section_count("#small"), 1)
        self.assertEqual(self.callback._section_count("#other"), 4)
        irc, msg = self._conversation(channel="#small")
        self.fetch.return_value = b'<queryresult success="true"><pod title="Result" primary="true"><subpod><plaintext>5 au</plaintext></subpod><infos><info><units><unit short="au" long="astronomical units"/></units></info></infos></pod></queryresult>'
        self.callback.wolframalpha(irc, msg, ["distance"])
        params = parse_qs(urlsplit(self.fetch.call_args.args[0]).query)
        self.assertEqual(params["units"], ["metric"])
        self.assertEqual(params["location"], ["Athens"])
        self.assertEqual(irc.reply.call_count, 1)
        from supybot import callbacks
        pending = callbacks.ReplyIrcProxy._mores["user@host"]
        self.assertEqual(len(pending), 1)
        self.assertIn("Units — Result :: au = astronomical units", pending[0].args[1])

    def testNativeMoreReturnsAllSelectedSections(self):
        self._assertNativeMore()

    def testLabeledUnicodeAndFormattingStayWithinBudget(self):
        settings = conf.supybot.plugins.WolframAlpha
        settings.disableANSI.setValue(False)
        settings.answerColor.setValue("white")
        self.addCleanup(settings.disableANSI.setValue, True)
        self.addCleanup(settings.answerColor.setValue, "default")
        record = self.callback._record("Τιμή", " ; ".join("κόσμος 🌍" for _ in range(40)), "answer")
        lines = list(self.callback._labeled_lines(record, None, 80))
        self.assertTrue(all(len(line.encode()) <= 80 for line in lines))
        from supybot import ircutils
        plain = [ircutils.stripFormatting(line) for line in lines]
        self.assertTrue(all(line.startswith("Τιμή :: ") for line in plain))
        self.assertEqual(sum(line.count("κόσμος") for line in plain), 40)
        self.assertEqual(sum(line.count("🌍") for line in plain), 40)
        self.assertTrue(all("\x02" in line for line in lines))

    def testNativeBufferMaximumHasExplicitNotice(self):
        setting = conf.supybot.reply.mores.maximum
        original = setting()
        setting.setValue(6)
        self.addCleanup(setting.setValue, original)
        irc, msg = self._conversation()
        self.callback._send_result(irc, msg, [self.callback._record("Facts", "distribution " * 1000)], "facts")
        from supybot import callbacks
        pending = callbacks.ReplyIrcProxy._mores["user@host"]
        self.assertEqual(irc.reply.call_count, 4)
        self.assertEqual(len(pending), 2)
        self.assertIn("exceeds the bot's more limit", pending[0].args[1])


class WolframAlphaChannelMoreTestCase(ChannelPluginTestCase):
    plugins = ("WolframAlpha",)
    config = WolframAlphaTestCase.config

    def testNativeMoreInChannel(self):
        with patch.object(plugin.utils.web, "getUrl") as fetch:
            self.fetch = fetch
            WolframAlphaTestCase._assertNativeMore(self)
