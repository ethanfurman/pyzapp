"""
xaml -- xml abstract markup language

https://bitbucket.org/stoneleaf/xaml

Copyright 2015-2019 Ethan Furman -- All rights reserved.
"""
# imports
from __future__ import unicode_literals, print_function
try:
    from aenum import Enum
except ImportError:
    from enum import Enum
import re
import sys
import textwrap
import unicodedata

__all__ = ['Xaml', ]
__metaclass__ = type

version = 0, 6, 5

try:
    unicode
except NameError:
    unicode = str
    unichr = chr

# only change default_encoding if you cannot specify the proper encoding with a meta tag
DEFAULT_ENCODING = 'utf-8'

# helpers

class AutoEnum(Enum):
    """
    Automatically numbers enum members starting from 1.
    Includes support for a custom docstring per member.
    """

    __last_number__ = 0

    def __new__(cls, *args):
        """Ignores arguments (will be handled in __init__."""
        value = cls.__last_number__ + 1
        cls.__last_number__ = value
        obj = object.__new__(cls)
        obj._value_ = value
        return obj

    def __init__(self, *args):
        """Can handle 0 or 1 argument; more requires a custom __init__.
        0  = auto-number w/o docstring
        1  = auto-number w/ docstring
        2+ = needs custom __init__
        """
        if len(args) == 1 and isinstance(args[0], (str, unicode)):
            self.__doc__ = args[0]
        elif args:
            raise TypeError('%s not dealt with -- need custom __init__' % (args,))

    @classmethod
    def export_to(cls, namespace):
        for name, member in cls.__members__.items():
            if name == member.name:
                namespace[name] = member

class State(AutoEnum):
    CONTENT = 'collecting content'
    DATA    = 'collecting element data'
    DENTING = 'calculating in/de-dents'
    ELEMENT = 'collecting element pieces'
    FILTER  = 'collecting filter text'
    NORMAL  = 'looking for next xaml header'
    PARENS  = 'skipping oel until closing paren'
    QUOTES  = 'oel converted to space, continuing lines must have greater indentation than first line'
s = State

class TokenType(AutoEnum):
    COMMENT     = 'a comment'
    CONTENT     = 'general content'
    CODE_ATTR   = 'an element attribute as python code'
    CODE_DATA   = "an element's data as code"
    DEDENT      = 'less space than before'
    ELEMENT     = 'name of current xaml element'
    FILTER      = 'filter lines'
    INDENT      = 'more space than before'
    META        = 'meta information'
    BLANK_LINE  = 'a whole new line'
    STR_ATTR    = 'an element attribute as a string'
    STR_DATA    = "an element's data as a string"
    PYTHON      = 'python code'
    SYMBOL      = 'something special'
    TEXT        = 'text to go with something special'
tt = TokenType

class LazyAttr(object):
    """
    doesn't create object until actually accessed
    """

    def __init__(self, func=None, doc=None):
        self.func = func
        self.__doc__ = doc or func.__doc__

    def __call__(self, func):
        self.func = func

    def __get__(self, instance, owner):
        if instance is None:
            return self
        result = self.func(instance)
        setattr(instance, self.func.__name__, result)
        return result

def _leading(line):
    """
    returns number of spaces before text
    """
    return len(line) - len(lstrip(line, ' '))

invalid_xml_chars = []
for r in (
    range(0, 9),
    range(0x0b, 0x0d),
    range(0x0e, 0x20),
    range(0xd800, 0xdfff),
    range(0xfffe, 0x10000),
    ):
    for n in r:
        invalid_xml_chars.append(unichr(n))
invalid_xml_chars = tuple(invalid_xml_chars)


# pushable iter for text stream

class PPLCStream:
    """
    Peekable, Pushable, Line & Character Stream
    """

    current_line = None

    def __init__(self, lines):
        self.data = lines
        self.data.reverse()
        self.chars = []
        self.lines = []
        self.lineno = 0

    def get_char(self):
        if not self.chars:
            line = self.get_line()
            if line is None:
                return None
            self.chars = list(line)
        return self.chars.pop(0)

    def get_line(self):
        if self.chars:
            line = ''.join(self.chars).rstrip('\n')
            self.chars = []
        elif self.lines:
            line = None
            while line is None:
                line = self.lines.pop()
                self.lineno += 1
        elif self.data:
            line = None
            while line is None:
                try:
                    line = self.data.pop()
                except IndexError:
                    self.current_line = None
                    return None
                else:
                    self.lineno += 1
        else:
            self.current_line = None
            return None
        line += '\n'
        self.current_line = line
        return line

    def peek_char(self):
        ch = self.get_char()
        self.push_char(ch)
        return ch

    def peek_line(self):
        line = self.get_line()
        self.push_line(line)
        return line

    def push_char(self, ch):
        if ch is None:
            return
        if ch == '\n':
            if self.chars:
                # convert existing chars to line, dropping newline char
                char_line = ''.join(self.chars).rstrip('\n')
                self.chars = []
                if char_line:
                    self.lines.append(char_line.rstrip('\n'))
                    self.lines -= 1
            self.chars = [ch]
        elif not self.chars:
            # nothing already in chars, and newline not being pushed,
            # so get an existing line to add to
            line = self.get_line()
            if line is None:
                line = '\n'
            self.chars = list(line)
            self.chars.insert(0, ch)
        else:
            self.chars.insert(0, ch)

    def push_line(self, line):
        if line is None:
            return
        if self.chars:
            char_line = ''.join(self.chars).rstrip('\n')
            self.chars = []
            if char_line:
                self.lines.append(char_line.rstrip('\n'))
                self.lineno -= 1
        self.lineno -= 1
        line = line.rstrip('\n')
        self.lines.append(line)
        self.current_line = line


# Token and Tokenizer

class Token:

    def __init__(self, ttype, payload=None, make_safe=True):
        self.type = ttype
        if payload is not None and isinstance(payload, unicode):
            payload = (payload, )
        self.payload = payload
        self.make_safe = make_safe

    def __eq__(self, other):
        if not isinstance(other, Token):
            return NotImplemented
        for attr in ('type', 'payload', 'make_safe'):
            if getattr(self, attr) != getattr(other, attr):
                return False
        return True

    def __ne__(self, other):
        if not isinstance(other, Token):
            return NotImplemented
        return not self.__eq__(other)

    def __repr__(self):
        attrs = ['%s' % self.type]
        for attr in ('payload', 'make_safe'):
            val = getattr(self, attr)
            if val is not None:
                attrs.append('%s=%r' % (attr, val))
        return 'Token(%s)' % (', '.join(attrs))


class Tokenizer:

    defaults = {
            '~' : 'field',
            '@' : 'name',
            '.' : 'class',
            '#' : 'id',
            '$' : 'string',
            }

    def __init__(self, lines):
        self.skipped_lines = sl = 0
        while lines and not lines[0]:
            sl += 1
            lines.pop(0)
        self.data = PPLCStream(lines)
        self.state = [s.NORMAL]
        self.indents = [0]
        self.open_parens = 0
        self.last_token = Token(None)
        self.defaults = self.defaults.copy()
        # element_lock is used to suspend xaml conversion for certain element data,
        # such as <style> (which has lines that can start with a '#'
        self.lock_elements = ()
        self.element_lock = None

    def __next__(self):
        res = self.get_token()
        if res is None:
            raise StopIteration
        return res
    next = __next__

    def __iter__(self):
        return self

    def _consume_ws(self, line=None, include=''):
        ws = ' ' + include
        if line is None:
            while self.data.peek_char() in ws:
                self.data.get_char()
        else:
            if line:
                chars = list(reversed(list(line)))
                while chars and chars[-1] in ws:
                    chars.pop()
                line = ''.join(reversed(chars))
            return line

    def _get_attribute(self, default=False):
        '''
        returns attribute name and value
        '''
        ws = ''
        if self.open_parens:
            ws = '\n'
        self._consume_ws(include=ws)
        # check if the element has ended
        ch = self.data.peek_char()
        if self.open_parens:
            if ch in ')':
                self.data.get_char()
                self.open_parens -= 1
                self._consume_ws()
                if self.data.peek_char() not in ('\n', None):
                    raise ParseError(self.data.lineno+self.skipped_lines, 'nothing allowed on same line after ")"')
                return self.get_token()
        elif ch in '/:\n':
            if self.open_parens:
                raise ParseError(self.data.lineno+self.skipped_lines, 'unclosed parens')
            self.data.get_char()
            old_state = self.state.pop()
            if ch == ':':
                if not self.data.peek_line().strip():
                    raise ParseError(self.data.lineno+self.skipped_lines, 'nothing after :')
                self.state.append(s.DATA)
                return self.get_token()
            elif ch == '/':
                self.state.append(s.CONTENT)
                return self.get_token()
            elif ch == '\n':
                next_line = self.data.peek_line()
                if next_line is None or next_line.lstrip()[:1] != '|':
                    return self.get_token()
                while ch != '|':
                    ch = self.data.get_char()
                ch = self.data.get_char()
                self.state.append(old_state)
        # collect the name
        name, disallow_quotes = self._get_name()
        # _get_name left ch at the '=', or the next non-ws character
        ch = self.data.peek_char()
        if ch == '=':
            self.data.get_char()
            attr_type, value = self._get_value(disallow_quotes)
            if name in ('string', ):
                value = value.replace('_', ' ')
        else:
            attr_type = tt.STR_ATTR
            value = name
        return Token(attr_type, (name, value), make_safe=True)

    def _get_comment(self):
        line = self.data.get_line().rstrip()[2:]
        if line[0:1] == ' ':
            line = line[1:]
        return Token(tt.COMMENT, line)

    def _get_content(self):
        line = self.data.get_line().rstrip('\n')
        if line[-1:] == '/' and line[-2:] != '\\/':
            line = line[:-1]
        else:
            line = line.rstrip()
        return Token(tt.CONTENT, line, make_safe=True if self.element_lock is None else False)

    def _get_data(self):
        line = self.data.get_line().rstrip('\n')
        # check for sneaky xaml token (must start with ~ )
        if line.lstrip()[0:1] == '~':
            # found one!
            self.state.pop()
            self.state.append(s.NORMAL)
            new_indent = self.indents[-1] + 4
            self.indents.append(new_indent)
            new_line = ' ' * new_indent + line.lstrip()
            self.data.push_line(new_line)
            return Token(tt.INDENT)
        # no xaml token, look for actual data
        strip_ws = True
        if line.strip() == '/':
            line = line.split('/', 1)[0]
            strip_ws = False
        elif line[-1:] == '/' and line[-2:] != '\\/':
            line = line[:-1].lstrip()
        else:
            line = line.strip()
        make_safe = True
        data_type = tt.STR_DATA
        if line[:2] == '!=':
            make_safe = False
            data_type = tt.CODE_DATA
            line = line[2:]
        elif line[0:1] == '=':
            data_type = tt.CODE_DATA
            line = line[1:]
        elif line[:2] == '&=':
            data_type = tt.CODE_DATA
            line = line[2:]
        if strip_ws:
            line = self._consume_ws(line)
        self.state.pop()
        return Token(data_type, line, make_safe)

    def _get_denting(self):
        last_indent = self.indents[-1]
        line = self.data.get_line()
        content = line.lstrip()
        current_indent = len(line) - len(content)
        if current_indent > last_indent:
            # indent
            self.data.push_line(line)
            self.indents.append(current_indent)
            self.state.pop()
            return Token(tt.INDENT)
        else:
            # dedent
            self.indents.pop()
            target_indent = self.indents[-1]
            self.data.push_line(line)
            if current_indent == target_indent:
                self.state.pop()
                return Token(tt.DEDENT)
            else:
                self.state.pop()
                return Token(tt.DEDENT)

    def _get_element(self, default=False):
        '''
        returns either the default element name, or the specified element name
        '''
        # save line, just in case
        if default:
            name = self.defaults['~']
        else:
            name, _ = self._get_name()
        if name in self.lock_elements:
            self.element_lock = self.indents[-1]
        return Token(tt.ELEMENT, name)

    def _get_filter(self, leading):
        name = self.data.get_line().strip()
        leading += 4
        lines = []
        while 'more lines in filter':
            line = self.data.get_line()
            if line is None:
                break
            ws = len(line) - len(line.lstrip(' '))
            if line.lstrip() and ws < leading:
                self.data.push_line(line)
                break
            lines.append(line)
        self.state.pop()
        token = Token(tt.FILTER, (name, ''.join(lines)), make_safe=False)
        return token

    def _get_meta(self):
        self.data.push_line(self.data.get_line()[3:])
        name, _ = self._get_name()
        typ = []
        for i, ch in enumerate(name.lower()):
            if ch not in 'abcdefghijklmnopqrstuvwxyz':
                ver = name[i:]
                break
            typ.append(ch)
        else:
            ver = ''
        typ = ''.join(typ)
        if not ver:
            if typ == 'xml':
                ver = '1.0'
            elif typ == 'html':
                ver = '5'
        name = typ, ver
        token = Token(tt.META, name)
        self.state.pop()
        self.state.append(s.ELEMENT)
        return token

    def _get_name(self, extra_chars=(), extra_types=(), extra_terminators=()):
        """
        gets the next tag or attribute name
        """
        self._consume_ws()
        ch = self.data.get_char()
        # check for line continuation
        if ch == '(':
            self.open_parens += 1
            self._consume_ws(include='\n')
            ch = self.data.get_char()
        # check for shortcuts
        name = self.defaults.get(ch, [])
        default = False
        if name:
            # set up for getting value
            self.data.push_char('=')
            default = True
        else:
            while 'collecting characters':
                if ch in '''!"#$%&'()*+,/;<=>?@[]^`{|}~ \n''':
                    break
                if ch == ':':
                    # if next char is ws, end name
                    if self.data.peek_char() in ' \t\n':
                        break
                if ch == '\\':
                    # needed to include : as last char in name
                    if self.data.peek_char() in ':':
                        ch = self.data.get_char()
                    else:
                        break
                name.append(ch)
                ch = self.data.get_char()
            name = ''.join(name)
            if not name:
                raise ParseError(self.data.lineno+self.skipped_lines, 'null name not allowed:\n\t%s' % self.data.current_line)
            if ch in '(':
                self.open_parens += 1
            elif ch in ')':
                self.open_parens -= 1
                if self.open_parens < 0:
                    raise ParseError(self.data.lineno+self.skipped_lines, 'mismatched parenthesis')
            elif ch == ' ':
                self._consume_ws()
            else:
                self.data.push_char(ch)
        # verify first character is legal
        ch = name[0]
        if ch in'-.' or unicodedata.category(ch)[0] == 'N':
            raise ParseError(self.data.lineno+self.skipped_lines, 'tag name cannot start with . - or digit: %r' % name)
        return name, default

    def _get_parens(self, line):
        pass

    def _get_python(self, line):
        return Token(tt.PYTHON, line.rstrip())

    def _get_quoted(self, line, quote, ptr):
        result = [quote]
        ptr += 1
        while "haven't found end-quote":
            ch = 'a'
            line.find(quote, ptr) == 'a'
        pass

    def _get_value(self, no_quotes=False):
        self._consume_ws()
        value = []
        ch = self.data.get_char()
        quote = None
        if ch in ('"', "'", '`'):
            if no_quotes and ch != '`':
                raise ParseError(self.data.lineno+self.skipped_lines, 'out-of-place quote')
            quote = ch
            ch = self.data.get_char()
        while 'collecting value':
            if ch is None:
                if quote:
                    raise ParseError(self.data.lineno+self.skipped_lines, 'unclosed quote while collecting value for %r: %r' % (''.join(value), ch))
                break
            if ch == quote:
                # skip past quote and finish
                ch = self.data.get_char()
                break
            elif quote and ch != '\\':
                if ch == '\n':
                    if quote == '`':
                        raise ParseError(self.data.lineno+self.skipped_lines, 'embedded newlines illegal in attribute level python code')
                    ch = ' '
                value.append(ch)
            elif ch in ('"', "'", '`'):
                raise ParseError(self.data.lineno+self.skipped_lines, 'embedded quote in:\n\t%s' % self.data.current_line)
            elif ch == '\\':
                ch = self.data.get_char()
                if ch == '\n':
                    raise ParseError(self.data.lineno+self.skipped_lines, 'newlines cannot be escaped')
                value.append('\\')
                value.append(ch)
            elif ch in ' )(/:\n':
                break
            else:
                value.append(ch)
            ch = self.data.get_char()
        value = ''.join(value)
        if ch in ')':
            self.open_parens -= 1
            if self.open_parens < 0:
                raise ParseError(self.data.lineno+self.skipped_lines, 'unbalanced parentheses')
        elif ch in '(':
            self.open_parens += 1
            self._consume_ws(include='\n')
        elif ch not in (' ', '\n', ':', '/', None):
            raise ParseError(self.data.lineno+self.skipped_lines, 'invalid character after value %r' % ''.join(value))
        else:
            self.data.push_char(ch)
            self._consume_ws()
        if quote == '`':
            token_type = tt.CODE_ATTR
        elif quote in ('"', "'"):
            token_type = tt.STR_ATTR
        elif no_quotes:
            token_type = tt.STR_ATTR
        else:
            token_type = tt.CODE_ATTR
        return token_type, value

    def _wind_down(self):
        try:
            self.indents.pop()
        except IndexError:
            return None
        return Token(tt.DEDENT)

    def get_token(self):
        state = self.state[-1]
        if state in (s.NORMAL, s.CONTENT):
            # looking for next whatever
            while 'nothing found yet...':
                line = self.data.peek_line()
                if line is None:
                    return self._wind_down()
                if not line.strip():
                    self.data.get_line()
                    self.last_token = Token(tt.BLANK_LINE)
                    return self.last_token
                # found something, check if indentation has changed and/or if element_lock is still needed
                last_indent = self.indents[-1]
                if self.element_lock is not None and (line[:self.element_lock].strip() or line[self.element_lock] != ' '):
                    self.element_lock = None
                if state is s.CONTENT and not line[:last_indent].strip() and line[last_indent] != ' ':
                        # this line is not (neccesarily) a content line
                        pass
                elif state is s.CONTENT and line[:last_indent].strip():
                        # dedent out of content
                        self.state.pop()
                        state = self.state[-1]
                # check if tag in normal content
                elif state is s.CONTENT and line[last_indent] not in '~:':
                    # still in content
                    line = self.data.get_line()
                    self.data.push_line(line[last_indent:])
                    self.last_token = self._get_content()
                    return self.last_token
                if not (line[:last_indent].lstrip() == '' and line[last_indent] != ' '):
                    self.state.append(s.DENTING)
                    self.last_token = self._get_denting()
                    return self.last_token
                else:
                    # discard white space, but save count
                    complete_line = self.data.get_line()
                    line = complete_line.lstrip()
                    leading_space = len(complete_line) - len(line)
                    self.data.push_line(line)
                    ch = line[0]
                if self.element_lock is None:
                    if ch == '~':
                        self.state.append(s.ELEMENT)
                        self.data.get_char()
                        self.last_token = self._get_element()
                        return self.last_token
                    elif ch in '@#.$':
                        self.state.append(s.ELEMENT)
                        self.last_token = self._get_element(default=True)
                        return self.last_token
                    elif line[:2] == '//':
                        self.last_token = self._get_comment()
                        return self.last_token
                    elif line[:3] == '!!!':
                        self.state.append(tt.META)
                        self.last_token = self._get_meta()
                        return self.last_token
                    elif ch == ':':
                        self.state.append(s.FILTER)
                        self.data.get_char()
                        self.last_token = self._get_filter(leading_space)
                        return self.last_token
                    elif ch == '-':
                        self.data.get_char()
                        self.last_token = self._get_python(self.data.get_line())
                        return self.last_token
                #must be random content
                if self.last_token.type is tt.INDENT:
                    self.state.append(s.CONTENT)
                self.last_token = self._get_content()
                return self.last_token
        elif state == s.ELEMENT:
            self.last_token = self._get_attribute()
            return self.last_token
        elif state == s.DATA:
            self.last_token = self._get_data()
            return self.last_token
        else:
            raise ParseError(self.data.lineno+self.skipped_lines, 'unknown state: %s' % state)

class ML:

    doc_types = {
            'xml': ['1.0', '1.1'],
            'xsl': ['1.0', ],
            'html': ['5', '4', '4t', '4s', '4-transitional', '4-strict'],
            }
    doc_headers = {
            'html5': ('<!', 'DOCTYPE html', '>'),
            'html4': ('<!', 'DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd"', '>'),
            'html4s': ('<!', 'DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd"', '>'),
            'html4-strict': ('<!', 'DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd"', '>'),
            'html4t': ('<!', 'DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" "http://www.w3.org/TR/html4/loose.dtd"', '>'),
            'html4-transitional': ('<!', 'DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" "http://www.w3.org/TR/html4/loose.dtd"', '>'),
            'xml1.0': ('<?', 'xml version="1.0"', '?>'),
            'xml1.1': ('<?', 'xml version="1.1"', '?>'),
            'xsl1.0': ('<?', 'xml version="1.0"', '?>'),
            }

    def __init__(self, values):
        if 'type' not in values:
            raise XamlError('type must be specified when creating ML')
        self.encoding = values.pop('encoding', DEFAULT_ENCODING)
        self.type = values.pop('type')
        version = values.pop('version', {'xml':'1.0', 'xsl':'1.0', 'html':'5'}[self.type])
        if version[5:6] == '-':
            version = version[:5] + version[6]
        self.version = version
        self.key = self.type + self.version
        self.attrs = []
        for k, v in values.items():
            self.attrs.append((k, v))
        self.attrs.append(('encoding', '"%s"' % self.encoding))

    def __str__(self):
        leader, middle, end = self.doc_headers[self.key]
        res = [middle]
        for name, value in self.attrs:
            if name == 'encoding':
                continue
            res.append('%s=%s' % (name, value))
        return '%s%s%s\n' % (leader, ' '.join(res), end)

    def bytes(self):
        leader, middle, end = self.doc_headers[self.key]
        res = [middle]
        for nv in self.attrs:
            if self.type == 'html' and nv[0] == 'encoding':
                continue
            res.append('%s=%s' % nv)
        return ('%s%s%s\n' % (leader, ' '.join(res), end)).encode(self.encoding)


# xaml itself

class Xaml(object):

    _cache = {}
    _init = False

    def __new__(cls, text, _parse=True, _compile=True, doc_type=None, **namespace):
        _cache_key = text, _parse, _compile, doc_type, tuple(namespace.items())
        if _cache_key in cls._cache:
            return cls._cache[_cache_key]
        else:
            obj = object.__new__(cls)
            cls._cache[_cache_key] = obj
            return obj

    def __init__(self, text, _parse=True, _compile=True, doc_type=None, **namespace):
        if self._init is True:
            return
        self._init = True
        streams = split_streams(decode(text))
        docs = self._document = XamlDoc(streams)
        if _parse:
            for page in docs.pages:
                page_doc_type = doc_type
                iter_tokens = Tokenizer(list(page.raw))
                page._tokens = []
                # check if html
                i = -1
                if page_doc_type == 'html':
                    # 'html' override, set default now
                    iter_tokens.defaults['~'] = 'div'
                    iter_tokens.lock_elements = ('script', 'style', )
                if page_doc_type in (None, 'html', 'xsl'):
                    seeking_head = seeking_xsl = False
                    xsl_found = {
                            'version': False,
                            'xmlns:fo': False,
                            'xmlns:xsl': False,
                            }
                    for token in iter_tokens:
                        page._tokens.append(token)
                        i += 1
                        if seeking_xsl:
                            if token.type is not tt.ELEMENT:
                                continue
                            elif token.payload[0] in ('xsl', 'xsl:stylesheet'):
                                token.payload = ('xsl:stylesheet', )
                                i += 1
                                next_token = next(iter_tokens)
                                page._tokens.append(next_token)
                                while next_token.type in (tt.STR_ATTR, tt.CODE_ATTR):
                                    xsl_found[next_token.payload[0]] = True
                                    next_token = next(iter_tokens)
                                    i += 1
                                    page._tokens.append(next_token)
                                for attr, present in xsl_found.items():
                                    if not present:
                                        page._tokens[i:i] = [Token(tt.STR_ATTR, (attr, xsl_attr[attr]))]
                                break
                        if seeking_head:
                            if token.type is not tt.ELEMENT:
                                continue
                            elif token.payload[0] == 'body':
                                # found body without head
                                # insert head and charset
                                page._tokens[i:i] = [
                                        Token(tt.ELEMENT, 'head'),
                                        Token(tt.INDENT),
                                        Token(tt.ELEMENT, 'meta'),
                                        Token(tt.STR_ATTR, ('charset', 'utf-8')),
                                        Token(tt.DEDENT),
                                        ]
                                break
                            elif token.payload[0] == 'head':
                                i += 1
                                next_token = next(iter_tokens)
                                page._tokens.append(next_token)
                                need_dedent = False
                                if next_token.type != tt.INDENT:
                                    page._tokens[i:i] = [
                                            Token(tt.INDENT),
                                            ]
                                    need_dedent = True
                                i += 1
                                page._tokens[i:i] = [
                                        Token(tt.ELEMENT, 'meta'),
                                        Token(tt.STR_ATTR, ('charset', 'utf-8')),
                                        ]
                                i += 2
                                if need_dedent:
                                    page._tokens[i:i] = [
                                            Token(tt.DEDENT),
                                            ]
                                break
                        if token.type in (tt.INDENT, ):
                            break
                        if token.type is tt.META:
                            if token.payload[0] == 'xsl':
                                page_doc_type = 'xsl'
                                seeking_xsl = True
                            if token.payload[0] == 'html':
                                page_doc_type = 'html'
                                iter_tokens.defaults['~'] = 'div'
                                iter_tokens.lock_elements = ('script', 'style', )
                                # this is an html document; scan for head and/or body
                                seeking_head = True
                page._tokens.extend(list(iter_tokens))
                page.doc_type = page_doc_type
                page._depth = [Token(None)]
                # indents tracks valid indentation levels
                page._indents = Indent(level=1)
                page._coder = minimal
                page.ml = None
                if _parse:
                    self._parse(page, _compile=_compile, **namespace)

    @property
    def document(self):
        return self._document

    @staticmethod
    def _append_newline(page):
        if page._depth[-1].type not in (tt.INDENT, tt.BLANK_LINE, None):
            page._depth.append(Token(tt.BLANK_LINE))

    @staticmethod
    def _check_for_newline(page, token):
        if token.type is not tt.BLANK_LINE:
            return token, False
        else:
            return page._depth[-2], page._depth.pop()

    def _parse(self, page, _compile=True, **namespace):
        output = []
        attrs = {}
        data = None
        meta = {}
        doc_type = page.doc_type
        for token in page._tokens:
            last_token = page._depth and page._depth[-1] or Token(None)
            if last_token.type is tt.META:
                if token.type is tt.STR_ATTR:
                    name, value = token.payload
                    meta[name] = value
                    continue
                elif token.type not in (tt.CODE_ATTR, tt.STR_DATA, tt.CODE_DATA):
                    page.ml = ML(meta)
                    if doc_type and doc_type != page.ml.type:
                        page.ml = None
                    else:
                        doc_type = page.doc_type = page.ml.type
                        page._coder = ml_types.get(page.ml.key)
                        if page._coder is None:
                            raise ParseError('markup language %r not supported' % page.ml.key)
                    page._depth.pop()
                    if token.type is tt.DEDENT:
                        break
                else:
                    raise ParseError('Token %s not allowed in/after META token' % token)
            elif last_token.type is tt.COMMENT:
                if token.type is not tt.COMMENT:
                    output.append(page._indents.blanks + '        )\n')
                    page._depth.pop()
                    last_token = page._depth[-1]
            # ATTR
            if token.type in (tt.CODE_ATTR, tt.STR_ATTR):
                assert last_token.type is tt.ELEMENT, 'the tokenizer is busted (ATTR and last is %r [current: %r])' % (last_token, token)
                name, value = token.payload
                if name in attrs and name != 'class':
                    raise ParseError('attribute %r already specified' % name)
                if token.type is tt.STR_ATTR and token.make_safe:
                    value = page._coder(value)
                    value = repr(value)
                if name in attrs:
                    value = attrs[name] + ' + u" " + ' + value
                attrs[name] = value
            # COMMENT
            elif token.type is tt.COMMENT:
                last_token, pending_newline = self._check_for_newline(page, last_token)
                if last_token.type is tt.ELEMENT:
                    output[-1] += join(attrs, data)
                    attrs = {}
                    data = None
                    page._depth.pop()
                    if pending_newline:
                        self._append_newline(page)
                if pending_newline:
                    output.append(page._indents.blanks + 'Blank()\n')
                if page._depth[-1].type is not tt.COMMENT:
                    page._depth.append(token)
                    output.append(page._indents.blanks + 'Comment(\n')
                output.append(page._indents.blanks + '        %r,\n' % token.payload)
            # CONTENT
            elif token.type is tt.CONTENT:
                last_token, pending_newline = self._check_for_newline(page, last_token)
                if last_token.type is tt.ELEMENT:
                    # close previous element
                    output[-1] += join(attrs, data)
                    attrs = {}
                    data = None
                    page._depth.pop()
                if pending_newline:
                    output.append(page._indents.blanks + 'Blank()\n')
                    self._append_newline(page)
                value = token.payload[0]
                if token.make_safe:
                    value = page._coder(value)
                value = repr(value)
                output.append(page._indents.blanks + 'Content(%s)\n' % value)
            # DATA
            elif token.type in (tt.CODE_DATA, tt.STR_DATA):
                value ,= token.payload
                if token.type is tt.CODE_DATA:
                    pass
                if token.type is tt.STR_DATA:
                    if token.make_safe:
                        value = page._coder(value)
                    value = repr(value)
                data =  value
            # DEDENT
            elif token.type is tt.DEDENT:
                last_token, pending_newline = self._check_for_newline(page, last_token)
                # need to close the immediately preceeding tag, and the
                # tags dedented to
                page._indents.dec()
                if last_token.type is tt.ELEMENT:
                    output[-1] += join(attrs, data)
                    attrs = {}
                    data = None
                    page._depth.pop()
                should_be_indent = page._depth.pop()
                assert should_be_indent.type in (tt.INDENT, None), 'something broke: %s\n%s' % (should_be_indent, ''.join(output))
                try:
                    last_token = page._depth[-1]
                except IndexError:
                    # all done!
                    break
                if last_token.type is tt.BLANK_LINE:
                    output.append('\n')
                    page._depth.pop()
                    last_token = page._depth[-1]
                    page._indents.dec()
                if last_token.type is tt.ELEMENT:
                    closing_token = page._depth.pop()
                if pending_newline:
                    page._depth.append(pending_newline)
            # ELEMENT
            elif token.type is tt.ELEMENT:
                last_token, pending_newline = self._check_for_newline(page, last_token)
                if last_token.type is tt.ELEMENT:
                    # close previous element
                    output[-1] += join(attrs, data)
                    attrs = {}
                    data = None
                    page._depth.pop()
                if pending_newline:
                    output.append(page._indents.blanks + 'Blank()\n')
                    self._append_newline(page)
                output.append(page._indents.blanks)
                output.append('Element(%r' % token.payload)
                page._depth.append(token)
            # FILTER
            elif token.type is tt.FILTER:
                last_token, pending_newline = self._check_for_newline(page, last_token)
                if last_token.type is tt.ELEMENT:
                    # close previous element
                    output[-1] += join(attrs, data)
                    attrs = {}
                    data = None
                    page._depth.pop()
                if pending_newline:
                    output.append(page._indents.blanks + 'Blank()\n')
                    self._append_newline(page)
                name, lines = token.payload
                pieces = name.split()
                blank = page._indents.blanks
                if len(pieces) > 1:
                    name = pieces[0]
                    for string in pieces[1:]:
                        key, value = string.split('=', 1)
                        attrs[key] = value
                if name[:6] in ('cdata', 'cdata-'):
                    name = name[:5]
                if name == 'python':
                    if 'type' not in attrs:
                        attrs['type'] = u'text/python'
                    output.append(blank + 'with Element(u"script", attrs=%r):\n' % attrs)
                    for line in textwrap.dedent(lines).strip().split('\n'):
                        output.append(blank + '    Content(%r)\n' % line)
                elif name == 'javascript':
                    if 'type' not in attrs:
                        attrs['type'] = u'text/javascript'
                    output.append(blank + 'with Element(u"script", attrs=%r):\n' % attrs)
                    for line in textwrap.dedent(lines).strip().split('\n'):
                        output.append(blank + '    Content(%r)\n' % line)
                elif name == 'css':
                    if 'type' not in attrs:
                        attrs['type'] = u'text/css'
                    output.append(blank + 'with Element(u"style", attrs=%r):\n' % attrs)
                    for line in textwrap.dedent(lines).strip().split('\n'):
                        output.append(blank + '    Content(%r)\n' % line)
                elif name == 'cdata':
                    output.append(blank + 'CData(\n')
                    for line in textwrap.dedent(lines).strip().split('\n'):
                        output.append(blank + '    %r,\n' % line)
                    output.append(blank + ')\n')
                else:
                    raise ParseError('unknown filter: %r' % name)
                attrs = {}
            # INDENT
            elif token.type is tt.INDENT:
                last_token, pending_newline = self._check_for_newline(page, last_token)
                with_ele = False
                if last_token.type is tt.ELEMENT:
                    output[-1] = 'with %s' % output[-1] + join(attrs, data, ':')
                    attrs = {}
                    data = None
                    with_ele = True
                page._indents.inc()
                if pending_newline:
                    if with_ele:
                        output.append(page._indents.blanks + 'with Blank(mirror=True):\n')
                        page._indents.inc()
                    else:
                        output.append(page._indents.blanks + 'Blank()\n')
                    self._append_newline(page)
                page._depth.append(token)
            # META
            elif token.type is tt.META:
                if len(page._depth) != 1 or page._depth[0].type != None:
                    raise ParseError('meta tags (such as %r) cannot be nested' % (token.payload,))
                name, value = token.payload
                if name in ML.doc_types and value in ML.doc_types[name]:
                    meta['type'] = name
                    meta['version'] = value
                else:
                    raise ParseError('unknown META: %r' % ((name, value), ))
                page._depth.append(token)
            # PYTHON
            elif token.type is tt.PYTHON:
                last_token, pending_newline = self._check_for_newline(page, last_token)
                if last_token.type is tt.ELEMENT:
                    # close previous element
                    output[-1] += join(attrs, data)
                    attrs = {}
                    data = None
                    page._depth.pop()
                if pending_newline:
                    output.append(page._indents.blanks + 'Blank()\n')
                    self._append_newline(page)
                output.append(page._indents.blanks + '%s\n' % token.payload)
            # BLANK_LINE
            elif token.type is tt.BLANK_LINE:
                if last_token.type is tt.BLANK_LINE:
                    continue
                else:
                    page._depth.append(token)
            # problem
            else:
                raise ParseError('unknown token: %r' % token)
        global_code = [
                """output = []\n""",
                """\n""",
                """class Args:\n""",
                """    def __init__(self, kwds):\n""",
                """        self.__keys = []\n""",
                """        for k, v in kwds.items():\n""",
                """            self.__keys.append(k)\n""",
                """            setattr(self, k, v)\n""",
                """\n""",
                """class Blank:\n""",
                """    def __init__(self, mirror=False):\n""",
                """        self.mirror = mirror\n""",
                """        output.append('')\n""",
                """    def __enter__(self):\n""",
                """        pass\n""",
                """    def __exit__(self, *args):\n""",
                """        if self.mirror:\n""",
                """            output.append('')\n""",
                """\n""",
                """def CData(*content):\n""",
                """    output.append('%s<![CDATA[' % indent.blanks)\n""",
                """    for line in content:\n""",
                """        output.append('%s    %s' % (indent.blanks, line))\n""",
                """    output.append('%s]]>' % indent.blanks)\n""",
                """\n""",
                """def Comment(*content):\n""",
                """    output.append('%s<!--' % indent.blanks)\n""",
                """    for line in content:\n""",
                """        output.append('%s |  %s' % (indent.blanks, line))\n""",
                """    output.append('%s-->' % indent.blanks)\n""",
                """\n""",
                """def Content(content):\n""",
                """    output.append('%s%s' % (indent.blanks, content))\n""",
                """\n""",
                """class Element:\n""",
                """    def __init__(self, tag, attrs={}):\n""",
                """        if (doc_type == 'html4s' and tag not in html4s or \n""",
                """            doc_type == 'html4t' and tag not in html4t or \n""",
                """            doc_type == 'html5' and tag not in html5):\n""",
                """                raise XamlError('tag %s not allowed in %s' % (tag, doc_type))\n""",
                """        self.tag = tag\n""",
                """        self.html = doc_type[:4] == 'html'\n""",
                """        self.void = self.html and tag in html_void_elements\n""",
                """        if self.void:\n""",
                """            template = '%s<%s%s>'\n""",
                """        elif self.html:\n""",
                """            template = '%s<%s%s>' + ('</%s>' % tag)\n""",
                """        else:\n""",
                """            template = '%s<%s%s/>'\n""",
                """        self.content = False\n""",
                """        pairs = []\n""",
                """        if tag == 'img' and 'src' in attrs:\n""",
                """            pairs.append(('src', attrs.pop('src')))\n""",
                """        if 'name' in attrs:\n""",
                """            pairs.append(('name', attrs.pop('name')))\n""",
                """        if 'id' in attrs:\n""",
                """            pairs.append(('id', attrs.pop('id')))\n""",
                """        if 'class' in attrs:\n""",
                """            pairs.append(('class', attrs.pop('class')))\n""",
                """        pairs.extend(sorted(attrs.items()))\n""",
                """        attrs = ' '.join(['%s="%s"' % (k, v) for k, v in pairs])\n"""
                """        if attrs:\n""",
                """            attrs = ' ' + attrs\n""",
                """        output.append(template % (indent.blanks, tag, attrs))\n""",
                """    def __call__(self, content):\n""",
                """        if self.void:\n""",
                """            raise XamlError('content not allowed for void element %s' % self.tag)\n""",
                """        self.content = True\n""",
                """        if self.html:\n""",
                """            insert = -(len(self.tag)+3)\n""",
                """            output[-1] = (output[-1][:insert] + '%s' + output[-1][insert:]) % content\n""",
                """        else:\n""",
                """            output[-1] = output[-1][:-2] + '>%s</%s>' % (content, self.tag)\n""",
                """        return self\n""",
                """    def __enter__(self):\n""",
                """        if self.void:\n""",
                """            raise XamlError('content not allowed for void element %s' % self.tag)\n""",
                """        if output and output[-1] == '':\n""",
                """            target = -2\n""",
                """        else:\n""",
                """            target = -1\n""",
                """        indent.inc()\n""",
                """        if self.content or self.html:\n""",
                """            blank = -len('</%s>' % self.tag)\n""",
                """            output[target] = output[target][:blank]\n""",
                """        else:\n""",
                """            output[target] = output[target][:-2] + '>'\n""",
                """    def __exit__(self, *args):\n""",
                """        indent.dec()\n""",
                """        output.append('%s</%s>' % (indent.blanks, self.tag))\n""",
                """\n""",
                """indent = Indent()\n""",
                """\n""",
                ]
        pre_code = [
                """def generate(**kwds):\n""",
                """    args = Args(kwds)\n""",
                """\n""",
                ]
        post_code = [
                """\n""",
                """    result = '\\n'.join(output)\n""",
                """    output[:] = []\n""",
                """    return result\n""",
                ]
        page.code = code = ''.join(pre_code+output+post_code)
        glbls = globals().copy()
        glbls.update(namespace)
        glbls['doc_type'] = doc_type or 'xml'
        exec(''.join(global_code), glbls)
        page._glbls = glbls


class XamlPage:

    def __init__(self, stream):
        # a stream is a list of unprocessed unicode lines
        self.raw = tuple(stream)
        self.doc_type = None
        self.code = None
        self.ml = None

    @property
    def encoding(self):
        return self.ml and self.ml.encoding or DEFAULT_ENCODING

    @LazyAttr
    def generate(self):
        if self.code is None:
            raise XamlError('XamlPage has been parsed')
        exec(self.code, self._glbls)
        return self._glbls['generate']

    def __repr__(self):
        return '<%s document>' % (self.doc_type or 'unknown')

    def string(self, **kwds):
        text = self.generate(**kwds)
        return str(self.ml or '') + text

    def bytes(self, **kwds):
        text = self.generate(**kwds)
        return (self.ml and self.ml.bytes() or b'') + text.encode(self.encoding)

class XamlDoc:

    def __init__(self, streams):
        self.pages = []
        for stream in streams:
            self.pages.append(XamlPage(stream))

    def __iter__(self):
        return iter(self.pages)


class Indent:

    def __init__(
            self,
            blank='    ',
            level=0,
            ):
        self.blank = blank
        self.indent = level

    def inc(self):
        self.indent += 1

    def dec(self):
        self.indent -= 1

    @property
    def blanks(self):
        return self.blank * self.indent


class XamlError(Exception):
    '''
    Base class for xaml errors
    '''

class ParseError(XamlError):
    '''
    Used for xaml parse errors
    '''

    line_no = None

    def __init__(self, line_no, message=None):
        if message is None:
            Exception.__init__(self, line_no)
        else:
            self.line_no = line_no
            Exception.__init__(self, 'line %s: %s' % (line_no, message))


class InvalidXmlCharacter(ParseError):
    '''
    Used for invalid code points
    '''

# minor utilities
def minimal(text):
    cp = {
        '<' : '&lt;',
        '>' : '&gt;',
        '&' : '&amp;',
        '"' : '&#x22;',
        }
    result = []
    # since &entity; may be present, when ampersand (&) is found scan forward until first
    # non-ascii-letter is found; if non-ascii-letter is a semi-colon (;) leave
    # as-is, otherwise convert the original &
    sc = text.count(';')
    amp = text.count('&')
    if amp == 0:
        sc = 0
    index = 0
    while sc:
        # take care while semicolons have not been processed
        ch = text[index]
        index += 1
        if ch == ';':
            sc -= 1
            result.append(ch)
        elif ch != '&':
            result.append(cp.get(ch, ch))
        else:
            # found & -- look for entity
            sc_index = text.find(';', index)
            # without & and ;
            entity = text[index:sc_index]
            if entity.isalpha():
                # store entity and update index
                result.append(text[index-1:sc_index+1])
                index = sc_index + 1
                sc -= 1
            else:
                # store & entity
                result.append(cp.get(ch, ch))
    else:
        # no semicolons left, use quick-and-easy
        text = text[index:]
        for ch in text:
            result.append(cp.get(ch, ch))
    return ''.join(result)

xmlify = minimal

ml_types = {
    'xsl1.0' : xmlify,
    'xml1.0' : xmlify,
    'html4'  : xmlify,
    'html4t' : xmlify,
    'html5'  : xmlify,
    }

xsl_attr = {
        'version': '1.0',
        'xmlns:fo': 'http://www.w3.org/1999/XSL/Format',
        'xmlns:xsl': 'http://www.w3.org/1999/XSL/Transform',
        }

html4s = html4_strict_elements = [
        'A', 'ABBR', 'ACRONYM', 'ADDRESS', 'AREA', 'B', 'BASE', 'BDO', 'BIG', 'BLOCKQUOTE', 'BODY', 'BR', 'BUTTON',
        'CAPTION', 'CITE', 'CODE', 'COL', 'COLGROUP', 'DD', 'DEL', 'DFN', 'DIV', 'DL', 'DT', 'EM', 'FIELDSET', 'FORM',
        'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HEAD', 'HR', 'HTML', 'I', 'IMG', 'INPUT', 'INS', 'KBD', 'LABEL', 'LEGEND',
        'LI', 'LINK', 'MAP', 'META', 'NOSCRIPT', 'OBJECT', 'OL', 'OPTGROUP', 'OPTION', 'P', 'PARAM', 'PRE', 'Q', 'SAMP',
        'SCRIPT', 'SELECT', 'SMALL', 'SPAN', 'STRONG', 'STYLE', 'SUB', 'SUP', 'TABLE', 'TBODY', 'TD', 'TEXTAREA', 'TFOOT',
        'TH', 'THEAD', 'TITLE', 'TR', 'TT', 'UL', 'VAR',
        ]

html4t = html4_transitional_elements = [
        'A', 'ABBR', 'ACRONYM', 'ADDRESS', 'APPLET', 'AREA', 'B', 'BASE', 'BASEFONT', 'BDO', 'BIG', 'BLOCKQUOTE',
        'BODY', 'BR', 'BUTTON', 'CAPTION', 'CENTER', 'CITE', 'CODE', 'COL', 'COLGROUP', 'DD', 'DEL', 'DFN', 'DIR',
        'DIV', 'DL', 'DT', 'EM', 'FIELDSET', 'FONT', 'FORM', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HEAD', 'HR', 'HTML',
        'I', 'IFRAME', 'IMG', 'INPUT', 'INS', 'ISINDEX', 'KBD', 'LABEL', 'LEGEND', 'LI', 'LINK', 'MAP', 'MENU', 'META',
        'NOSCRIPT', 'OBJECT', 'OL', 'OPTGROUP', 'OPTION', 'P', 'PARAM', 'PRE', 'Q', 'S', 'SAMP', 'SCRIPT', 'SELECT',
        'SMALL', 'SPAN', 'STRIKE', 'STRONG', 'STYLE', 'SUB', 'SUP', 'TABLE', 'TBODY', 'TD', 'TEXTAREA', 'TFOOT', 'TH',
        'THEAD', 'TITLE', 'TR', 'TT', 'U', 'UL', 'VAR',
        ]

html5 = html5_elements = [
        'A', 'ABBR', 'ADDRESS', 'AREA', 'ARTICLE', 'ASIDE', 'AUDIO', 'B', 'BASE', 'BDI', 'BDO', 'BLOCKQUOTE', 'BODY',
        'BR', 'BUTTON', 'CANVAS', 'CAPTION', 'CITE', 'CODE', 'COL', 'COLGROUP', 'COMMAND', 'DATALIST', 'DD', 'DEL',
        'DETAILS', 'DFN', 'DIV', 'DL', 'DT', 'EM', 'EMBED', 'FIELDSET', 'FIGCAPTION', 'FIGURE', 'FORM', 'H1', 'H2',
        'H3', 'H4', 'H5', 'H6', 'HEAD', 'HEADER', 'HGROUP', 'HR', 'HTML', 'I', 'IMG', 'INPUT', 'INS', 'KBD', 'LABEL',
        'LEGEND', 'LI', 'LINK', 'MAP', 'MARK', 'MENU', 'META', 'METER', 'NAV', 'NOSCRIPT', 'OBJECT', 'OL', 'OPTGROUP',
        'OPTION', 'P', 'PARAM', 'PRE', 'PROGRESS', 'Q', 'RP', 'RT', 'RUBY', 'S', 'SAMP', 'SCRIPT', 'SECTION', 'SELECT',
        'SMALL', 'SOURCE', 'SPAN', 'STRONG', 'STYLE', 'SUB', 'SUMMARY', 'SUP', 'TABLE', 'TBODY', 'TD', 'TEXTAREA',
        'TFOOT', 'TH', 'THEAD', 'TIME', 'TITLE', 'TR', 'TRACK', 'U', 'UL', 'VAR', 'VIDEO', 'WBR',
        ]
html_void_elements = [
        'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'keygen', 'link',
        'meta', 'param', 'source', 'track', 'wbr',
        ]

def join(attrs, data, trailing=''):
    "create attribute and string portion of Element"
    string = ')'
    if attrs:
        order = []
        id = None
        keys = list(attrs.keys())
        for attr in ('name', 'model', 'class'):
            if attr in keys:
                order.append(attr)
                keys.remove(attr)
        if 'id' in keys:
            keys.remove('id')
            id = True
        order.extend(sorted(keys))
        if id:
            order.append('id')
        string = ', attrs={%s})' % ', '.join(['%r:%s' % (k, attrs[k]) for k in order])
    if data:
        string += '(%s)' % data
    string += trailing + '\n'
    return string

def split_streams(text_lines):
    '''
    Return a list of text streams from a list of lines, one for each !!! declaration after data.
    '''
    current = []
    streams = [current]
    data_started = False
    for lineno, line in enumerate(text_lines):
        if line is None:
            pass
        elif line.startswith('!!!'):
            if data_started:
                # last "file" ended, next one begins
                current = []
                streams.append(current)
                data_started = False
        elif line.strip():
            data_started = True
        current.append(line)
    return streams

def decode(text):
    "Decode text to unicode, return as list of lines."
    i = None
    if not isinstance(text, unicode):
        encoding = 'utf-8'
        first_lines = text[:256].split(b'\n')
        for i, line in enumerate(first_lines):
            if not line.startswith(b'!!!'):
                i = None
                break
            match = re.search('coding\s*[:=]\s*([-\w.]*)', line.decode('ascii'))
            if match:
                encoding = match.groups()[0]
                if not encoding:
                    raise XamlError("no encoding specified in code line")
                break
        else:
            i = None
        try:
            text = text.decode(encoding)
        except LookupError:
            exc = sys.exc_info()[1]
            raise XamlError(exc)
    code_line = i
    lines = text.rstrip().split('\n')
    for i, line in enumerate(lines):
        for ch in invalid_xml_chars:
            if ch in line:
                raise InvalidXmlCharacter(i, 'Character %r not allowed in xaml documents' % ch)
    if code_line is not None:
        lines[code_line] = None
    return lines

