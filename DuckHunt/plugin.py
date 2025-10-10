# Copyright (c) 2012, Matthias Meusburger
# Copyright (c) 2020, oddluck <oddluck@riseup.net>
# Copyleft  (ↄ) 2023: Stathis Xantinidis (https://github.com/spithash)
#
# Modifications 2025: integrated befriending fully, autosave, huntscore, totals, etc.
# All wrongs reserved.
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

from supybot.commands import *
import supybot.plugins as plugins
import supybot.callbacks as callbacks
import supybot.schedule as schedule
import supybot.ircdb as ircdb
import supybot.ircmsgs as ircmsgs
import supybot.log as log
import supybot.conf as conf
from operator import itemgetter

import threading, random, pickle, os, time, datetime


class DuckHunt(callbacks.Plugin):
    """
    A DuckHunt game for supybot. Use the "starthunt" command to start a game.
    The bot will randomly launch ducks. Whenever a duck is launched, the first
    person to use the "bang" command wins a point. Same goes for "bef" which is befriending the ducks.
    """

    threaded = True

    # Those parameters are per-channel parameters
    started = {}  # Has the hunt started?
    duck = {}  # Is there currently a duck to shoot?
    shoots = {}  # Number of successful shoots in a hunt
    scores = {}  # Scores for the current hunt (shooting)
    times = {}  # Elapsed time since the last duck was launched (for bang)
    channelscores = {}  # Saved scores for the channel (persistent)
    toptimes = {}  # Times for the current hunt (best times)
    channeltimes = {}  # Saved times for the channel (persistent)
    worsttimes = {}  # Worst times for the current hunt
    channelworsttimes = {}  # Saved worst times for the channel (persistent)
    averagetime = {}  # Average shooting time for the current hunt
    fridayMode = {}  # Are we on friday mode? (automatic)
    manualFriday = {}  # Are we on friday mode? (manual)
    missprobability = {}  # Probability to miss a duck when shooting
    week = {}  # Scores for the week (in-memory)
    channelweek = {}  # Saved scores for the week (persistent)
    leader = {}  # Who is the leader for the week?
    reloading = {}  # Who is currently reloading?
    reloadtime = {}  # Time to reload after shooting (in seconds)
    reloadcount = {}  # Number of shots fired while reloading

    # New: befriending stats
    friends = {}  # current hunt friendship counts (per-channel)
    channelfriends = {}  # saved friendship totals per channel (persistent)

    # Enable autosave so progress is flushed to disk immediately (protect against crashes)
    autosave = True  # Set to False to keep old behavior (accumulate only at end-of-hunt)

    # Does a duck needs to be launched?
    lastSpoke = {}
    minthrottle = {}
    maxthrottle = {}
    throttle = {}

    # Where to save scores?
    fileprefix = "DuckHunt_"
    path = conf.supybot.directories.data

    # Enable the 'dbg' command, which launch a duck, if true
    debug = 0

    # Other params
    perfectbonus = 5  # How many extra-points are given when someone does a perfect hunt?
    toplist = 15  # How many high{scores|times} are displayed by default?
    dow = int(time.strftime("%u"))  # Day of week
    woy = int(time.strftime("%V"))  # Week of year
    year = time.strftime("%Y")
    dayname = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]

    # ---------------------------
    # Data persistence utilities
    # ---------------------------
    def _ensure_channel_files_exist(self, channel):
        """
        Ensure the persistent dictionaries exist (after reading).
        """
        if channel not in self.channelscores:
            self.channelscores[channel] = {}
        if channel not in self.channeltimes:
            self.channeltimes[channel] = {}
        if channel not in self.channelworsttimes:
            self.channelworsttimes[channel] = {}
        if channel not in self.channelweek:
            self.channelweek[channel] = {}
        if channel not in self.channelfriends:
            self.channelfriends[channel] = {}

    def _calc_scores(self, channel):
        """
        Adds new scores and times to the already saved ones.

        If autosave is enabled, scores and friendships are already persistent,
        so we skip re-adding them to avoid double-counting.
        We still handle times and week merging.
        """
        # Ensure persistent dicts exist
        try:
            self._read_scores(channel)
        except Exception:
            pass

        # If autosave is OFF, we need to aggregate current hunt scores into persistent totals.
        if not self.autosave:
            # scores
            for player, value in self.scores.get(channel, {}).items():
                if player not in self.channelscores[channel]:
                    self.channelscores[channel][player] = value
                else:
                    self.channelscores[channel][player] += value

            # friendships
            if channel not in self.channelfriends:
                self.channelfriends[channel] = {}
            for player, val in self.friends.get(channel, {}).items():
                if player not in self.channelfriends[channel]:
                    self.channelfriends[channel][player] = val
                else:
                    self.channelfriends[channel][player] += val

        # times (best)
        for player, value in self.toptimes.get(channel, {}).items():
            if player not in self.channeltimes[channel]:
                self.channeltimes[channel][player] = value
            else:
                # Keep minimum (best) time
                if value < self.channeltimes[channel][player]:
                    self.channeltimes[channel][player] = value

        # worst times (keep maximum)
        for player, value in self.worsttimes.get(channel, {}).items():
            if player not in self.channelworsttimes[channel]:
                self.channelworsttimes[channel][player] = value
            else:
                if value > self.channelworsttimes[channel][player]:
                    self.channelworsttimes[channel][player] = value

        # week scores: always add current hunt scores to week summary.
        for player, value in self.scores.get(channel, {}).items():
            if channel not in self.channelweek:
                self.channelweek[channel] = {}
            if self.woy not in self.channelweek[channel]:
                self.channelweek[channel][self.woy] = {}
            if self.dow not in self.channelweek[channel][self.woy]:
                self.channelweek[channel][self.woy][self.dow] = {}
            if player not in self.channelweek[channel][self.woy][self.dow]:
                self.channelweek[channel][self.woy][self.dow][player] = value
            else:
                self.channelweek[channel][self.woy][self.dow][player] += value

    def _write_scores(self, channel):
        """
        Write persistent structures to disk. Robust to missing dicts.
        """
        filename_base = self.path.dirize(self.fileprefix + channel)

        # Ensure we have dicts to write
        self._ensure_channel_files_exist(channel)

        try:
            with open(filename_base + ".scores", "wb") as outputfile:
                pickle.dump(self.channelscores[channel], outputfile)
        except Exception:
            pass

        try:
            with open(filename_base + ".times", "wb") as outputfile:
                pickle.dump(self.channeltimes[channel], outputfile)
        except Exception:
            pass

        try:
            with open(filename_base + ".worsttimes", "wb") as outputfile:
                pickle.dump(self.channelworsttimes[channel], outputfile)
        except Exception:
            pass

        try:
            with open(filename_base + self.year + ".weekscores", "wb") as outputfile:
                pickle.dump(self.channelweek[channel], outputfile)
        except Exception:
            pass

        try:
            with open(filename_base + ".friends", "wb") as outputfile:
                pickle.dump(self.channelfriends[channel], outputfile)
        except Exception:
            pass

    def _read_scores(self, channel):
        """
        Reads scores and times from disk
        """
        filename = self.path.dirize(self.fileprefix + channel)
        # scores
        try:
            if not self.channelscores.get(channel):
                if os.path.isfile(filename + ".scores"):
                    with open(filename + ".scores", "rb") as inputfile:
                        self.channelscores[channel] = pickle.load(inputfile)
                else:
                    self.channelscores[channel] = {}
        except Exception:
            self.channelscores[channel] = {}

        # times
        try:
            if not self.channeltimes.get(channel):
                if os.path.isfile(filename + ".times"):
                    with open(filename + ".times", "rb") as inputfile:
                        self.channeltimes[channel] = pickle.load(inputfile)
                else:
                    self.channeltimes[channel] = {}
        except Exception:
            self.channeltimes[channel] = {}

        # worst times
        try:
            if not self.channelworsttimes.get(channel):
                if os.path.isfile(filename + ".worsttimes"):
                    with open(filename + ".worsttimes", "rb") as inputfile:
                        self.channelworsttimes[channel] = pickle.load(inputfile)
                else:
                    self.channelworsttimes[channel] = {}
        except Exception:
            self.channelworsttimes[channel] = {}

        # week scores
        try:
            if not self.channelweek.get(channel):
                if os.path.isfile(filename + self.year + ".weekscores"):
                    with open(filename + self.year + ".weekscores", "rb") as inputfile:
                        self.channelweek[channel] = pickle.load(inputfile)
                else:
                    self.channelweek[channel] = {}
        except Exception:
            self.channelweek[channel] = {}

        # friendships
        try:
            if not self.channelfriends.get(channel):
                if os.path.isfile(filename + ".friends"):
                    with open(filename + ".friends", "rb") as inputfile:
                        self.channelfriends[channel] = pickle.load(inputfile)
                else:
                    self.channelfriends[channel] = {}
        except Exception:
            self.channelfriends[channel] = {}

    # ---------------------------
    # Initialization helpers
    # ---------------------------
    def _initdayweekyear(self, channel):
        self.dow = int(time.strftime("%u"))  # Day of week
        self.woy = int(time.strftime("%V"))  # Week of year
        year = time.strftime("%Y")

        # Init week scores
        try:
            self.channelweek[channel]
        except:
            self.channelweek[channel] = {}
        try:
            self.channelweek[channel][self.woy]
        except:
            self.channelweek[channel][self.woy] = {}
        try:
            self.channelweek[channel][self.woy][self.dow]
        except:
            self.channelweek[channel][self.woy][self.dow] = {}

    def _initthrottle(self, irc, msg, args, channel):

        self._initdayweekyear(channel)

        if not self.leader.get(channel):
            self.leader[channel] = None

        # autoFriday?
        if not self.fridayMode.get(channel):
            self.fridayMode[channel] = False

        if not self.manualFriday.get(channel):
            self.manualFriday[channel] = False

        if self.registryValue("autoFriday", channel) == True:
            if (
                int(time.strftime("%w")) == 5
                and int(time.strftime("%H")) > 8
                and int(time.strftime("%H")) < 17
            ):
                self.fridayMode[channel] = True
            else:
                self.fridayMode[channel] = False

        # Miss probability
        if self.registryValue("missProbability", channel):
            self.missprobability[channel] = self.registryValue(
                "missProbability", channel
            )
        else:
            self.missprobability[channel] = 0.2

        # Reload time
        if self.registryValue("reloadTime", channel):
            self.reloadtime[channel] = self.registryValue("reloadTime", channel)
        else:
            self.reloadtime[channel] = 5

        if self.fridayMode[channel] == False and self.manualFriday[channel] == False:
            # Init min throttle[currentChannel] and max throttle[currentChannel]
            if self.registryValue("minthrottle", channel):
                self.minthrottle[channel] = self.registryValue("minthrottle", channel)
            else:
                self.minthrottle[channel] = 30

            if self.registryValue("maxthrottle", channel):
                self.maxthrottle[channel] = self.registryValue("maxthrottle", channel)
            else:
                self.maxthrottle[channel] = 300

        else:
            self.minthrottle[channel] = 3
            self.maxthrottle[channel] = 60

        self.throttle[channel] = random.randint(
            self.minthrottle[channel], self.maxthrottle[channel]
        )

    # ---------------------------
    # Game control
    # ---------------------------
    def starthunt(self, irc, msg, args):
        """
        Starts the hunt
        """

        currentChannel = msg.args[0]
        if irc.isChannel(currentChannel):

            if self.started.get(currentChannel) == True:
                irc.reply("✔️ There is already a hunt right now!")
            else:

                # First of all, let's read the score if needed
                self._read_scores(currentChannel)

                self._initthrottle(irc, msg, args, currentChannel)

                # Init saved scores
                try:
                    self.channelscores[currentChannel]
                except:
                    self.channelscores[currentChannel] = {}

                # Init saved times
                try:
                    self.channeltimes[currentChannel]
                except:
                    self.channeltimes[currentChannel] = {}

                # Init saved times
                try:
                    self.channelworsttimes[currentChannel]
                except:
                    self.channelworsttimes[currentChannel] = {}

                # Init times
                self.toptimes[currentChannel] = {}
                self.worsttimes[currentChannel] = {}

                # Init bangdelay
                self.times[currentChannel] = False

                # Init lastSpoke
                self.lastSpoke[currentChannel] = time.time()

                # Reinit current hunt scores
                if self.scores.get(currentChannel):
                    self.scores[currentChannel] = {}

                # Reinit reloading
                self.reloading[currentChannel] = {}

                # Reinit reloadcount
                self.reloadcount[currentChannel] = {}

                # No duck launched
                self.duck[currentChannel] = False

                # Hunt started
                self.started[currentChannel] = True

                # Init shoots
                self.shoots[currentChannel] = 0

                # Init averagetime
                self.averagetime[currentChannel] = 0

                # Init schedule

                # First of all, stop the scheduler if it was still running
                try:
                    schedule.removeEvent("DuckHunt_" + currentChannel)
                except KeyError:
                    pass

                # Then restart it
                def myEventCaller():
                    self._launchEvent(irc, msg)

                try:
                    schedule.addPeriodicEvent(
                        myEventCaller, 5, "DuckHunt_" + currentChannel, False
                    )
                except AssertionError:
                    pass

                # Init friendships for this hunt
                try:
                    self.friends[currentChannel]
                except:
                    self.friends[currentChannel] = {}
                try:
                    self.channelfriends[currentChannel]
                except:
                    # don't overwrite if it was loaded from disk
                    self.channelfriends[currentChannel] = {}

                irc.reply("✔️ The hunt starts now! 🦆🦆🦆", prefixNick=False)
        else:
            irc.error("You have to be on a channel")

    starthunt = wrap(starthunt)

    def _launchEvent(self, irc, msg):
        currentChannel = msg.args[0]
        now = time.time()
        if irc.isChannel(currentChannel):
            if self.started.get(currentChannel) == True:
                if self.duck[currentChannel] == False:
                    if (
                        now
                        > self.lastSpoke[currentChannel] + self.throttle[currentChannel]
                    ):
                        self._launch(irc, msg, "")

    def stophunt(self, irc, msg, args):
        """
        Stops the current hunt
        """

        currentChannel = msg.args[0]
        if irc.isChannel(currentChannel):
            if self.started.get(currentChannel) == True:
                self._end(irc, msg, args)
            else:
                irc.reply("❗ Nothing to stop: there's no hunt right now.")
            # If someone uses the stop command,
            # we stop the scheduler, even if autoRestart is enabled
            try:
                schedule.removeEvent("DuckHunt_" + currentChannel)
            except:
                pass
        else:
            irc.error("You have to be on a channel")

    stophunt = wrap(stophunt)

    def fridaymode(self, irc, msg, args, channel, status):
        """
        [<status>]
        Enable/disable friday mode! (there are lots of ducks on friday :))
        """
        if irc.isChannel(channel):

            if status == "status":
                irc.reply(
                    "Manual friday mode for "
                    + channel
                    + " is "
                    + str(self.manualFriday.get(channel))
                )
                irc.reply(
                    "Auto friday mode for "
                    + channel
                    + " is "
                    + str(self.fridayMode.get(channel))
                )
            else:
                if (
                    self.manualFriday.get(channel) == None
                    or self.manualFriday[channel] == False
                ):
                    self.manualFriday[channel] = True
                    irc.reply(
                        "Friday mode is now enabled! Shoot alllllllllllll the ducks!"
                    )
                else:
                    self.manualFriday[channel] = False
                    irc.reply("Friday mode is now disabled.")

            self._initthrottle(irc, msg, args, channel)
        else:
            irc.error("You have to be on a channel")

    fridaymode = wrap(fridaymode, ["channel", "admin", optional("anything")])

    def launched(self, irc, msg, args):
        """
        Is there a duck right now?
        """

        currentChannel = msg.args[0]
        if irc.isChannel(currentChannel):
            if self.started.get(currentChannel) == True:
                if self.duck[currentChannel] == True:
                    irc.reply(
                        "✔️ There is currently a duck! You can shoot it with the 'bang'"
                        " command"
                    )
                else:
                    irc.reply(
                        "❗ There is no duck right now! Wait for one to be launched!"
                    )
            else:
                irc.reply(
                    "❗ There is no hunt right now! You can start a hunt with the 'starthunt'"
                    " command"
                )
        else:
            irc.error("You have to be on a channel")

    launched = wrap(launched)

    # ---------------------------
    # Score & listing commands
    # ---------------------------
    def score(self, irc, msg, args, nick):
        """
        <nick>

        Shows the persistent score for a given nick (total ducks shot in this channel)
        """
        currentChannel = msg.args[0]
        if irc.isChannel(currentChannel):
            self._read_scores(currentChannel)
            try:
                self.channelscores[currentChannel]
            except:
                self.channelscores[currentChannel] = {}

            try:
                irc.reply(self.channelscores[currentChannel][nick])
            except:
                irc.reply("There is no persistent score for %s on %s" % (nick, currentChannel))
        else:
            irc.error("You have to be on a channel")

    score = wrap(score, ["nick"])

    def huntscore(self, irc, msg, args, nick):
        """
        <nick>

        Shows the CURRENT-HUNT scores for a given nick (both shooting and befriending).
        Useful to check your live score during a hunt.
        """
        currentChannel = msg.args[0]
        if irc.isChannel(currentChannel):
            if not self.started.get(currentChannel):
                irc.reply("❗ There is no hunt right now!")
                return

            # current in-memory scores
            shoot = 0
            bef = 0
            try:
                shoot = self.scores.get(currentChannel, {}).get(nick, 0)
            except:
                shoot = 0
            try:
                bef = self.friends.get(currentChannel, {}).get(nick, 0)
            except:
                bef = 0

            irc.reply(
                "%s — current hunt: shooting: %i | befriending: %i"
                % (nick, shoot, bef)
            )
        else:
            irc.error("You have to be on a channel")

    huntscore = wrap(huntscore, ["nick"])

    def mergescores(self, irc, msg, args, channel, nickto, nickfrom):
        """
        [<channel>] <nickto> <nickfrom>
        nickto gets the points of nickfrom and nickfrom is removed from the scorelist
        """
        if irc.isChannel(channel):
            self._read_scores(channel)

            # Total scores
            try:
                self.channelscores[channel][nickto] += self.channelscores[channel][
                    nickfrom
                ]
                del self.channelscores[channel][nickfrom]
                self._write_scores(channel)
                irc.reply("Total scores merged")

            except:
                irc.error("Can't merge total scores")

            # Day scores
            try:
                self._initdayweekyear(channel)
                day = self.dow
                week = self.woy

                try:
                    self.channelweek[channel][week][day][nickto] += self.channelweek[
                        channel
                    ][week][day][nickfrom]
                except:
                    self.channelweek[channel][week][day][nickto] = self.channelweek[
                        channel
                    ][week][day][nickfrom]

                del self.channelweek[channel][week][day][nickfrom]
                self._write_scores(channel)
                irc.reply("Day scores merged")

            except:
                irc.error("Can't merge day scores")

        else:
            irc.error("You have to be on a channel")

    mergescores = wrap(mergescores, ["channel", "nick", "nick", "admin"])

    def mergetimes(self, irc, msg, args, channel, nickto, nickfrom):
        """
        [<channel>] <nickto> <nickfrom>
        nickto gets the best time of nickfrom if nickfrom time is better than nickto time, and nickfrom is removed from the timelist. Also works with worst times.
        """
        if irc.isChannel(channel):
            try:
                self._read_scores(channel)

                # Merge best times
                if (
                    self.channeltimes[channel][nickfrom]
                    < self.channeltimes[channel][nickto]
                ):
                    self.channeltimes[channel][nickto] = self.channeltimes[channel][
                        nickfrom
                    ]
                del self.channeltimes[channel][nickfrom]

                # Merge worst times
                if (
                    self.channelworsttimes[channel][nickfrom]
                    > self.channelworsttimes[channel][nickto]
                ):
                    self.channelworsttimes[channel][nickto] = self.channelworsttimes[
                        channel
                    ][nickfrom]
                del self.channelworsttimes[channel][nickfrom]

                self._write_scores(channel)

                irc.replySuccess()

            except:
                irc.replyError()

        else:
            irc.error("You have to be on a channel")

    mergetimes = wrap(mergetimes, ["channel", "nick", "nick", "admin"])

    def rmtime(self, irc, msg, args, channel, nick):
        """
        [<channel>] <nick>
        Remove <nick>'s best time
        """
        if irc.isChannel(channel):
            self._read_scores(channel)
            try:
                del self.channeltimes[channel][nick]
                self._write_scores(channel)
                irc.replySuccess()
            except Exception:
                irc.replyError()

        else:
            irc.error("Are you sure " + str(channel) + " is a channel?")

    rmtime = wrap(rmtime, ["channel", "nick", "admin"])

    def rmscore(self, irc, msg, args, channel, nick):
        """
        [<channel>] <nick>
        Remove <nick>'s score
        """
        if irc.isChannel(channel):
            try:
                self._read_scores(channel)
                del self.channelscores[channel][nick]
                self._write_scores(channel)
                irc.replySuccess()

            except:
                irc.replyError()

        else:
            irc.error("Are you sure this is a channel?")

    rmscore = wrap(rmscore, ["channel", "nick", "admin"])

    def dayscores(self, irc, msg, args, channel):
        """
        [<channel>]
        Shows the score list of the day for <channel>.
        """

        if irc.isChannel(channel):

            self._read_scores(channel)
            self._initdayweekyear(channel)
            day = self.dow
            week = self.woy

            if self.channelweek.get(channel):
                if self.channelweek[channel].get(week):
                    if self.channelweek[channel][week].get(day):
                        # Getting all scores, to get the winner of the week
                        msgstring = ""
                        scores = sorted(
                            iter(self.channelweek[channel][week][day].items()),
                            key=itemgetter(1),
                            reverse=True,
                        )
                        for item in scores:
                            msgstring += "({0}: {1}) ".format(item[0], str(item[1]))

                        if msgstring != "":
                            irc.reply("Scores for today:")
                            irc.reply(msgstring)
                        else:
                            irc.reply("❗ There aren't any day scores for today yet.")
                    else:
                        irc.reply("❗ There aren't any day scores for today yet.")
                else:
                    irc.reply("❗ There aren't any day scores for today yet.")
            else:
                irc.reply("❗ There aren't any day scores for this channel yet.")
        else:
            irc.reply("Are you sure this is a channel?")

    dayscores = wrap(dayscores, ["channel"])

    def weekscores(self, irc, msg, args, week, nick, channel):
        """
        [<week>] [<nick>] [<channel>]
        Shows the score list of the week for <channel>. If <nick> is provided, it will only show <nick>'s scores.
        """

        if irc.isChannel(channel):

            self._read_scores(channel)
            weekscores = {}

            if not week:
                week = self.woy

            if self.channelweek.get(channel):
                if self.channelweek[channel].get(week):
                    # Showing the winner for each day
                    if not nick:
                        msgstring = ""
                        # for each day of week
                        for i in (1, 2, 3, 4, 5, 6, 7):
                            if self.channelweek[channel][week].get(i):
                                # Getting winner of the day
                                winnernick, winnerscore = max(
                                    iter(self.channelweek[channel][week][i].items()),
                                    key=lambda k_v: (k_v[1], k_v[0]),
                                )
                                msgstring += "{0}: ({1}: {2}) ".format(
                                    self.dayname[i - 1], winnernick, str(winnerscore)
                                )

                        # Getting all scores, to get the winner of the week
                        for i, players in self.channelweek[channel][week].items():
                            for player, value in players.items():
                                weekscores.setdefault(player, 0)
                                weekscores[player] += value

                        if msgstring != "":
                            irc.reply("Scores for week " + str(week) + ":")
                            irc.reply(msgstring)
                            # Who's the winner at this point?
                            winnernick, winnerscore = max(
                                iter(weekscores.items()),
                                key=lambda k_v1: (k_v1[1], k_v1[0]),
                            )
                            irc.reply(
                                "🏆 Leader: %s with %i points."
                                % (winnernick, winnerscore)
                            )

                        else:
                            irc.reply("❗ There aren't any week scores for this week yet.")
                    else:
                        # Showing the scores of <nick>
                        msgstring = ""
                        total = 0
                        for i in (1, 2, 3, 4, 5, 6, 7):
                            if self.channelweek[channel][week].get(i):
                                if self.channelweek[channel][week][i].get(nick):
                                    msgstring += "({0}: {1}) ".format(
                                        self.dayname[i - 1],
                                        str(
                                            self.channelweek[channel][week][i].get(nick)
                                        ),
                                    )
                                    total += self.channelweek[channel][week][i].get(
                                        nick
                                    )

                        if msgstring != "":
                            irc.reply(nick + " scores for week " + str(self.woy) + ":")
                            irc.reply(msgstring)
                            irc.reply("Total: " + str(total) + " points.")
                        else:
                            irc.reply("❗ There aren't any week scores for this nick.")

                else:
                    irc.reply("❗ There aren't any week scores for this week yet.")
            else:
                irc.reply("❗ There aren't any week scores for this channel yet.")
        else:
            irc.reply("❗ Are you sure this is a channel?")

    weekscores = wrap(weekscores, [optional("int"), optional("nick"), "channel"])

    def listscores(self, irc, msg, args, size, channel):
        """
        [<size>] [<channel>]
        Shows the <size>-sized persistent kill score list for <channel> and also shows persistent friendship top list.
        """

        if irc.isChannel(channel):
            try:
                self.channelscores[channel]
            except:
                self.channelscores[channel] = {}

            self._read_scores(channel)

            # How many results do we display?
            if not size:
                listsize = self.toplist
            else:
                listsize = size

            # Sort the scores (reversed: the higher the better)
            scores = sorted(
                iter(self.channelscores[channel].items()),
                key=itemgetter(1),
                reverse=True,
            )
            del scores[listsize:]

            msgstring = ""
            for item in scores:
                msgstring += "({0}: {1}) ".format(item[0], str(item[1]))
            if msgstring != "":
                irc.reply(
                    "🦆 ~ DuckHunt top-"
                    + str(listsize)
                    + " scores 🏆🏆🏆 for "
                    + channel
                    + " ~ 🦆"
                )
                irc.reply(msgstring)
            else:
                irc.reply("There aren't any scores for this channel yet.")

            # Also show persistent friendship leaderboard
            try:
                friendsdict = self.channelfriends.get(channel, {})
            except:
                friendsdict = {}

            friends = sorted(iter(friendsdict.items()), key=itemgetter(1), reverse=True)
            del friends[listsize:]

            msgstring = ""
            for item in friends:
                msgstring += "({0}: {1}) ".format(item[0], str(item[1]))
            if msgstring != "":
                irc.reply(
                    "🦆❤️ ~ DuckHunt top-"
                    + str(listsize)
                    + " duck friends for "
                    + channel
                    + " ~ 🦆"
                )
                irc.reply(msgstring)
            else:
                # If there are no friendship records yet, be silent about it (or inform)
                irc.reply("There aren't any friendship records for this channel yet.")
        else:
            irc.reply("Are you sure this is a channel?")

    listscores = wrap(listscores, [optional("int"), "channel"])

    def total(self, irc, msg, args, channel):
        """
        Shows the total amount of ducks shot in <channel> (or in the current channel if no channel is given)
        """
        if irc.isChannel(channel):
            self._read_scores(channel)
            if self.channelscores.get(channel):
                scores = self.channelscores[channel]
                total = 0
                for player, value in scores.items():
                    total += value
                irc.reply(str(total) + " 🦆 ducks have been shot in " + channel + "!")
            else:
                irc.reply("There are no scores for this channel yet")

            # Also show total befriended ducks (persistent)
            try:
                friends = self.channelfriends.get(channel, {})
                total_bef = sum(friends.values()) if friends else 0
            except:
                total_bef = 0

            irc.reply(str(total_bef) + " 🦆 ducks have been befriended in " + channel + "!")
        else:
            irc.error("Are you sure this is a channel?")

    total = wrap(total, ["channel"])

    def listtimes(self, irc, msg, args, size, channel):
        """
        [<size>] [<channel>]
        Shows the <size>-sized time list for <channel> (or for the current channel if no channel is given)
        """

        if irc.isChannel(channel):
            self._read_scores(channel)

            try:
                self.channeltimes[channel]
            except:
                self.channeltimes[channel] = {}

            try:
                self.channelworsttimes[channel]
            except:
                self.channelworsttimes[channel] = {}

            # How many results do we display?
            if not size:
                listsize = self.toplist
            else:
                listsize = size

            # Sort the times (not reversed: the lower the better)
            times = sorted(
                iter(self.channeltimes[channel].items()),
                key=itemgetter(1),
                reverse=False,
            )
            del times[listsize:]

            msgstring = ""
            for item in times:
                msgstring += "({0}: {1}) ".format(item[0], str(round(item[1], 2)))
            if msgstring != "":
                irc.reply(
                    "🦆 ~ DuckHunt top-"
                    + str(listsize)
                    + " fastest times 🕒 for "
                    + channel
                    + " ~ 🦆"
                )
                irc.reply(msgstring)
            else:
                irc.reply("There aren't any best times for this channel yet.")

            times = sorted(
                iter(self.channelworsttimes[channel].items()),
                key=itemgetter(1),
                reverse=True,
            )
            del times[listsize:]

            msgstring = ""
            for item in times:
                roundseconds = round(item[1])
                delta = datetime.timedelta(seconds=roundseconds)
                msgstring += "({0}: {1}) ".format(item[0], str(delta))
            if msgstring != "":
                irc.reply(
                    "🦆 ~ DuckHunt top-"
                    + str(listsize)
                    + " longest times 🕒 for "
                    + channel
                    + " ~ 🦆"
                )
                irc.reply(msgstring)
            else:
                irc.reply("❗ There aren't any longest times for this channel yet.")

        else:
            irc.reply("Are you sure this is a channel?")

    listtimes = wrap(listtimes, [optional("int"), "channel"])

    def dbg(self, irc, msg, args):
        """
        This is a debug command. If debug mode is not enabled, it won't do anything
        """
        currentChannel = msg.args[0]
        if self.debug:
            if irc.isChannel(currentChannel):
                self._launch(irc, msg, "")

    dbg = wrap(dbg)

    # ---------------------------
    # Bang (shoot) command
    # ---------------------------
    def bang(self, irc, msg, args):
        """
        Shoots the duck! ▄︻デ══━一💥
        """
        currentChannel = msg.args[0]

        if irc.isChannel(currentChannel):
            if self.started.get(currentChannel) == True:

                # bangdelay: how much time between the duck was launched and this shot?
                if self.times[currentChannel]:
                    bangdelay = time.time() - self.times[currentChannel]
                else:
                    bangdelay = False

                # Is the player reloading?
                if (
                    self.reloading[currentChannel].get(msg.nick)
                    and time.time() - self.reloading[currentChannel][msg.nick]
                    < self.reloadtime[currentChannel]
                    and self.reloadcount[currentChannel][msg.nick] < 1
                ):
                    irc.reply(
                        "⏳ ▄︻デ══━一 You are reloading... (Reloading takes %i seconds)"
                        % (self.reloadtime[currentChannel])
                    )
                    self.reloadcount[currentChannel][msg.nick] += 1
                    return 0
                if (
                    self.reloading[currentChannel].get(msg.nick)
                    and time.time() - self.reloading[currentChannel][msg.nick]
                    < self.reloadtime[currentChannel]
                    and self.reloadcount[currentChannel][msg.nick] > 0
                ):
                    try:
                        self.scores[currentChannel][msg.nick] -= 1
                    except:
                        try:
                            self.scores[currentChannel][msg.nick] = -1
                        except:
                            self.scores[currentChannel] = {}
                            self.scores[currentChannel][msg.nick] = -1

                    # Base message
                    message = "❌ You shot yourself while trying to reload! ▄︻デ══━一💥"

                    # Adding additional message if kick
                    if (
                        self.registryValue("kickMode", currentChannel)
                        and irc.nick in irc.state.channels[currentChannel].ops
                    ):
                        message += (
                            "⏳ Reloading takes %s seconds."
                            % self.reloadtime[currentChannel]
                        )

                    # Adding nick and score
                    message += " %s: %i" % (
                        msg.nick,
                        self.scores[currentChannel][msg.nick],
                    )

                    # If we were able to have a bangdelay (ie: a duck was launched before someone did bang)
                    if bangdelay:
                        # Adding time
                        message += " (" + str(round(bangdelay, 2)) + " seconds)"

                    # If kickMode is enabled for this channel, and the bot have op capability, let's kick!
                    if (
                        self.registryValue("kickMode", currentChannel)
                        and irc.nick in irc.state.channels[currentChannel].ops
                    ):
                        irc.queueMsg(ircmsgs.kick(currentChannel, msg.nick, message))
                    else:
                        # Else, just say it
                        irc.reply(message)
                    return 0

                # This player is now reloading
                self.reloading[currentChannel][msg.nick] = time.time()
                self.reloadcount[currentChannel][msg.nick] = 0

                # There was a duck
                if self.duck[currentChannel] == True:

                    # Did the player miss it?
                    if random.random() < self.missprobability[currentChannel]:
                        irc.reply("❌ You missed the duck! ❌")
                    else:

                        # Adds one point for the nick that shot the duck (current hunt)
                        try:
                            self.scores[currentChannel][msg.nick] += 1
                        except:
                            try:
                                self.scores[currentChannel][msg.nick] = 1
                            except:
                                self.scores[currentChannel] = {}
                                self.scores[currentChannel][msg.nick] = 1

                        # Also update persistent totals immediately if autosave is ON
                        if self.autosave:
                            try:
                                self._read_scores(currentChannel)
                                if msg.nick not in self.channelscores[currentChannel]:
                                    self.channelscores[currentChannel][msg.nick] = 1
                                else:
                                    self.channelscores[currentChannel][msg.nick] += 1
                                # Write persistent scores right away
                                self._write_scores(currentChannel)
                            except Exception:
                                pass

                        irc.reply(
                            "🦆✔️ | Score: %i (%.2f seconds )"
                            % (self.scores[currentChannel][msg.nick], bangdelay)
                        )

                        # Update average time
                        if bangdelay:
                            self.averagetime[currentChannel] += bangdelay

                            # Now save the bang delay for the player (if it's quicker than it's previous bangdelay)
                            try:
                                previoustime = self.toptimes[currentChannel][msg.nick]
                                if bangdelay < previoustime:
                                    self.toptimes[currentChannel][msg.nick] = bangdelay
                            except:
                                self.toptimes[currentChannel][msg.nick] = bangdelay

                            # Now save the bang delay for the player (if it's worse than it's previous bangdelay)
                            try:
                                previoustime = self.worsttimes[currentChannel][msg.nick]
                                if bangdelay > previoustime:
                                    self.worsttimes[currentChannel][msg.nick] = bangdelay
                            except:
                                self.worsttimes[currentChannel][msg.nick] = bangdelay

                            # If this produced a new persistent best/worst time, save immediately
                            try:
                                self._read_scores(currentChannel)
                                # best
                                if (
                                    msg.nick not in self.channeltimes[currentChannel]
                                    or self.toptimes[currentChannel][msg.nick]
                                    < self.channeltimes[currentChannel][msg.nick]
                                ):
                                    self.channeltimes[currentChannel][msg.nick] = self.toptimes[currentChannel][msg.nick]
                                # worst
                                if (
                                    msg.nick not in self.channelworsttimes[currentChannel]
                                    or self.worsttimes[currentChannel][msg.nick]
                                    > self.channelworsttimes[currentChannel][msg.nick]
                                ):
                                    self.channelworsttimes[currentChannel][msg.nick] = self.worsttimes[currentChannel][msg.nick]
                                self._write_scores(currentChannel)
                            except Exception:
                                pass

                        self.duck[currentChannel] = False

                        # Reset the basetime for the waiting time before the next duck
                        self.lastSpoke[currentChannel] = time.time()

                        if self.registryValue("ducks", currentChannel):
                            maxShoots = self.registryValue("ducks", currentChannel)
                        else:
                            maxShoots = 10

                        # End of Hunt
                        if self.shoots[currentChannel] == maxShoots:
                            self._end(irc, msg, args)

                            # If autorestart is enabled, we restart a hunt automatically!
                            if self.registryValue("autoRestart", currentChannel):
                                # This code shouldn't be here
                                self.started[currentChannel] = True
                                self._initthrottle(irc, msg, args, currentChannel)
                                if self.scores.get(currentChannel):
                                    self.scores[currentChannel] = {}
                                if self.reloading.get(currentChannel):
                                    self.reloading[currentChannel] = {}

                                self.averagetime[currentChannel] = 0

                # There was no duck or the duck has already been shot
                else:

                    # Removes one point for the nick that shot
                    try:
                        self.scores[currentChannel][msg.nick] -= 1
                    except:
                        try:
                            self.scores[currentChannel][msg.nick] = -1
                        except:
                            self.scores[currentChannel] = {}
                            self.scores[currentChannel][msg.nick] = -1

                    # If autosave: also reflect persistent totals immediately
                    if self.autosave:
                        try:
                            self._read_scores(currentChannel)
                            if msg.nick not in self.channelscores[currentChannel]:
                                self.channelscores[currentChannel][msg.nick] = self.scores[currentChannel][msg.nick]
                            else:
                                self.channelscores[currentChannel][msg.nick] = self.channelscores[currentChannel].get(msg.nick,0) + ( -1 )
                            self._write_scores(currentChannel)
                        except Exception:
                            pass

                    # Base message
                    message = "❌ There was no duck! ❌"

                    # Adding additional message if kick
                    if (
                        self.registryValue("kickMode", currentChannel)
                        and irc.nick in irc.state.channels[currentChannel].ops
                    ):
                        message += "❌ You just shot yourself! ▄︻デ══━一💥"

                    # Adding nick and score
                    message += " %s: %i" % (
                        msg.nick,
                        self.scores[currentChannel][msg.nick],
                    )

                    # If we were able to have a bangdelay (ie: a duck was launched before someone did bang)
                    if bangdelay:
                        # Adding time
                        message += " (" + str(round(bangdelay, 2)) + " seconds)"

                    # If kickMode is enabled for this channel, and the bot have op capability, let's kick!
                    if (
                        self.registryValue("kickMode", currentChannel)
                        and irc.nick in irc.state.channels[currentChannel].ops
                    ):
                        irc.queueMsg(ircmsgs.kick(currentChannel, msg.nick, message))
                    else:
                        # Else, just say it
                        irc.reply(message)

            else:
                irc.reply(
                    "❗ There is no hunt right now! You can start a hunt with the 'starthunt'"
                    " command"
                )
        else:
            irc.error("You have to be on a channel ❗")

    bang = wrap(bang)

    # ---------------------------
    # Befriend (bef) command — fully integrated and autosaved
    # ---------------------------
    def bef(self, irc, msg, args):
        """
        Try to befriend a duck 🦆❤️
        """
        currentChannel = msg.args[0]

        if irc.isChannel(currentChannel):
            if self.started.get(currentChannel) == True:

                # Ensure in-memory dicts exist
                try:
                    self.friends[currentChannel]
                except:
                    self.friends[currentChannel] = {}
                try:
                    self.channelfriends[currentChannel]
                except:
                    self.channelfriends[currentChannel] = {}

                # There was a duck
                if self.duck[currentChannel] == True:
                    # 60% success chance
                    roll = random.random()
                    if roll <= 0.6:
                        # success: +1 friendship point (current hunt)
                        try:
                            self.friends[currentChannel][msg.nick] += 1
                        except:
                            self.friends[currentChannel][msg.nick] = 1

                        # Autosave persistent friendship total if configured
                        if self.autosave:
                            try:
                                self._read_scores(currentChannel)
                                if msg.nick not in self.channelfriends[currentChannel]:
                                    self.channelfriends[currentChannel][msg.nick] = 1
                                else:
                                    self.channelfriends[currentChannel][msg.nick] += 1
                                self._write_scores(currentChannel)
                            except Exception:
                                pass

                        irc.reply(
                            "🦆❤️ %s, you gently befriended the duck! (+1 friendship point)"
                            % (msg.nick,)
                        )
                    else:
                        # fail: -1 friendship point
                        try:
                            self.friends[currentChannel][msg.nick] -= 1
                        except:
                            self.friends[currentChannel][msg.nick] = -1

                        # Reflect penalty in persistent totals if autosave
                        if self.autosave:
                            try:
                                self._read_scores(currentChannel)
                                if msg.nick not in self.channelfriends[currentChannel]:
                                    self.channelfriends[currentChannel][msg.nick] = self.friends[currentChannel][msg.nick]
                                else:
                                    self.channelfriends[currentChannel][msg.nick] += -1
                                self._write_scores(currentChannel)
                            except Exception:
                                pass

                        irc.reply(
                            "💨 %s, the duck got scared and flew away! (-1 friendship point)"
                            % (msg.nick,)
                        )

                    # Duck leaves regardless
                    self.duck[currentChannel] = False
                    # Reset throttle base time
                    self.lastSpoke[currentChannel] = time.time()

                else:
                    # No duck present -> penalty
                    try:
                        self.friends[currentChannel][msg.nick] -= 1
                    except:
                        self.friends[currentChannel][msg.nick] = -1

                    # Persist penalty if autosave
                    if self.autosave:
                        try:
                            self._read_scores(currentChannel)
                            if msg.nick not in self.channelfriends[currentChannel]:
                                self.channelfriends[currentChannel][msg.nick] = self.friends[currentChannel][msg.nick]
                            else:
                                self.channelfriends[currentChannel][msg.nick] += -1
                            self._write_scores(currentChannel)
                        except Exception:
                            pass

                    irc.reply(
                        "😅 %s, there’s no duck to befriend right now! (-1 friendship point)"
                        % (msg.nick,)
                    )

            else:
                irc.reply(
                    "❗ There is no hunt right now! You can start a hunt with the 'starthunt'"
                    " command"
                )
        else:
            irc.error("You have to be on a channel ❗")

    bef = wrap(bef)

    # ---------------------------
    # Event handling
    # ---------------------------
    def doPrivmsg(self, irc, msg):
        currentChannel = msg.args[0]
        if irc.isChannel(msg.args[0]):
            if msg.args[1] == "🌳🌳🌳 •*´¨`*•.¸¸.•*´¨`*•.¸¸.••*´¨`*•.¸¸ 🦆 QUACK!":
                message = msg.nick + ", don't pretend to be me!"
                # If kickMode is enabled for this channel, and the bot have op capability, let's kick
                if (
                    self.registryValue("kickMode", currentChannel)
                    and irc.nick in irc.state.channels[currentChannel].ops
                ):
                    irc.queueMsg(ircmsgs.kick(currentChannel, msg.nick, message))
                else:
                    # Else, just say it
                    irc.reply(message)

    # ---------------------------
    # End hunt summary & cleanup
    # ---------------------------
    def _end(self, irc, msg, args):
        """
        End of the hunt (is called when the hunts stop "naturally" or when someone uses the !stop command)
        """

        currentChannel = msg.args[0]

        # End the hunt
        self.started[currentChannel] = False

        try:
            self.channelscores[currentChannel]
        except:
            self.channelscores[currentChannel] = {}

        if not self.registryValue("autoRestart", currentChannel):
            irc.reply("❗ The hunt stops now! ❗", prefixNick=False)

        # Showing shooting scores
        if self.scores.get(currentChannel):

            # Getting winner
            try:
                winnernick, winnerscore = max(
                    iter(self.scores.get(currentChannel).items()),
                    key=lambda k_v12: (k_v12[1], k_v12[0]),
                )
            except ValueError:
                winnernick, winnerscore = (None, 0)

            if self.registryValue("ducks", currentChannel):
                maxShoots = self.registryValue("ducks", currentChannel)
            else:
                maxShoots = 10

            # Is there a perfect?
            if winnerscore == maxShoots:
                irc.reply(
                    "😮 %s: %i ducks out of %i: perfect!!! +%i 😮"
                    % (winnernick, winnerscore, maxShoots, self.perfectbonus),
                    prefixNick=False,
                )
                # If autosave is ON, this perfect bonus should be applied persistently already;
                # if autosave is OFF, make sure we add it before writing.
                try:
                    self.scores[currentChannel][winnernick] += self.perfectbonus
                    if self.autosave:
                        # reflect in permanent totals as well
                        self._read_scores(currentChannel)
                        self.channelscores[currentChannel][winnernick] = self.channelscores[currentChannel].get(winnernick,0) + self.perfectbonus
                        self._write_scores(currentChannel)
                except Exception:
                    pass
            else:
                # Showing scores
                reply = []
                for nick, score in sorted(
                    iter(self.scores.get(currentChannel).items()),
                    key=itemgetter(1),
                    reverse=True,
                ):
                    reply.append("({0}: {1})".format(nick, score))
                irc.reply(
                    "Scores: "
                    + str(reply)
                    .replace("[", "")
                    .replace("]", "")
                    .replace(",", "")
                    .replace("'", ""),
                    prefixNick=False,
                )

            # Showing best time for this hunt
            try:
                if self.toptimes.get(currentChannel):
                    key, value = min(
                        iter(self.toptimes.get(currentChannel).items()),
                        key=lambda k_v6: (k_v6[1], k_v6[0]),
                    )
                    # compare with persistent top
                    channelbestnick = None
                    channelbesttime = None
                    if self.channeltimes.get(currentChannel):
                        try:
                            channelbestnick, channelbesttime = min(
                                iter(self.channeltimes.get(currentChannel).items()),
                                key=lambda k_v5: (k_v5[1], k_v5[0]),
                            )
                        except Exception:
                            channelbestnick, channelbesttime = (None, None)

                    recordmsg = ""
                    if channelbesttime and value < channelbesttime:
                        recordmsg = (
                            ". 🏆 This is the new record for this channel! (previous record"
                            " was held by "
                            + str(channelbestnick)
                            + " with "
                            + str(round(channelbesttime, 2))
                            + " seconds)"
                        )
                    irc.reply(
                        "🕒 Best time: %s with %.2f seconds%s" % (key, value, recordmsg),
                        prefixNick=False,
                    )
            except Exception:
                pass

            # Show longest time if meaningful
            try:
                if self.worsttimes.get(currentChannel):
                    key, value = max(
                        iter(self.worsttimes.get(currentChannel).items()),
                        key=lambda k_v8: (k_v8[1], k_v8[0]),
                    )
                    channelworstnick = None
                    channelworsttime = None
                    if self.channelworsttimes.get(currentChannel):
                        try:
                            channelworstnick, channelworsttime = max(
                                iter(self.channelworsttimes.get(currentChannel).items()),
                                key=lambda k_v7: (k_v7[1], k_v7[0]),
                            )
                        except Exception:
                            channelworstnick, channelworsttime = (None, None)

                    recordmsg = ""
                    if channelworsttime and value > channelworsttime:
                        recordmsg = (
                            ". 🕒 This is the new longest time for this channel! (previous"
                            " longest time was held by "
                            + str(channelworstnick)
                            + " with "
                            + str(round(channelworsttime, 2))
                            + " seconds)"
                        )
                    if recordmsg != "":
                        irc.reply(
                            "🕒 Longest time: %s with %.2f seconds%s"
                            % (key, value, recordmsg),
                            prefixNick=False,
                        )
            except Exception:
                pass

            # Write persistent times/records and week scores and friendships
            try:
                # If autosave is disabled, aggregate scores/friends now (to persistent totals)
                if not self.autosave:
                    self._calc_scores(currentChannel)
                else:
                    # autosave enabled: we still need to merge times and week info
                    # merge times/week:
                    # _calc_scores handles times and week; call it but it won't double-add scores/friends
                    self._calc_scores(currentChannel)

                # Write everything to disk
                self._write_scores(currentChannel)
            except Exception:
                pass

            # Did someone took the lead?
            weekscores = {}
            if self.channelweek.get(currentChannel):
                if self.channelweek[currentChannel].get(self.woy):
                    # for each day of week
                    for i in (1, 2, 3, 4, 5, 6, 7):
                        if self.channelweek[currentChannel][self.woy].get(i):
                            # Getting all scores, to get the winner of the week
                            for i, players in self.channelweek[currentChannel][
                                self.woy
                            ].items():
                                for player, value in players.items():
                                    weekscores.setdefault(player, 0)
                                    weekscores[player] += value
                            winnernick, winnerscore = max(
                                iter(weekscores.items()),
                                key=lambda k_v3: (k_v3[1], k_v3[0]),
                            )
                            if winnernick != self.leader[currentChannel]:
                                if self.leader[currentChannel] != None:
                                    irc.reply(
                                        "%s took the lead for the week over %s with %i"
                                        " points. 🏆"
                                        % (
                                            winnernick,
                                            self.leader[currentChannel],
                                            winnerscore,
                                        ),
                                        prefixNick=False,
                                    )
                                else:
                                    irc.reply(
                                        "%s has the lead for the week with %i points. 🏆"
                                        % (winnernick, winnerscore),
                                        prefixNick=False,
                                    )
                                self.leader[currentChannel] = winnernick
        else:
            irc.reply("❗😮 Not a single duck was shot during this hunt!", prefixNick=False)

        # --- Friendship summary for this hunt ---
        try:
            if self.friends.get(currentChannel):
                reply = []
                for nick, score in sorted(
                    iter(self.friends.get(currentChannel).items()),
                    key=itemgetter(1),
                    reverse=True,
                ):
                    reply.append("({0}: {1})".format(nick, score))
                irc.reply(
                    "🦆❤️ Friendship scores this hunt: "
                    + str(reply)
                    .replace("[", "")
                    .replace("]", "")
                    .replace(",", "")
                    .replace("'", ""),
                    prefixNick=False,
                )
        except Exception:
            pass

        # Reinit current hunt scores
        if self.scores.get(currentChannel):
            self.scores[currentChannel] = {}

        # Reinit current hunt times
        if self.toptimes.get(currentChannel):
            self.toptimes[currentChannel] = {}
        if self.worsttimes.get(currentChannel):
            self.worsttimes[currentChannel] = {}

        # Reinit friendships for current hunt
        if self.friends.get(currentChannel):
            self.friends[currentChannel] = {}

        # No duck launched
        self.duck[currentChannel] = False

        # Reinit number of shoots
        self.shoots[currentChannel] = 0

    # ---------------------------
    # Launch a duck
    # ---------------------------
    def _launch(self, irc, msg, args):
        """
        Launch a duck
        """
        currentChannel = msg.args[0]
        if irc.isChannel(currentChannel):
            if self.started[currentChannel] == True:
                if self.duck[currentChannel] == False:

                    # Store the time when the duck has been launched
                    self.times[currentChannel] = time.time()

                    # Store the fact that there's a duck now
                    self.duck[currentChannel] = True

                    # Send message directly (instead of queuing it with irc.reply)
                    irc.sendMsg(ircmsgs.privmsg(currentChannel, "🌳🌳🌳 •*´¨`*•.¸¸.•*´¨`*•.¸¸.••*´¨`*•.¸¸ 🦆 QUACK!"))

                    # Define a new throttle[currentChannel] for the next launch
                    self.throttle[currentChannel] = random.randint(
                        self.minthrottle[currentChannel],
                        self.maxthrottle[currentChannel],
                    )

                    try:
                        self.shoots[currentChannel] += 1
                    except:
                        self.shoots[currentChannel] = 1
                else:
                    irc.reply("Already a duck")
            else:
                irc.reply("❌ The hunt has not started yet!")
        else:
            irc.error("❗ You have to be on a channel")

    # ---------------------------
    # listfriends (persistent)
    # ---------------------------
    def listfriends(self, irc, msg, args, size, channel):
        """
        [<size>] [<channel>]
        Shows the <size>-sized friendship list for <channel> (or current channel)
        """
        if irc.isChannel(channel):
            self._read_scores(channel)

            try:
                friendsdict = self.channelfriends.get(channel, {})
            except:
                friendsdict = {}

            if not size:
                listsize = 10
            else:
                listsize = size

            # Sort friendships (higher is better)
            friends = sorted(iter(friendsdict.items()), key=itemgetter(1), reverse=True)
            del friends[listsize:]

            msgstring = ""
            for item in friends:
                msgstring += "({0}: {1}) ".format(item[0], str(item[1]))
            if msgstring != "":
                irc.reply(
                    "🦆❤️ ~ DuckHunt top-"
                    + str(listsize)
                    + " duck friends for "
                    + channel
                    + " ~ 🦆"
                )
                irc.reply(msgstring)
            else:
                irc.reply("There aren't any friendships for this channel yet.")
        else:
            irc.reply("Are you sure this is a channel?")

    listfriends = wrap(listfriends, [optional("int"), "channel"])

Class = DuckHunt

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:

