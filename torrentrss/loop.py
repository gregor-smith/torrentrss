import os
import time
import concurrent.futures

import requests
import feedparser

def worker(feed):
    while True:
        rss = feedparser.parse(feed.url)
        for name, subscription in feed.subscriptions.items():
            for entry in rss.entries:
                match = subscription.pattern.search(entry.title)
                if match:
                    response = requests.get(entry.link)
                    path = os.path.join(subscription.directory, entry.title+'.torrent')
                    with open(path, 'wb') as file:
                        file.write(response.content)
                    subscription.command(path)

        time.sleep(feed.interval_minutes*60)

def run(config):
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {name: executor.submit(worker, feed) for name, feed in config['feeds'].items()}
        concurrent.futures.wait(futures)
