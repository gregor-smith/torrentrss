import os
import json
import tempfile
import platform
import subprocess
import collections

SYSTEM = platform.system()

def start_file(path):
    if platform == 'Windows':
        return os.startfile(path)
    elif platform == 'Linux':
        command = 'xdg-open'
    elif platform == 'Darwin':
        command = 'open'
    else:
        raise NotImplementedError('Only Windows, Linux and OSX are supported')
    return subprocess.Popen([command, path])

class Config(collections.OrderedDict):
    def load(self, path):
        with open(path) as file:
            dict = json.load(file, object_pairs_hook=collections.OrderedDict)
        self.update(dict)
config = Config()

class Feed:
    def __init__(self, name, url, interval=60):
        self.name = name
        self.url = url
        self.interval = interval
        self.subscriptions = {}

class Subscription:
    def __init__(self, name, feed, pattern, directory=None, command=None):
        self.name = name
        self.feed = feed
        self.pattern = pattern
        self.directory = directory
        self.command = command

