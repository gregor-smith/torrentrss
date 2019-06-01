## torrentrss
An RSS torrent fetcher intended to be ran on a schedule with the likes of `cron` on Linux or Task Scheduler on Windows.

### Why?
I've always found the builtin RSS support to be lackluster in every torrent client I've tried. Other programs seemed complicated or annoying to set up. I needed something that was simple and could track episode numbers.

### Features
* Configuration is a simple JSON file with a well-commented schema for reference
* Keeps track of episode numbers, so no downloading last week's episode when you've already seen it
* Uses regular expressions to match RSS entries
* Can pass torrent URLs to commands instead of downloading and passing torrent files (enabled by default)
* Can use a custom user agent for downloading each feed
* Can set custom commands to be run on the path or URL for each subscription
* Can download torrent files to a custom directory for each subscription (by default the operating system's temporary directory is used)

### Requirements
Python 3.6 or newer, and the following packages:

* `click`
* `requests`
* `feedparser`
* `jsonschema`

For error messages to appear as a notification, `notify-send` must be on the `$PATH`.
