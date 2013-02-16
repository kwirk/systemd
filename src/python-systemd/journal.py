#  -*- Mode: python; indent-tabs-mode: nil -*- */
#
#  This file is part of systemd.
#
#  Copyright 2012 David Strauss
#
#  systemd is free software; you can redistribute it and/or modify it
#  under the terms of the GNU Lesser General Public License as published by
#  the Free Software Foundation; either version 2.1 of the License, or
#  (at your option) any later version.
#
#  systemd is distributed in the hope that it will be useful, but
#  WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with systemd; If not, see <http://www.gnu.org/licenses/>.

import datetime
import functools
import sys
import uuid
import traceback as _traceback
import os as _os
import logging as _logging
if sys.version_info >= (3,):
    from collections import ChainMap
from syslog import (LOG_EMERG, LOG_ALERT, LOG_CRIT, LOG_ERR,
                    LOG_WARNING, LOG_NOTICE, LOG_INFO, LOG_DEBUG)
from ._journal import sendv, stream_fd
from ._reader import (_Journal, NOP, APPEND, INVALIDATE,
                      LOCAL_ONLY, RUNTIME_ONLY, SYSTEM_ONLY)

_MONOTONIC_CONVERTER = lambda x: datetime.timedelta(microseconds=float(x))
_REALTIME_CONVERTER = lambda x: datetime.datetime.fromtimestamp(float(x)/1E6)
DEFAULT_CONVERTERS = {
    'MESSAGE_ID': uuid.UUID,
    'PRIORITY': int,
    'LEADER': int,
    'SESSION_ID': int,
    'USERSPACE_USEC': int,
    'INITRD_USEC': int,
    'KERNEL_USEC': int,
    '_UID': int,
    '_GID': int,
    '_PID': int,
    'SYSLOG_FACILITY': int,
    'SYSLOG_PID': int,
    '_AUDIT_SESSION': int,
    '_AUDIT_LOGINUID': int,
    '_SYSTEMD_SESSION': int,
    '_SYSTEMD_OWNER_UID': int,
    'CODE_LINE': int,
    'ERRNO': int,
    'EXIT_STATUS': int,
    '_SOURCE_REALTIME_TIMESTAMP': _REALTIME_CONVERTER,
    '__REALTIME_TIMESTAMP': _REALTIME_CONVERTER,
    '_SOURCE_MONOTONIC_TIMESTAMP': _MONOTONIC_CONVERTER,
    '__MONOTONIC_TIMESTAMP': _MONOTONIC_CONVERTER,
    'COREDUMP': bytes,
    'COREDUMP_PID': int,
    'COREDUMP_UID': int,
    'COREDUMP_GID': int,
    'COREDUMP_SESSION': int,
    'COREDUMP_SIGNAL': int,
    'COREDUMP_TIMESTAMP': _REALTIME_CONVERTER,
}

if sys.version_info >= (3,):
    _convert_unicode = functools.partial(str, encoding='utf-8')
else:
    _convert_unicode = functools.partial(unicode, encoding='utf-8')

class Journal(_Journal):
    def __init__(self, converters=None, *args, **kwargs):
        super(Journal, self).__init__(*args, **kwargs)
        if sys.version_info >= (3,3):
            self.converters = ChainMap()
            if converters is not None:
                self.converters.maps.append(converters)
            self.converters.maps.append(DEFAULT_CONVERTERS)
        else:
            # suitable fallback, e.g.
            self.converters = DEFAULT_CONVERTERS.copy()
            if converters is not None:
                self.converters.update(converters)

    def _convert_field(self, key, value):
        try:
            result = self.converters[key](value)
        except:
            # Default conversion in unicode
            try:
                result = _convert_unicode(value)
            except:
                # Leave in default bytes
                result = value
        return result

    def _convert_entry(self, entry):
        result = {}
        for key, value in entry.items():
            if isinstance(value, list):
                result[key] = [self._convert_field(key, val) for val in value]
            else:
                result[key] = self._convert_field(key, value)
        return result

    def add_match(self, *args, **kwargs):
        args = list(args)
        args.extend(_make_line(key, val) for key, val in kwargs.items())
        for arg in args:
            super(Journal, self).add_match(arg)

    def get_next(self, skip=1):
        return self._convert_entry(
            super(Journal, self).get_next(skip))

    def query_unique(self, key):
        return set(self._convert_field(key, value)
            for value in super(Journal, self).query_unique(key))

    def seek_realtime(self, timestamp):
        if isinstance(timestamp, datetime.datetime):
            timestamp = int(timestamp.strftime("%s%f"))
        return super(Journal, self).seek_realtime(timestamp)

    def seek_monotonic(self, timestamp, bootid=None):
        if isinstance(timestamp, datetime.timedelta):
            timestamp = timestamp.totalseconds()
        return super(Journal, self).seek_monotonic(timestamp, bootid)

    def log_level(self, level):
        """Sets maximum log level by setting matches for PRIORITY."""
        if 0 <= level <= 7:
            for i in range(level+1):
                self.add_match(PRIORITY="%s" % i)
        else:
            raise ValueError("Log level must be 0 <= level <= 7")

    def this_boot(self):
        #TODO: self.add_match(_BOOT_ID=id128.get_boot().get_hex())
        raise NotImplementedError

    def this_machine(self):
        #TODO: self.add_match(_MACHINE_ID=id128.get_machine().get_hex())
        raise NotImplementedError

def _make_line(field, value):
        if isinstance(value, bytes):
                return field.encode('utf-8') + b'=' + value
        else:
                return field + '=' + value

def send(MESSAGE, MESSAGE_ID=None,
         CODE_FILE=None, CODE_LINE=None, CODE_FUNC=None,
         **kwargs):
        r"""Send a message to journald.

        >>> journal.send('Hello world')
        >>> journal.send('Hello, again, world', FIELD2='Greetings!')
        >>> journal.send('Binary message', BINARY=b'\xde\xad\xbe\xef')

        Value of the MESSAGE argument will be used for the MESSAGE=
        field.

        MESSAGE_ID can be given to uniquely identify the type of
        message.

        Other parts of the message can be specified as keyword
        arguments.

        Both MESSAGE and MESSAGE_ID, if present, must be strings, and
        will be sent as UTF-8 to journal. Other arguments can be
        bytes, in which case they will be sent as-is to journal.

        CODE_LINE, CODE_FILE, and CODE_FUNC can be specified to
        identify the caller. Unless at least on of the three is given,
        values are extracted from the stack frame of the caller of
        send(). CODE_FILE and CODE_FUNC must be strings, CODE_LINE
        must be an integer.

        Other useful fields include PRIORITY, SYSLOG_FACILITY,
        SYSLOG_IDENTIFIER, SYSLOG_PID.
        """

        args = ['MESSAGE=' + MESSAGE]

        if MESSAGE_ID is not None:
                args.append('MESSAGE_ID=' + MESSAGE_ID)

        if CODE_LINE == CODE_FILE == CODE_FUNC == None:
                CODE_FILE, CODE_LINE, CODE_FUNC = \
                        _traceback.extract_stack(limit=2)[0][:3]
        if CODE_FILE is not None:
                args.append('CODE_FILE=' + CODE_FILE)
        if CODE_LINE is not None:
                args.append('CODE_LINE={:d}'.format(CODE_LINE))
        if CODE_FUNC is not None:
                args.append('CODE_FUNC=' + CODE_FUNC)

        args.extend(_make_line(key, val) for key, val in kwargs.items())
        return sendv(*args)

def stream(identifier, priority=LOG_DEBUG, level_prefix=False):
        r"""Return a file object wrapping a stream to journal.

        Log messages written to this file as simple newline sepearted
        text strings are written to the journal.

        The file will be line buffered, so messages are actually sent
        after a newline character is written.

        >>> stream = journal.stream('myapp')
        >>> stream
        <open file '<fdopen>', mode 'w' at 0x...>
        >>> stream.write('message...\n')

        will produce the following message in the journal:

        PRIORITY=7
        SYSLOG_IDENTIFIER=myapp
        MESSAGE=message...

        Using the interface with print might be more convinient:

        >>> from __future__ import print_function
        >>> print('message...', file=stream)

        priority is the syslog priority, one of LOG_EMERG, LOG_ALERT,
        LOG_CRIT, LOG_ERR, LOG_WARNING, LOG_NOTICE, LOG_INFO, LOG_DEBUG.

        level_prefix is a boolean. If true, kernel-style log priority
        level prefixes (such as '<1>') are interpreted. See
        sd-daemon(3) for more information.
        """

        fd = stream_fd(identifier, priority, level_prefix)
        return _os.fdopen(fd, 'w', 1)

class JournalHandler(_logging.Handler):
        """Journal handler class for the Python logging framework.

        Please see the Python logging module documentation for an
        overview: http://docs.python.org/library/logging.html

        To create a custom logger whose messages go only to journal:

        >>> log = logging.getLogger('custom_logger_name')
        >>> log.propagate = False
        >>> log.addHandler(journal.JournalHandler())
        >>> log.warn("Some message: %s", detail)

        Note that by default, message levels INFO and DEBUG are ignored
        by the logging framework. To enable those log levels:

        >>> log.setLevel(logging.DEBUG)

        To attach journal MESSAGE_ID, an extra field is supported:

        >>> log.warn("Message with ID",
        >>>     extra={'MESSAGE_ID': '22bb01335f724c959ac4799627d1cb61'})

        To redirect all logging messages to journal regardless of where
        they come from, attach it to the root logger:

        >>> logging.root.addHandler(journal.JournalHandler())

        For more complex configurations when using dictConfig or
        fileConfig, specify 'systemd.journal.JournalHandler' as the
        handler class.  Only standard handler configuration options
        are supported: level, formatter, filters.

        The following journal fields will be sent:

        MESSAGE, PRIORITY, THREAD_NAME, CODE_FILE, CODE_LINE,
        CODE_FUNC, LOGGER (name as supplied to getLogger call),
        MESSAGE_ID (optional, see above).
        """

        def emit(self, record):
                """Write record as journal event.

                MESSAGE is taken from the message provided by the
                user, and PRIORITY, LOGGER, THREAD_NAME,
                CODE_{FILE,LINE,FUNC} fields are appended
                automatically. In addition, record.MESSAGE_ID will be
                used if present.
                """
                try:
                        msg = self.format(record)
                        pri = self.mapPriority(record.levelno)
                        mid = getattr(record, 'MESSAGE_ID', None)
                        send(msg,
                             MESSAGE_ID=mid,
                             PRIORITY=format(pri),
                             LOGGER=record.name,
                             THREAD_NAME=record.threadName,
                             CODE_FILE=record.pathname,
                             CODE_LINE=record.lineno,
                             CODE_FUNC=record.funcName)
                except Exception:
                        self.handleError(record)

        @staticmethod
        def mapPriority(levelno):
                """Map logging levels to journald priorities.

                Since Python log level numbers are "sparse", we have
                to map numbers in between the standard levels too.
                """
                if levelno <= _logging.DEBUG:
                        return LOG_DEBUG
                elif levelno <= _logging.INFO:
                        return LOG_INFO
                elif levelno <= _logging.WARNING:
                        return LOG_WARNING
                elif levelno <= _logging.ERROR:
                        return LOG_ERR
                elif levelno <= _logging.CRITICAL:
                        return LOG_CRIT
                else:
                        return LOG_ALERT
