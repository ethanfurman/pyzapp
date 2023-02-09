from __future__ import print_function
from scription import *
from antipathy import Path
from . import Document, write_file


@Command(
        source=Spec('name of source file to convert', type=Path),
        target=Spec('name of output file [default: source base name with .html extension]', default='', type=Path),
        header_sizes=Spec('sizes for the three header categories', MULTI, abbrev='sizes', force_default=(1,2,3)),
        header_title=Spec('make first header a title', FLAG, abbrev='title'),
        css=Spec('use specified css file instead of default css settings', OPTION, force_default='stonemark.css'),
        fragment=Spec('do not include <body>, css, etc., in target file', FLAG),
        )
def stonemark(source, target, header_sizes, header_title, css, fragment):
    if not source.exists():
        abort("'%s' does not exist" % source)
    if target == '':
        target = source.strip_ext() + '.html'
    elif target.isdir():
        target += source.filename.strip_ext() + '.html'
    with open(source) as f:
        text = f.read()
    doc = Document(text, header_sizes=header_sizes, first_header_is_title=header_title)
    write_file(target, doc, fragment, css)


Run()
