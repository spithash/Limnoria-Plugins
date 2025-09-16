import supybot.callbacks as callbacks
import supybot.commands as commands
import supybot.ircmsgs as ircmsgs
import supybot.schedule as schedule
import re
import time

class RemindMe(callbacks.Plugin):
    threaded = True

    def remindme(self, irc, msg, args, text):
        """<time><m|h|s> <message>
        Sets a reminder. Example: @remindme 15m work on task A
        """
        nick = msg.nick

        # Parse "15m task"
        match = re.match(r'(\d+)([smh])\s+(.*)', text.strip())
        if not match:
            irc.reply("Usage: @remindme <time><s|m|h> <message>")
            return

        amount, unit, message = match.groups()
        amount = int(amount)

        # Convert to seconds
        if unit == 's':
            delay = amount
        elif unit == 'm':
            delay = amount * 60
        elif unit == 'h':
            delay = amount * 3600
        else:
            irc.reply("Invalid time unit (use s, m, or h).")
            return

        def reminder():
            target = msg.args[0]  # where the command was issued (channel or PM)
            action_text = f"reminds {nick} to {message}"
            irc.queueMsg(ircmsgs.action(target, action_text))

        schedule.addEvent(reminder, time.time() + delay)
        irc.reply(f"Okay {nick}, I’ll remind you in {amount}{unit}.")

    remindme = commands.wrap(remindme, ['text'])

Class = RemindMe
