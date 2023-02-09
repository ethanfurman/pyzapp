"""
intelligently parses command lines

(help, kind, abbrev, type, choices, usage_name, remove, default)

  - help --> the help message

  - kind --> what kind of parameter
    - flag       --> simple boolean
    - option     --> option_name value
    - multi      --> option_name value option_name value
    - required   --> required_name value

  - abbrev is a one-character string (defaults to first letter of
    argument)

  - type is a callable that converts the arguments to any Python
    type; by default there is no conversion and type is effectively str

  - choices is a discrete sequence of values used to restrict the
    number of the valid options; by default there are no restrictions
    (i.e. choices=None)

  - usage_name is used as the name of the parameter in the help message

  - remove determines if this argument is removed from sys.argv

  - default is the default value, either converted with type if type is
    specified, or type becomes the default value's type if unspecified
"""

# future imports
from __future__ import print_function

# version
version = 0, 86, 15

# imports
import sys
pyver = sys.version_info[:2]
PY2 = pyver < (3, )
PY3 = pyver >= (3, )
PY25 = (2, 5)
PY26 = (2, 6)
PY33 = (3, 3)
PY34 = (3, 4)
PY35 = (3, 5)
PY36 = (3, 6)

is_win = sys.platform.startswith('win')
if is_win:
    import signal
    KILL_SIGNALS = [getattr(signal, sig) for sig in ('SIGTERM') if hasattr(signal, sig)]
    from subprocess import Popen, PIPE
else:
    from pty import fork
    import resource
    import termios
    # from syslog import syslog
    import signal
    KILL_SIGNALS = [getattr(signal, sig) for sig in ('SIGTERM', 'SIGQUIT', 'SIGKILL') if hasattr(signal, sig)]
    from subprocess import Popen, PIPE

from threading import Thread
try:
    from Queue import Queue, Empty
except ImportError:
    from queue import Queue, Empty

if PY3:
    from inspect import getfullargspec
    def getargspec(method):
        args, varargs, keywords, defaults, _, _, _ = getfullargspec(method)
        return args, varargs, keywords, defaults
else:
    from inspect import getargspec

import ast
import codecs
import datetime
import email
import errno
import locale
import logging
import os
import re
import shlex
import smtplib
import socket
import textwrap
import threading
import time
import traceback
from aenum import Enum, IntEnum, Flag, export
from collections import OrderedDict
from math import floor
from sys import stdin, stdout, stderr
from types import GeneratorType

# locks, etc.
print_lock = threading.RLock()
io_lock = threading.Lock()

# py 2/3 compatibility shims
raise_with_traceback = None
if PY2:
    import __builtin__ as builtins
    b = str
    u = unicode
    bytes = b
    str = u
    unicode = u
    basestring = bytes, unicode
    integer = int, long
    number = int, long, float
    from itertools import izip_longest as zip_longest
    from __builtin__ import print as _print
    from __builtin__ import raw_input as raw_input
    from __builtin__ import type as type_of
    exec(textwrap.dedent('''\
        def raise_with_traceback(exc, tb):
            raise exc, None, tb
            '''))
else:
    import builtins
    b = bytes
    u = str
    bytes = b
    unicode = u
    str = u
    basestring = unicode,
    integer = int,
    number = int, float
    from itertools import zip_longest
    from builtins import print as _print
    from builtins import input as raw_input
    from builtins import type as type_of
    exec(textwrap.dedent('''\
        def raise_with_traceback(exc, tb):
            raise exc.with_traceback(tb)
            '''))

    # keep pyflakes happy
builtins

def _input(*args, **kwds):
    from warnings import warn
    warn('`_input` is deprecated; use `raw_input` instead.', stacklevel=2)
    return raw_input(*args, **kwds)

# data
# __all__ includes the common elements that might be used frequently in scripts
__all__ = (
    'Alias', 'Command', 'Script', 'Main', 'Run', 'Spec',
    'Bool','InputFile', 'OutputFile', 'IniError', 'IniFile', 'OrmError', 'OrmFile', 'NameSpace', 'OrmSection',
    'FLAG', 'OPTION', 'MULTI', 'MULTIREQ', 'REQUIRED',
    'ScriptionError', 'ExecuteError', 'FailedPassword', 'TimeoutError', 'Execute', 'Job', 'ProgressView', 'ViewProgress',
    'abort', 'echo', 'error', 'get_response', 'help', 'input', 'raw_input', 'mail', 'user_ids', 'print', 'box', 'table_display',
    'stdout', 'stderr', 'wait_and_check', 'b', 'bytes', 'str', 'u', 'unicode', 'ColorTemplate', 'Color',
    'basestring', 'integer', 'number', 'raise_with_traceback',
    'Trivalent', 'Truthy', 'Unknown', 'Falsey', 'Exit', 'Var', 'Sentinel',
    # the following are actually injected directly into the calling module, but are
    # added here as well for pylakes' benefit
    'script_main',          # Script decorator instance if used
    'script_aliases',       # alternate command names
    'script_commands',      # defined commands
    'script_command',       # callback to run chosen command function
    'script_command_name',  # name of above
    'script_command_line',  # original command line
    'script_fullname',      # sys.argv[0]
    'script_name',          # above without path
    'script_verbosity',     # vebosity level from command line
    'verbose',              # same as above
    'script_module',        # module that imported scription
    'module',               # same as above
    'script_abort_message', # copy of message sent to abort()
    'script_exception_lines', # traceback from unhandled exception
    )

VERBOSITY = 0
SCRIPTION_DEBUG = 0
LOCALE_ENCODING = locale.getpreferredencoding() or 'utf-8'
THREAD_STORAGE = threading.local()
THREAD_STORAGE.script_main = None

# bootstrap VERBOSITY
tmp = os.environ.get('SCRIPTION_VERBOSITY')
if tmp:
    try:
        VERBOSITY = int(tmp)
    except ValueError:
        VERBOSITY = 1
        print('BAD VALUE FOR SCRIPTION_VEBOSITY: %r' % (tmp, ))
# bootstrap SCRIPTION_DEBUG
tmp = os.environ.get('SCRIPTION_DEBUG')
if tmp:
    try:
        SCRIPTION_DEBUG = int(tmp)
        print('\n-------------------\n')
    except ValueError:
        SCRIPTION_DEBUG = 1
        print('BAD VALUE FOR SCRIPTION_DEBUG: %r' % (tmp, ))
del tmp
for arg in sys.argv:
    if arg.startswith(('--SCRIPTION_DEBUG', '--SCRIPTION-DEBUG')):
        SCRIPTION_DEBUG = 1
        if arg[17:] == '=2':
            SCRIPTION_DEBUG = 2
        elif arg[17:] == '=3':
            SCRIPTION_DEBUG = 3
        elif arg[17:] == '=4':
            SCRIPTION_DEBUG = 4
        elif arg[17:] == '=5':
            SCRIPTION_DEBUG = 5

module = verbose = script_main = None
script_fullname = script_name = script_verbosity = script_command = script_command_name = script_command_line = None
script_abort_message = script_exception_lines = None
script_module = {}
script_commands = {}
script_aliases = {}

registered = False
run_once = False

# for use with table printing
try:
    raise ImportError
    from dbf import Date, DateTime, Time, Logical, Quantum
    dates = datetime.date, Date
    datetimes = datetime.datetime, DateTime
    times = datetime.time, Time
    logical = bool, Logical, Quantum
    fixed = dates + datetimes + times + logical
except ImportError:
    dates = datetime.date
    datetimes = datetime.datetime
    times = datetime.time
    logical = bool
    fixed = dates, datetimes, times, logical
left = basestring
right = integer

class undefined(object):
    def __repr__(self):
        return '<undefined>'
    def __bool__(self):
        return False
    __nonzero__ = __bool__
undefined = undefined()

# back-compatibility
# the __version__ and __VERSION__ are for compatibility with existing code,
# but those names are reserved by the Python interpreter and should not be
# used
_version_strings = 'version', 'VERSION', '__version__', '__VERSION__'

# set logging
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

logger = logging.getLogger('scription')
logger.addHandler(NullHandler())

# enumerations
class DocEnum(Enum):
    """
    compares equal to all cased versions of its name
    accepts a docstring for each member
    """
    _init_ = 'value __doc__'

    def _generate_next_value_(name, start, count, last_values, *args, **kwds):
        return (name.lower(), ) + args

    def __eq__(self, other):
        if isinstance(other, basestring):
            return self._value_ == other.lower()
        elif isinstance(other, self.__class__):
            return self is other
        else:
            return NotImplemented

    def __hash__(self):
        return hash(self._value_)

    def __ne__(self, other):
        if isinstance(other, basestring):
            return self._value_ != other.lower()
        elif isinstance(other, self.__class__):
            return not self is other
        else:
            return NotImplemented

    def __repr__(self):
        return '<%s.%s>' % (self.__class__.__name__, self._name_)

class Exit(IntEnum):
    '''
    Non-zero values indicate an error
    '''
    _init_ = 'value __doc__'
    _ignore_ = 'v name text sig'

    Success                         =   0, 'ran successfully'
    Error = UnknownError = Unknown  =   1, 'unspecified error'
    MissingFile                     =   2, 'file not found'
    InvalidPath                     =   3, 'path not possible'
    ScriptionError                  =  63, 'fatal scription error'
    UsageError     = Usage          =  64, 'command line usage error'
    DataError                       =  65, 'data error'
    NoInput                         =  66, 'cannot open input'
    NoUser                          =  67, 'user unknown'
    NoHost                          =  68, 'host unknown'
    Unavailable                     =  69, 'service unavailable'
    SoftwareError  = Software       =  70, 'internal error'
    OsError                         =  71, 'system error'
    OsFileError    = OsFile         =  72, 'critical OS file missing'
    CantCreate                      =  73, 'cannot create (user) output file'
    IoError                         =  74, 'input/output error'
    TempFail                        =  75, 'temp failure; user is invited to retry'
    ProtocolError  = Protocol       =  76, 'remote error in protocol'
    NoPermission                    =  77, 'permission denied'
    ConfigError    = Config         =  78, 'configuration error'
    CannotExecute                   = 126, 'command invoked cannot execute'
    ExitOutOfRange                  = 255, 'exit code out of range'
    InvalidExitCode                 = 127, 'invalid argument to exit'
    UserCancelled                   = 130, 'ctrl-c received'

    # add signal exit codes
    v = vars()
    for name, text in (
            ('SIGHUP',  'controlling process died'),
            ('SIGINT',  'interrupt from keyboard'),
            ('SIGQUIT', 'quit from keyboard'),
            ('SIGILL',  'illegal instruction (machine code)'),
            ('SIGABRT', 'abort from abort(3)'),
            ('SIGBUS',  'bus error (bad memory address)'),
            ('SIGFPE',  'floating point exception'),
            ('SIGKILL', 'kill'),
            ('SIGUSR1', 'user-defined signal 1'),
            ('SIGSEGV', 'invalid memory reference'),
            ('SIGUSR2', 'user-defined signal 2'),
            ('SIGPIPE', 'broken pipe, or write to read pipe'),
            ('SIGALRM', 'timer expired'),
            ('SIGTERM', 'terminate'),
            ('SIGCHILD', 'child error'),
        ):
        sig = getattr(signal, name, None)
        if sig is not None:
            v[name] = -sig, name
    # and add a catch-all unknown signal
    v['SIGNKWN'] = -128, 'invalid signal'


@export(globals())
class SpecKind(DocEnum):
    REQUIRED = "required value"
    OPTION = "single value per name"
    MULTI = "multiple values per name (list form, no whitespace)"
    MULTIREQ = "multiple values per name (list form, no whitespace, required)"
    FLAG = "boolean/trivalent value per name"


# exceptions
class ExecuteError(Exception):
    "errors raised by Execute/Job"
    def __init__(self, msg=None, process=None):
        self.process = process
        Exception.__init__(self, msg)

class FailedPassword(ExecuteError):
    "Bad or too few passwords"
    def __init__(self, msg=None, process=None):
        super(FailedPassword, self).__init__(msg or 'invalid/too few passwords', process)

class TimeoutError(ExecuteError):
    "Execute timed out"

class UnableToKillJob(ExecuteError):
    "Job will not die"

ExecutionError = ExecuteError   # deprecated
ExecuteTimeout = TimeoutError   # deprecated

class OrmError(ValueError):
    """
    used to signify errors in the ORM file
    """

class ScriptionError(Exception):
    "raised for errors in user script"
    def __init__(self, msg=False, returncode=Exit.ScriptionError, use_help=False):
        Exception.__init__(self, msg)
        self.returncode = returncode
        self.use_help = use_help

# internal
def _add_annotations(func, annotations, script=False):
    '''
    add annotations as __scription__ to func
    '''
    scription_debug('adding annotations to %r' % (func.__name__, ))
    params, varargs, keywords, defaults = getargspec(func)
    radio = {}
    if varargs:
        params.append(varargs)
    if keywords:
        params.append(keywords)
    errors = []
    default_order = 128
    # add global, order, and radio attributes
    for name, spec in annotations.items():
        scription_debug('  processing %r with %r' % (name, spec), verbose=2)
        annote = annotations[name]
        if name in params:
            annote._global = False
            annote._order = params.index(name)
        elif spec._target in params:
            # sort it out in the next loop
            pass
        elif not script:
            errors.append(name)
        else:
            annote._global = True
            annote._order = default_order
            default_order += 1
        if spec._radio:
            radio.setdefault(spec._radio, []).append(name)
    if errors:
        raise ScriptionError("name(s) %r not in %s's signature" % (errors, func.__name__))
    # assign correct targets
    for name, spec in annotations.items():
        if spec._target is not empty:
            target = annotations.get(spec._target)
            if target is None:
                errors.append(target)
            spec._order = target._order
            spec._global = target._global
    if errors:
        raise ScriptionError("target name(s) %r not in %s's annotations" % (errors, func.__name__))
    func.__scription__ = annotations
    func.names = sorted(annotations.keys())
    func.radio = radio
    func.all_params = sorted(params)
    func.named_params = sorted(params)

def _and_list(names):
    names = sorted([n.upper() for n in names])
    if len(names) == 2:
        return '%s and %s' % tuple(names)
    else:
        return '%s, and %s' % (', '.join(names[:-1]), names[-1])

class empty(object):
    def __add__(self, other):
        # adding emptiness to something else is just something else
        return other
    def __len__(self):
        # emptiness has zero length
        return 0
    def __nonzero__(self):
        return False
    __bool__ = __nonzero__
    def __repr__(self):
        return '<empty>'
    def __str__(self):
        return ''
empty = empty()

def _func_globals(func):
    '''
    return the function's globals
    '''
    if PY2:
        return func.func_globals
    else:
        return func.__globals__

def _get_version(from_module, _try_other=True):
    for ver in _version_strings:
        if from_module.get(ver):
            version = from_module.get(ver)
            if not isinstance(version, basestring):
                version = '.'.join([str(x) for x in version])
            break
    else:
        # try to find package name
        try:
            package = os.path.split(os.path.split(sys.modules['__main__'].__file__)[0])[1]
        except IndexError:
            version = 'unknown'
        else:
            if package in sys.modules and any(hasattr(sys.modules[package], v) for v in _version_strings):
                for ver in _version_strings:
                    version = getattr(sys.modules[package], ver, '')
                    if version:
                        break
                if not isinstance(version, basestring):
                    version = '.'.join([str(x) for x in version])
            elif _try_other:
                version = ' / '.join(_get_all_versions(from_module, _try_other=False))
            if not version.strip():
                version = 'unknown'
    return version

def _get_all_versions(from_module, _try_other=True):
    versions = ['%s=%s' % (from_module['module']['script_name'], _get_version(from_module, _try_other=False))]
    for name, module in sys.modules.items():
        fm_obj = from_module.get(name)
        if fm_obj is module:
            for ver in _version_strings:
                if hasattr(module, ver):
                    version = getattr(module, ver)
                    if not isinstance(version, basestring):
                        version = '.'.join(['%s' % x for x in version])
                    versions.append('%s=%s' % (name, version))
                    break
    versions.append('python=%s' % '.'.join([str(i) for i in sys.version_info]))
    return versions

def _help(func, script=False):
    '''
    create help from __scription__ annotations and header defaults
    '''
    scription_debug('_help for', func.__name__, verbose=3)
    params, vararg, keywordarg, defaults = getargspec(func)
    scription_debug('  PARAMS', params, vararg, keywordarg, defaults, verbose=3)
    params = func.params = list(params)
    vararg = func.vararg = [vararg] if vararg else []
    keywordarg = func.keywordarg = [keywordarg] if keywordarg else []
    annotations = func.__scription__
    pos = None
    max_pos = 0
    if not script:
        script_obj = script_module['script_main']
        script_func = getattr(script_obj, 'command', None)
        max_pos = getattr(script_func, 'max_pos', max_pos)
    for i, name in enumerate(params + vararg + keywordarg, start=max_pos):
        scription_debug('processing', name, verbose=3)
        if name[0] == '_':
            # ignore private params
            continue
        spec = annotations.get(name, None)
        pos = None
        if spec is None:
            raise ScriptionError('%s not annotated' % name)
        help, kind, abbrev, arg_type, choices, usage_name, remove, default, envvar, target = spec
        if name in vararg:
            spec._type_default = tuple()
            if kind is empty:
                kind = 'multi'
        elif name in keywordarg:
            spec._type_default = dict()
            if kind is empty:
                kind = 'option'
        elif kind == 'required':
            pos = max_pos
            max_pos += 1
        elif kind == 'multireq':
            pos = max_pos
            max_pos += 1
            if default:
                if isinstance(default, list):
                    default = tuple(default)
                elif not isinstance(default, tuple):
                    default = (default, )
        elif kind == 'flag':
            if abbrev is empty:
                abbrev = (name[0], )
        elif kind == 'option':
            if abbrev is empty:
                abbrev = (name[0], )
        elif kind == 'multi':
            if abbrev is empty:
                abbrev = (name[0], )
            if default:
                if isinstance(default, list):
                    default = tuple(default)
                elif not isinstance(default, tuple):
                    default = (default, )
        else:
            raise ValueError('unknown kind: %r' % kind)
        for ab in abbrev or ():
            if ab in annotations:
                raise ScriptionError('duplicate abbreviations: %r' % abbrev)
            if script and script_module['script_commands']:
                raise ScriptionError('Script must be defined before any Command')
            else:
                # check against Script
                script_obj = script_module['script_main']
                script_func = getattr(script_obj, 'command', None)
                script_annotations = getattr(script_func, '__scription__', {})
                if ab in script_annotations:
                    raise ScriptionError('abbreviation %r is duplicate of %r in Script command %r' % (ab, script_annotations[ab].__name__, script_func.__name__))
        usage_name = (usage_name or name).upper()
        if arg_type is _identity and default is not empty and default is not None:
            if kind in ('multi', 'multireq'):
                if default:
                    arg_type = type_of(default[0])
            else:
                arg_type = type_of(default)
        spec.kind = kind
        spec.abbrev = abbrev
        spec.type = arg_type
        spec.usage = usage_name
        if pos != max_pos:
            annotations[i] = spec
        annotations[name] = spec
        for ab in abbrev or ():
            annotations[ab] = spec
    usage_max = 0
    for annote in annotations.values():
        usage_max = max(usage_max, len(annote.usage))
    func._var_arg = func._kwd_arg = None
    if vararg:
        func._var_arg = annotations[vararg[0]]
    if keywordarg:
        func._kwd_arg = annotations[keywordarg[0]]
    if defaults:
        # check the defaults in the header
        for name, dflt in zip(reversed(params), reversed(defaults)):
            if name[0] == '_':
                # ignore private params
                continue
            annote = annotations[name]
            if annote._script_default:
                # default specified in two places
                raise ScriptionError('default value for %s specified in Spec and in header (%r, %r)' %
                        (name, annote._script_default, dflt))
            annote._use_default = True
            if annote.kind not in ('multi', 'multireq'):
                if annote.type is _identity and dflt is not None:
                    annote.type = type_of(dflt)
                annote._script_default = annote.type(dflt)
            else:
                if dflt is None:
                    annote._script_default = dflt
                else:
                    if not isinstance(dflt, tuple):
                        dflt = (dflt, )
                    if annote.type is _identity and dflt:
                        annote.type = type_of(dflt[0])
                    new_dflt = []
                    for d in dflt:
                        new_dflt.append(annote.type(d))
                    annote._script_default = tuple(new_dflt)
    # also prepare help for global options
    global_params = [n for n in func.names if n not in func.all_params]
    print_params = []
    in_required = True
    for param in global_params + params:
        if param[0] == '_':
            # ignore private params
            continue
        annote = annotations[param]
        if annote.kind != 'required' and in_required:
            in_required = False
            if vararg and func._var_arg.kind == 'multireq':
                print_params.append('%s [, %s [...]]' % (func._var_arg.usage, func._var_arg.usage))
        example = annote.usage
        if annote.kind == 'flag':
            if annote._script_default is True and annote._use_default:
                print_params.append('--no-%s' % example.lower())
            else:
                print_params.append('--%s' % example.lower())
        elif annote.kind == 'option':
            print_params.append('--%s %s' % (example.lower(), example))
        elif annote.kind == 'multi':
            print_params.append('--%s %s [--%s ...]' % (example.lower(), example, example.lower()))
        else:
            print_params.append(example)
    usage = print_params
    if vararg and func._var_arg.kind != 'multireq':
        usage.append("[%s [%s [...]]]" % (func._var_arg.usage, func._var_arg.usage))
    if keywordarg:
        usage.append("[name1=value1 [name2=value2 [...]]]")
    usage = [' '.join(usage), '']
    if func.__doc__:
        for line in func.__doc__.split('\n'):
            usage.append('    ' + line)
        usage.append('')
    name_order = []
    in_params = False
    for name in global_params + ['start'] + params:
        if name[0] == '_':
            # ignore private params
            continue
        if name == 'start':
            in_params = True
            continue
        annote = annotations[name]
        if in_params and annote.kind != 'required':
            in_params = False
            if vararg and func._var_arg.kind == 'multireq':
                name_order.append(vararg[0])
        name_order.append(name)
    if vararg and vararg[0] not in name_order:
        name_order.append(vararg[0])
    if keywordarg:
        name_order.append(keywordarg[0])
    for name in name_order:
        annote = annotations[name]
        choices = ''
        if annote._script_default in (empty, None) or '[default: ' in annote.help or annote.kind == 'flag':
            posi = ''
        elif not annote._use_default:
            posi = '[option default: ' + repr(annote._script_default) + ']'
        else:
            posi = '[default: ' + repr(annote._script_default) + ']'
        if annote.choices:
            choices = '[ %s ]' % ' | '.join(annote.choices)
        if annote._script_default is True and annote._use_default:
            annote_usage = 'NO-%s' % annote.usage.upper()
        else:
            annote_usage = '%s' % annote.usage.upper()
        usage.append('    %-*s   %s   %s %s' % (
            usage_max,
            annote_usage,
            annote.help,
            posi,
            choices,
            ))
    func.max_pos = max_pos
    func.__usage__ = '\n'.join(usage)

def _identity(*args):
    if len(args) == 1:
        return args[0]
    return args

def _init_script_module(func):
    scription_debug('creating script_module', verbose=2)
    global script_module
    script_module = _func_globals(func)
    script_module['module'] = NameSpace(script_module)
    script_module['script_module'] = script_module['module']
    script_module['script_name'] = '<unknown>'
    script_module['script_main'] = THREAD_STORAGE.script_main
    script_module['script_commands'] = {}
    script_module['script_aliases'] = {}
    script_module['script_command'] = None
    script_module['script_command_name'] = ''
    script_module['script_fullname'] = ''
    script_module['verbose'] = 0
    script_module['script_verbosity'] = script_module['verbose']
    script_module['script_abort_message'] = ''
    script_module['script_exception_lines'] = []


class NameSpace(object):

    def __init__(self, wrapped_dict=None):
        if wrapped_dict is not None:
            self.__dict__ = wrapped_dict

    def __repr__(self):
        return '%s(%r)' % (self.__class__.__name__, self.__dict__)

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return NotImplemented
        return self.__dict__ == other.__dict__

    def __ne__(self, other):
        if not isinstance(other, self.__class__):
            return NotImplemented
        return self.__dict__ != other.__dict__

    def __contains__(self, name):
        return name in self.__dict__

    def __iter__(self):
        for key, value in sorted(self.__dict__.items()):
            yield key, value

    def __getitem__(self, name):
        try:
            return self.__dict__[name]
        except KeyError:
            raise ScriptionError("namespace object has nothing named %r" % name)

    def __setitem__(self, name, value):
        self.__dict__[name] = value

    def get(self, key, default=None):
        # deprecated, will be removed by 1.0
        try:
            return self.__dict__[key]
        except KeyError:
            return default

def _rewrite_args(args):
    "prog -abc heh --foo bar  -->  prog -a -b -c heh --foo bar"
    scription_debug('incoming args: %r' % (args, ), verbose=2)
    new_args = []
    pass_through = False
    for arg in args:
        if arg == '--':
            pass_through = True
        if pass_through:
            new_args.append(arg)
            continue
        if arg.startswith('--') or not arg.startswith('-'):
            new_args.append(arg)
            continue
        if arg[2:3] == '=':
            new_args.append('-%s' % arg)
            continue
        for ch in arg[1:]:
            new_args.append('-%s' % ch)
    scription_debug('outgoing args: %r' % (new_args, ), verbose=2)
    return new_args

def _run_once(func, args, kwds):
    scription_debug('creating run_once function')
    cache = []
    def later():
        scription_debug('running later')
        global run_once
        if run_once:
            scription_debug('returning cached value')
            return cache[0]
        run_once = True
        scription_debug('calling function')
        result = func(*args, **kwds)
        cache.append(result)
        return result
    scription_debug('returning <later>')
    return later

def _split_on_comma(text):
    scription_debug('_split_on_comma(%r)' % (text,), verbose=2)
    if text.endswith(',') and not text.endswith('\\,'):
        raise ScriptionError('trailing "," in argument %r' % text)
    if ',' not in text:
        scription_debug('  -> %r' % ([text], ), verbose=2)
        return [text]
    elif '\\,' not in text:
        scription_debug('  -> %r' % text.split(','), verbose=2)
        return text.split(',')
    else:
        values = []
        new_value = []
        last_ch = None
        for ch in text+',':
            if last_ch == '\\':
                new_value.append(ch)
            elif ch == '\\':
                pass
            elif ch == ',':
                values.append(''.join(new_value))
                new_value = []
            else:
                new_value.append(ch)
            last_ch = ch
        if new_value:
            raise ScriptionError('trailing "\\" in argument %r' % text)
        scription_debug('  -> %r' % values, verbose=2)
        return values

def _usage(func, param_line_args):
    global VERBOSITY, SCRIPTION_DEBUG
    Script = script_module['script_main']
    scription_debug('_usage(%r, %r' % (func, param_line_args), verbose=2)
    program, param_line_args = param_line_args[0], _rewrite_args(param_line_args[1:])
    radio = set()
    pos = 0
    max_pos = func.max_pos
    scription_debug('max_pos:', max_pos, verbose=2)
    print_help = print_version = print_all_versions = False
    value = None
    annotations = {}
    var_arg_spec = kwd_arg_spec = None
    if Script and Script.command:
        var_arg_spec = getattr(Script.command, '_var_arg', None)
        kwd_arg_spec = getattr(Script.command, '_kwd_arg', None)
        scription_debug('kwd_arg_spec', kwd_arg_spec, verbose=3)
        annotations.update(Script.command.__scription__)
    annotations.update(func.__scription__)
    scription_debug('annotations: %r' % annotations, verbose=2)
    if func._var_arg:
        var_arg_spec = func._var_arg
    if func._kwd_arg:
        kwd_arg_spec = func._kwd_arg
    if kwd_arg_spec:
        kwd_arg_spec._cli_value = {}
    to_be_removed = []
    all_to_varargs = False
    annote = last_item = None
    for offset, item in enumerate(param_line_args + [None]):
        scription_debug('%r: %r' % (offset, item), verbose=2)
        original_item = item
        if value is not None:
            scription_debug('None branch', verbose=2)
            if item is None or item.startswith('-') or '=' in item:
                scription_debug('new flag/option, checking for previous flag/option default', verbose=2)
                # check for default
                if annote._script_default:
                    scription_debug('found: %r' % (annote._script_default, ))
                    annote._cli_value = annote._script_default
                    if annote._cli_value and annote._radio:
                        if annote._radio in radio:
                            raise ScriptionError('only one of %s may be specified'
                                        % _and_list(func.radio[annote._radio]))
                        radio.add(annote._radio)
                else:
                    raise ScriptionError('%s has no value' % last_item, use_help=True)
                value = None
            else:
                if annote.remove:
                    # only remove if not using the annotation default
                    to_be_removed.append(offset)
                value = item
                if annote.kind == 'option':
                    scription_debug('processing as option', verbose=2)
                    scription_debug('checking choice membership: %r in %r?' % (item, annote.choices), verbose=2)
                    if annote.choices and value not in annote.choices:
                        raise ScriptionError('%s: %r not in [ %s ]' % (annote.usage, value, ' | '.join(annote.choices)), use_help=True)
                    annote._cli_value = annote.type(value)
                    if annote._cli_value and annote._radio:
                        if annote._radio in radio:
                            raise ScriptionError('only one of %s may be specified'
                                        % _and_list(func.radio[annote._radio]))
                        radio.add(annote._radio)
                elif annote.kind in ('multi', 'multireq'):
                    scription_debug('processing as multi', verbose=2)
                    scription_debug('checking choice membership: %r in %r?' % (item, annote.choices), verbose=2)
                    values = _split_on_comma(value)
                    if annote.choices:
                        for v in values:
                            if v not in annote.choices:
                                raise ScriptionError(
                                        '%s: %r not in [ %s ]'
                                            % (annote.usage, v, ' | '.join(annote.choices)),
                                        use_help=True,
                                        )
                    values = [annote.type(v) for v in values]
                    annote._cli_value += tuple(values)
                    if annote._cli_value and annote._radio:
                        if annote._radio in radio:
                            raise ScriptionError('only one of %s may be specified'
                                        % _and_list(func.radio[annote._radio]))
                        radio.add(annote._radio)
                else:
                    raise ScriptionError("Error: %s's kind %r not in (option, multi, multireq)" % (last_item, annote.kind))
                value = None
                continue
        last_item = item
        if item is None:
            scription_debug('done with loop', verbose=2)
            break
        elif item == '--':
            scription_debug('all to varargs', verbose=2)
            all_to_varargs = True
            continue
        if all_to_varargs:
            if var_arg_spec is None:
                raise ScriptionError("don't know what to do with %r" % item, use_help=True)
            var_arg_spec._cli_value += (var_arg_spec.type(item), )
            continue
        if item.startswith('-'):
            scription_debug('option or flag', verbose=2)
            # (multi)option or flag
            if item.lower() == '--help' or item == '-h' and 'h' not in annotations:
                scription_debug('help flag', verbose=2)
                print_help = True
                continue
            elif item.lower() == '--version':
                scription_debug('version flag', verbose=2)
                print_version = True
                continue
            elif item.lower() in ('--all-versions', '--all_versions'):
                scription_debug('all versions flag', verbose=2)
                print_all_versions = True
                continue
            elif item == '-v' and 'v' not in annotations:
                scription_debug('verbosity flag', verbose=2)
                VERBOSITY += 1
                continue
            item = item.lstrip('-')
            value = True
            if item.lower().startswith('no-') and '=' not in item:
                scription_debug('no- (disabling)', verbose=2)
                value = False
                item = item[3:]
            elif '=' in item:
                scription_debug('name & value', verbose=2)
                item, value = item.split('=', 1)
            item = item.replace('-','_')
            if item.lower() == 'verbose' and 'verbose' not in annotations:
                scription_debug('verbosity option', verbose=2)
                try:
                    VERBOSITY = int(value)
                except ValueError:
                    raise ScriptionError('invalid verbosity level: %r' % value, use_help=True)
                value = None
                continue
            if item in annotations:
                scription_debug('Command setting', verbose=2)
                annote = annotations[item]
            elif Script and item in Script.settings:
                scription_debug('Script setting', verbose=2)
                annote = Script.settings[item]
            elif item in ('SCRIPTION_DEBUG', ):
                scription_debug('SCRIPTION_DEBUG', verbose=2)
                SCRIPTION_DEBUG = int(value)
                value = None
                continue
            else:
                raise ScriptionError('%s not valid' % original_item, use_help=True)
            if annote.remove:
                scription_debug('removed setting', verbose=2)
                to_be_removed.append(offset)
            if annote.kind == 'flag':
                scription_debug('flag', verbose=2)
                if annote._target:
                    scription_debug('  for', annote._target, verbose=2)
                    target_annote = annotations[annote._target]
                    target_annote._cli_value = value = target_annote.type(annote._script_default)
                    scription_debug('  value', value)
                else:
                    value = annote.type(value)
                    annote._cli_value = value
                # check for other radio set
                scription_debug('checking radio setting %r for flag %s in %r' % (annote._radio, item, radio), verbose=2)
                scription_debug('value: %r' % (value, ), verbose=2)
                if annote._radio:
                    if annote._radio in radio:
                        raise ScriptionError('only one of %s may be specified'
                                % _and_list(func.radio[annote._radio]))
                    radio.add(annote._radio)
                    scription_debug('radio settings: %r' % radio, verbose=2)
                value = None
            elif annote.kind in ('multi', 'option'):
                scription_debug('(multi)option' , verbose=2)
                if value is True:
                    # if value is True, it will trigger a value lookup on the next pass
                    continue
                if value is False:
                    # if value is False, the name was disable with a leading --no-
                    annote._cli_value = annote._type_default
                    value = None
                    continue
                scription_debug('value is %r' % (value, ), verbose=2)
                if annote.kind == 'option':
                    scription_debug('processing as option', verbose=2)
                    scription_debug('checking choice membership: %r in %r?' % (item, annote.choices), verbose=2)
                    if annote.choices and value not in annote.choices:
                        raise ScriptionError('%s: %r not in [ %s ]' % (annote.usage, value, ' | '.join(annote.choices)), use_help=True)
                    annote._cli_value = annote.type(value)
                    scription_debug('checking radio setting %r for option %s in %r' % (annote._radio, item, radio), verbose=2)
                    scription_debug('value: %r' % (value, ), verbose=2)
                    if annote._radio:
                        if annote._radio in radio:
                            raise ScriptionError('only one of %s may be specified'
                                    % _and_list(func.radio[annote._radio]))
                        radio.add(annote._radio)
                        scription_debug('radio settings: %r' % radio, verbose=2)
                else:
                    scription_debug('processing as multi-option', verbose=2)
                    # value could be a list of comma-separated values
                    scription_debug('_usage:multi ->', annote.type, verbose=2)
                    scription_debug('checking choice membership: %r in %r?' % (item, annote.choices), verbose=2)
                    values = _split_on_comma(value)
                    if annote.choices:
                        for v in values:
                            if v not in annote.choices:
                                raise ScriptionError(
                                        '%s: %r not in [ %s ]'
                                            % (annote.usage, v, ' | '.join(annote.choices)),
                                        use_help=True,
                                        )
                    values = [annote.type(v) for v in values]
                    annote._cli_value += tuple(values)
                    scription_debug('_usage:multi ->', annote._cli_value, verbose=2)
                    scription_debug('checking radio settings for multioption %s' % (item, ), verbose=2)
                    if annote._radio:
                        if annote._radio in radio:
                            raise ScriptionError('only one of %s may be specified'
                                    % _and_list(func.radio[annote._radio]))
                        radio.add(annote._radio)
                value = None
            else:
                raise ScriptionError('%s argument %s should not be introduced with --' % (annote.kind, item), use_help=True)
        elif pos >= max_pos and '=' in item:
            # no lead dash, keyword args
            scription_debug('keyword arg', verbose=2)
            if kwd_arg_spec is None:
                raise ScriptionError("don't know what to do with %r" % item, use_help=True)
            item, value = item.split('=')
            item = item.replace('-','_')
            if item in func.named_params:
                raise ScriptionError('%s must be specified as a %s' % (item, annotations[item].kind), use_help=True)
            item, value = kwd_arg_spec.type(item, value)
            if not isinstance(item, str):
                raise ScriptionError('keyword names must be strings', use_help=True)
            kwd_arg_spec._cli_value[item] = value
            value = None
        else:
            scription_debug('positional?', verbose=2)
            # positional (required?) argument
            scription_debug('positional argument:', item)
            if pos < max_pos:
                annote = annotations[pos]
                scription_debug('  with Spec:', annote)
                if annote.remove:
                    to_be_removed.append(offset)
                if annote.kind == 'multireq' and item:
                    scription_debug('_usage:multireq ->', annote.type, verbose=2)
                    values = _split_on_comma(item)
                    if annote.choices:
                        for v in values:
                            if v not in annote.choices:
                                raise ScriptionError(
                                        '%s: %r not in [ %s ]'
                                            % (annote.usage, v, ' | '.join(annote.choices)),
                                        use_help=True,
                                        )
                    annote._cli_value += tuple([annote.type(v) for v in values])
                    scription_debug('_usage:multireq ->', annote._cli_value, verbose=2)
                else:
                    # check for choices membership before transforming into a type
                    scription_debug('choice membership: %r in %r?' % (item, annote.choices), verbose=2)
                    if annote.choices and item not in annote.choices:
                        raise ScriptionError('%s: %r not in [ %s ]' % (annote.usage, item, ' | '.join(annote.choices)), use_help=True)
                    item = annote.type(item)
                    annote._cli_value = item
                pos += 1
            else:
                if var_arg_spec is None:
                    raise ScriptionError("don't know what to do with %r" % item, use_help=True)
                var_arg_spec._cli_value += (var_arg_spec.type(item), )
    if print_help:
        _print()
        if Script and Script.__usage__:
            _print('global settings: ' + Script.__usage__ + '\n')
        _print('%s %s' % (program, func.__usage__))
        _print()
        sys.exit(Exit.Success)
    elif print_version:
        _print(_get_version(script_module['module']))
        sys.exit(Exit.Success)
    elif print_all_versions:
        _print('\n'.join(_get_all_versions(script_module)))
        sys.exit(Exit.Success)
    for setting in set(func.__scription__.values()):
        if setting.kind == 'required':
            setting.value
    if var_arg_spec and var_arg_spec.kind == 'required':
        var_arg_spec.value
    # remove any command line args that shouldn't be passed on
    new_args = []
    for i, arg in enumerate(param_line_args):
        if i not in to_be_removed:
            new_args.append(arg)
    sys.argv[1:] = new_args
    main_args, main_kwds = [], {}
    args, varargs = [], None
    if Script:
        for name in Script.names:
            annote = Script.settings[name]
            value = annote.value
            if annote._global:
                script_module[name] = value
            else:
                if annote is var_arg_spec:
                    varargs = value
                elif annote is kwd_arg_spec:
                    main_kwds = value
                else:
                    args.append(annote)
    args = [arg.value for arg in sorted(args, key=lambda a: a._order) if arg._target is empty]
    scription_debug('args:    %r' % (args, ))
    scription_debug('varargs: %r' % (varargs, ))
    if varargs is not None:
        main_args = tuple(args) + varargs
    else:
        main_args = tuple(args)
    sub_args, sub_kwds = [], {}
    args, varargs = [], None
    for name in func.all_params:
        if name[0] == '_':
            # ignore private params
            continue
        annote = func.__scription__[name]
        value = annote.value
        if annote is var_arg_spec:
            varargs = value
        elif annote is kwd_arg_spec:
            sub_kwds = value
        else:
            args.append(annote)
    args = [arg.value for arg in sorted(args, key=lambda a: a._order) if arg._target is empty]
    if varargs is not None:
        sub_args = tuple(args) + varargs
    else:
        sub_args = tuple(args)
    return main_args, main_kwds, sub_args, sub_kwds

# API

## required
class Alias(object):
    "adds aliases for the function"
    def __init__(self, *aliases, **canonical):
        scription_debug('recording aliases', aliases, verbose=2)
        if len(canonical) > 1:
            raise TypeError('invalid keywords: %s' % ', '.join(repr(k) for k in canonical))
        elif canonical and 'canonical' not in canonical:
            raise ScriptionError('invalid keyword: %s' % ', '.join(repr(k) for k in canonical))
        self.aliases = aliases
        self.canonical = canonical.get('canonical', False)
    def __call__(self, func):
        scription_debug('applying aliases to', func.__name__, verbose=2)
        if not script_module:
            _init_script_module(func)
        canonical = self.canonical
        if canonical:
            func_name = func.__name__.replace('_', '-').lower()
            try:
                script_module['script_aliases'][func_name] = func
                del script_module['script_commands'][func_name]
            except KeyError:
                raise ScriptionError('canonical Alias %r must run after (be placed before) its Command' % self.aliases[0])
        for alias in self.aliases:
            alias_name = alias.replace('_', '-').lower()
            if alias_name in script_module['script_commands']:
                raise ScriptionError('alias %r already in use as command %r' % (alias_name, alias_name))
            elif alias_name in script_module['script_aliases']:
                raise ScriptionError('alias %r already in use for command %r' % (alias_name, script_module['script_aliases'][alias_name]))
            if canonical:
                script_module['script_commands'][alias_name] = func
                canonical = False
            else:
                script_module['script_aliases'][alias_name] = func
        return func


class Command(object):
    "adds __scription__ to decorated function, and adds func to script_commands"
    def __init__(self, **annotations):
        scription_debug('Command -> initializing', verbose=1)
        scription_debug(annotations, verbose=2)
        for name, annotation in annotations.items():
            spec = Spec(annotation)
            spec.__name__ = name
            if spec.usage is empty:
                spec.usage = name.upper()
            annotations[name] = spec
        self.annotations = annotations
    def __call__(self, func):
        scription_debug('Command -> applying to', func.__name__, verbose=1)
        if not script_module:
            _init_script_module(func)
        if func.__doc__ is not None:
            func.__doc__ = textwrap.dedent(func.__doc__).strip()
        _add_annotations(func, self.annotations)
        func_name = func.__name__.replace('_', '-').lower()
        if func_name.startswith('-'):
            # internal name, possibly shadowing a keyword or data type -- an alias will be needed
            # to access this command
            pass
        else:
            if func_name in script_module['script_commands']:
                raise ScriptionError('command name %r already defined' % (func_name, ))
            elif func_name in script_module['script_aliases']:
                raise ScriptionError('command name %r already defined as an alias' % (func_name, ))
            script_module['script_commands'][func_name] = func
        _help(func)
        return func


class Script(object):
    """
    adds __scription__ to decorated function, and stores func in self.command
    """
    def __init__(self, **settings):
        scription_debug('Script -> recording', verbose=1)
        scription_debug(settings, verbose=2)
        for name, annotation in settings.items():
            if isinstance(annotation, (Spec, tuple)):
                spec = Spec(annotation)
            else:
                if isinstance(annotation, (bool, Trivalent)):
                    kind = 'flag'
                else:
                    kind = 'option'
                spec = Spec('', kind, None, type_of(annotation), default=annotation)
            spec.__name__ = name
            if spec.usage is empty:
                spec.usage = name.upper()
            settings[name] = spec
        self.settings = settings
        self.names = sorted(settings.keys())
        def dummy():
            pass
        _add_annotations(dummy, settings, script=True)
        _help(dummy, script=True)
        self.__usage__ = dummy.__usage__.strip()
        self.command = dummy
        self.all_params = dummy.all_params
        self.named_params = dummy.named_params
        self.settings = dummy.__scription__
        THREAD_STORAGE.script_main = self
    def __call__(self, func):
        scription_debug('Script -> applying to', func, verbose=1)
        THREAD_STORAGE.script_main = None
        if not script_module:
            _init_script_module(func)
        if script_module['script_commands']:
            raise ScriptionError('Script must be defined before any Command')
        func_name = func.__name__.replace('_', '-').lower()
        if func_name in script_module['script_commands']:
            raise ScriptionError('%r cannot be both Command and Script' % func_name)
        if func.__doc__ is not None:
            func.__doc__ = textwrap.dedent(func.__doc__).strip()
        _add_annotations(func, self.settings, script=True)
        _help(func, script=True)
        self.all_params = func.all_params
        self.named_params = func.named_params
        self.settings = func.__scription__
        self.__usage__ = func.__usage__.strip()
        self.command = func
        script_module['script_main'] = self
        return func


class Spec(object):
    """tuple with named attributes for representing a command-line paramter

    help, kind, abbrev, type, choices, usage_name, remove, default, envvar, force_default, radio, target
    """

    __name__ = None

    def __init__(self,
            help=empty, kind=empty, abbrev=empty, type=empty,
            choices=empty, usage=empty, remove=False, default=empty,
            envvar=empty, force_default=empty, radio=empty, target=empty
            ):
        if isinstance(help, Spec):
            self.__dict__.update(help.__dict__)
            return
        if isinstance(help, tuple):
            args = list(help) + [empty] * (10 - len(help))
            help, kind, abbrev, type, choices, usage, remove, default, envvar, force_default = args
        if not help:
            help = ''
        if not kind:
            kind = 'required'
        if not type:
            type = _identity
        if not choices:
            choices = []
        elif isinstance(choices, basestring):
            choices = choices.replace(',',' ').split()
        else:
            # choices had better be some kind of iterator
            choices = [str(c) for c in choices]
        arg_type_default = empty
        use_default = False
        if default is not empty and (force_default == True or kind == 'required'):
            # support use of force_default as flag for default
            # make defaults for required arguments forced
            use_default = True
        elif force_default is not empty:
            # otherwise force_default is the always used default itself
            default = force_default
            use_default = True
        if kind not in ('required', 'multireq', 'option', 'multi', 'flag'):
            raise ScriptionError('unknown parameter kind: %r' % kind)
        if kind == 'flag':
            if type is Trivalent:
                arg_type_default = Unknown
            else:
                arg_type_default = False
                if type is _identity:
                    type = Bool
        elif kind == 'option':
            arg_type_default = None
        elif kind in ('multi', 'multireq'):
            arg_type_default = tuple()
        elif default is not empty:
            arg_type_default = type_of(default)
        if abbrev not in(empty, None) and not isinstance(abbrev, tuple):
            abbrev = (abbrev, )
        if usage is not empty:
            if isinstance(abbrev, tuple):
                abbrev = abbrev + (usage.lower(), )
            else:
                abbrev = (usage.lower(), )
        self.help = help
        self.kind = kind
        self.abbrev = abbrev
        self.type = type
        self.choices = choices
        self.usage = usage
        self.remove = remove
        self._cli_value = empty
        self._script_default = default
        self._type_default = arg_type_default
        self._use_default = use_default
        self._global = False
        self._envvar = envvar
        if radio is empty:
            radio = ''
        if not isinstance(radio, basestring):
            raise ScriptionError('radio setting must be a name')
        self._radio = radio or empty
        if target is not empty:
            if kind != 'flag':
                raise ScriptionError('target is only valid for FLAGs')
        self._target = target

    def __iter__(self):
        return iter((self.help, self.kind, self.abbrev, self.type, self.choices, self.usage, self.remove, self._script_default, self._envvar, self._target))

    def __repr__(self):
        return "Spec(help=%r, kind=%r, abbrev=%r, type=%r, choices=%r, usage=%r, remove=%r, default=%r, envvar=%r, target=%r)" % (
                self.help, self.kind, self.abbrev, self.type, self.choices, self.usage, self.remove, self._script_default, self._envvar, self._target)

    @property
    def value(self):
        scription_debug('getting value for %r' % self.__name__)
        if self._cli_value is not empty:
            value = self._cli_value
            scription_debug('   cli --> %r' % (value, ), verbose=2)
        elif self._envvar is not empty and pocket(value=os.environ.get(self._envvar)):
            value = pocket.value
            if self.kind == 'multi':
                value = tuple([self.type(v) for v in _split_on_comma(value)])
            else:
                value = self.type(value)
            scription_debug('   env --> %r' % (value, ), verbose=2)
        elif self._script_default is not empty and self._use_default:
            value = self._script_default
            scription_debug('   default --> %r' % (value, ), verbose=2)
            scription_debug('   type of --> %r' % (self.type, ), verbose=2)
            if PY2 and isinstance(value, bytes):
                value = value.decode(LOCALE_ENCODING)
            if value is not None:
                if self._type_default == ():
                    if isinstance(value, tuple):
                        value = tuple(self.type(v) for v in value)
                    else:
                        value = (self.type(value), )
                else:
                    value = self.type(value)
            scription_debug('     final --> %r' % (value, ), verbose=2)
        elif self._type_default is not empty:
            value = self._type_default
            scription_debug('   type default --> %r' % (value, ), verbose=2)
        else:
            raise ScriptionError('no value specified for %s' % self.usage)
        return value

def Main(module=None):
    "calls Run() only if the script is being run as __main__"
    scription_debug('Main entered')
    # TODO: replace the frame hack if a blessed way to know the calling
    # module is ever developed
    if module is None:
        try:
            module = sys._getframe(1).f_globals['__name__']
        except (AttributeError, KeyError):
            module = script_module['__name__']
    if module == '__main__':
        Run()


def Run():
    "parses command-line and compares with either func or, if None, script_module['script_main']"
    global SYS_ARGS
    scription_debug('Run entered')
    if globals().get('HAS_BEEN_RUN'):
        scription_debug('Run already called once, returning')
        return
    globals()['HAS_BEEN_RUN'] = True
    if PY2:
        SYS_ARGS = [arg.decode(LOCALE_ENCODING) for arg in sys.argv]
    else:
        SYS_ARGS = sys.argv[:]
    script_module['script_command_line'] = SYS_ARGS
    Script = script_module['script_main']
    Command = script_module['script_commands']
    Alias = script_module['script_aliases']
    Alias.update(Command)
    try:
        prog_path, prog_name = os.path.split(SYS_ARGS[0])
        if prog_name == '__main__.py':
            # started with python -m, get actual package name for prog_name
            prog_name = os.path.split(prog_path)[1]
        scription_debug(prog_name, verbose=2)
        script_module['script_fullname'] = SYS_ARGS[0]
        script_module['script_name'] = prog_name
        prog_name = prog_name.replace('_','-')
        if not Command:
            raise ScriptionError("no Commands defined in script")
        func_name = SYS_ARGS[1:2]
        if not func_name:
            func_name = None
        else:
            func_name = func_name[0].lower()
            if func_name == '--version':
                _print(_get_version(script_module['module']))
                sys.exit(Exit.Success)
            elif func_name in ('--all-versions', '--all_versions'):
                _print('\n'.join(_get_all_versions(script_module)))
                sys.exit(Exit.Success)
            else:
                func_name = func_name.replace('_', '-')
        func = Alias.get(func_name)
        if func is not None:
            prog_name = SYS_ARGS[1].lower()
            param_line = [prog_name] + SYS_ARGS[2:]
        else:
            func = Alias.get(prog_name.lower())
            if func is None and prog_name.lower().endswith('.py'):
                func = Alias.get(prog_name.lower()[:-3])
            if func is not None and func_name != '--help':
                param_line = [prog_name] + SYS_ARGS[1:]
            else:
                prog_name_is_command = prog_name.lower() in Alias
                if not prog_name_is_command and prog_name.lower().endswith('.py'):
                    prog_name_is_command = prog_name.lower()[:-3] in Alias
                if script_module['__doc__']:
                    _print(script_module['__doc__'].strip())
                if len(Command) == 1:
                    _detail_help = True
                else:
                    _detail_help = False
                    _name_length = max([len(name) for name in Alias])
                if not (_detail_help or script_module['__doc__']):
                    _print("Available commands/options in", script_module['script_name'])
                if Script and Script.__usage__:
                    if _detail_help:
                        _print("\nglobal settings: %s" % Script.__usage__)
                    else:
                        _print("\n   global settings: %s\n" % Script.__usage__.split('\n')[0])
                for name, func in sorted(Command.items()):
                    if _detail_help:
                        if prog_name_is_command and len(Command) == 1:
                            name = prog_name
                        elif not (prog_name_is_command or name != prog_name) and len(Command) > 1:
                            continue
                            name = '%s %s' % (prog_name, name)
                        _print("\n%s %s" % (name, func.__usage__))
                    else:
                        doc = (func.__doc__ or func.__usage__.split('\n')[0]).split('\n')[0]
                        _print("   %*s  %s" % (-_name_length, name, doc))

                if func_name in ('-h', '--help'):
                    sys.exit(Exit.Success)
                else:
                    sys.exit(Exit.ScriptionError)
        main_args, main_kwds, sub_args, sub_kwds = _usage(func, param_line)
        main_cmd = Script and Script.command
        scription_debug('main command: %r\n  %r\n  %r' % (main_cmd, main_args, main_kwds))
        scription_debug('sub command', sub_args, sub_kwds)
        subcommand = _run_once(func, sub_args, sub_kwds)
        script_module['script_command'] = subcommand
        script_module['script_command_name'] = func.__name__
        script_module['verbose'] = VERBOSITY
        script_module['script_verbosity'] = VERBOSITY
        if main_cmd:
            scription_debug('running Script')
            main_cmd(*main_args, **main_kwds)
            scription_debug('done with Script')
        sys.exit(subcommand() or Exit.Success)
    except Exception:
        exc = sys.exc_info()[1]
        scription_debug(exc)
        result = log_exception()
        script_module['script_exception_lines'] = result
        if isinstance(exc, ScriptionError):
            if exc.use_help:
                help(str(exc), exc.returncode)
            else:
                abort(str(exc), exc.returncode)
        raise
    except KeyboardInterrupt:
        _print('\n<Ctrl-C> detected, aborting')
        sys.exit(Exit.UserCancelled)

## optional
def Execute(args, cwd=None, password=None, password_timeout=None, input=None, input_delay=2.5, timeout=None, pty=None, interactive=None, env=None, **new_env_vars):
    scription_debug('creating job:', args)
    job = Job(args, cwd=cwd, pty=pty, env=env, **new_env_vars)
    try:
        scription_debug('communicating')
        job.communicate(timeout=timeout, interactive=interactive, password=password, password_timeout=password_timeout, input=input, input_delay=input_delay)
    except BaseException as exc:
        if getattr(exc, 'process', None) is None:
            exc.process = job
        if interactive is None:
            echo(job.stdout)
            echo(job.stderr)
            echo()
        scription_debug(exc)
        raise
    finally:
        job.close()
    scription_debug('returning')
    return job

class Job(object):
    """
    if pty is True runs command in a forked process, otherwise runs in a subprocess
    """

    name = None
    # if subprocess is used record the process
    process = None
    returncode = None
    # if killed by a signal, record it
    signal = None
    # if job is no longer alive
    terminated = False
    # if job has been closed
    closed = False
    # str of stdout and stderr from job
    stdout = None
    stderr = None
    # all exceptions that occured (set to a list in __init__)
    exceptions = None
    # emergency abort
    abort = False

    def __init__(self, args, cwd=None, pty=None, env=None, **new_env_vars):
        # args        -> command to run
        # cwd         -> directory to run in
        # pty         -> False = subprocess, True = fork
        self.exceptions = []
        self._process_thread = None
        env = self.env = (env or os.environ).copy()
        if new_env_vars:
            env.update(new_env_vars)
        if pty and is_win:
            raise OSError("pty support for Job not currently implemented for Windows")
        self.kill_signals = list(KILL_SIGNALS)
        if isinstance(args, basestring):
            args = shlex.split(args)
        else:
            args = list(args)
        self.name = args[0]
        if not pty:
            # use subprocess
            scription_debug('subprocess args:', args)
            try:
                self.process = process = Popen(args, stdin=PIPE, stdout=PIPE, stderr=PIPE, cwd=cwd, env=env)
            except OSError as exc:
                scription_debug('subprocess cwd:', cwd)
                scription_debug('subprocess env:', env)
                if exc.errno == 2:
                    self.pid = -1
                    self.closed = True
                    self.terminated = True
                    try:
                        raise ExecutionError('%s --> %r' % (args[0], exc), process=self)
                    except ExecutionError:
                        _, exc, tb = sys.exc_info()
                        self._set_exc(exc, traceback=tb)
                        return
                raise
            self.pid = process.pid
            self.child_fd_out = process.stdout
            self.child_fd_in = process.stdin
            self.child_fd_err = process.stderr
            self.poll = self._log_wrap(process.poll, 'polling')
            self.terminate = self._log_wrap(process.terminate, 'terminating')
            self.kill = self._log_wrap(process.kill, 'killing')
            self.send_signal = self._log_wrap(process.send_signal, 'sending signal')
        else:
            error_read, error_write = os.pipe()
            self.pid, self.child_fd = fork()
            if self.pid == 0: # child process
                os.close(error_read)
                self.child_fd_out = sys.stdout.fileno()
                os.dup2(error_write, 2)
                os.close(error_write)
                self.error_pipe = 2
                try:
                    max_fd = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
                    for fd in range(3, max_fd):
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                    if cwd:
                        os.chdir(cwd)
                    if self.env:
                        os.execvpe(args[0], args, self.env)
                    else:
                        os.execvp(args[0], args)
                except Exception:
                    exc = sys.exc_info()[1]
                    self.write_error("EXCEPTION: %s --> %s(%s)" % (args[0], exc.__class__.__name__, ', '.join([repr(a) for a in exc.args])))
                    os._exit(Exit.UnknownError)
            # parent process
            os.close(error_write)
            self.child_fd_out = self.child_fd
            self.child_fd_in = self.child_fd
            self.child_fd_err = error_read
        # start reading output
        self._all_output = Queue()
        self._all_input = Queue()
        self._stdout = []
        self._stderr = []
        self._stdout_history = []
        self._stderr_history = []
        def read_comm(name, channel, q):
            try:
                if isinstance(channel, int):
                    read = lambda size: os.read(channel, size)
                else:
                    read = channel.read
                while not self.abort:
                    scription_debug('reading', name)
                    data = read(1024)
                    with io_lock:
                        scription_debug('putting %s %r (%d bytes)' % (name, data, len(data)))
                        if not data:
                            data = None
                        q.put((name, data))
                        if data is None:
                            break
                else:
                    q.put((name, None))
                    scription_debug('read_comm dying from self.abort')
            except Exception:
                _, exc, tb = sys.exc_info()
                with io_lock:
                    q.put((name, None))
                    scription_debug('dying %s (from exception %s)' % (name, exc))
                    if not isinstance(exc, OSError) or exc.errno not in (errno.EBADF, errno.EIO, errno.EPIPE):
                        raise self._set_exc(exc, traceback=tb)
        def write_comm(channel, q):
            try:
                if isinstance(channel, int):
                    write = lambda data: os.write(channel, data)
                    flush = lambda: ''
                else:
                    write = channel.write
                    flush = channel.flush
                while not self.abort:
                    scription_debug('stdin waiting')
                    data = q.get()
                    with io_lock:
                        if data is None:
                            scription_debug('dying stdin')
                            break
                        scription_debug('stdin writing', repr(data))
                        write(data)
                        scription_debug('   done writing', repr(data))
                        flush()
                else:
                    scription_debug('write_comm dying from self.abort')
            except Exception:
                _, exc, tb = sys.exc_info()
                if isinstance(exc, (IOError, OSError)) and exc.errno == errno.EPIPE:
                    pass
                else:
                    raise self._set_exc(exc, traceback=tb)
        t = Thread(target=read_comm, name='stdout', args=('stdout', self.child_fd_out, self._all_output))
        t.daemon = True
        t.start()
        t = Thread(target=read_comm, name='stderr', args=('stderr', self.child_fd_err, self._all_output))
        t.daemon = True
        t.start()
        t = Thread(target=write_comm, name='stdin', args=(self.child_fd_in, self._all_input))
        t.daemon = True
        t.start()
        # do not add the stdin thread to the list of threads that automatically die if the job dies, as
        # it has to be manually ended

    def _log_wrap(self, func, msg):
        def wrapper(*args, **kwds):
            scription_debug(msg, args, kwds)
            return func(*args, **kwds)
        return wrapper

    def _set_exc(self, exc, message=None, traceback=None):
        'sets self.exceptions if not already set, or unsets if exc is None'
        scription_debug('setting exception to: %r' % (exc,))
        if not isinstance(exc, Exception) and issubclass(exc, Exception):
            exc = exc(message)
        if exc is None:
            self.exceptions[:] = []
        elif exc is not None:
            self.exceptions.append((exc, traceback))
        return exc

    def communicate(self, input=None, input_delay=2.5, password=None, timeout=None, interactive=None, encoding='utf-8', password_timeout=None):
        # password          -> single password or tuple of passwords (pty=True only)
        # password_timeout  -> time allowed for successful password transmission
        # timeout           -> time allowed for successful completion of job
        # interactive       -> False = record only, 'echo' = echo output as we get it
        self.raise_if_exceptions()
        try:
            deadman_switch = None
            scription_debug('timeout: %r, password_timeout: %r' % (timeout, password_timeout))
            if timeout is not None and password_timeout is not None and password_timeout >= timeout:
                self._set_exc(ValueError, 'password_timeout must be less than timeout')
                self.kill()
                return
            if timeout is not None and password and password_timeout is None:
                password_timeout = min(90, timeout / 10.0)
            elif password and password_timeout is None:
                password_timeout = 90
            scription_debug('    password_timeout is now: %r' % (password_timeout, ))
            if timeout is not None:
                def prejudice():
                    scription_debug('timed out')
                    message = '\nTIMEOUT: process failed to complete in %s seconds\n' % timeout
                    with io_lock:
                        self._stderr.append(message)
                    self._set_exc(TimeoutError, message.strip())
                    self.kill()
                deadman_switch = threading.Timer(timeout, prejudice)
                deadman_switch.name = 'deadman'
                deadman_switch.start()
            if self._process_thread is None:
                def process_comm():
                    active = 2
                    while active and not self.abort:
                        # check if any threads still alive
                        try:
                            stream, data = self._all_output.get(timeout=1)
                        except Empty:
                            continue
                        with io_lock:
                            if data is None:
                                active -= 1
                                scription_debug('dead thread:', stream)
                                continue
                            if encoding is not None:
                                data = data.decode(encoding)
                            scription_debug('adding %r to %s' % (data, stream))
                            if stream == 'stdout':
                                self._stdout.append(data)
                                if interactive == 'echo':
                                    echo(data, end='')
                                    sys.stdout.flush()
                            elif stream == 'stderr':
                                self._stderr.append(data)
                                if interactive == 'echo':
                                    echo(data, end='', file=stderr)
                                    sys.stderr.flush()
                            else:
                                self._set_exc(Exception, 'unknown stream: %r' % stream)
                                self.kill()
                    else:
                        scription_debug('process_comm dying' + ('', ' from self.abort')[self.abort])
                process_thread = self._process_thread = Thread(target=process_comm, name='process')
                process_thread.start()
            passwords = []
            if isinstance(input, unicode):
                input = input.encode('utf-8')
            if isinstance(input, bytes):
                add_newline = input[-1:] == [b'\n']
                input = [i + b'\n' for i in input.split(b'\n')]
                if not add_newline:
                    input[-1] = input[-1].strip(b'\n')
                    if not input[-1]:
                        input.pop()
            scription_debug('input is: %r' % (input, ), verbose=2)
            if password is None:
                password = ()
            elif isinstance(password, (bytes, unicode)):
                password = (password, )
            for pwd in password:
                if not isinstance(pwd, bytes):
                    passwords.append((pwd + '\n').encode('utf-8'))
                else:
                    passwords.append(pwd + '\n'.encode('utf-8'))
            if passwords:
                while passwords:
                    if self.process:
                        # feed all passwords at once, after a short delay
                        time.sleep(0.1)
                        pwd = passwords[0]
                        for next_pwd in passwords[1:]:
                            pwd += next_pwd
                        try:
                            self.write(pwd, )
                        except IOError:
                            # ignore write errors (probably due to password not needed and job finishing)
                            self._set_exc(None)
                        passwords = []
                    else:
                        try:
                            # pty -- look for echo off first
                            remaining_timeout = password_timeout
                            while remaining_timeout > 0 and self.get_echo() and self.is_alive():
                                scription_debug('[echo: %s] waiting for echo off (%s remaining)' % (self.get_echo(), remaining_timeout))
                                remaining_timeout -= 0.1
                                time.sleep(0.1)
                            if not self.is_alive():
                                # job died
                                try:
                                    raise ExecuteError('job died', process=self)
                                except ExecuteError:
                                    cls, exc, tb = sys.exc_info()
                                    self._set_exc(exc, traceback=tb)
                                    raise exc
                            elif self.get_echo():
                                # too long
                                try:
                                    raise TimeoutError('Password prompt not seen.')
                                except TimeoutError:
                                    cls, exc, tb = sys.exc_info()
                                    self._set_exc(exc, traceback=tb)
                                    self.kill()
                                    raise exc
                            pw, passwords = passwords[0], passwords[1:]
                            scription_debug('[echo: %s] writing password %r' % (self.get_echo(), pw))
                            self.write(pw, )
                        except IOError:
                            # ignore get_echo and write errors (probably due to password not needed and job finishing)
                            self._set_exc(None)
                            break
                else:
                    # wait a moment for any passwords to be sent
                    scription_debug('[echo: %s] sleeping at least 5 seconds so passwords can be sent and response read' % (self.get_echo(), ))
                    waiting_time = 5.0
                    while waiting_time > 0.05:
                        scription_debug('[echo: %s]      quick sleep (%s remaning)' % (self.get_echo(), waiting_time))
                        waiting_time -= 0.1
                        time.sleep(0.1)
                        if not self.get_echo():
                            # host still wants a password -- not good
                            if not self.process:
                                if self.is_alive():
                                    scription_debug('[echo: %s] PASSWORD FAILURE:  invalid passwords or none given' % (self.get_echo(), ))
                                    with io_lock:
                                        self._stderr.append('Invalid/too few passwords\n')
                                    e = self._set_exc(FailedPassword)
                                    self.kill()
                                    raise e
                    else:
                        scription_debug('[echo: %s] password entry finished' % (self.get_echo(), ))
            if input is not None:
                scription_debug('writing input: %r' % input, verbose=2)
                time.sleep(input_delay)
                for line in input:
                    self.write(line)
                    time.sleep(0.1)
            scription_debug('joining process thread...')
            while not self.abort:
                process_thread.join(1)
                if not process_thread.is_alive():
                    break
            scription_debug('process thread joined (or abort registered)')
        finally:
            if self.process:
                if not self.abort:
                    self.returncode = self.process.wait()
                self.terminated = True
            if deadman_switch is not None:
                scription_debug('cancelling deadman switch')
                deadman_switch.cancel()
                deadman_switch.join()
            scription_debug('closing job')
            self.close()

    def close(self, force=True):
        'parent method'
        if not self.closed:
            try:
                if self.is_alive() and not self.abort:
                    self.terminate()
                    time.sleep(0.1)
                    if force and self.is_alive():
                        self.kill(error='ignore')
                        time.sleep(0.1)
                        self.is_alive()
                # shutdown stdin thread
                self._all_input.put(None)
                # close handles and pipes
                if self.process is not None:
                    if not isinstance(self.child_fd_in, int):
                        self.child_fd_in.close()
                    if not isinstance(self.child_fd_out, int):
                        self.child_fd_out.close()
                    if not isinstance(self.child_fd_err, int):
                        self.child_fd_err.close()
                else:
                    for fd in (self.child_fd, self.child_fd_err):
                        try:
                            os.close(fd)
                        except OSError:
                            exc_type, exc, tb = sys.exc_info()
                            if exc_type is OSError and exc.errno == errno.EBADF:
                                pass
                            else:
                                self._set_exc(exc, traceback=tb)
                self.child_fd = -1
                self.child_fd_in = -1
                self.child_fd_out = -1
                self.child_fd_err = -1
                time.sleep(0.1)
                self.closed = True
            except Exception:
                exc_type, exc, tb = sys.exc_info()
                self._set_exc(exc, traceback=tb)
            finally:
                with io_lock:
                    scription_debug('saving stdout')
                    self.stdout = ''.join(self._stdout).replace('\r\n', '\n')
                    scription_debug('saving stderr')
                    self.stderr = ''.join(self._stderr).replace('\r\n', '\n')
                self.raise_if_exceptions()

    def fileno(self):
        'parent method'
        return self.child_fd

    def get_echo(self):
        "return the child's terminal echo status (True is on) (parent method)"
        try:
            child_fd = self.child_fd
        except AttributeError:
            return True
        try:
            attr = termios.tcgetattr(child_fd)
        except Exception:
            _, exc, tb = sys.exc_info()
            raise self._set_exc(IOError, errno.EBADF, str(exc), traceback=tb)
        else:
            if attr[3] & termios.ECHO:
                return True
        return False

    def isatty(self):
        'parent method'
        return os.isatty(self.child_fd)

    def is_alive(self):
        'parent method'
        scription_debug("checking for life")
        time.sleep(0.1)
        if self.terminated:
            scription_debug("already terminated", verbose=2)
            return False
        try:
            scription_debug("asking O/S", verbose=2)
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except Exception:
            _, exc, tb = sys.exc_info()
            scription_debug('exc: %s' % (exc, ), verbose=2)
            if isinstance(exc, OSError) and exc.errno == errno.ECHILD:
                scription_debug('child is dead', verbose=2)
                return False
            scription_debug('recording exception', verbose=2)
            raise self._set_exc(ExecuteError, str(exc), traceback=tb)
        if pid != 0:
            scription_debug('child dead, status available', verbose=2)
            self.signal = status % 256
            if self.signal:
                self.returncode = -self.signal
            else:
                self.returncode = status >> 8
            self.terminated = True
            scription_debug('returncode:', self.returncode)
            return False
        return True

    def kill(self, error='raise'):
        '''kills child job, or raises UnableToKillJob

        parent method'''
        exc = None
        for s in self.kill_signals:
            try:
                scription_debug('killing with', s)
                self.send_signal(s)
                scription_debug('checking job for life')
                if not self.is_alive():
                    scription_debug('dead, exiting')
                    break
            except Exception:
                cls, exc, tb = sys.exc_info()
                scription_debug('received', exc)
                if cls in (IOError, OSError) and exc.errno in (errno.ESRCH, errno.ECHILD):
                    # child already died
                    break
        else:
            # unable to kill job
            self.abort = True
            scription_debug('abort switch set')
            if exc is None:
                try:
                    raise UnableToKillJob('Signals %s failed' % ', '.join(self.kill_signals))
                except Exception:
                    cls, exc, tb = sys.exc_info()
                    e = self._set_exc(UnableToKillJob, None, traceback=tb)
            else:
                e = self._set_exc(UnableToKillJob, '%s: %s' % (exc.__class__.__name__, exc), traceback=tb)
            if error == 'raise':
                raise e

    def poll(self):
        scription_debug('polling')
        if self.is_alive():
            return None
        else:
            return self.returncode

    def raise_if_exceptions(self):
        "raise if any stored exceptions"
        scription_debug('saved exceptions: %r' % (self.exceptions, ))
        scription_debug('stderr: %r' % (self.stderr, ))
        if self.stderr and len(self.stderr.split('\n')) == 1 and self.stderr.startswith('EXCEPTION: '):
            # report the exception raised when trying to start the child
            msg = self.stderr[11:]
            raise ExecuteError(msg, process=self)
        if not self.exceptions:
            return
        if len(self.exceptions) == 1:
            raise_with_traceback(*self.exceptions[0])
        error_text = ['', '-' * 50]
        final_exc = None
        for exc, tb in self.exceptions:
            if isinstance(exc, UnableToKillJob):
                scription_debug('setting final_exc to', exc)
                final_exc = exc
            if tb is None:
                scription_debug('encountered %r' % (exc, ))
                error_text.append('%s: %s' % (exc.__class__.__name__, exc))
            else:
                scription_debug('encountered %r w/traceback' % (exc, ))
                lines = traceback.format_list(traceback.extract_tb(tb))
                error_text.extend(lines)
                error_text.append('%s: %s' % (exc.__class__.__name__, exc))
            error_text.append('-' * 50)
        if final_exc is None:
            scription_debug('setting final_exc to', exc)
            final_exc = exc
        error_text = "\n%s" % '\n'.join('  '+l for r in error_text for l in r.split('\n') if l)
        final_exc = final_exc.__class__(error_text)
        raise_with_traceback(final_exc, None)

    def read(self, max_size, block=True, encoding='utf-8'):
        # if block is False, return None if no data ready
        # otherwise, encode to string with encoding, or raw if
        # encoding is None
        #
        # check for any unread data
        while "looking for data":
            while self._all_output.qsize() or block:
                stream, data = self._all_output.get()
                if encoding is not None:
                    data = data.decode(encoding)
                if stream == 'stdout':
                    self._stdout.append(data)
                elif stream == 'stderr':
                    self._stderr.append(data)
                else:
                    try:
                        raise Exception('unknown stream: %r' % stream)
                    except Exception:
                        _, exc, tb = sys.exc_info()
                        raise self._set_exc(exc, traceback=tb)
                if self._stdout:
                    break
            if self._stdout:
                # TODO: make test case to expose below bug (self.pop)
                data = self.pop(0)
                if len(data) > max_size:
                    # trim
                    self._stdout.insert(0, data[max_size:])
                self._stdout_history.append(data)
                return data
            elif not block:
                return None

    def send_signal(self, signal):
        "parent method"
        scription_debug('sending signal:', signal)
        os.kill(self.pid, signal)
        time.sleep(0.1)

    def terminate(self):
        '''
        Send SIGTERM to child.

        parent method'''
        scription_debug('terminating')
        if self.is_alive() and self.kill_signals:
            sig = self.kill_signals[0]
            os.kill(self.pid, sig)
            time.sleep(0.1)

    def write(self, data, block=True):
        'parent method'
        scription_debug('writing %r' % data, verbose=2)
        if not self.is_alive():
            try:
                raise OSError(errno.ECHILD, "No child processes")
            except Exception:
                _, exc, tb = sys.exc_info()
                raise self._set_exc(exc, traceback=tb)
        if not isinstance(data, bytes):
            data = data.encode('utf-8')
        self._all_input.put(data)
        if block:
            while not self._all_input.empty():
                time.sleep(0.1)
        return len(data)

    def write_error(self, data):
        'child method'
        if not isinstance(data, bytes):
            data = data.encode('utf-8')
        os.write(self.error_pipe, data)

class ormclassmethod(object):

    def __init__(self, func):
        self.func = func

    def __get__(self, instance, owner):
        if instance:
            raise AttributeError('%r instance has no attribute %r' % (owner.__name__, self.func.__name__))
        return self.func


class OrmSection(NameSpace):

    __slots__ = '_OrmSection__name_', '_OrmSection__order_', '_OrmSection__comment_'

    def __init__(self, comment='', name=None):
        super(OrmSection, self).__init__()
        self.__order_ = []
        self.__name_ = name
        self.__comment_ = None
        if comment:
            self.__comment_ = '; ' + comment.replace('\n','\n; ')

    def __hash__(self):
        if self.__name_ is None:
            raise TypeError('nameless OrmSection is not hashable')
        return hash(self.__name_)

    def __iter__(self):
        for key in self.__order_:
            yield key, self[key]

    def __setattr__(self, name, value):
        if isinstance(value, OrmSection) and value._OrmSection__name_ is None:
            value._OrmSection__name_ = name
        res = super(OrmSection, self).__setattr__(name, value)
        if name not in self.__slots__ and name not in self.__order_:
            self.__order_.append(name)
        return res

    def __setitem__(self, name, value):
        if isinstance(value, OrmSection) and value._OrmSection__name_ is None:
            value._OrmSection__name_ = name
        res = super(OrmSection, self).__setitem__(name, value)
        if name not in self.__slots__ and name not in self.__order_:
            self.__order_.append(name)
        return res

    def __repr__(self):
        return '%r' % (tuple(self.__dict__.items()), )

    @ormclassmethod
    def get(section, name, default=None):
        try:
            return section.__dict__[name]
        except KeyError:
            return default


class OrmFile(object):
    """
    lightweight ORM for scalar values

    read and make available the settings of a configuration file,
    converting the values as str, int, float, date, time, or
    datetime based on:
      - presence of quotes
      - presence of colons and/or hyphens
      - presence of period

    if `plain` is True, then only True/False/None and numbers are
    converted, everything else is a string.
    """
    _str = unicode
    _path = unicode
    _date = datetime.date
    _time = datetime.time
    _datetime = datetime.datetime
    _bool = bool
    _float = float
    _int = int
    _none = lambda s: None

    def __init__(self, filename, section=None, export_to=None, types={}, encoding='utf-8', plain=False):
        # if section, only return defaults merged with section
        # if export_to, it should be a mapping, and will be populated
        # with the settings
        # if types, use those instead of the default orm types
        for n, t in types.items():
            if n not in (
                    '_str', '_path', '_date', '_time', '_datetime',
                    '_bool', '_float', '_int',
                    ):
                raise TypeError('OrmFile %r: invalid orm type -> %r' % (filename, n))
            setattr(self, n, t)
        target_sections = []
        self._saveable = True
        if section:
            target_sections = section.lower().split('.')
            self._saveable = False
        self._section = section
        self._filename = filename
        defaults = OrderedDict()
        settings = self._settings = OrmSection(name=filename)
        if not os.path.exists(filename):
            open(filename, 'w').close()
        if PY2:
            fh = open(filename)
        else:
            fh = open(filename, encoding=encoding)
        try:
            section = None
            for line in fh:
                if PY2:
                    line = line.decode(encoding)
                line = line.strip()
                if not line or line.startswith(('#',';')):
                    continue
                if line[0] == '[':
                    # better be a section header
                    if line[-1] != ']':
                        raise OrmError('OrmFile %r; section headers must start and end with "[]" [got %r]' % (filename, line, ))
                    sections = self._verify_section_header(line[1:-1])
                    prior, section = sections[:-1], sections[-1]
                    new_section = OrmSection(name=section)
                    for key, value in defaults.items():
                        setattr(new_section, key, value)
                    prev_namespace = self
                    for prev_name in prior:
                        prev_namespace = prev_namespace[prev_name]
                        for key_value in prev_namespace:
                            key, value = key_value
                            if not isinstance(value, OrmSection):
                                setattr(new_section, key, value)
                    setattr(prev_namespace, section, new_section)
                else:
                    # setting
                    name, value = line.split('=', 1)
                    name = self._verify_name(name)
                    value = self._verify_value(value, plain=plain)
                    if section:
                        setattr(new_section, name, value)
                    else:
                        setattr(settings, name, value)
                        defaults[name] = value
        finally:
            fh.close()
        for section in target_sections:
            settings = settings[section]
        self._settings = settings
        if export_to is not None:
            for name, value in settings.__dict__.items():
                if name[0] != '_':
                    export_to[name] = value

    def __repr__(self):
        if self._section is None:
            return '%s(%r)' % (self.__class__.__name__, self._filename)
        else:
            return '%s(%r, section=%r)' % (self.__class__.__name__, self._filename, self._section)

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return NotImplemented
        return self._settings == other._settings

    def __ne__(self, other):
        if not isinstance(other, self.__class__):
            return NotImplemented
        return self._settings != other._settings

    def __iter__(self):
        values = []
        sections = []
        for key, value in self._settings:
            if isinstance(value, OrmSection):
                sections.append((key, value))
            else:
                values.append((key, value))
        for key, value in values:
            yield key, value
        for key, value in sections:
            yield key, value
        return

    def __getattr__(self, name):
        name = name.lower()
        if name in self._settings.__dict__:
            return getattr(self._settings, name)
        raise OrmError("OrmFile %r: no section/default named %r" % (self._filename, name))

    def __getitem__(self, name):
        return self._settings[name]

    def __setattr__(self, name, value):
        if name in (
                '_settings', '_filename', '_section', '_saveable',
                '_str', '_path', '_date', '_time', '_datetime', '_bool', '_float', '_int',
                ):
            object.__setattr__(self, name, value)
        else:
            self._settings[name] = value

    def __setitem__(self, name, value):
        self._settings[name] = value

    def _verify_name(self, name):
        name = name.strip().lower()
        if not name[0].isalpha():
            raise OrmError('OrmFile %r: names must start with a letter (got %r)' % (self._filename, name, ))
        if re.sub('\w*', '', name):
            # illegal characters in name
            raise OrmError('OrmFile %r: names can only contain letters, digits, and the underscore [%r]' % (self._filename, name))
        return name

    def _verify_section_header(self, section):
        sections = section.strip().lower().split('.')
        current_section = sections[-1]
        if not current_section[0].isalpha():
            raise OrmError('OrmFile %r: names must start with a letter' % (self._filename, ))
        if re.sub('\w*', '', current_section):
            # illegal characters in section
            raise OrmError('OrmFile %r: names can only contain letters, digits, and the underscore [%r]' % (self._filename, current_section))
        if current_section in self.__dict__:
            # section already exists
            raise OrmError('OrmFile %r: section %r is a duplicate, or already exists as a default value' % (self._filename, current_section))
        sections[-1] = current_section
        return sections

    def _verify_value(self, value, plain=False):
        # quotes indicate a string
        # / or \ indicates a path
        # : or - indicates time, date, datetime (big-endian)
        # . indicates float
        # True/False indicates True/False
        # anything else is fed through int()
        #
        # except if `plain` is True, then
        # True/False/None are True/False/None
        # numbers are integer or float
        # everything else is a string
        value = value.strip()
        if plain:
            if not value:
                return value
            if value.lower() in ('true', 'false', 'none'):
                return (self._bool(True), self._bool(False), self._none())[('true', 'false', 'none').index(value.lower())]
            try:
                return self._int(value)
            except ValueError:
                try:
                    return self._float(value)
                except ValueError:
                    pass
            return value
        if value[0] in ('"', "'"):
            # definitely a string
            if value[0] != value[-1]:
                raise OrmError('OrmFile %r: string must be quoted at both ends [%r]' % (self._filename, value))
            start, end = 1, -1
            if value[:3] in ('"""', "'''"):
                if value[:3] != value[-3:] or len(value) < 6:
                    raise OrmError('OrmFile %r: invalid string value: %r' % (self._filename, value))
                start, end = 3, -3
            return self._str(value[start:end])
        elif value.startswith(('[', '(', '{')):
            # list/tuple/dict
            return ast.literal_eval(value)
        elif '/' in value or '\\' in value:
            # path
            return self._path(value)
        elif ':' in value and '-' in value:
            # datetime
            try:
                date = map(int, value[:10].split('-'))
                time = map(int, value[11:].split(':'))
                return self._datetime(*(date+time))
            except ValueError:
                raise OrmError('OrmFile %r: invalid datetime value: %r' % (self._filename, value))
        elif '-' in value:
            # date
            try:
                date = map(int, value.split('-'))
                return self._date(date)
            except ValueError:
                raise OrmError('OrmFile %r: invalid date value: %r' % (self._filename, value))
        elif ':' in value:
            # time
            try:
                time = map(int, value.split(':'))
                return self._time(*time)
            except ValueError:
                raise OrmError('OrmFile %r: invalid time value: %r' % (self._filename, value))
        elif '.' in value:
            # float
            try:
                value = self._float(value)
            except ValueError:
                raise OrmError('OrmFile %r: invalid float value: %r' % (self._filename, value))
        elif value.lower() == 'true':
            # boolean - True
            return self._bool(True)
        elif value.lower() in ('false', ''):
            # boolean - False
            return self._bool(False)
        elif value.lower() == 'none':
            # None
            return self._none()
        elif any(c.isdigit() for c in value):
            # int
            try:
                return self._int(value)
            except ValueError:
                raise OrmError('OrmFile %r: invalid integer value: %r' % (self._filename, value))
        else:
            # must be a string
            return value

    @ormclassmethod
    def save(orm, filename=None, force=False):
        # quotes indicate a string
        # / or \ indicates a path
        # : or - indicates time, date, datetime (big-endian)
        # . indicates float
        # True/False/None indicates True/False/None
        # anything else is fed through int()
        #
        # except if `plain` is True, then
        # True/False/None are True/False/None
        # numbers are integer or float
        # everything else is a string
        if not orm._saveable:
            raise OrmError('unable to save when sections specified on opening')
        filename = filename or orm._filename
        def savelines(settings, lines=None, section_name=''):
            if lines is None:
                lines = []
            if section_name:
                lines.append('\n[%s]' % (section_name, ))
            if settings._OrmSection__comment_:
                lines.append(settings._OrmSection__comment_)
            items = sorted(
                    settings,
                    key=lambda item: (isinstance(item[1], OrmSection)),
                    )
            for setting_name, obj in items:
                if obj in (True, False, None) or isinstance(obj, number):
                    lines.append('%s = %s' % (setting_name, obj))
                elif isinstance(obj, orm._datetime):
                    if obj.second:
                        lines.append(obj.strftime('%%s = %Y-%m-%d %H:%M:%S') % (setting_name, ))
                    else:
                        lines.append(obj.strftime('%%s = %Y-%m-%d %H:%M') % (setting_name, ))
                elif isinstance(obj, orm._date):
                        lines.append(obj.strftime('%%s = %Y-%m-%d') % (setting_name, ))
                elif isinstance(obj, orm._time):
                    if obj.second:
                        lines.append(obj.strftime('%%s = %H:%M:%S') % (setting_name, ))
                    else:
                        lines.append(obj.strftime('%%s = %H:%M') % (setting_name, ))
                elif isinstance(obj, orm._path) and not orm._path in basestring:
                    lines.append('%s = %s' % (setting_name, obj))
                elif not isinstance(obj, basestring) and not isinstance(obj, OrmSection):
                    # list, tuple, dict, etc.
                    lines.append('%s = %s' % (setting_name, obj))
                elif isinstance(obj, basestring):
                    lines.append('%s = "%s"' % (setting_name, obj))
                else:
                    # at this point, it's a Section
                    savelines(obj, lines, ('%s.%s' % (section_name, setting_name)).strip('.'))
            return lines
        lines = savelines(orm._settings)
        if (
                orm._filename
                and filename != orm._filename
                and os.path.exists(filename)
                and not force
            ):
            raise Exception('file %r exists; use force=True to overwrite' % (filename, ))
        with open(filename, 'w') as f:
            f.write('\n'.join(lines))

IniError = OrmError     # deprecated, will be removed by 1.0
IniFile = OrmFile       # deprecated, will be removed by 1.0


class ColorTemplate(object):
    "string %-templates that support color"

    class Multiline(Enum):
        IGNORE = 'ignore'
        TRUNCATE = 'truncate'
        WRAP = 'wrap'

    def __init__(self, template, multiline=Multiline.IGNORE, default_color=None, select_colors=lambda r: (lambda d: d, )*len(r)):
        self.template = re.sub(r'%[+-]?\d*[sdf]', lambda m: '%s'+m.group()+'%s', template)
        if default_color is None:
            default_color = Color.AllReset
        self.default_color = default_color
        self.select_colors = select_colors
        self.multiline = self.Multiline(multiline)

    def __call__(self, *cells):
        colors = self.select_colors(cells)
        result = []
        if self.multiline is self.Multiline.WRAP:
            cells = tuple([c.split('\n') if isinstance(c, basestring) else [c] for c in cells])
            for row in zip_longest(*cells, fillvalue=''):
                line = []
                for color, data in zip(colors, row):
                    line.extend([str(color), data, str(self.default_color)])
                result.append(self.template % tuple(line))
            return str(self.default_color) + '\n'.join(result)
        elif self.multiline is self.Multiline.TRUNCATE:
            cells = tuple([c.split('\n')[0] if isinstance(c, basestring) else c for c in cells])
        for color, data in zip(colors, cells):
            result.extend([str(color), data, str(self.default_color)])
        return str(self.default_color) + (self.template % tuple(result))


class Color(str, Flag):

    def __new__(cls, value, code):
        str_value = '\x1b[%sm' % code
        obj = str.__new__(cls, str_value)
        obj._value_ = value
        obj.code = code
        return obj

    @classmethod
    def _create_pseudo_member_values_(cls, members, *values):
        code = ';'.join(m.code for m in members)
        return values + (code, )

    AllReset = '0'           # ESC [ 0 m       # reset all (colors and brightness)
    Bright = '1'          # ESC [ 1 m       # bright
    Dim = '2'             # ESC [ 2 m       # dim (looks same as normal brightness)
    Underline = '4'
    Normal = '22'         # ESC [ 22 m      # normal brightness
                        #
                        # # FOREGROUND - 30s  BACKGROUND - 40s:
    FG_Black = '30'           # ESC [ 30 m      # black
    FG_Red = '31'             # ESC [ 31 m      # red
    FG_Green = '32'           # ESC [ 32 m      # green
    FG_Yellow = '33'          # ESC [ 33 m      # yellow
    FG_Blue = '34'            # ESC [ 34 m      # blue
    FG_Magenta = '35'         # ESC [ 35 m      # magenta
    FG_Cyan = '36'            # ESC [ 36 m      # cyan
    FG_White = '37'           # ESC [ 37 m      # white
    FG_Reset = '39'           # ESC [ 39 m      # reset
                            #
    BG_Black = '40'           # ESC [ 30 m      # black
    BG_Red = '41'             # ESC [ 31 m      # red
    BG_Green = '42'           # ESC [ 32 m      # green
    BG_Yellow = '43'          # ESC [ 33 m      # yellow
    BG_Blue = '44'            # ESC [ 34 m      # blue
    BG_Magenta = '45'         # ESC [ 35 m      # magenta
    BG_Cyan = '46'            # ESC [ 36 m      # cyan
    BG_White = '47'           # ESC [ 37 m      # white
    BG_Reset = '49'           # ESC [ 39 m      # reset

    __str__ = str.__str__

    def __repr__(self):
        if len(self) == 1:
            return '<%s.%s>' % (self.__class__.__name__, self._name_)
        else:
            return '<%s: %s>' % (self.__class__.__name__, self._name_)

    def __enter__(self):
        print(self.AllReset, end='', verbose=0)
        return self

    def __exit__(self, *args):
        print(self.AllReset, end='', verbose=0)

class ViewProgress(object):
    """
    Displays progress as a bar or a numeric count.
    """
    ViewType = Enum(
            'ViewType',
            (('BAR', 'bar'), ('PERCENT', 'percent'), ('COUNT', 'count'), ('NONE', 'none')),
            type=str,
            )
    export(ViewType, vars())

    def __init__(self, iterable, message=None, total=None, sep=': ', view_type='bar', bar_char='*'):
        try:
            view_type = self.ViewType(view_type)
        except KeyError:
            raise ScriptionError(
                    'unknown value %r for view_type; allowed values: %s'
                        % ', '.join(repr(vt.value) for vt in self.ViewType)
                    )
        headless = not stdout_is_atty
        if total is None and iterable is None:
            raise ValueError('total must be specified if not wrapping an iterable')
        elif total is None:
            try:
                total = len(iterable)
            except TypeError:
                get_hint = getattr(iterable, '__length_hint__', None)
                try:
                    total = get_hint(iterable)
                except TypeError:
                    pass
                if total is None and view_type is not self.NONE:
                    view_type = 'count'
        verbosity = script_module.get('script_verbosity', 1)
        self.blank = verbosity < 1 or view_type is self.NONE or headless
        self.iterator = iter(iterable)
        self.current_count = 0
        self.total = total
        self.blockcount = 0
        self.bar_char = bar_char
        self.view_type = self.ViewType(view_type)
        if self.view_type is self.ViewType.BAR and verbosity > 1:
            self.view_type = self.ViewType.PERCENT
        self.last_percent = 0
        self.last_count = 0
        self.last_time = time.time()
        self.f = sys.stdout
        if not self.blank:
            if message is not None:
                if total is not None and '$total' in message:
                    message = message.replace('$total', str(total))
                else:
                    message = ' '.join([w for w in message.split() if w != '$total'])
                if view_type is self.BAR:
                    message = '\n' + message
                if verbosity:
                    self.f.write('%s' % message)
                    if self.view_type is not self.BAR:
                        self.f.write(sep)
            if self.view_type is self.PERCENT:
                self.progress = self._bar_progress
                self.f.write('  0%')
            elif self.view_type is self.BAR:
                self.progress = self._bar_progress
                self.f.write('\n-------------------- % Progress ---------------- 1\n')
                self.f.write('    1    2    3    4    5    6    7    8    9    0\n')
                self.f.write('    0    0    0    0    0    0    0    0    0    0\n')
            elif self.view_type is self.COUNT:
                self.progress = self._count_progress
                self.time = time.time()
                self.f.write('0')
            else:
                raise Exception('unknown value for view_type: %r' % self.view_type)
            self.f.flush()
        scription_debug('ProgressView')
        for attr in 'blank iterator total bar_char view_type last_time progress'.split():
            scription_debug('  %s:  %r' % (attr, getattr(self, attr)), verbose=2)

    def __iter__(self):
        return self

    def __next__(self):
        try:
            obj = next(self.iterator)
        except StopIteration:
            self.progress(self.current_count, done=True)
            raise
        self.progress(self.current_count+1)
        return obj
    next = __next__

    def _count_progress(self, count, done=False):
        """
        Calculate current count, update views.
        """
        if self.blank:
            return
        self.current_count = count
        now = time.time()
        if now - self.last_time < 1 and not done:
            return
        self.f.write(''*len(str(self.last_count))+str(count))
        self.f.flush()
        self.last_count = count
        self.last_time = now
        if done:
            self.f.write('\n')
            self.f.flush()

    def _bar_progress(self, count, done=False):
        """
        Calculate current percent, update views.
        """
        if self.blank:
            return
        self.current_count = count
        count = min(count, self.total)
        if self.total == count or not self.total:
            complete = 100
        else:
            complete = int(floor(100.0*count/self.total))
        if complete <= self.last_percent:
            return
        self.last_percent = complete
        if self.view_type is self.PERCENT:
            self.f.write('%3d%%' % complete)
        elif self.view_type is self.BAR:
            blockcount = int(complete//2)
            if blockcount <= self.blockcount:
                return
            for i in range(self.blockcount, blockcount):
                self.f.write(self.bar_char)
            self.blockcount = blockcount
        else:
            raise Exception('unknown value for view_type: %r' % self.view_type)
        if complete == 100:
            self.f.write('\n')
        self.f.flush()

    def progress(self, count, done=False):
        # placeholder for one of above *_progress functions
        pass

    def tick(self):
        """
        Add one to counter, possibly update view.
        """
        self.current_count += 1
        self.progress(self.current_count)


class ProgressView(ViewProgress):
    """
    deprecated: use ViewProgress instead
    """
    def __init__(self, total=None, view_type='count', message=None, bar_char='*', sep=': ', iterable=None):
        from warnings import warn
        warn('ProgressView is deprecated; use ViewProgress (and double-check argument order).')
        return super(ProgressView, self).__init__(iterable, message, total, sep, view_type, bar_char)


class Trivalent(object):
    """
    three-value logic

    Accepts values of True, False, or None/empty.
    boolean value of Unknown is Unknown, and will raise.
    Truthy value is +1
    Unknown value is 0
    Falsey value is -1
    """
    def __new__(cls, value=None):
        if isinstance(value, cls):
            return value
        elif value in (None, empty):
            return cls.unknown
        elif isinstance(value, bool):
            return (cls.false, cls.true)[value]
        elif value in (-1, 0, +1):
            return (cls.unknown, cls.true, cls.false)[value]
        elif isinstance(value, basestring):
            if value.lower() in ('t', 'true', 'y', 'yes', 'on'):
                return cls.true
            elif value.lower() in ('f', 'false', 'n', 'no', 'off'):
                return cls.false
            elif value.lower() in ('?', 'unknown', 'null', 'none', ' ', ''):
                return cls.unknown
        raise ValueError('unknown value for %s: %s' % (cls.__name__, value))

    def __hash__(x):
        return hash(x.value)

    def __index__(x):
        return x.value

    def __int__(x):
        return x.value

    def __invert__(x):
        cls = x.__class__
        if x is cls.true:
            return cls.false
        elif x is cls.false:
            return cls.true
        return x

    def __and__(x, y):
        """
        AND (conjunction) x & y:
        True iff both x,y are True
        False iff at least one of x,y is False

              F   U   T
         ---+---+---+---
         F  | F | F | F
         ---+---+---+---
         U  | F | U | U
         ---+---+---+---
         T  | F | U | T
        """
        cls = x.__class__
        if not isinstance(y, cls) and y not in (None, empty, True, False):
            return NotImplemented
        y = cls(y)
        if x == y == cls.true:
            return cls.true
        elif x is cls.false or y is cls.false:
            return cls.false
        else:
            return cls.unknown
    __rand__ = __and__

    def __or__(x, y):
        """
        OR (disjunction): x | y:
        True iff at least one of x,y is True
        False iif both x,y are False

              F   U   T
         ---+---+---+---
         F  | F | U | T
         ---+---+---+---
         U  | U | U | T
         ---+---+---+---
         T  | T | T | T
        """
        cls = x.__class__
        if not isinstance(y, cls) and y not in (None, empty, True, False):
            return NotImplemented
        y = cls(y)
        if x is y is cls.false:
            return cls.false
        elif x is cls.true or y is cls.true:
            return cls.true
        else:
            return cls.unknown
    __ror__ = __or__

    def __xor__(x, y):
        """
        XOR (parity) x ^ y:
        True iff only one of x,y is True and other of x,y is False
        False iff both of x,y are False or both of x,y are True

              F   U   T
         ---+---+---+---
         F  | F | U | T
         ---+---+---+---
         U  | U | U | U
         ---+---+---+---
         T  | T | U | F
        """
        cls = x.__class__
        if not isinstance(y, cls) and y not in (None, empty, True, False):
            return NotImplemented
        y = cls(y)
        if x is cls.unknown or y is cls.unknown:
            return cls.unknown
        elif x is cls.true and y is cls.false or x is cls.false and y is cls.true:
            return cls.true
        else:
            return cls.false
    __rxor__ = __xor__

    def __bool__(x):
        """
        boolean value of Unknown is Unknown, and will raise
        """
        if x.value == 1:
            return True
        elif x.value == -1:
            return False
        else:
            raise ValueError('cannot determine boolean value of Unknown')
    __nonzero__ = __bool__

    def __eq__(x, y):
        cls = x.__class__
        if not isinstance(y, cls) and y not in (None, empty, True, False):
            return NotImplemented
        y = cls(y)
        return x.value == y.value

    def __ge__(x, y):
        cls = x.__class__
        if not isinstance(y, cls) and y not in (None, empty, True, False):
            return NotImplemented
        y = cls(y)
        return x.value >= y.value

    def __gt__(x, y):
        cls = x.__class__
        if not isinstance(y, cls) and y not in (None, empty, True, False):
            return NotImplemented
        y = cls(y)
        return x.value > y.value

    def __le__(x, y):
        cls = x.__class__
        if not isinstance(y, cls) and y not in (None, empty, True, False):
            return NotImplemented
        y = cls(y)
        return x.value <= y.value

    def __lt__(x, y):
        cls = x.__class__
        if not isinstance(y, cls) and y not in (None, empty, True, False):
            return NotImplemented
        y = cls(y)
        return x.value < y.value

    def __ne__(x, y):
        cls = x.__class__
        if not isinstance(y, cls) and y not in (None, empty, True, False):
            return NotImplemented
        y = cls(y)
        return x.value != y.value

    def __repr__(x):
        return "%s.%s: %r" % (x.__class__.__name__, x.name, x.value)

    def __str__(x):
        return x.name

Trivalent.true = object.__new__(Trivalent)
Trivalent.true.value = +1
Trivalent.true.name = 'Truthy'
Trivalent.false = object.__new__(Trivalent)
Trivalent.false.value = -1
Trivalent.false.name = 'Falsey'
Trivalent.unknown = object.__new__(Trivalent)
Trivalent.unknown.value = 0
Trivalent.unknown.name = 'Unknown'
Truthy = Trivalent.true
Unknown = Trivalent.unknown
Falsey = Trivalent.false

## casting for types of arguments
def Bool(arg):
    if arg in (True, False):
        return arg
    if isinstance(arg, Trivalent):
        return arg
    return arg.lower() in "true t yes y 1 on".split()

def InputFile(arg):
    return open(arg)

def OutputFile(arg):
    return open(arg, 'w')

## utilities

### quiting
def abort(msg=None, returncode=Exit.Error):
    "prints msg to stderr, calls sys.exit() with returncode"
    with print_lock:
        if msg:
            if script_module.get('script_verbosity', 1) > 0:
                progname = script_module['script_fullname']
            else:
                progname = script_module['script_name']
            result = '%s: %s' % (progname, msg)
            script_module['script_abort_message'] = result
            print(result, file=stderr, verbose=0)
        sys.exit(returncode)

def help(msg, returncode=Exit.ScriptionError):
    "conditionally adds reference to --help"
    if '--help' not in msg:
        msg += ' (use --help for more information)'
    abort(msg, returncode)

### printing
def scription_debug(*values, **kwds):
    # kwds can contain sep (' '), end ('\n'), file (sys.stdout), and
    # verbose (1)
    with print_lock:
        verbose_level = kwds.pop('verbose', 1)
        if 'file' not in kwds:
            kwds['file'] = stderr
        if verbose_level > SCRIPTION_DEBUG:
            return
        _print('scription> ', *values, **kwds)

def debug(*args, **kwds):
    with print_lock:
        kwds.setdefault('verbose', 4)
        print(*args, **kwds)

def echo(*args, **kwds):
    with print_lock:
        kwds.setdefault('verbose', 0)
        print(*args, **kwds)

def error(*args, **kwds):
    with print_lock:
        returncode = kwds.pop('returncode', None)
        kwds['file'] = stderr
        kwds.setdefault('verbose', 0)
        print(*args, **kwds)
        if returncode:
            abort(returncode=returncode)

def info(*args, **kwds):
    with print_lock:
        kwds.setdefault('verbose', 1)
        print(*args, **kwds)

def split_text(text, max):
    # return a list of strings where each string is <= max, and words are whole
    # newlines are honorod
    lines = []
    text = text.split('\n')
    for line in text:
        while line:
            if len(line) <= max:
                lines.append(line.rstrip())
                break
            limit = max
            while line[limit] not in ' \t' and limit:
                limit -= 1
            if limit:
                lines.append(line[:limit].rstrip())
                line = line[limit:].lstrip()
            else:
                # no whitespace, just take a chunk
                lines.append(line[:max].rstrip())
                line = line[max:]
    return lines

def box(message, *style, **kwds):
    """ draws box around text using style -> ([border,] [char [, char [ ...]]])

    stlye:

    -------
    | flag
    -------
    -------
    | box |
    -------

    --------
    overline

    underline
    ---------

    -----
    lined
    -----

    chars:

    single str or 1-item tuple -> char to use for all positions
    2-item tuple ->  (top_bottom, left_right)
    4-item tuple ->  (top, bottom, left, right)
    """
    if style and style[0] in ('flag', 'box', 'overline', 'underline', 'lined'):
        border = style[0]
        chars = style[1:]
    else:
        border = 'box'
        chars = style
    lines = message.split('\n')
    width = max([len(re.sub('\x1b\[[\d;]*\w', '', l)) for l in lines])
    if not chars:
        top = bottom = '-'
        left = right = '|'
    elif len(chars) == 1:
        top = bottom = left = right = chars[0]
    elif len(chars) == 2:
        top = bottom = chars[0]
        left = right = chars[1]
    elif len(chars) == 4:
        top, bottom, left, right = chars
    else:
        raise ScriptionError('if box chars specified, must be a single item for use as all four, two items for use as top/bottom and left/right, or four items')
    # calculate rule now
    rule = '-' * width
    #
    padding = 0
    if border == 'box':
        padding = 1
        width += len(left) + len(right) + 2 * padding
    elif border == 'flag':
        padding = 1
        width += len(left) + 2 * padding
        # make sure right is not used
        right = ''
    else:
        # make sure left and right are not used
        left = right = ''
    #
    times, remainder = divmod(width, len(top))
    top_line = top * times
    if remainder:
        top_line += top[-remainder:]
    #
    times, remainder = divmod(width, len(bottom))
    bottom_line = bottom * times
    if remainder:
        bottom_line += bottom[-remainder:]
    #
    box = []
    padding = padding * ' '
    if border != 'underline':
        box.append(top_line)
    for line in lines:
        if line == '---':
            line = rule
        leading = ('%(left)s%(padding)s%(line)s' %
                {'left': left, 'padding': padding, 'line':line}
                )
        line = '%-*s%s' % (width-len(right), leading, right)
        box.append(line)
    if border != 'overline':
        box.append(bottom_line)
    return '\n'.join(box)

def table_display(rows, widths=None, types=None, header=True, display_none=None, record='row', display_tz=False):
    # assemble the table
    if widths:
        types = types or [''] * len(rows[0])
    else:
        rows = list(rows)
        if not rows:
            return
        widths = [0] * len(rows[0])
        types = [''] * len(rows[0])
        first_row = header
        rows_copy = []
        for row in rows:
            rows_copy.append(row)
            if row is None:
                continue
            if not isinstance(row, (tuple, list)):
                continue
            for i, cell in enumerate(row):
                if isinstance(cell, logical):
                    width = 1
                elif isinstance(cell, datetimes) and display_tz:
                    width = 25
                elif isinstance(cell, datetimes):
                    width = 19
                elif isinstance(cell, times) and display_tz:
                    width = 14
                elif isinstance(cell, times):
                    width = 8
                elif isinstance(cell, dates):
                    width = 10
                elif cell is None:
                    width = 1
                else:
                    width = max([len(p) for p in str(cell).split('\n')])
                widths[i] = max(widths[i], width)
                if record == 'column' or cell in [None, ''] or first_row:
                    continue
                if not types[i]:
                    # check fixed first as bool is both number and fixed
                    if isinstance(cell, fixed):
                        types[i] = 'f'
                    elif isinstance(cell, number):
                        types[i] = 'n'
                    else:
                        types[i] = 't'
            first_row = False
        rows = rows_copy
    # sum(widths) -> how much space is alloted to other data
    # 3*len(widths) -> how much space used by margins of interior lines
    # -3 -> one less interior line than column
    single_cell_width = sum(widths) + 3*len(widths) - 3
    edge = '-' * (single_cell_width + 4)
    sep = ' | '.join(['-' * w for w in widths])
    sides = '| %s |'
    printed = False
    for row in rows:
        if not printed:
            yield(edge)
            printed = True
        if row is None:
            # lines.append(sep)
            yield(sides % sep)
        elif not isinstance(row, (tuple, list)):
            # handle a single, joined row
            if not isinstance(row, basestring):
                raise ValueError('joined row value must be a string, not %r [%r]' % (type_of(row), row))
            if len(row) == 1:
                # make a line using the row character
                row = row * single_cell_width
            for line in split_text(row, single_cell_width):
                yield(sides % ('%-*s' % (single_cell_width, line)))
        else:
            for row in zip_values(row, widths, types):
                line = []
                for value, width, align in row:
                    if align == '':
                        if isinstance(value, fixed):
                            align = 'f'
                        elif isinstance(value, number):
                            align = 'n'
                        else:
                            align = 't'
                    if value is None:
                        # special case: use display_none
                        if display_none is None:
                            cell = width * ' '
                        elif len(display_none) == 1:
                            if width < 3:
                                cell = display_none * width
                            elif width <= 5:
                                cell = (display_none * (width-2)).center(width)
                                # cell = '%^*s' % (width, (display_none * (width-2)))
                            elif width % 2:
                                cell = (display_none * 3).center(width)
                                # cell = '%^*s' % (width, display_none * 3)
                            else:
                                cell = (display_none * 4).center(width)
                                # cell = '%^*s' % (width, display_none * 4)
                        else:
                            cell = (display_none[:width]).center(width)
                            # cell = '%^*s' % (width, display_none[:width])
                    elif align == 't':
                        # left
                        cell = '%-*s' % (width, value)
                    elif align == 'n':
                        # right
                        cell = '%*s' % (width, value)
                    elif align == 'f':
                        if isinstance(value, bool):
                            value = 'fT'[value]
                        elif isinstance(value, datetimes) and display_tz:
                            if value.tzinfo:
                                value = value.strftime('%Y-%m-%d %H:%M:%S %z')
                            else:
                                value = value.strftime('%Y-%m-%d %H:%M:%S <unk>')
                        elif isinstance(value, datetimes):
                            value = value.strftime('%Y-%m-%d %H:%M:%S')
                        elif isinstance(value, times) and display_tz:
                            if value.tzinfo:
                                value = value.strftime('%H:%M:%S %z')
                            else:
                                value = value.strftime('%H:%M:%S <unk>')
                        elif isinstance(value, times):
                            value = value.strftime('%H:%M:%S')
                        elif isinstance(value, dates):
                            value = value.strftime('%Y-%m-%d')
                        else:
                            value = str(value)
                        t = len(value)
                        # center/fixed
                        l = (width-t) // 2
                        r = width - t - l
                        l = l * ' '
                        r = r * ' '
                        cell = '%s%s%s' % (l, value, r)
                    line.append(cell)
                yield(sides % ' | '.join(line))
    if printed:
        yield(edge)

def print(*values, **kwds):
    # kwds can contain sep (' '), end ('\n'), file (sys.stdout), border (None),
    # and verbose (1)
    with print_lock:
        verbose_level = kwds.pop('verbose', 1)
        target = kwds.get('file') or stdout
        if verbose_level > script_module.get('script_verbosity', 1):
            return
        border = kwds.pop('border', None)
        if border == 'table':
            if len(values) != 1 or not isinstance(values[0], (tuple, list)):
                raise ValueError('invalid table value')
            types, widths = kwds.pop('table_specs', (None, None))
            values = (table_display(
                    values[0],
                    widths=widths,
                    types=types,
                    header=kwds.pop('table_header', True),
                    display_none=kwds.pop('table_display_none', None),
                    display_tz=kwds.pop('table_display_tz', False),
                    record=kwds.pop('table_record', 'row')
                    ),
                    )
            border = None
        if border is not None and not isinstance(border, tuple):
            border = (border, )
        sep = kwds.get('sep', ' ')
        if target in _is_atty:
            is_tty = _is_atty[target]
        else:
            try:
                is_tty = os.isatty(target.fileno())
                _is_atty[target] = is_tty
            except Exception:
                _is_atty[target] = is_tty = False
        gen = None
        for v in values:
            if isinstance(v, GeneratorType):
                if gen is False:
                    raise ValueError("cannot mix generators and non-generators in print() call")
                gen = True
            else:
                if gen is True:
                    raise ValueError("cannot mix generators and non-generators in print() call")
                gen = False
        if not gen and (not is_tty or border is not None):
            old_values = []
            new_values = []
            for v in values:
                v = str(v)
                old_values.append(v)
                v = re.sub('\x1b\[[\d;]*\w', '', v)
                new_values.append(v)
            if not is_tty:
                values = new_values
            else:
                values = old_values
            if border is not None:
                values = (box(sep.join(values), *border), )
        try:
            if gen:
                for v in values:
                    for data in v:
                        if not is_tty:
                            data = re.sub('\x1b\[[\d;]*\w', '', data)
                        _print(data, **kwds)
                        target.flush()
            else:
                _print(*values, **kwds)
                target.flush()
        except IOError:
            cls, exc, tb = sys.exc_info()
            if exc.errno == errno.EPIPE:
                sys.exit(Exit.IoError)
            raise

# get/set terminal writers
_is_atty = {}
try:
    _is_atty[stdin] = os.isatty(stdin.fileno())
except Exception:
    _is_atty[stdin] = False
stdin_is_atty = _is_atty[stdin]

try:
    stdout_is_atty = os.isatty(stdout.fileno())
except Exception:
    stdout_is_atty = False

try:
    stderr_is_atty = os.isatty(stderr.fileno())
except Exception:
    stderr_is_atty = False

# ensure proper unicode handling; based on
# https://stackoverflow.com/a/27347906, and
# https://stackoverflow.com/a/27347913
if stdout.encoding is None or stdout.encoding == 'ANSI_X3.4-1968' or not stdout_is_atty:
    channel_writer = codecs.getwriter('UTF-8')
    errors = None
else:
    channel_writer = codecs.getwriter(sys.stdout.encoding)
    errors = 'replace'
if sys.version_info.major < 3:
    sys.stdout = channel_writer(sys.stdout, errors=errors)
else:
    sys.stdout = channel_writer(sys.stdout.buffer, errors=errors)
stdout = sys.stdout
_is_atty[stdout] = stdout_is_atty

if stderr.encoding is None or stderr.encoding == 'ANSI_X3.4-1968' or not stderr_is_atty:
    channel_writer = codecs.getwriter('UTF-8')
    errors = None
else:
    channel_writer = codecs.getwriter(sys.stderr.encoding)
    errors = 'replace'
if sys.version_info.major < 3:
    sys.stderr = channel_writer(sys.stderr, errors=errors)
else:
    sys.stderr = channel_writer(sys.stderr.buffer, errors=errors)
stderr = sys.stderr
_is_atty[stderr] = stderr_is_atty

def zip_values(row, widths, types):
    """
    each value of row may also be multiple values
    """
    expanded_row = []
    for i, cell in enumerate(row):
        if isinstance(cell, basestring):
            expanded_row.append(tuple(split_text(cell, widths[i])))
        else:
            expanded_row.append((cell, ))
    for row in zip_longest(*expanded_row, fillvalue=empty):
        line = []
        for c, w, t in zip(row, widths, types):
            line.append((c, w, t))
        yield line

def log_exception(tb=None):
    if tb is None:
        cls, exc, tb = sys.exc_info()
        lines = traceback.format_list(traceback.extract_tb(tb))
        lines.append('%s: %s\n' % (cls.__name__, exc))
        logger.critical('Traceback (most recent call last):')
    else:
        lines = tb.split('\\n')
    for line in lines:
        for ln in line.rstrip().split('\n'):
            logger.critical(ln)
    return lines


### interaction
def input(
        question='',
        validate=None,
        type=None,
        retry='bad response, please try again',
        default=undefined,
        encoding='utf8',
        ):
    # True/False: no square brackets, ends with '?'
    #   'Do you like green eggs and ham?'
    # Multiple Choice: square brackets
    #   'Delete files matching *.xml? [N/y/a]'
    #   'Are hamburgers good? [Always/sometimes/never]'
    # Anything: no square brackets, does not end in '?'
    #   'name'
    #   'age'
    if default:
        default = default.lower()
    if '[' not in question and question.rstrip().endswith('?'):
        # yes/no question
        if type is None:
            type = lambda ans: ans.lower() in ('y', 'yes', 't', 'true')
        if validate is None:
            validate = lambda ans: ans.lower() in ('y', 'yes', 'n', 'no', 't', 'true', 'f', 'false')
    elif '[' not in question:
        # answer can be anything
        if type is None:
            type = str
        if validate is None:
            validate = lambda ans: True
    else:
        # two supported options:
        #   'some question [always/maybe/never]'
        # and
        #   'some question:\n[a]lways\n[m]aybe\n[n]ever'
        # responses are embedded in question between '[]' and consist
        # of first letter if all lowercase, else first capital letter
        actual_question = []
        allowed_responses = {}
        left_brackets = question.count('[')
        right_brackets = question.count(']')
        if left_brackets != right_brackets:
            raise ScriptionError('mismatched [ ]')
        elif left_brackets == 1:
            # first option
            current_word = []
            in_response = False
            for ch in question:
                if ch == '[':
                    in_response = True
                elif in_response and ch not in ('abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
                    word = ''.join(current_word)
                    current_word = []
                    if not word:
                        raise ScriptionError('empty choice')
                    uppers = ''.join([l for l in word if l == l.upper()])
                    lower_word = word.lower()
                    if not uppers:
                        uppers = lower_word[0]
                    allowed_responses[lower_word] = lower_word
                    allowed_responses[uppers.lower()] = lower_word
                    if default in (word, uppers):
                        actual_question.append('-')
                        actual_question.extend([c for c in word])
                        actual_question.append('-')
                    else:
                        actual_question.extend([c for c in word])
                    if ch == ']':
                        in_response = False
                elif in_response:
                    current_word.append(ch)
                    continue
                actual_question.append(ch)
        else:
            # second option
            current_response = []
            current_word = []
            in_response = False
            capture_word = False
            for ch in question+' ':
                if ch == '[':
                    in_response = True
                    capture_word = True
                elif ch == ']':
                    in_response = False
                    response = ''.join(current_response).lower()
                    allowed_responses[response] = response
                    current_response = []
                    if response == default:
                        response = response.upper()
                    actual_question.extend([c for c in response])
                elif ch not in ('abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
                    if capture_word:
                        word = ''.join(current_word).lower()
                        allowed_responses[response.lower()] = word
                        allowed_responses[word] = word
                    capture_word = False
                    current_word = []
                if ch not in '[]':
                    if capture_word:
                        current_word.append(ch)
                    if in_response:
                        current_response.append(ch.lower())
                        # and skip adding to question
                        continue
                actual_question.append(ch)
            if in_response:
                raise ScriptionError('question missing closing "]"')
        question = ''.join(actual_question)
        if type is None:
            type = lambda ans: allowed_responses[ans.strip().lower()]
        else:
            old_type = type
            type = lambda ans: old_type(allowed_responses[ans.strip().lower()])
        if validate is None:
            validate = lambda ans: ans and ans.strip().lower() in allowed_responses
    if not question[-1:] in (' ','\n', ''):
        question += ' '
    # check that a supplied default is valid
    if default and not validate(default):
        raise ScriptionError('supplied default is not valid')
    # setup is done, ask question and get answer
    while 'answer is unacceptable':
        answer = raw_input(question)
        if isinstance(answer, bytes):
            answer = str(answer, encoding=encoding)
        if default and not answer:
            answer = default
        if validate(answer):
            break
    return type(answer)
get_response = input

def mail(server=None, port=25, message=None):
    """
    sends email.message to server:port

    if message is a str, will break apart To, Cc, and Bcc at commas
    """
    receivers = []
    if message is None:
        raise ValueError('message not specified')
    elif isinstance(message, basestring):
        scription_debug('converting string -> email.message')
        scription_debug(message, verbose=2)
        message = email.message_from_string(message)
        for targets in ('To', 'Cc', 'Bcc'):
            scription_debug('   recipient target:', targets, verbose=2)
            groups = message.get_all(targets, [])
            scription_debug('      groups:', groups, verbose=2)
            del message[targets]
            for group in groups:
                scription_debug('      group:', group, verbose=2)
                addresses = group.split(',')
                for target in addresses:
                    scription_debug('         individual:', target, verbose=2)
                    target = target.strip()
                    message[targets] = target
                    receivers.append(target)
    scription_debug('receivers:', receivers, verbose=2)
    if 'date' not in message:
        message['date'] = email.utils.formatdate(localtime=True)
    sender = message['From']
    if server is None:
        scription_debug('skipping stage 1', verbose=2)
        send_errs = dict.fromkeys(receivers)
    else:
        try:
            scription_debug('stage 1: connect to smtp server', server, port)
            smtp = smtplib.SMTP(server, port)
        except socket.error:
            exc = sys.exc_info()[1]
            scription_debug('error:', exc)
            send_errs = {}
            for rec in receivers:
                send_errs[rec] = (server, exc.args)
        else:
            try:
                scription_debug('         sending mail')
                send_errs = smtp.sendmail(sender, receivers, message.as_string())
            except smtplib.SMTPRecipientsRefused:
                exc = sys.exc_info()[1]
                scription_debug('error:', exc)
                send_errs = {}
                for user, detail in exc.recipients.items():
                    send_errs[user] = (server, detail)
            finally:
                scription_debug('         quiting')
                smtp.quit()
    errs = {}
    if send_errs:
        for user in send_errs:
            try:
                server = 'mail.' + user.split('@')[1].strip('<>')
                scription_debug('stage 2: connect to user smtp server', server, 25)
                smtp = smtplib.SMTP(server, 25)
            except socket.error:
                exc = sys.exc_info()[1]
                errs[user] = [send_errs[user], (server, exc.args)]
                scription_debug('error:', exc)
            else:
                try:
                    smtp.sendmail(sender, [user], message.as_string())
                except smtplib.SMTPRecipientsRefused:
                    exc = sys.exc_info()[1]
                    errs[user] = [send_errs[user], (server, exc.recipients[user])]
                    scription_debug('error:', exc)
                finally:
                    smtp.quit()
    return errs

### miscellaneous
class Sentinel(object):
    "provides better help for sentinels"
    #
    def __init__(self, text, boolean=True):
        self.text = text
        self.boolean = boolean
    #
    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self.text)
    #
    def __str__(self):
        return '<%s>' % self.text
    #
    def __bool__(self):
        return self.boolean
    __nonzero__ = __bool__

def Singleton(cls):
    "transforms class into a Singleton object"
    return cls()

@Singleton
class pocket(object):
    '''
    container to save values from intermediate expressions

    nb: return value is unordered
    '''
    pocket = threading.local()

    def __call__(self, **kwds):
        res = []
        # setattr(self.pocket, 'data', {})
        level = self.pocket.data = {}
        for names, value in kwds.items():
            names = names.split('.')
            for name in names[:-1]:
                if name not in level:
                    level[name] = {}
                    level = level[name]
            name = names[-1]
            level[name] = value
            res.append(value)
        if len(res) == 1:
            [res] = res
        else:
            res = tuple(res)
        return res

    def __getattr__(self, name):
        try:
            return self.pocket.data[name]
        except KeyError:
            raise AttributeError('%s has not been saved' % name)

_Var_Sentinel = Sentinel("Var")
class Var(object):
    '''
    := for Python's less than 3.8
    '''
    def __init__(self, func=None):
        self._data = _Var_Sentinel
        self._func = func
    #
    def __call__(self, *args, **kwds):
        if not args:
            if self._data is _Var_Sentinel:
                raise ValueError('nothing saved in var')
            else:
                return self._data
        # we have args, what shall we do with them?
        if self._func is not None:
            # run user-supplied function
            self._data = self._func(*args, **kwds)
            return self._data
        elif kwds:
            raise ValueError('keywords not supported unless function is specified')
        elif len(args) == 1:
            self._data = args[0]
            return self._data
        else:
            self._data = args
            return self._data
    #
    def __getattr__(self, name):
        if self._data is _Var_Sentinel:
            raise ValueError('nothing saved in var')
        try:
            return getattr(self._data, name)
        except AttributeError:
            raise AttributeError('%r has no %r' % (self._data, name))



class user_ids(object):
    """
    maintains root as one of the ids
    """
    def __init__(self, uid, gid):
        self.target_uid = uid
        self.target_gid = gid
        self.saved_uids = os.getuid(), os.geteuid()
        self.saved_gids = os.getgid(), os.getegid()
    def __enter__(self):
        os.seteuid(0)
        os.setegid(0)
        os.setregid(0, self.target_gid)
        os.setreuid(0, self.target_uid)
    def __exit__(self, *args):
        os.seteuid(0)
        os.setegid(0)
        os.setregid(*self.saved_gids)
        os.setreuid(*self.saved_uids)

class wait_and_check(object):
    'is True until <seconds> have passed; waits <period> seconds on each check'
    def __init__(self, seconds, period=1):
        if seconds < 0:
            raise ValueError('seconds cannot be less than zero')
        if period <= 0:
            raise ValueError('period must be greater than zero')
        self.limit = time.time() + seconds
        self.period = period
    def __bool__(self):
        if time.time() < self.limit:
            time.sleep(self.period)
            if time.time() < self.limit:
                return True
        return False
    __nonzero__ = __bool__
