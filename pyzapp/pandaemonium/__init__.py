# -*- coding: utf-8 -*-
"""
=========
Copyright
=========

    - Copyright 2014 Ethan Furman -- All rights reserved.
    - Author: Ethan Furman
    - Contact: ethan@stoneleaf.us

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
    - Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    - Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    - The names of contributors may not be used to endorse or promote
      products derived from this software without specific prior written
      permission.

THIS SOFTWARE IS PROVIDED ''AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL
ITS CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""


import atexit
import datetime
import errno
import logging
import os
import pwd
import resource
import signal
import socket
import sys
import threading
import time

try:
    from Queue import Queue
except ImportError:
    from queue import Queue

if hasattr(os, 'initgroups'):
    INITGROUPS = True
else:
    INITGROUPS = False

__all__ = [
        'Daemon', 'DaemonError', 'Parent', 'check_stage',
        'LockError', 'NotMyLock', 'LockFailed', 'AlreadyLocked', 'PidLockFile',
        'FileTracker',
        ]

class NullHandler(logging.Handler):
    """
    This handler does nothing. It's intended to be used to avoid the
    "No handlers could be found for logger XXX" one-off warning. This is
    important for library code, which may contain code to log events. If a user
    of the library does not configure logging, the one-off warning might be
    produced; to avoid this, the library developer simply needs to instantiate
    a NullHandler and add it to the top-level logger of the library module or
    package.

    Taken from 2.7 lib.
    """
    def handle(self, record):
        """Stub."""

    def emit(self, record):
        """Stub."""

    def createLock(self):
        self.lock = None

logger = logging.getLogger('pandaemonium')
logger.addHandler(NullHandler())

version = 0, 9, 2, 1

STDIN = 0
STDOUT = 1
STDERR = 2

INIT_PID = 1

AUTO = 'auto'

signal_map = dict(
    SIGTERM = 'sig_terminate',
    SIGTSTP = None,
    SIGTTIN = None,
    SIGTTOU = None,
    )

try:
    basestring
    baseint = int, long
except NameError:
    basestring = str
    baseint = int

def check_stage(func):
    def wrapper(self):
        first = self._stage_completed + 1
        stage = int(func.__name__[-1])
        if self._stage_completed >= stage or self.i_am == 'parent':
            raise DaemonError("Attempted to run stage %s twice" % stage)
        for i in range(first, stage):
            next_stage = getattr(self, 'stage%s' % i)
            next_stage()
        self._logger.debug('stage %s', stage)
        func(self)
        self._stage_completed = stage
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


class Parent(SystemExit):
    """
    raised by parent method after child detaches
    """


class DaemonError(Exception):
    """
    Exception raised if errors found in Daemon set up or processing"
    """


class Daemon(object):
    """
    Turn current process into a daemon.
    """

    i_am = 'main'

    def __init__(self,
            target=None,                # function to run as daemon
            args=None,                  # args and kwargs for function
            kwargs=None,
            detach=AUTO,                # True means do it, False means don't,
                                        # AUTO means True unless started by init
                                        # or superserver
            working_directory='/',      # directory to change to
            umask=0o077,                # only allow the daemon access to its files
            prevent_core=True,          # don't write core files
            process_ids=AUTO,           # uid, gid to switch to (AUTO means ask
                                        # the os who is really running things)
            init_groups=AUTO,           # default to True if present in 'os'
            pid_file=None,              # string or actual locking file
            inherit_files=None,         # iterable of files or fds to not close
            signal_map=None,            # map of signals:functions for daemon
            stdin=None,                 # redirect stdin after daemonization
            stdout=None,                # redirect stdout after daemonization
            stderr=None,                # redirect stderr after daemonization
            ):
        self._logger = logging.getLogger('pandaemonium.' + self.__class__.__name__)
        self._logger.debug('creating daemon')
        self.prevent_core = prevent_core
        if prevent_core:
            prevent_core_dump()
        self.target = target
        if args is None:
            args = tuple()
        self.args = args
        if kwargs is None:
            kwargs = dict()
        self.kwargs = kwargs
        self.detach = detach
        self.working_directory = working_directory
        self.umask = umask
        if process_ids == AUTO:
            process_ids = os.getuid(), os.getgid()
        elif process_ids is None:
            process_ids = None, None
        self.uid, self.gid = process_ids
        self.init_groups = init_groups
        self.pid_file = pid_file
        self.inherit_files = inherit_files
        self.signal_map = signal_map
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self._redirect = False
        self._stage_completed = 0
        self._logger.debug('finished __init__')

    def activate(self):
        """
        Enter daemon mode and return to caller (parent exits).
        """
        self._logger.debug('calling activate')
        self.i_am = 'daemon'
        if self._stage_completed == 10:
            raise DaemonError("daemon already started/activated")
        try:
            if self._stage_completed < 9:
                self.stage9()
            self._stage_completed = 10
        except Parent:
            self.i_am = 'parent'
            if self.output:
                print(self.output)
            if self.error:
                print(self.error)
            os._exit(os.EX_OK)

    def __detach(self):
        """
        Detach from current session.  Parent raises exception, child keeps going.
        """
        from_daemon_stdout, to_parent_out = os.pipe()
        from_daemon_stderr, to_parent_err = os.pipe()
        comms_channel = Queue()
        # first fork
        pid = os.fork()
        if pid > 0:
            self.i_am = 'parent'
            os.close(to_parent_out)
            os.close(to_parent_err)
            self.monitor(comms_channel, from_daemon_stdout, from_daemon_stderr)
            raise Parent
        # redirect stdout/err for rest of daemon set up
        # both activate() and start() will have already set self.i_am to 'daemon'
        for stream, dest in ((sys.stdout, to_parent_out), (sys.stderr, to_parent_err)):
            if (
                    hasattr(stream, 'fileno')
                    and stream.fileno() in (STDOUT, STDERR)
                ):
                os.dup2(dest, stream.fileno())
        os.close(from_daemon_stdout)
        os.close(from_daemon_stderr)
        os.close(to_parent_out)
        os.close(to_parent_err)
        # start a new session
        os.setsid()
        # second fork
        pid = os.fork()
        if pid > 0:
            self.i_am = 'child'
            os._exit(os.EX_OK)

    def monitor(self, comms_channel, from_daemon_stdout, from_daemon_stderr):
        """
        Parent gets telemetry readings from daemon
        """
        def read_comm(name, channel):
            while True:
                data = os.read(channel, 1024)
                comms_channel.put((name, data))
                if not data:
                    os.close(channel)
                    break
        threading.Thread(target=read_comm, args=('out', from_daemon_stdout)).start()
        threading.Thread(target=read_comm, args=('err', from_daemon_stderr)).start()
        output = bytes()
        error = bytes()
        active = 2
        while active:
            source, data = comms_channel.get()
            if not data:
                active -= 1
            if source == 'err':
                error += data
            else:
                output += data
        self.output = output.decode('utf8')
        self.error = error.decode('utf8')

    def report(self):
        """
        print output and error attributes, and quit
        """
        # .start() now prints automatically on error, but this is still useful
        # for printing normal output when no error occurs.
        print(self.output)
        raise SystemExit

    def run(self):
        """
        Either override this method, or pass target function to __init__.
        """
        if self.target is None:
            raise DaemonError('nothing to do')
        self._logger.info('running target: %s', self.target.__name__)
        self.target(*self.args, **self.kwargs)

    def __set_signals(self):
        """
        Map terminate and job control signals, and whatever user supplied.
        """
        self.set_signals()
        self.handler_map = hm = {}
        for sig, func in self.signal_map.items():
            if not isinstance(sig, baseint):
                raise DaemonError("%r is not a valid signal" % sig)
            if func is None:
                func = signal.SIG_IGN
            elif isinstance(func, basestring):
                func = getattr(self, func)
            hm[sig] = func
            signal.signal(sig, func)

    def set_signals(self):
        """
        Either override this method, or give a signal_map to __init__, for fine
        tuning which signals are handled.
        """
        sm = dict([
            (getattr(signal, name), handler)
            for name, handler in signal_map.items()
            if hasattr(signal, name)
            ])
        sm.update(self.signal_map or {})
        self.signal_map = sm

    def sig_terminate(self, signal, stack_frame):
        raise SystemExit("Terminated by signal %s" % signal)

    @check_stage
    def stage1(self):
        """
        Detach (if necessary), and redirect stdout to monitor_startup (if detaching).
        """
        self.i_am = 'daemon'
        self.inherit_files = set(self.inherit_files or [])
        if isinstance(self.stdin, basestring):
            def write_to_stdin(data):
                os.write(w, data.encode('utf-8'))
                os.close(w)
            r, w = os.pipe()
            text = self.stdin
            threading.Thread(target=write_to_stdin, args=(text, )).start()
            r = os.fdopen(r)
            self.stdin = r
            self._logger.debug('  stdin swap complete')
        if self.detach == AUTO:
            if started_by_init():
                self._logger.debug('  started_by_init')
                self.detach = False
            elif started_by_super_server():
                self._logger.debug('  started_by_super_server')
                self.inherit_files |= set([0, 1, 2])
                self.detach = False
            else:
                self.detach = True
        if self.detach:
            self._logger.debug('  checking std streams')
            for stream in (self.stdin, self.stdout, self.stderr):
                if stream is not None:
                    self.inherit_files.add(stream)
            self._logger.debug('  detaching')
            self.__detach()
            self._logger.debug('  detached')
            self._redirect = True

    @check_stage
    def stage2(self):
        """
        Turn off core dumps.
        """
        if self.prevent_core:
            self._logger.debug('  turning off core dumps')
            prevent_core_dump()

    @check_stage
    def stage3(self):
        """
        Set uid & gid (possibly losing privilege), call os.initgroups().
        """
        if self.init_groups != AUTO:
            init_groups = self.init_groups
        else:
            init_groups = os.getuid() == 0 and INITGROUPS
        if init_groups:
            self._logger.debug('  calling initgroups')
            if not INITGROUPS:
                raise DaemonError('initgroups not supported in this version of Python')
            target_uid = self.uid or os.geteuid()
            target_gid = self.gid or os.getegid()
            name = pwd.getpwuid(target_uid).pw_name
            os.initgroups(name, target_gid)
        if self.gid is not None:
            self._logger.debug('  setting gid: %s' % self.gid)
            os.setgid(self.gid)
        if self.uid is not None:
            self._logger.debug('  setting uid: %s' % self.uid)
            os.setuid(self.uid)

    @check_stage
    def stage4(self):
        """
        Change umask.

        Default setting allows group and world nothing.
        """
        self._logger.debug('  setting umask: 0o%03o', self.umask)
        os.umask(self.umask)

    @check_stage
    def stage5(self):
        """
        Change working directory (default is /).
        """
        if not self.working_directory:
            raise DaemonError(
                    'working_directory must be a valid path (received %r)' %
                    self.working_directory
                    )
        self._logger.debug('  changing working directory to: %s' % self.working_directory)
        os.chdir(self.working_directory)

    @check_stage
    def stage6(self):
        """
        Set up PID file.
        """
        self._logger.debug('  self.pid_file: %r' % self.pid_file)
        if self.pid_file is not None:
            self._logger.debug('  locking pid file')
            pid_file = self.pid_file
            if isinstance(pid_file, basestring):
                self.pid_file = pid_file = PidLockFile(pid_file)
                pid_file.acquire()
                atexit.register(self.pid_file.break_lock)
            pid = pid_file.seal()
            self._logger.debug('  pid: %s' % pid)

    @check_stage
    def stage7(self):
        """
        Set up signal handlers.
        """
        self._logger.debug('  setting signal handlers')
        self.__set_signals()

    @check_stage
    def stage8(self):
        """
        Close open files.
        """
        self._logger.debug('  closing open files')
        close_open_files(exclude=self.inherit_files)

    @check_stage
    def stage9(self):
        """
        If detached, redirect streams to user supplied, or os.devnul.
        """
        if self._redirect:
            self._logger.debug('  redirecting standard streams:')
            if STDIN not in self.inherit_files or self.stdin is not None:
                self._logger.debug('    stdin  --> %s' % self.stdin)
                redirect(self.stdin, STDIN)
            if STDOUT not in self.inherit_files or self.stdout is not None:
                self._logger.debug('    stdout --> %s' % self.stdout)
                redirect(self.stdout, STDOUT)
            if STDERR not in self.inherit_files or self.stderr is not None:
                self._logger.debug('    stderr --> %s' % self.stderr)
                redirect(self.stderr, STDERR)
            self._logger.debug('  done redirecting')

    def start(self):
        """
        Enter daemon mode and call target (if it exists).
        """
        self._logger.debug('calling start')
        if self._stage_completed == 10:
            raise DaemonError("daemon already started/activated")
        try:
            if self._stage_completed < 9:
                self.stage9()
            self._stage_completed = 10
            self.run()
            raise SystemExit
        except Parent:
            if self.error:
                print(self.output)
                raise DaemonError('\n' + self.error)
            return


class LockError(Exception):
    """
    Base class for lock-file errors.
    """

    def __init__(self, msg):
        super(LockError, self).__init__(msg)
        logger.error(msg)


class NotMyLock(LockError):
    """
    Lock file owned by another PID.
    """

class LockFailed(LockError):
    """
    Unable to lock file.
    """

class AlreadyLocked(LockError):
    """
    Lock has already been obtained.
    """


class LockNotReentrant(AlreadyLocked):
    """
    Lock is sealed and not reentrant.
    """


class PidLockFile(object):
    """
    Simple daemon status via a pid file.
    """
    def __init__(self, file_name, timeout=0, reentrant=False):
        """
        file_name and timeout to be used for pid file. (timeout is in minutes)
        """
        self._logger = logging.getLogger('pandaemonium.PidLockFile')
        self._logger.info('creating lock for %s', file_name)
        if not file_name or file_name[0] != '/':
            raise LockError("%r is not an absolute path" % file_name)
        self.file_name = file_name
        if timeout is None:
            timeout = 0
        self.timeout = timeout
        self.reentrant = reentrant
        self._file_obj = None
        self._lock_count = 0
        # last data read from file
        self.last_read_pid = None
        self.last_read_timestamp = None
        # pid used to lock file
        self.my_pid = None
        self.outside_lock = False
        # timestamp of file/lock creation
        self.my_timestamp = None

    def __enter__(self):
        """
        Acquire and seal the lock.
        """
        self._logger.debug('%s: entering lock (current count: %s)', self.file_name, self._lock_count)
        if self._lock_count and not self.reentrant:
            self._logger.error('%s: lock already sealed and not reentrant', self.file_name)
            raise LockNotReentrant('lock is already sealed and is not reentrant')
        if not self._lock_count:
            self.seal()
        else:
            self._lock_count += 1
        return self

    def __exit__(self, *args):
        """
        Release lock.
        """
        self._logger.debug('%s: exiting lock (current count: %s)', self.file_name, self._lock_count)
        if self._lock_count > 1:
            self._lock_count -= 1
        elif self._lock_count == 1:
            self.release()
        else:
            self._logger.error('%s: lock count of of sync', self.file_name)
            raise LockError('%s: lock count out of sync' % self.file_name)

    def acquire(self, timeout=None):
        """
        Create the file, establishing the lock, but do not write the PID. (timeout is in minutes)
        """
        self._logger.debug('%s: acquiring lock', self.file_name)
        if self.my_pid is not None:
            if self.my_pid != self.read_pid():
                # something else stole our lock
                self._logger.error('%s: lock has been stolen (possibly by %r)', self.file_name, self.last_read_pid)
                raise LockError('lock stolen (by %s?)' % self.last_read_pid)
            elif not self.reentrant:
                self._logger.error('%s: lock already sealed and not reentrant', self.file_name)
                raise LockNotReentrant('non-reentrant lock already sealed')
            else:
                self._logger.error('%s: reentrancy requires using the context manager protocol', self.file_name)
                raise LockError('reentrant lock used outside context manager protocol')
        elif self._file_obj is not None:
            # lock has already been acquired but not sealed
            self._logger.error('%s: lock already acquired', self.file_name)
            raise LockError('lock already acquired')
        elif self.read_pid() in (os.getpid(), os.getppid()):
            # third-party lock
            self._logger.debug('%s: third-party lock detected', self.file_name)
            self.outside_lock = True
            self._file_obj = _dummy_file
            return
        if self.is_stale():
            self._logger.debug('%s: lock is stale', self.file_name)
            self.break_lock()
        if timeout is None:
            timeout = self.timeout
        timeout *= 60
        end_time = time.time() + timeout
        while True:
            self._logger.debug('%s: trying to create lock', self.file_name)
            try:
                fd = os.open(
                        self.file_name,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        0o644,
                        )
            except OSError:
                exc = sys.exc_info()[1]
                if exc.errno != errno.EEXIST:
                    self._logger.error('unable to create %s', self.file_name)
                    raise LockFailed('Unable to create %s' % self.file_name)
                elif timeout <= 0:
                    self._logger.error('%s: already locked', self.file_name)
                    raise AlreadyLocked('%s: already locked' % self.file_name)
                elif time.time() <= end_time:
                    self._logger.debug('%s: lock taken, sleeping', self.file_name)
                    time.sleep(60)
                else:
                    # out of attempts
                    self._logger.debug('%s: already locked', self.file_name)
                    raise AlreadyLocked('%s: already locked' % self.file_name)
            else:
                self._file_obj = os.fdopen(fd, 'w')
                self._logger.debug('%s: lock created', self.file_name)
                break

    def break_lock(self):
        """
        Remove the file, thus breaking the lock.
        """
        self._logger.debug('%s: removing lock', self.file_name)
        if self._file_obj is not None:
            self._file_obj.close()
            self._file_obj = None
        self.my_pid = None
        self.last_read_pid = None
        self.my_timestamp = None
        self.last_read_timestamp = None
        self._lock_count = 0
        if self.outside_lock:
            self._logger.debug('%s: third-party lock, removing reference, not removing file', self.file_name)
            self.outside_lock = False
            return
        try:
            os.unlink(self.file_name)
        except OSError:
            exc = sys.exc_info()[1]
            if exc.errno == errno.ENOENT:
                self._logger.warning('%s: unable to remove -> does not exist', self.file_name)
            if exc.errno == errno.EACCES:
                self._logger.warning('%s: unable to remove -> permission denied', self.file_name)
        except Exception:
            exc = sys.exc_info()[1]
            self._logger.exception('%s: unable to break lock -> %s', self.file_name, exc)

    def is_active(self):
        """
        Return True if the pid file exists, and is not stale.
        """
        self._logger.debug('%s: checking if active', self.file_name)
        return os.path.exists(self.file_name) and not self.is_stale()

    def is_locked(self):
        """
        Return True if the pid file is locking this process or this process' parent.
        """
        self._logger.debug('%s: checking if locked', self.file_name)
        return self.read_pid() in (os.getpid(), os.getppid())

    def is_primary(self):
        """
        Return True if this file is my lock and is owned by me.

        Always returns False until seal/acquire has been called or with-block entered.
        """
        self._logger.debug('%s: checking if primary', self.file_name)
        return (
                self._file_obj is not None
                or
                self.read_pid() == self.my_pid != None and not self.outside_lock
                )

    def is_stale(self, timeout=1):
        """
        Return True if the pid file is empty for 60 seconds, or contains a PID and no such process exists.

        (timeout is in minutes)
        """
        self._logger.debug('%s: checking if stale', self.file_name)
        if self._file_obj is not None:
            self._logger.debug('%s: our lock, definitely not stale', self.file_name)
            return False
        elif not os.path.exists(self.file_name):
            self._logger.debug("%s: lock doesn't exist, can't be stale", self.file_name)
            return False
        pid = self.read_pid()
        if pid is None:
            self._logger.debug('%s: not our lock, but not yet sealed', self.file_name)
            # give it a few seconds to seal; if it doesn't
            # consider it abandoned and therefore stale
            end_time = time.time() + timeout * 60
            while end_time > time.time():
                time.sleep(1)
                pid = self.read_pid()
                if pid is not None:
                    break
            else:
                self._logger.debug('%s: still not sealed (== stale)', self.file_name)
                return True
        try:
            # see if there is such a process by sending the null-signal
            self._logger.debug('%s: checking on pid: %s', self.file_name, pid)
            os.kill(pid, 0)
        except OSError:
            exc = sys.exc_info()[1]
            if exc.errno == errno.ESRCH:
                self._logger.debug("%s: process <%s> is dead", self.file_name, pid)
                return True
            self._logger.exception('%s: unhandled exception while checking for stale lock -> %s', self.file_name, exc)
        self._logger.debug("%s: process <%s> is active", self.file_name, pid)
        return False

    def read_pid(self):
        "Return PID stored in file, or None"
        self._logger.debug('%s: reading pid and timestamp', self.file_name)
        if self._file_obj is not None:
            # we are in between acquiring and sealing the lock
            self._logger.debug('%s: our lock, but not yet sealed', self.file_name)
            self.last_read_pid = None
            self.last_read_timestamp = None
            return None
        timestamp = None
        try:
            pid_file = open(self.file_name)
            data = pid_file.read()
            pid_file.close()
            data = data.split('\n')
            pid = int(data[0])
        except Exception:
            pid = None
            data = ''
        if data[1:]:
            try:
                timestamp = datetime.datetime.strptime(data[1], '%Y-%m-%d.%H:%M:%S')
            except ValueError:
                pass
        self._logger.debug('%s: stored pid is <%s>', self.file_name, pid)
        self._logger.debug('%s: stored timestamp is <%s>', self.file_name, timestamp)
        self.last_read_pid = pid
        self.last_read_timestamp = timestamp
        return pid

    def release(self):
        """
        delete the file/lock if it is ours
        """
        # release if we have the file open, or if we have sealed the lock and it
        # is still ours
        #
        # first check that we should have a seal
        # then check that file matches our expectations
        # then remove file
        self._logger.info('%s: releasing lock', self.file_name)
        if self._file_obj is not None:
            self.break_lock()
        elif self.my_pid is not None:
            # we should have the lock -- do we?
            if self.read_pid() == self.my_pid:
                # yes -- but only once?
                if self._lock_count > 1:
                    self._logger.error('%s: reentrant lock must use context manager protocol', self.file_name)
                    raise LockError('%s: reentrant lock used outside context manager protocol' % self.file_name)
                self.break_lock()
                return

            # nope, try to log useful error
            if self.last_read_pid is not None:
                # file exists and has another PID in it
                self._logger.warning('%s: hijacked by process <%s>', self.file_name, self.last_read_pid)
            elif os.path.exists(self.file_name):
                # file exists but is empty
                self._logger.warning('%s: hijacked by unknown process', self.file_name)
            else:
                # file does not exist
                self._logger.warning('%s: removed by unknown process', self.file_name)
        else:
            self._logger.error('%s: not our lock', self.file_name)
            raise LockError('%s: attempt to release unowned lock' % self.file_name)

    def seal(self, pid=None):
        """
        Write PID to the file lock, and close the file.  Defaults to current PID.

        This should only be called after becoming a daemon if using default PID.
        """
        pid = pid if pid is not None else os.getpid()
        timestamp = "%s" % datetime.datetime.now().strftime("%Y-%m-%d.%H:%M:%S")
        self._logger.info('%s: sealing lock with %s and %s', self.file_name, pid, timestamp)
        if self.read_pid() is not None and self.last_read_pid == self.my_pid:
            # already sealed
            if self.reentrant:
                self._logger.error('%s: reentrant lock must use context manager protocol', self.file_name)
                raise LockError('%s: reentrant lock used outside context manager protocol')
            else:
                self._logger.error('%s: lock already sealed and not reentrant', self.file_name)
                raise LockNotReentrant('non-reentrant lock already sealed')
        if self._file_obj is None:
            self.acquire()
        self.my_pid = pid
        self.my_timestamp = timestamp
        self._file_obj.write("%s\n" % pid)
        self._file_obj.write(self.my_timestamp)
        self._file_obj.close()
        self._file_obj = None
        # double-check our pid is in the file
        if self.read_pid() not in (pid, os.getppid()):
            self._logger.error('%s: lock has been hijacked', self.file_name)
            raise LockError('%s: lock has been hijacked' % self.file_name)
        self._lock_count = 1
        return pid

class _DummyFile(object):
    def write(*args, **kwds):
        pass
    def close(*args, **kwds):
        pass
_dummy_file = _DummyFile()

ft_sentinel = object()
class FileTracker(object):
    """
    useful for tracking files that are still open at time of daemonization
    that need to stay open (such as /dev/random)
    """
    builtin_open = open
    _cache = {}
    _active = False
    _logger = logging.getLogger('pandaemonium.FileTracker')
    @classmethod
    def __call__(cls, name, *args, **kwds):
        file = cls.builtin_open(name, *args, **kwds)
        cls._cache[name] = file
        return file
    @classmethod
    def active(cls, name, default=ft_sentinel):
        """
        return the open file object referred to by name, or None if no longer open
        """
        cls.open_files()
        try:
            return cls._cache[name]
        except KeyError:
            if default is ft_sentinel:
                raise ValueError('%s is not open' % name)
            return default
    @classmethod
    def open_files(cls):
        """
        return list of (name, file_object) tuples for all tracked, open files
        """
        closed = []
        for name, file in cls._cache.items():
            if file.closed:
                closed.append(name)
        for name in closed:
            cls._cache.pop(name)
        return cls._cache.items()
    @classmethod
    def install(cls):
        """
        start tracking calls to `open()`
        """
        if cls._active is not True:
            cls._active = True
            if isinstance(__builtins__, dict):
                __builtins__['open'] = cls()
            else:
                __builtins__.open = cls()
            cls._logger.debug('FileTracker active')


def close_open_files(exclude):
    max_files = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    keep = set()
    for file in exclude:
        if isinstance(file, baseint):
            keep.add(file)
        elif hasattr(file, 'fileno'):
            keep.add(file.fileno())
        else:
            raise ValueError(
                    'files to not close should be either a file descriptor, '
                    'or a file-type object, not %r (%s)' % (type(file), file))
    for fd in range(max_files, -1, -1):
        if fd in keep:
            continue
        try:
            os.close(fd)
        except OSError:
            exc = sys.exc_info()[1]
            if exc.errno == errno.EBADF:
                continue
            raise

def prevent_core_dump():
    """
    Set resource limits to inhibit creation of a core file.
    """
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

def redirect(stream, target_fd):
    """
    Close target_fd and clone stream's fileno() onto it.
    """
    if stream is None:
        stream_fd = os.open(os.devnull, os.O_RDWR)
    elif not isinstance(stream, int):
        stream_fd = stream.fileno()
    else:
        stream_fd = stream
    os.dup2(stream_fd, target_fd)

def started_by_init():
    """Return True if this process was started by init.

    This is apparent by the parent process being 1.
    """
    return os.getppid() == INIT_PID

def started_by_super_server():
    """Return True if this process was started by the [x]inetd.

    This is apparent by the std streams being bound to a socket.
    """
    try:
        sock = socket.fromfd(STDIN, socket.AF_INET, socket.SOCK_STREAM)
        sock.getsockname()
        sock = None
    except (OSError, socket.error):
        sock = None
        exc = sys.exc_info()[1]
        if exc.errno == errno.ENOTSOCK:
            return False
    return True
