# SubReddit

A Limnoria plugin that uses the [RedListen API](https://redlisten.com/docs) to
announce new subreddit posts in IRC channels. Announcements contain only the
subreddit, title, and Reddit URL.

## Authentication

RedListen expects an HTTP `Authorization` header in this form:

```text
Authorization: Bearer rl_live_YOUR_KEY
```

`Bearer` is the authentication method, not part of the key. It tells the API
that the following value is the secret access token. Never paste a real key in
a channel, repository, screenshot, or log.

Load the plugin, identify to the bot as its owner, and send the key in a
**private message**:

```text
@load SubReddit
@reddit setkey rl_live_YOUR_KEY
```

The command is owner-only, refuses to run in a channel, and stores the Limnoria
registry value as private.

## Commands

All commands are bot-owner-only. A channel is always an explicit argument, so
the command can be issued from another channel or by private message. Without
a network argument, the plugin uses the IRC network where the command was
issued.

```text
@reddit subscribe ##rtlsdr rtlsdr
@reddit unsubscribe ##rtlsdr rtlsdr
@reddit list ##rtlsdr
@reddit status
@reddit pull
```

`reddit pull` immediately checks every active unique subreddit feed. It is
owner-only, consumes one request per unique feed, respects the configured
monthly budget, announces any newly discovered posts, and privately reports
the number of successful checks and announcements. Use it sparingly because
manual pulls consume the same Free-plan quota as automatic polling.

To manage a channel on another configured IRC network, put the network name
before the channel:

```text
@reddit subscribe Libera ##rtlsdr rtlsdr
@reddit unsubscribe Libera ##rtlsdr rtlsdr
@reddit list Libera ##rtlsdr
```

Subscriptions are keyed by both network and channel. Thus `Libera/#news` and
`OFTC/#news` are independent even though their channel names are identical.

The bot must be joined to the destination channel. The first successful check
creates a baseline and does not announce existing posts. Later checks announce
new posts oldest-first, for example:

```text
[r/rtlsdr] New SDR receiver released https://www.reddit.com/r/RTLSDR/comments/...
```

## Free-plan request budgeting

The plugin is configured for the **RedListen Free plan by default**. According
to the [RedListen documentation](https://redlisten.com/docs), the Free plan
provides:

- 1,000 API requests per month;
- up to 50 results per request;
- access to the most recent 7 days of searchable data;
- 30 requests per minute;
- up to 2 simultaneous requests; and
- 3 keyword streams, with 7-day backfill, webhooks, and email digests.

This plugin uses subreddit listing requests for IRC announcements. It does not
create keyword streams or use RedListen webhooks or email digests.

The plugin limits itself to **990 requests per UTC month**, leaving 10 of the
Free plan's 1,000 requests for retries or manual use. It polls each unique
subreddit once per round and calculates the interval from:

- requests remaining in the configured budget;
- seconds remaining in the UTC month; and
- number of unique subscribed subreddits.

Thus one feed is checked roughly every 41–46 minutes, depending on the month,
while more feeds are
checked less often so the monthly budget is not exceeded. The persistent
counter resets at the start of each UTC month. Duplicate subreddit feeds shared
by multiple channels use one API request and fan out the announcement.

RedListen returns at most 50 results on the Free plan. A very busy subreddit
can publish more than 50 posts between budgeted checks; those extra posts cannot
be recovered without using more requests or a higher plan.

Optional configuration:

```text
@config supybot.plugins.SubReddit.monthlyRequestBudget 990
@config supybot.plugins.SubReddit.minimumPollInterval 900
```

Setting a budget below 1,000 is recommended because other clients using the
same key count against the provider's quota but are invisible to this plugin's
local counter.
