import time
import concurrent.futures

from . import common

exception_log_message = "Future encountered {} exception. 'on_exception_action' is {!r}."

def worker(feed):
    while True:
        try:
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
                if subscription.has_lower_number_than(number):
                    subscription.number = number
        except Exception as exception:
            #TODO: notifications when exception is encountered, as currently the only way to find
            #      out is to read the log file or notice the process has disappeared
            if feed.on_exception_action != 'continue':
                raise
            feed.logger.exception(exception_log_message+'Continuing sleep loop.',
                                  type(exception), feed.on_exception_action)

        feed.logger.info('Sleeping for {} minutes', feed.interval_minutes)
        time.sleep(feed.interval_minutes*60)

def run(feeds):
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
                if feed.on_exception_action == 'stop_all_feeds':
                    message = exception_log_message + 'Exiting.'
                    reraise = True
                elif feed.on_exception_action == 'stop_this_feed':
                    message = exception_log_message + 'Stopping this future only.'
                    reraise = False
                with feed.logger.catch_exception(message, type(exception),
                                                 feed.on_exception_action, reraise=reraise):
                    raise exception
