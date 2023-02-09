"""
a strict version of commonmark

currently supported syntax:

    Element                 StoneMark Syntax

                            ==========
    Heading                 H1 Heading    H2 Heading   H3 Heading
                            ==========    ==========   ----------

    Bold                    **bold text**

    Italic                  *italicized text*

    Bold & Italic           ***bold, italicized text***

    Ordered List            1. First item
                            2. Second item
                            3. Third item

    Unordered List          - First item
                            - Second item
                            - Third item

    Blockquote              > three-score and seven years ago
                            > our forefathers ...

    Code                    `code`

    Horizontal Rule         --- or ***

    Link         (in-line)  [title](https://www.example.com)

                (separate)  [title]  or [title][id]
                            ...
                            [title | id]: <https://www.example.com>

                            # if no marker defined, then [title] becomes
                            <a href="title">title</a>

           (link is title)  [](https://www.example.com)

    Image                   ![alt text](image.jpg "title")


    Fenced Code Block       ``` or ~~~
                            {
                              "firstName": "John",
                              "lastName": "Smith",
                              "age": 25
                            }
                            ``` or ~~~

    Details/Summary         --> One-line summary here
                            --# some detail
                            --# on the following lines

    Footnote                Here's a sentence with a footnote. [^1]

                            [^1]: This is the footnote.

    Strikethrough           ~~The world is flat.~~

    Underline               __Pay attention.__

    Highlight               I need to highlight these ==very important words==.

    Subscript               H~2~O
    Superscript             X^2^

    Table                 | Syntax | Description |
                          | ----------- | ----------- |
                          | Header | Title |
                          | Paragraph | Text |

"""

from __future__ import print_function

# syntax still to do
#
# Heading ID            ### My Great Heading {#custom-id}
#
# Definition List       term
#                       : definition
#
# Task List             - [x] Write the press release
#                       - [ ] Update the website
#                       - [ ] Contact the media
#
# Emoji                 That is so funny! :joy:

# imports & globals
from abc import ABCMeta
from aenum import Enum, Flag, auto, export
from scription import *
import codecs
import re


__all__ = [
        'FormatError', 'BadFormat', 'AmbiguousFormat', 'IndentError',
        'Node', 'Heading', 'Paragraph', 'List', 'ListItem', 'CodeBlock', 'BlockQuote', 'Rule',
        'Link', 'Image', 'IDLink', 'ID', 'Definition', 'Text', 'Table', 'Detail',
        'Document',
        ]

version = 0, 3, 0

HEADING = PARAGRAPH = TEXT = QUOTE = O_LIST = U_LIST = LISTITEM = CODEBLOCK = RULE = IMAGE = FOOTNOTE = LINK = ID = DEFINITION = TABLE = DETAIL = None
END = SAME = CHILD = CONCLUDE = ORDERED = UNORDERED = None
PLAIN = BOLD = ITALIC = STRIKE = UNDERLINE = HIGHLIGHT = SUB = SUPER = CODE = FOOT_NOTE = ALL_TEXT = None
TERMINATE = INCLUDE = RESET = WORD = NUMBER = WHITESPACE = PUNCTUATION = OTHER = None

# classes

class NodeType(ABCMeta):
    def __repr__(self):
        return "%s" % self.__name__


ABC = NodeType('ABC', (object, ), {})

class DocEnum(Enum):
    _init_ = 'value __doc__'
    def __repr__(self): return "<%s.%s>" % (self.__class__.__name__, self.name)

@export(globals())
class NodeType(Flag):
    HEADING         = auto()
    PARAGRAPH       = auto()
    TEXT            = auto()
    QUOTE           = auto()
    O_LIST          = auto()
    U_LIST          = auto()
    LISTITEM        = auto()
    CODEBLOCK       = auto()
    RULE            = auto()
    IMAGE           = auto()
    FOOTNOTE        = auto()
    LINK            = auto()
    ID              = auto()
    DEFINITION      = auto()
    TABLE           = auto()
    DETAIL          = auto()

@export(globals())
class NodeStatus(DocEnum):
    """
    used by check() to determine effect of next line
    """
    END             = 'not part of the current node (typically a blank line)'
    SAME            = 'part of the current node'
    CHILD           = 'begins a new child node'
    CONCLUDE        = 'last line of the current node (e.g. a fenced code block)'

@export(globals())
class ListType(DocEnum):
    ORDERED         = 'ordered list'
    UNORDERED       = 'unordered list'

@export(globals())
class CharType(DocEnum):
    WORD        = 'letters and underscore'
    NUMBER      = 'arabic digits'
    WHITESPACE   = 'space, new-line, etc'
    PUNCTUATION = 'comma, period, etc'
    OTHER       = 'none of the above'

@export(globals())
class TextType(int, Flag):
    _order_ = 'PLAIN ITALIC BOLD BOLD_ITALIC STRIKE UNDERLINE HIGHLIGHT SUB SUPER CODE FOOT_NOTE ALL_TEXT'

    def __new__(cls, value, docstring, open, close, whitespace, start, end):
        if value == -1:
            value = 2 ** (len(cls)-1) -1
        member = int.__new__(cls, value)
        member._value_ = value
        if close and open is None:
            close = open
        member.open = open
        member.close = close
        member.__doc__ = docstring
        member.start = start
        member.end = end
        cls._value2member_map_[open] = member
        return member

    @classmethod
    def _create_pseudo_member_values_(cls, members, *values):
        start = ''
        end = ''
        for m in members:
            start = start + m.start
            end = m.end + end
        return values + ('', '', '', False, start, end)

    @classmethod
    def _missing_(cls, value):
        # support lookup by marker
        if isinstance(value, basestring):
            return cls._value2member_map_.get(value)
        else:
            return super(TextType, cls)._missing_(value)


    PLAIN           = 0, 'normal', '', '', False, '', ''
    ITALIC          = '*italic words*', '*', '*', True, '<i>', '</i>'
    BOLD            = '**bolded stuff**', '**', '**', True, '<b>', '</b>'
    BOLD_ITALIC     = 3, '***really super important!***', '***', '***', True, '<b><i>', '</i></b>'
    STRIKE          = '~~strike-through text~~', '~~', '~~', True, '<del>', '</del>'
    UNDERLINE       = '__underlined info__', '__', '__', True, '<u>', '</u>'
    HIGHLIGHT       = '==highilighted words==', '==', '==', True, '<mark>', '</mark>'
    SUB             = 'subscripted: H~2~O', '~', '~', False, '<sub>', '</sub>'
    SUPER           = 'superscripted: x^2^', '^', '^', False, '<sup>', '</sup>'
    CODE            = '`if blah is None`', '`', '`', False, '<code>', '</code>'
    FOOT_NOTE       = 'a statement [^1]', '[^', ']', True, '<sup>', '</sup>'
    ALL_TEXT        = -1, 'no restrictions', '', '', False, '', ''

@export(globals())
class BlankLineType(DocEnum):
    TERMINATE       = 'a blank line terminates the node (e.g. paragraph)'
    INCLUDE         = 'a blank line is included in the node (e.g. fenced code block)'
    RESET           = 'may or may not indicate end of node'


class FormatError(Exception):
    pass

class BadFormat(FormatError):
    pass

class AmbiguousFormat(FormatError):
    pass

class IndentError(FormatError):
    pass

class MissingLink(FormatError):
    pass

class Node(ABC):

    allowed_blocks = ()
    allowed_text = PLAIN
    text = None
    start_line = None
    end_line = None
    blank_line = TERMINATE
    blank_line_required = True
    parent = None
    children = False
    terminate_after_children = True
    sequence = None
    links = {}

    def __init__(self, stream, indent=0, sequence=None, parent=None):
        self.node_id = next(UID)
        if parent is None:
            raise TypeError('parent cannot be None')
        self.parent = parent
        self.links = parent.links
        self.indent = indent
        self.stream = stream
        self.items = []
        self.final = False
        self.reset = None
        self.sequence = sequence
        if stream is not None:
            self.start_line = stream.line_no
        else:
            self.start_line = None
        while not isinstance(parent, Document):
            assert self.links is parent.links
            parent = parent.parent


    def __repr__(self):
        items = "items=%r" % (self.items, )
        other = self._repr()
        if other:
            text = '%s, %s' % (other, items)
        else:
            text = items
        return "<%s: start_line=%r, %s>" % (self.__class__.__name__, self.start_line, text)

    def _repr(self):
        return ""

    def parse(self):
        items = self.items
        stream = self.stream
        self.reset = False
        #
        while stream:
            line = stream.current_line.rstrip()
            if not line:
                if self.blank_line is TERMINATE:
                    status = END
                elif self.blank_line is INCLUDE:
                    items.append(line)
                    stream.skip_line()
                    continue
                elif self.blank_line is RESET:
                    self.reset = True
                    # save the line number in case this is the end
                    self.end_line = stream.line_no
                    stream.skip_line()
                    continue
            else:
                ws, line = line[:self.indent], line[self.indent:]
                if ws.strip():
                    # no longer in this node
                    status = END
                    if self.blank_line_required and not self.reset:
                        # maybe raise, maybe okay
                        self.premature_end('%s%s' % (ws, line))
                else:
                    status = self.check(line)
            if status is SAME and self.children and self.terminate_after_children:
                raise AmbiguousFormat('ambiguous format at line %d:\n%r' % (stream.line_no, line))
            if status in (CONCLUDE, END):
                self.reset = False
                self.end_line = self.end_line or stream.line_no
                if status is END:
                    self.end_line -= 1
                elif status is CONCLUDE:
                    # line was added by check(), skip the line here
                    stream.skip_line()
                try:
                    keep = self.finalize()
                except FormatError:
                    raise
                except Exception:
                    raise
                break
            elif status is SAME:
                # line was added by check(); reset reset and end_line and skip line
                stream.skip_line()
                self.reset = False
                self.end_line = None
            elif status is CHILD:
                self.children = True
                # self.reset = False
                self.end_line = None
                for child in self.allowed_blocks:
                    match, offset, kwds = child.is_type(stream.last_line, line, stream.peek_line())
                    if match:
                        new_indent = self.indent + offset
                        child = child(stream=stream, indent=new_indent, parent=self, **kwds)
                        items.append(child)
                        child.parse()
                        break
                else:
                    # no valid child type found
                    raise BadFormat('failed match at line %d\n%r' % (stream.line_no, line))
            else:
                raise Exception('unknown status: %r' % (status, ))
        else:
            keep = self.finalize()
            self.reset = False
        return keep

    def premature_end(self, line):
        # by default, that's an error
        raise IndentError('bad indent at line %d (missing blank line above?)' % self.stream.line_no)

    def check(self, line):
        """
        return END, SAME, CHILD, or CONCLUDE depending on nature of next line
        """
        raise NotImplementedError

    @classmethod
    def is_type(cls, last_line, line, next_line):
        """
        checks if next line(s) are this node's type

        returns True, additional indent, {any keywords for __init__}; or
                NO_MATCH

        must leave stream at same location
        """
        raise NotImplementedError

    def finalize(self):
        """
        any final processing for the node goes here
        """
        self.final = True
        return True

    def get_child_node():
        pass

    def to_html(self):
        raise NotImplementedError


class Heading(Node):
    type = HEADING
    allowed_text = ALL_TEXT
    level = None
    blank_line_required = False

    def __init__(self, level=None, **kwds):
        super(Heading, self).__init__(**kwds)
        self.level = level
        self.text = level

    def _repr(self):
        return 'level=%r, text=%r' % (self.level, self.text)

    def check(self, line):
        # this is only called when handling a level 1 header
        if self.text == self.level:
            self.text = None
            return SAME
        chars = set(line)
        self.items.append(line)
        if len(chars) == 1 and len(line) >= 3 and chars.pop() == '=':
            return CONCLUDE
        return SAME


    def finalize(self):
        first, second, third, fourth = self.parent.header_sizes
        ch = set(self.items[-1].strip()).pop()
        if self.level == first and ch != '=':
            raise BadFormat('Top level Headings must end with at least three = characters')
        self.items.pop()
        if self.level == 'first':
            self.level = first
        elif not self.level:
            if self.parent.first_header_is_title and self.sequence == 0:
                self.level = first
            elif ch == '=':
                self.level = second
            elif ch == '-':
                self.level = third
            elif ch == '.':
                self.level = fourth
            else:
                raise Exception('unknown header character: %r' % ch)
        self.items = format(self.items, allowed_styles=self.allowed_text, parent=self)
        if self.parent.first_header_is_title and self.level == first:
            self.parent.title = re.sub('<[^>]*>','', self.to_html()).strip()
        return super(Heading, self).finalize()

    @classmethod
    def is_type(cls, last_line, line, next_line):
        chars = set(line.strip())
        if len(chars) == 1 and len(line) >= 3 and chars.pop() == '=':
            return True, 0, {'level': 'first'}
        return NO_MATCH

    def to_html(self):
        start = '<h%d>' % self.level
        end = '</h%d>' % self.level
        result = []
        for txt in self.items:
            result.append(txt.to_html())
        return '%s%s%s' % (start, ''.join(result), end)


class Paragraph(Node):
    type = PARAGRAPH
    allowed_text = ALL_TEXT

    def __init__(self, possible_header=True, **kwds):
        super(Paragraph, self).__init__(**kwds)
        self.possible_header = possible_header

    def check(self, line=0):
        if self.items:
            # paragraph may have ended
            for regex in UL, OL, CB, BQ, FCB, DTLS, DTLD:
                if match(regex, line):
                    # paragraph has ended, new block has started
                    return END
        if match(HD, line):
            # either the ending paragraph is really a header,
            # or we have a horizontal rule between blocks;
            # multiple lines in the paragraph means horizontal rule;
            # no preceding blank line before the block means horizontal rule;
            # otherwise, single line in paragraph means header
            if len(self.items) == 1 and self.possible_header:
                self.items.append(line)
                return CONCLUDE
            else:
                return END
        if self.items and isinstance(self.items[-1], str) and self.items[-1].endswith('-'):
            self.items[-1] = self.items[-1][:-1] + line
        elif self.items:
            self.items.append(' '+line)
        else:
            self.items.append(line)
        return SAME

    @classmethod
    def is_type(cls, last_line, line, next_line):
        if not line.strip() or not line[:1].strip():
            return NO_MATCH
        return True, 0, {'possible_header': not bool(last_line.strip())}

    def finalize(self):
        if match(HD, self.items[-1]) and self.possible_header:
            self.__class__ = Heading
            return self.finalize()
        else:
            self.items = format(self.items, allowed_styles=self.allowed_text, parent=self)
        return super(Paragraph, self).finalize()

    def to_html(self):
        start = '<p>'
        end = '</p>'
        result = []
        for txt in self.items:
            result.append(txt.to_html())
        return '%s%s%s' % (start, ''.join(result), end)


class CodeBlock(Node):
    type = CODEBLOCK
    allowed_text = PLAIN
    blank_line = INCLUDE
    blank_line_required = False

    def __init__(self, block_type, attrs, **kwds):
        super(CodeBlock, self).__init__(**kwds)
        self.block_type = block_type
        self.language = ''
        self.attrs = ''
        attrs = attrs and attrs.strip()
        if not attrs:
            return
        l_count = attrs.count('{')
        r_count = attrs.count('}')
        if l_count != r_count or l_count > 1 or r_count > 1:
            raise BadFormat('mismatch {} in fenced block attributes %r' % attrs)
        elif attrs[::(len(attrs)-1)] != '{}':
            # handle language-only attribute
            if attrs.startswith('.'):
                raise BadFormat('leading . not allowed in %r when only language is specified' % attrs)
            if ' ' in attrs:
                raise BadFormat('invalid language specifier %r (no spaces allowed)' % attrs)
            attrs = '{ .%s }' % attrs
        #
        attrs = attrs[1:-1].strip().split()
        if not attrs:
            return
        language = attrs[0]
        attrs = attrs[1:]
        if not language.startswith('.'):
            raise BadFormat('missing leading . in language specifier %r' % language)
        self.language = language[1:]
        new_attrs = []
        for a in attrs:
            if not a.startswith('.'):
                raise BadFormat('missing leading . in attribute %r' % a)
            new_attrs.append(a[1:])
        self.attrs = ' '.join(new_attrs)

    def check(self, line):
        if self.block_type == 'indented':
            self.items.append(line)
            return SAME
        else:
            if not self.items:
                self.items.append(line)
                return SAME
            self.items.append(line)
            if line.rstrip() == self.block_type:
                return CONCLUDE
            return SAME

    @classmethod
    def is_type(cls, last_line, line, next_line):
        if match(FCB, line):
            indent, block_type, attrs = match().groups()
            indent = indent and len(indent) or 0
            attrs = attrs.strip()
            return True, indent, {'block_type': block_type, 'attrs': attrs}
        if match(CB, line):
            return True, 4, {'block_type': 'indented', 'attrs': None}
        return NO_MATCH

    def finalize(self):
        if self.block_type == 'indented':
            while not self.items[-1].strip():
                self.items.pop()
        else:
            if self.items[-1].rstrip() != self.block_type:
                raise BadFormat('missing code block fence %r at line %d' % (self.block_type, self.end_line))
            self.items.pop()
            self.items.pop(0)
        return super(CodeBlock, self).finalize()

    def to_html(self):
        pre = '<pre>'
        code = '<code>'
        if self.attrs:
            pre = '<pre class="%s">' % self.attrs
        if self.language:
            code = '<code class="language-%s">' % self.language
        start = '%s%s' % (pre, code)
        end = '</code></pre>'
        return start + escape('\n'.join(self.items)) + end


class Image(Node):
    type = IMAGE
    allowed_text = ALL_TEXT

    def __init__(self, title, text, url, **kwds):
        super(Image, self).__init__(**kwds)
        self.items = format(text, allowed_styles=self.allowed_text, parent=self)
        self.title = title or ''
        self.url = url.strip()

    def check(self, line):
        return CONCLUDE

    @classmethod
    def is_type(cls, last_line, line, next_line):
        if match(IMAGE_LINK, line):
            alt_text, url, title = match().groups()
            return True, 0, {'text':alt_text, 'title':title, 'url':url}
        return NO_MATCH

    def to_html(self):
        alt_text = []
        for txt in self.items:
            alt_text.append(txt.to_html())
        alt_text = ''.join(alt_text)
        if alt_text:
            alt_text = 'alt="%s"' % alt_text
        title = self.title
        if title:
            title = 'title=%s' % title
        attrs = ('%s %s' % (title, alt_text)).strip()
        return '\n<div><img src="%s" %s></div>\n' % (self.url, attrs)

class List(Node):
    type = O_LIST | U_LIST
    allowed_blocks = ()             # ListItem added after definition
    blank_line = RESET
    terminate_after_children = False
    blank_line_required = False

    def __init__(self, marker, list_type, **kwds):
        super(List, self).__init__(**kwds)
        self.marker = marker
        self.list_type = list_type
        if list_type is U_LIST:
            self.regex = UL
        else:
            self.regex = OL

    def check(self, line):
        if not match(self.regex, line):
            return END
        if self.list_type is U_LIST:
            marker, text = match().groups()
        elif self.list_type is O_LIST:
            number, marker, text = match().groups()
        if marker == self.marker:
            return CHILD
        if self.reset:
            # we hit a blank line first, end this list and start a new one
            return END
        else:
            # different lists must be separated by a blank line
            raise BadFormat('attempt to change list marker inside a list at line %d' % self.stream.line_no)

    @classmethod
    def is_type(cls, last_line, line, next_line):
        if match(UL, line):
            marker, text = match().groups()
            return True, 0, {'marker': marker, 'list_type': U_LIST}
        elif match(OL, line):
            number, marker, text = match().groups()
            return True, 0, {'marker': marker, 'list_type': O_LIST}
        return NO_MATCH

    def premature_end(self, line):
        pass

    def to_html(self, indent=0):
        spacing = ' ' * indent
        if self.regex == UL:
            start = spacing + '<ul>'
            end = spacing + '</ul>'
        else:
            start = spacing + '<ol>'
            end = spacing + '</ol>'
        result = [start]
        for item in self.items:
            result.append(spacing + item.to_html())
        result.append(end)
        return '\n'.join(result)

class ListItem(Node):
    type = LISTITEM
    allowed_blocks = CodeBlock, Image, List
    allowed_text = ALL_TEXT
    regex = None
    marker = None
    number = None
    blank_line_required = False
    blank_line = RESET
    terminate_after_children = False

    def __init__(self, marker=None, list_type=None, text=None, **kwds):
        super(ListItem, self).__init__(**kwds)
        self.marker = marker
        self.list_type = list_type
        self.text = text
        if list_type is U_LIST:
            self.regex = UL
        else:
            self.regex = OL

    def check(self, line):
        # if blank line seen, make sure List knows about it
        self.parent.reset = True
        if not self.items:
            self.items.append(self.text)
            self.text = None
            return SAME
        c_indent = len(self.marker) + 1
        indent, text = match(CL, line).groups()
        if not indent or len(indent) < c_indent:
            return END
        text = line[c_indent:]  # preserve beginning whitespace of sub-items
        if self.reset:
            # get empty line back
            self.items.append('')
        self.items.append(text)
        return SAME

    @classmethod
    def is_type(cls, last_line, line, next_line):
        if match(UL, line):
            marker, text = match().groups()
            return True, 0, {'marker': marker, 'list_type': U_LIST, 'text': text}
        elif match(OL, line):
            number, marker, text = match().groups()
            return True, 0, {'marker': '%s%s'%(number,marker), 'list_type': O_LIST, 'text': text}
        return NO_MATCH

    def finalize(self):
        # remove paragraph status from item[0] if present
        # handle sub-elements
        final_items = []
        sub_doc = Document('\n'.join(self.items), links=self.links)
        final_items.extend(sub_doc.nodes)
        self.items = final_items
        if self.items and isinstance(self.items[0], Paragraph):
            self.items[0:1] = self.items[0].items
        return super(ListItem, self).finalize()

    def to_html(self, indent=0):
        spacing = ' ' * indent
        start = '%s<li>' % spacing
        end =  '%s</li>' % spacing
        result = []
        list_item = []
        for i in self.items:
            if not isinstance(i, List):
                list_item.append(spacing+i.to_html())
            else:
                result.append('\n%s%s' % (spacing, i.to_html(indent+4)))
        result.insert(0, start + ''.join(list_item) + end)
        return ''.join(result)

List.allowed_blocks = ListItem,

class Rule(Node):
    type = RULE
    allowed_text = None

    def check(self, line):
        self.items.append(line)
        return CONCLUDE

    @classmethod
    def is_type(cls, last_line, line, next_line):
        chars = set(line)
        if len(line) >= 3 and len(chars) == 1 and chars.pop() in '-*':
            return True, 0, {}
        return NO_MATCH

    def to_html(self):
        return '<hr>'


class Text(Node):
    type = TEXT
    allowed_text = ALL_TEXT

    def __init__(self, text=None, single='!', style=PLAIN, **kwds):
        if 'stream' not in kwds:
            kwds['stream'] = None
        self.text = text
        self.char = single
        self.style = style
        super(Text, self).__init__(**kwds)

    def __repr__(self):
        if self.text is None and not self.items:
            return self.char
        elif self.text:
            return "%s(%s: %s)" % (self.__class__.__name__, self.style.name, self.text)
        else:
            return ("%s(style=%s,\n     items=%r)"
                    % (self.__class__.__name__, self.style, self.items))

    def to_html(self):
        start = ''
        end = ''
        for mark, open, close in (
                (BOLD, '<b>', '</b>'),
                (ITALIC, '<i>', '</i>'),
                (STRIKE, '<del>', '</del>'),
                (UNDERLINE, '<u>', '</u>'),
                (HIGHLIGHT, '<mark>', '</mark>'),
                (SUB, '<sub>', '</sub>'),
                (SUPER, '<sup>', '</sup>'),
                (CODE, '<code>', '</code>'),
                (FOOT_NOTE, '<sup>', '</sup>'),
            ):
            if mark in self.style:
                start = start + open
                end = close + end
        if self.text is not None:
            body = escape(self.text)
        else:
            result = []
            for txt in self.items:
                try:
                    result.append(txt.to_html())
                except AttributeError:
                    print('v' * 50)
                    print(self)
                    print('^' * 50)
                    raise
            body = ''.join(result)
        return '%s%s%s' % (start, body, end)


class IDLink(Node):
    type = LINK
    allowed_blocks = CodeBlock, List, Image, Paragraph
    allowed_text = ALL_TEXT
    blank_line = INCLUDE
    blank_line_required = False

    def __init__(self, marker, text, **kwds):
        super(IDLink, self).__init__(**kwds)
        if marker[0] == '^':
            self.type = 'footnote'
        else:
            #external link
            self.type = 'link'
        self.marker = marker
        self.text = text

    def check(self, line):
        if not self.items:
            self.indent += len(self.marker) + 4
            self.items.append(self.text)
            self.text = None
            return SAME
        if match(ID_LINK, line):
            return END
        self.items.append(line)
        return SAME

    def finalize(self):
        if self.type == 'footnote':
            final_items = []
            sub_doc = Document('\n'.join(self.items), links=self.links)
            final_items.extend(sub_doc.nodes)
            # self.items = format(final_items, allowed_styles=self.allowed_text, parent=self)
            self.items = final_items
            if self.items and isinstance(self.items[0], Paragraph):
                self.items[0:1] = self.items[0].items
            for link in self.links[self.marker]:
                link.final = True
            keep = True
        else:
            for link in self.links[self.marker]:
                link.text %= ''.join(self.items)
                link.final = True
            keep = False
        return keep

    @classmethod
    def is_type(cls, last_line, line, next_line):
        if match(ID_LINK, line):
            marker, text = match().groups()
            return True, 0, {'marker': marker, 'text': text}
        return NO_MATCH

    def premature_end(self, line):
        if match(ID_LINK, line):
            # if ending non-blank line is another footnote, we're okay
            pass
        else:
            super(IDLink, self).premature_end(line)

    def to_html(self):
        s_marker = self.marker[1:]
        start = '<div class="footnote" id="footnote-%s"><sup>%s</sup>' % (s_marker, s_marker)
        end = '</div>'
        result = []
        for item in self.items:
            if self.type == 'footnote' and not isinstance(item, Text):
                result.append('\n')
            result.append(item.to_html())
        if result[-1][-1] == '\n':
            result[-1][-1] = ''
        return "%s%s%s" % (start, ''.join(result), end)


class Link(Text):
    # Wiki, external, and footnote
    type = LINK
    allowed_text = PLAIN

    def __init__(self, text=None, url=None, marker=None, **kwds):
        """
        text: stuff between the <a href...> and the </a>
        url: stuff in the href="..."
        marker: identifier for footnotes and separate external links
        """
        super(Link, self).__init__(**kwds)
        self.marker = marker = marker and marker.strip()
        self.url = url = url and url.strip()
        text = text and text.strip()
        if marker is not None:
            if marker[0] == '^':
                s_marker = escape(marker[1:])
                self.type = 'footnote'
                self.text = '<sup><a href="#footnote-%s">%s</a></sup>' % (s_marker, s_marker)
                self.links.setdefault(marker, []).append(self)
            else:
                self.type = 'separate'
                self.text = '<a href="%%s">%s</a>' % (escape(text), )
                self.links.setdefault(marker, []).append(self)
        elif text == url:
            # either a wiki page link, or the real link will be discovered later
            self.type = 'self'
            stext = escape(url)
            self.marker = url
            self.text = '<a href="%%s">%s</a>' % stext
            self.url = '<a href="%s">%s</a>' % (url, stext)
            self.links.setdefault(url, []).append(self)
        elif url is not None:
            self.type = 'simple'
            self.text = '<a href="%s">%s</a>' % (url, escape(text))
            self.final = True

    def to_html(self):
        if not self.final:
            if self.url is None:
                raise MissingLink('link %r never found' % (self.marker, ))
            self.text = self.url
            self.final = True
        return self.text

class BlockQuote(Node):
    type = QUOTE
    allowed_text = ALL_TEXT
    terminate_after_children = False
    blank_line_required = False

    def __init__(self, **kwds):
        super(BlockQuote,self).__init__(**kwds)
        self.level = 1
        if isinstance(self.parent, self.__class__):
            self.level += self.parent.level

    def check(self, line):
        if match(BQ, line):
            level = len(match.groups()[0])
            line = line[level:]
            if line and line[0] != ' ':
                raise BadFormat('a space is needed in %r' % line)
            line = line[1:]
            if level == self.level:
                self.items.append(line)
                return SAME
            elif level > self.level:
                return CHILD
            else: # level < self.level
                return END
        return END


    @classmethod
    def is_type(cls, last_line, line, next_line):
        if match(BQ, line):
            return True, 0, {}
        return NO_MATCH

    def finalize(self):
        # handle sub-elements
        final_items = []
        doc = []
        for item in self.items:
            if isinstance(item, unicode):
                # simple text, append it
                doc.append(item)
            else:
                # an embedded node, process any text lines
                if doc:
                    doc = Document('\n'.join(doc), links=self.links)
                    final_items.extend(doc.nodes)
                doc = []
                final_items.append(item)
        if doc:
            doc = Document('\n'.join(doc), links=self.links)
            final_items.extend(doc.nodes)
        # self.items = format(final_items, allowed_styles=self.allowed_text, parent=self)
        self.items = final_items
        return super(BlockQuote, self).finalize()

    def to_html(self):
        mid_space = '\n' + ' ' * 12
        start = '<blockquote>'
        end = '\n</blockquote>'
        result = []
        result.append(start)
        for item in self.items:
            if isinstance(item, Node):
                result.append(mid_space + mid_space.join(item.to_html().split('\n')))
            else:
                result.append(mid_space + item)
        result.append(end)
        return ''.join(result)
BlockQuote.allowed_blocks = (BlockQuote, )

class Detail(Node):
    type = DETAIL
    allowed_text = ALL_TEXT
    terminate_after_children = False
    blank_line_required = True

    def __init__(self, summary, text, **kwds):
        super(Detail,self).__init__(**kwds)
        self.text = text
        self.summary = summary
        if isinstance(self.parent, self.__class__):
            raise BadFormat('nested details not supported (%r)' % self.stream.line_no)

    def check(self, line):
        if self.text is not None:
            self.items.append(self.text)
            self.text = None
            return SAME
        if match(DTLS, line):
            if self.items:
                raise BadFormat('blank line required to separate detail blocks (%r)' % line)
            return SAME
        if match(DTLD, line):
            marker, text = match.groups()
            self.items.append(text)
            return SAME
        return END

    @classmethod
    def is_type(cls, last_line, line, next_line):
        if match(DTLS, line) or match(DTLD, line):
            marker, text = match().groups()
            summary = None
            if marker == '-->':
                summary = text
                text = None
            return True, 0, {'text':text, 'summary':summary}
        return NO_MATCH

    def finalize(self):
        # handle summary
        if self.summary:
            self.summary = format(self.summary, allowed_styles=self.allowed_text, parent=self)
        # handle sub-elements
        # final_items = []
        doc = Document('\n'.join(self.items), links=self.links)
        # self.items = format(doc.nodes, allowed_styles=self.allowed_text, parent=self)
        self.items = doc.nodes
        return super(Detail, self).finalize()

    def to_html(self):
        start = '\n<details>'
        end = '</details>'
        result = []
        result.append(start)
        if self.summary:
            sums = []
            for item in self.summary:
                if isinstance(item, Node):
                    sums.append(item.to_html())
                else:
                    sums.append(item)
            result.append('<summary>%s</summary>' % ''.join(sums))
        for item in self.items:
            if isinstance(item, Node):
                result.append(item.to_html())
            else:
                result.append(item)
        result.append(end)
        return '\n'.join(result)

class Table(Node):
    type = TABLE
    allowed_text = ALL_TEXT
    blank_line_required = True
    cell_count = 0
    header_cells = []
    body_cells = []
    rows = []
    dividers = 0

    def __init__(self, line, **kwds):
        super(Table, self).__init__(**kwds)
        self.header_cells = []
        self.body_cells = []
        self.rows = []
        cells = self.split_row(line)
        self.cell_count = len(cells)

    def check(self, line):
        cells = self.split_row(line.rstrip())
        if len(cells) != self.cell_count:
            raise BadFormat('line %r does not have %d cells' % (line, self.cell_count))
        self.rows.append(cells)
        return SAME

    def finalize(self):
        """
        the entire table is in rows as lists of cells
        parse out the headers, etc.
        """
        i = 0
        if len(self.rows) > 1:
            for cell in self.rows[1]:
                if set(cell) != set('-'):
                    break
            else:
                # first row is header
                self.header_cells = [
                        format(cell, allowed_styles=self.allowed_text, parent=self)
                        for cell in self.rows[0]
                        ]
                i = 2
        for row in self.rows[i:]:
            self.body_cells.append([
                    format(cell, allowed_styles=self.allowed_text, parent=self)
                    for cell in row
                    ])
        return super(Table, self).finalize()

    @classmethod
    def is_type(cls, last_line, line, next_line):
        if line.startswith('|'):
            return True, 0, {'line':line}
        return NO_MATCH

    def to_html(self):
        result = ['<table>']
        if self.header_cells:
            result.append('    <thead>\n        <tr>')
            for cell in self.header_cells:
                result.append('            <th>%s</th>' % ''.join([c.to_html() for c in cell]))
            result.append('        </tr>\n    </thead>')
        result.append('    <tbody>')
        for row in self.body_cells:
            result.append('        <tr>')
            for cell in row:
                result.append('            <td>%s</td>' % ''.join([c.to_html() for c in cell]))
            result.append('        </tr>')
        result.append('    </tbody>\n</table>')
        return '\n'.join(result)

    def split_row(self, line):
        if line[0] != '|' or line[-1] != '|':
            raise BadFormat('table lines must start and end with | [%r]' % line)
        cells = []
        cell = []
        i = 1
        while i < len(line):
            ch = line[i]
            if ch == '\\':
                cell.append(line[i+1])
                i += 2
                continue
            elif ch == '|':
                cells.append(''.join(cell).strip())
                cell = []
            else:
                cell.append(ch)
            i += 1
        # there should be nothing in cell at this point
        if cell:
            raise BadFormat('table lines must start and end with | [%r]' % line)
        return cells

class ID(Node):
    type = ID
    allowed_text = None

class Definition(Node):
    type = DEFINITION
    allowed_text = ALL_TEXT


class PPLCStream(object):
    """
    Peekable, Pushable, Line & Character Stream
    """

    # line_no starts at zero and represents the line number of the line that is accessible by
    # current_line
    line_no = 0

    # current_line is the line pointed to by line_no, and the line that *_char functions deal with
    @property
    def current_line(self):
        return ''.join(self.chars)

    def __init__(self, text):
        self.data = text.split('\n')
        self.data.reverse()
        self.chars = []
        self.line_no = 0
        if self.data:
            self.chars = list(self.data.pop()) + ['\n']
        self.last_line = ''

    def __bool__(self):
        return bool(self.current_line)
    __nonzero__ = __bool__

    def _get_line(self):
        if not self.chars:
            raise EOFError
        line = ''.join(self.chars)
        self.chars = []
        self.line_no += 1
        if self.data:
            self.chars = list(self.data.pop()) + ['\n']
        return line

    def peek_line(self):
        "return line after current line, or an empty string"
        if self.data:
            return self.data[-1] + '\n'
        return ''

    def skip_blank_lines(self):
        while self:
            if self.current_line.strip():
                return True
            self.skip_line()
        return False

    def skip_line(self, count=1):
        for _ in range(count):
            self.last_line = self.current_line
            self._get_line()

def format(texts, allowed_styles, parent, _recurse=False):
    def f(open, close, start=0, ws_needed=True):
        end = len(chars)
        match_len = len(close or open)
        open_count = 1
        close_count = 0
        i = start
        while i < end:
            if chars[i].char == '\\':
                if _recurse:
                    i += 2
                else:
                    del chars[i]
                    i += 1
                continue
            possible = ''.join(c.char for c in chars[i:i+match_len])
            if possible == close:
                # count must be the same
                # whitespace setting must match
                if not ws_needed:
                    if open == close:
                        # count doesn't matter
                        # whitespace doesn't matter
                        return i
                    close_count += 1
                    if open_count == close_count:
                        return i
                else:
                    # check for needed whitespace
                    if ws(i+1):
                        close_count += 1
                        if open_count == close_count:
                            return i
            # check for valid open tag
            if possible == open:
                if not ws_needed or ws(i, forward=False):
                    open_count += 1
                    i += match_len - 1
            i += 1
        else:
            return -1
    def s(start=None, ws=False, forward=True):
        if start is None:
            start = pos
        if forward:
            for ch in chars[start:]:
                if not ch.char.isalnum() and ch.char.strip():
                    continue
                if ws and not ch.char.strip():
                    return True
                if not ws and ch.char.strip():
                    return True
                return False
            return ws
        else:
            if start == 0:
                return ws
            for ch in chars[start-1::-1]:
                if not ch.char.isalnum() and ch.char.strip():
                    continue
                if ws and not ch.char.strip():
                    return True
                if not ws and ch.char.strip():
                    return True
                return False
            return ws
    ws = lambda start, forward=True: s(start, ws=True, forward=forward)
    non_ws = lambda start, forward=True: s(start, ws=False, forward=forward)
    find = Var(f)
    peek_char = Var(lambda index, len=1: ''.join(c.char for c in chars[index:index+len]))

    if isinstance(texts, basestring):
        texts = [texts]
    result = []
    if _recurse:
        chars = texts
    else:
        chars = []
        # convert to 'c' format
        for text in texts:
            if isinstance(text, Node):
                # already processed
                chars.append(text)
                continue
            else:
                chars.extend([Text(single=c, parent=parent) for c in text])
    # look for subgroups: parentheticals, editorial comments, links, etc
    # link types: internal, wiki, external, footnote
    pos = end = 0
    while pos < len(chars):
        start = pos
        ch = chars[pos]
        if ch.final:
            result.append(ch)
            pos += 1
            continue
        if ch.char == '\\':
            if _recurse:
                pos += 2
            else:
                del chars[pos]
                pos += 1
            continue
        if ch.char == "`":
            # code
            end = find("`", "`", start+1, False)
            if end == -1:
                # oops
                raise BadFormat( 'failed to find matching "`" starting near %r between %r and %r' % (chars[pos-10:pos+10], parent.start_line, parent.end_line))
            chars[start:end+1] = [Text(
                    ''.join([c.char for c in chars[start+1:end]]),
                    style=CODE,
                    parent=parent,
                    )]
            pos += 1
            continue
        if ch.char == '(':
            # parens
            end = find('(', ')', start+1, False)
            if end == -1:
                # oops
                raise BadFormat(
                        "failed to find matching `)` starting near %r between %r and %r"
                        % (chars[pos-10:pos+10], parent.start_line, parent.end_line))
            chars[start+1:end] = format(chars[start+1:end], allowed_styles, parent=parent, _recurse=True)
            pos += 3
            continue
        if ch.char == '[':
            # link
            if ''.join(c.char for c in chars[pos:pos+2]) == '[[':
                # possible editoral comment
                end = find('[[', ']]', pos+2, False)
                if end == -1:
                    # oops
                    raise BadFormat(
                            "failed to find matching `]]` starting near %r between %r and %r"
                            % (chars[pos-10:pos+10], parent.start_line, parent.end_line))
                chars[start+1:end+1] = format(chars[start+2:end], allowed_styles, parent=parent, _recurse=True)
                pos += 3
                continue
            # look for closing bracket and process link
            end = find('[', ']', start+1, False)
            if end == -1:
                # oops
                raise BadFormat(
                        "failed to find matching `]` starting near %r between %r and %r"
                        % (chars[pos-10:pos+10], parent.start_line, parent.end_line))
            # what kind of link do we have?
            if chars[start+1].char == '^':
                # a foot note
                # remove previous spaces
                while chars[start-1].char == ' ':
                    chars.pop(start-1)
                    start -= 1
                    end -=1
                marker = ''.join([c.char for c in chars[start+1:end]])
                fn = Link(marker=marker, parent=parent)
                chars[start:end+1] = [fn]
                pos += 1
                continue
            elif end+1 == len(chars) or peek_char(end+1) not in '[(':
                # either simple wiki page link, or a separately listed url and this
                # text is also the marker
                text = ''.join([c.char for c in chars[start+1:end]])
                link = Link(text=text, url=text, parent=parent)
                chars[start:end+1] = [link]
                pos += 1
                continue
            else:
                text = ''.join([c.char for c in chars[start+1:end]])
                second = end + 2
                if peek_char(end+1) == '[':
                    # url is listed separately, save the marker
                    end = find('[', ']', second, False)
                    if end == -1:
                        # oops
                        raise BadFormat(
                                "failed to find matching `]` starting near %r between lines %r and %r"
                                % (chars[pos-10:pos+10], parent.start_line, parent.end_line))
                    marker = ''.join([c.char for c in chars[second:end]])
                    link = Link(text=text, marker=marker, parent=parent)
                    chars[start:end+1] = [link]
                    pos += 1
                    continue
                else:
                    # url is between ( and )
                    end = find('(', ')', second, False)
                    if end == -1:
                        # oops
                        raise BadFormat(
                                "failed to find matching `)` starting near %r between lines %r and %r"
                                % (chars[pos-10:pos+10], parent.start_line, parent.end_line))
                    url = ''.join([c.char for c in chars[second:end]])
                    # if text is empty, use the url for it
                    link = Link(text=text or url, url=url, parent=parent)
                    chars[start:end+1] = [link]
                    pos += 1
                    continue
        # stars, tildes, underscores, equals, carets
        if ch.char not in "*~_=^":
            pos += 1
            continue
        single = ch.char
        double = peek_char(pos, 2)
        triple = peek_char(pos, 3)
        if triple == '***':
            # bold italic
            marker = '***'
            ws_needed = True
        elif double == '**':
            # bold
            marker = '**'
            ws_needed = True
        elif single == '*':
            # italic
            marker = '*'
            ws_needed = True
        elif double == '__':
            # underline
            marker = '__'
            ws_needed = True
        elif double == '==':
            # highlight
            marker = '=='
            ws_needed = True
        elif double == '~~':
            # strike-through
            marker = '~~'
            ws_needed = True
        elif single == '~':
            # subscript
            marker = '~'
            ws_needed = False
        elif single == '^':
            # superscript
            marker = '^'
            ws_needed = False
        else:
            # no possible match, continue on
            pos += 1
            continue
        # to be the start of formatting, the previous (non-mark) character must be white space
        if ws_needed and not ws(pos, forward=False):
            pos += len(marker)
            continue
        # even if preceding white-space is not needed, a succeeding non-whitespace is
        if not non_ws(pos+len(marker)):
            pos += len(marker)
            continue
        # at this point, we have a valid starting marker, but only if we can find a valid ending marker as well
        end = find(marker, marker, pos+len(marker), ws_needed)
        if end == -1:
            # found nothing
            pos += len(marker)
            continue
        # found something -- does it meet the other criteria?
        if ws_needed and not ws(end+len(marker)):
            # failed the white space test
            pos += len(marker)
            continue
        # again, even if succedding white-space is not needed,a preceding non-whitespace is
        if not non_ws(end, forward=False):
            pos += len(marker)
            continue
        # we have matching markers!

        txt = Text(style=TextType(marker), parent=parent)
        mask = ~txt.style
        items = format(chars[start+len(marker):end], allowed_styles, parent=parent, _recurse=True)
        if False: #len(items) == 1:
            txt.text = items[0].text
        else:
            for item in items:
                item.style &= mask
            txt.items = items

        chars[start:end+len(marker)] = [txt]
        pos += 1
        continue
    # made it through the string!
    # TODO: condense styles if possible; for now, just return it

    string = []
    for ch in chars:
        if ch.text is not None or ch.items:
            if string:
                result.append(Text(''.join(string), parent=parent))
                string = []
            result.append(ch)
        else:
            string.append(ch.char)
    if string:
        result.append(Text(''.join(string), parent=parent))
    return result

def write_css(target):
    with codecs.open(target, 'w', encoding='utf8') as fh:
        fh.write(default_css)

def write_file(target, doc, fragment=False, css='stonemark.css'):
    page = []
    if not fragment:
        page.append(html_page_head)
        if doc.title:
            page.append(html_page_title % doc.title)
        if css:
            page.append(html_page_css % css)
        page.append(html_page_body)
    page.append(doc.to_html())
    if not fragment:
        page.append(html_page_post)
    with codecs.open(target, 'w', encoding='utf8') as f:
        f.write('\n'.join(page).strip())


class Document(object):

    title = None

    def __init__(self, text, first_header_is_title=False, header_sizes=(1, 2, 3, 4), links=None):
        if links is None:
            links = {}
        self.links = links
        # TODO: use `self.blocks` to enable enforcing lead blank lines for headers
        self.blocks = []
        self.first_header_is_title = first_header_is_title
        if len(header_sizes) == 3:
            # backwards compatibility
            if header_sizes[2] == 3:
                header_sizes = header_sizes + (4, )
            else:
                header_sizes = header_sizes + (header_sizes[2], )
        self.header_sizes = header_sizes
        #
        blocks = Detail, CodeBlock, Table, Heading, List, Rule, IDLink, Image, BlockQuote, Paragraph
        stream = PPLCStream(text)
        nodes = []
        count = 0
        while stream.skip_blank_lines():
            line = stream.current_line.rstrip()
            next_line = stream.peek_line().rstrip()
            for nt in blocks:
                match, indent, kwds = nt.is_type(stream.last_line, line, next_line)
                if match:
                    if nodes and nt is List:
                        if indent != 0 and isinstance(nodes[-1], CodeBlock) and nodes[-1].block_type == 'indented':
                            raise BadFormat('lists that follow indented code blocks cannot be indented (line %d)' % stream.line_no)
                    node = nt(stream=stream, indent=indent, parent=self, sequence=count, **kwds)
                    count += 1
                    break
            else:
                raise FormatError('no match found at line %d\n%r' % (stream.line_no, line))
            try:
                keep = node.parse()
            except Exception as exc:
                raise
                raise FormatError('conversion error: %r in\n%r' % (exc.args, node.items))
            if nodes and nt is CodeBlock and node.block_type == 'indented' and isinstance(nodes[-1], List):
                raise BadFormat('indented code blocks cannot follow lists (line %d)\n%r' % (node.start_line, line))
            if keep:
                nodes.append(node)
        self.nodes = nodes

    def to_html(self):
        result = []
        for node in self.nodes:
            result.append(node.to_html())
        return '\n\n'.join(result)


def escape(s, quote=True):
    """
    Replace special characters "&", "<" and ">" to HTML-safe sequences.
    If the optional flag quote is true (the default), the quotation mark
    characters, both double quote (") and single quote (') characters are also
    translated.
    """
    s = s.replace("&", "&amp;") # Must be done first!
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    if quote:
        s = s.replace('"', "&quot;")
        s = s.replace('\'', "&apos;")
    return s

def UID():
    i = 0
    while True:
        i += 1
        yield i
UID = UID()
match = Var(re.match)

UL = r'(-|\+|\*) (.*)'
OL = r'(\d+)(\.|\)) (.*)'
CL = r'( *)?(.*)'
BQ = r'(>+)'
CB = r'    (.*)'
FCB = r'( *)(```|~~~) ?(.*)'
DTLS = r'(-->) ?(.*)'
DTLD = r'(--\|) ?(.*)'
HR = r'(---+|\*\*\*+)$'
HD = r'(===+|---+|\.\.\.+)$'
ID_LINK = r'^\[(\^?.*?)\]: (.*)'
EXT_LINK = r'\b\[((?!^).*?)\]\((.*?)\)\b'
WIKI_LINK = r'\b\[((?!^).*?)\]\b'
FOOT_NOTE_LINK = r'\[(\^.*?)\]:'
IMAGE_LINK = r'^!\[([^]]*)]\(([^"]*)(".*")?\)$'

NO_MATCH = False, 0, {}
WHITE_SPACE = ' \t\n'
MARKS = "*~_^`[]"

html_page_head = '''\
<!doctype html>
<html>
<head>
    <meta charset="UTF-8"/>'''

html_page_title = '    <title>%s</title>'
html_page_css =   '    <link rel="stylesheet" type="text/css" href="./%s"/>'

html_page_body = '''\
</head>
<body>'''

html_page_post = '''\
</body>
</html>'''

default_css = '''\
@charset "utf-8";
/*
 *  portions adapted from:
 *  - Chris Coyier [https://css-tricks.com/snippets/css/simple-and-nice-blockquote-styling/]
 *  - Dom (dcode) at [https://dev.to/dcodeyt/creating-beautiful-html-tables-with-css-428l]
 *  - [https://css-tricks.com/styling-code-in-and-out-of-blocks/]
 */

table {
    border-collapse: collapse;
    margin: 10px 0;
    box-shadow: 0 0 20px rgba(0, 0, 0, 0.15);
}

table th {
  text-align: left;
  min-width: 150px;
  background-color: #f3f3f3;
  border-bottom: thin black solid;
}

table th:first-child {
  text-align: left;
}

table td {
  padding: 7px 25px 7px 0px;
  margin: 5px;
  text-align: left;
}

tbody tr:nth-of-type(even) {
    background-color: #f3f3f3;
}

tbody tr.active-row {
    font-weight: bold;
    color: #009879;
}

select {
  max-width: 225px;
}

blockquote {
  background: #f9f9f9;
  border-left: 10px solid #ccc;
  margin: 1.5em 10px;
  padding: 0.5em 10px;
  quotes: "\201C""\201D""\2018""\2019";
}

blockquote:before {
  color: #ccc;
  content: open-quote;
  font-size: 4em;
  line-height: 0.1em;
  margin-right: 0.25em;
  vertical-align: -0.4em;
}

blockquote p {
  display: inline;
}

/* For all <code> */
code {
  font-family: monospace;
  font-size: inherit;
  background: #efefff;
}

/* Code in text */
p > code,
li > code,
dd > code,
td > code {
  word-wrap: break-word;
  box-decoration-break: clone;
  padding: .1rem .3rem .2rem;
  border-radius: .75rem;
}

h1 > code,
h2 > code,
h3 > code,
h4 > code,
h5 > code {
  padding: .0rem .2rem;
  border-radius: .5rem;
  background: inherit;
  border: thin black solid;
}

pre code {
  display: block;
  white-space: pre;
  -webkit-overflow-scrolling: touch;
  overflow-x: scroll;
  max-width: 100%;
  min-width: 100px;
  padding: 10px;
  border: thin black solid;
  border-radius: .2rem;
}

sup {
  padding: 0px 3px;
  }

.footnote {
  padding: 5px 0px;
  }

*
ol,
ul {
    padding: 0px 15px;
}

li {
  padding: 2px;
}

h1 {
    display: block;
    font-size: 2em;
    margin-top: 0.25em;
    margin-bottom: 0em;
    margin-left: 0;
    margin-right: 0;
    font-weight: bold
}

h2 {
    display: block;
    font-size: 1.5em;
    margin-top: 1.25em;
    margin-bottom: 0em;
    margin-left: 0;
    margin-right: 0;
    font-weight: bold;
}

h3 {
    display: block;
    font-size: 1.25;
    margin-top: 1.25em;
    margin-bottom: 0em;
    margin-left: 0;
    margin-right: 0;
    font-weight: bold;
}

h4 {
    display: block;
    font-size: 1.0em;
    margin-top: 1.25em;
    margin-bottom: 0em;
    margin-left: 0;
    margin-right: 0;
    font-weight: bold;
    font-style: italic;
}

'''

