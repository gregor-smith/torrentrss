import time
import shutil
import pathlib
import traceback
import subprocess
import concurrent.futures

try:
    from PyQt5.QtWidgets import QApplication, QMessageBox
    HAS_PYQT = True
    PYQT_VERSION = 5
except ImportError:
    try:
        from PyQt4.QtGui import QApplication, QMessageBox
        HAS_PYQT = True
        PYQT_VERSION = 4
    except ImportError:
        HAS_PYQT = False
        PYQT_VERSION = None

from . import common

EXCEPTION_LOG_MESSAGE = "Future encountered {} exception. 'on_exception_action' is {!r}."
EXCEPTION_GUI_MESSAGE = 'Feed {!r} encountered an {} exception.'
EXCEPTION_ACTION_MESSAGE_START = "Since 'on_exception_action' is {0.on_exception_action!r}, "
EXCEPTION_ACTION_MESSAGES = {
    'stop_this_feed': EXCEPTION_ACTION_MESSAGE_START + "only this feed's future has stopped.",
    'stop_all_feeds': EXCEPTION_ACTION_MESSAGE_START + "all feeds' futures will stop.",
    'continue': EXCEPTION_ACTION_MESSAGE_START + 'this feed will retry in {0.interval_minutes}m.'
}

HAS_NOTIFY_SEND = shutil.which('notify-send') is not None
QAPPLICATION = None

def show_gui_error(feed, exception):
    log_path = feed.logger.current_file_path()
    text = EXCEPTION_GUI_MESSAGE.format(feed.name, type(exception))
    informative_text = EXCEPTION_ACTION_MESSAGES[feed.on_exception_action].format(feed)
    detailed_text = traceback.format_exc()

    if feed.on_exception_gui == 'qt_messagebox':
        if HAS_PYQT:
            show_error_as_pyqt_messagebox(log_path, text, informative_text, detailed_text)
        else:
            feed.logger.warning("'on_exception_gui' is 'qt_messagebox'"
                                'but neither PyQt5 nor PyQt4 could be imported.')
    elif feed.on_exception_action == 'notify-send':
        if HAS_NOTIFY_SEND:
            show_error_as_notify_send_notification(log_path, text, informative_text)
        else:
            feed.logger.warning("'on_exception_gui' is 'notify-send'"
                                "but the 'notify-send' executable could not be found.")

def show_error_as_pyqt_messagebox(log_path, text, informative_text, detailed_text):
    if QAPPLICATION is None:
        global QAPPLICATION
        QAPPLICATION = QApplication([])

    messagebox = QMessageBox()
    messagebox.setWindowTitle(common.NAME)
    messagebox.setText(text)
    messagebox.setInformativeText(informative_text)
    messagebox.setDetailedText(detailed_text)

    ok_button = messagebox.addButton(messagebox.Ok)
    open_button = messagebox.addButton('Open Log', messagebox.ActionRole)
    messagebox.setDefaultButton(ok_button)

    messagebox.exec_()
    if messagebox.clickedButton() == open_button:
        common.startfile(log_path)

def show_error_as_notify_send_notification(log_path, text, informative_text):
    path = pathlib.Path(log_path).as_uri()
    message = '{} {} Click to open log file:\n{}'.format(text, informative_text, path)
    subprocess.Popen(['notify-send', '--app-name', common.NAME, common.NAME, message])

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
            if feed.on_exception_gui is not None:
                show_gui_error(feed, exception)
            if feed.on_exception_action != 'continue':
                raise
            feed.logger.exception(EXCEPTION_LOG_MESSAGE+'Continuing sleep loop',
                                  type(exception), feed.on_exception_action)

        feed.logger.info('Sleeping for {} minutes', feed.interval_minutes)
        time.sleep(feed.interval_minutes*60)

def run(feeds):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(feeds)) as executor:
        futures = {}
        for feed in feeds:
            if feed.has_enabled_subscription():
                future = executor.submit(worker, feed)
                futures[future] = feed
                feed.logger.info('Future created')
            else:
                feed.logger.info('No enabled subscriptions found')
        for future in concurrent.futures.as_completed(futures):
            feed = futures[future]

            exception = future.exception()
            if exception is None:
                feed.logger.critical('Future somehow finished without raising '
                                     "an exception, which shouldn't be possible")
            else:
                if feed.on_exception_action == 'stop_all_feeds':
                    message = EXCEPTION_LOG_MESSAGE + 'Exiting'
                    reraise = True
                elif feed.on_exception_action == 'stop_this_feed':
                    message = EXCEPTION_LOG_MESSAGE + 'Stopping this future only'
                    reraise = False
                with feed.logger.catch_exception(message, type(exception),
                                                 feed.on_exception_action, reraise=reraise):
                    raise exception
