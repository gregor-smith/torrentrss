import os
import pprint
import inspect
import logging
import datetime
import contextlib

PATH_FORMAT = 'logs/{0:%Y}/{0:%m}/{0:%Y-%m-%d_%H-%M-%S}.log'
MESSAGE_FORMAT = '[{asctime} {levelname} {threadName} {name} {module} line:{lineno}]\n{message}'
NAME_FORMAT = '{module}.{type}'

ROOT_NAME = 'torrentrss'

pprinter = pprint.PrettyPrinter(width=100)

# from https://stackoverflow.com/a/24683360, '__str__' modified
class BraceMessage:
    def __init__(self, fmt, args, kwargs):
        self.fmt = fmt
        self.args = args
        self.kwargs = kwargs

    def __str__(self):
        if isinstance(self.fmt, str):
            return self.fmt.format(*self.args, **self.kwargs)
        return pprinter.pformat(self.fmt)

# from https://stackoverflow.com/a/24683360, 'process' modified and 'catch_exception' new
class StyleAdapter(logging.LoggerAdapter):
    def __init__(self, logger):
        self.logger = logger

    def log(self, level, msg, *args, **kwargs):
        if self.isEnabledFor(level):
            msg, log_kwargs = self.process(msg, kwargs)
            self.logger._log(level, BraceMessage(msg, args, kwargs), (), **log_kwargs)

    def process(self, msg, kwargs):
        log_signature = inspect.signature(self.logger._log)
        return msg, {key: kwargs[key] for key in log_signature.parameters.keys() if key in kwargs}

    @contextlib.contextmanager
    def catch_exception(self, *message, reraise=True):
        try:
            yield
        except Exception as exception:
            self.exception(*message) if message else self.exception(type(exception))
            if reraise:
                raise

def get_path(config_dir, path_format=PATH_FORMAT):
    path = os.path.join(config_dir, path_format)
    path = path.format(datetime.datetime.now())
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    return path

def create(config_dir, file_level, console_level):
    logger = logging.getLogger(ROOT_NAME)
    logger.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(get_path(config_dir))
    file_handler.setLevel(file_level)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)

    formatter = logging.Formatter(MESSAGE_FORMAT, style='{')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return StyleAdapter(logger)

def create_child(module_name, type_name, instance_name=None, name_format=NAME_FORMAT):
    name = name_format.format(module=module_name, type=type_name)
    if instance_name is not None:
        name += '({!r})'.format(instance_name)
    logger = logging.getLogger(name)
    return StyleAdapter(logger)
