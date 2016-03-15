## torrentrss
An RSS torrent downloader intended to be ran on a schedule with the likes of cron on Linux or Task Scheduler on Windows.

### Why?
I've always found the builtin RSS support to be lackluster in every torrent client I've tried. Other programs seemed complicated or annoying to set up. I needed something that was simple, could track episode numbers, and supported magnet links.

### Features
* Configuration is a simple JSON file with a well-commented schema for reference
* Keeps track of episode numbers, so no downloading last week's episode when you've already seen it
* Uses regular expressions to match filenames
* Can pass magnet links to commands if available instead of downloading and passing torrent files (enabled by default)
* Can pass torrent URLs to commands instead of downloading and passing torrent files (enabled by default, but magnet links are preferred)
* Can use a custom user agent for downloading each feed
* Can download torrent files to a custom directory for each subscription (by default the operating system's temporary directory is used)

### Requirements
Python 3.5 or newer, and the following packages:

* click
* easygui
* requests
* feedparser
* jsonschema
