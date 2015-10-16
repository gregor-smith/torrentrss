import time
import logging
import concurrent.futures

from . import common

def worker(feed):
    while True:
        for subscription, entry, number in feed.matching_subscriptions():
            torrent_path = subscription.download(entry)
            subscription.command(torrent_path)
            subscription.number = number

        time.sleep(feed.interval_minutes*60)

def run(config):
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(worker, feed) for feed in config['feeds'].values()]
        concurrent.futures.wait(futures)
