import time
import concurrent.futures

def worker(feed):
    while True:
        for subscription, entry, number in feed.matching_subscriptions():
            path = subscription.download(entry)
            subscription.command(path)
            subscription.number = number

        time.sleep(feed.interval_minutes*60)

def run(config):
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(worker, feed) for feed in config['feeds'].values()]
        concurrent.futures.wait(futures)
