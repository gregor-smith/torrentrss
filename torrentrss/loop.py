import time
import concurrent.futures

from . import common

def worker(feed):
    with feed.logger.catch_exception():
        while True:
            for subscription, entry, number in feed.matching_subscriptions():
                torrent_path = subscription.download(entry)
                feed.logger.info('{!r} downloaded to {!r}', entry.link, torrent_path)
                subscription.command(torrent_path)
                feed.logger.info('{!r} launched with {!r}', torrent_path, subscription.command)
                subscription.number = number

            feed.logger.info('Sleeping for {} minutes', feed.interval_minutes)
            time.sleep(feed.interval_minutes*60)

def run(config):
    feeds = config['feeds'].values()

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(feeds)) as executor:
        futures = {}
        for feed in feeds:
            future = executor.submit(worker, feed)
            futures[future] = feed
            feed.logger.info('Future created')
        for future in concurrent.futures.as_completed(futures):
            feed = futures[future]

            exception = future.exception()
            if exception is None:
                feed.logger.critical('Future somehow finished without raising '
                                     "an exception, which shouldn't be possible")
            else:
                feed.logger.critical('Future encountered an exception')
                # TODO: other options for when one future raises exception
                with feed.logger.catch_exception():
                    raise exception

