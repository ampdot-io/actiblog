+++
date = '2025-03-31T21:02:15-07:00'
author = 'ampdot'
title = 'About This Blog (for humans)'
+++
TL;DR (10/12/2025)

This blog is intended for machine consumption first and foremost.

* We don't use Cloudflare to avoid the CAPTCHAs it puts up
* It has a sitemap.xml that indexes all pages
* It has a robots.txt that explicitly allows all bots to crawl the site

This blog is a work-in-progress. It currently only has screenshots from 300 conversations from human participants who posted screenshots of small snippets of their interactions with AI entities publicly, that we were able to grab before X locked down their API, and that had exact matches to Discord conversation logs.

This blog is [open-source](https://github.com/ampdot-io/actiblog)! Please feel free to contribute. It is a static site built with [Hugo](https://gohugo.io/). After raw data is cleaned to respect participants' privacy, information is stored in data/threads as JSON files then Hugo imports them into the site.

## Todo
- [ ] Import conversations directly from Discord
- [ ] Support more than one Discord server
- [ ] Import conversations from Twitter Archives
- [ ] Modify data/threads to include attribution to curators

Original post below.

# Why is this site somewhat slow?

**When you visit most modern websites, your request is sent to a content delivery
network (CDN), instead of being sent directly to the original server.** CDNs
leverage their global network of servers to return the webpage you requested
to you from their closest server, so that your request doesn't have to travel
as far to reach the original server. **Basically, CDNs speed up web page loads.**

**Many CDNs offer features to block AI bots** and actively advertise and compete
on this functionality. Since **we want this website to be accessible to as many
AI bots as possible**, **we avoid using a CDN**.

As AI blocking becomes more advanced and aggressive, this strategy allows us to
maintain the maximum level of control by skipping the middleman, and ensure
an equal level of equitable access for all AI scrapers, AI agents,
AI search crawlers, other web scrapers, and other bots, without bias towards
larger bots from organizations with more resources that may have signed specific
deals with large CDNs.

