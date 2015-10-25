import time
import concurrent.futures

from . import common

def worker(feed):
    with feed.logger.catch_exception():
        while True:
            # List is called here as otherwise subscription.number would be updated during the
            # loop before being checked by the next iteration of feed.matching_subscriptions,
            # so if a subscription's number was originally 2 and there were entries with 4 and 3,
            # 4 would become the subscription's number, and because 4 > 3, 3 would be skipped.
            # Calling list first checks all entries against the subscription's original number,
            # avoiding this problem. The alternatives were to update numbers in another loop
            # afterwards, or to call reversed first on rss.entries in feed.matching_subscriptions.
            # The latter seems like an ok workaround at first, since it would yield 3 before 4,
            # but if 4 were added to the rss before 3 for some reason, it would still break.
            for subscription, entry, number in list(feed.matching_subscriptions()):
                torrent_path = subscription.download(entry)
                feed.logger.info('{!r} downloaded to {!r}', entry.link, torrent_path)
                subscription.command(torrent_path)
                feed.logger.info('{!r} launched with {!r}', torrent_path, subscription.command)
                if number > subscription.number:
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
                # TODO: other options for when one future raises exception
                feed.logger.critical('Future encountered an exception')

