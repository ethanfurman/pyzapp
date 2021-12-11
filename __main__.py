#!/usr/bin/env python

# imports

from __future__ import print_function

from scription import *
from antipathy import Path
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

import sys

# API

@Command(
        source=Spec('script file or application directory', type=Path),
        output=Spec('output directory and/or name of pyzapp file', OPTION, type=Path),
        include=Spec('modules to include in pyzapp file', MULTI),
        python=Spec('shebang line for python interpreter (without the shebang)', OPTION),
        compress=Spec('compress with DEFLATE [default: no compression]', FLAG),
        force=Spec('overwrite existing output file', FLAG),
        )
def pyzapp(source, output, include, python, compress, force):
   # verify source
    if not source.exists():
        abort('unable to find %r' % source)
    source = Path.abspath(source)
    # verify output
    if not output:
        if source.ext:
            output = source.strip_ext()
        else:
            output = source + 'pyz'
    if source == output:
        abort('refusing to overwrite source with output')
    if output.exists():
        if output.isdir():
            abort('refusing to overwrite directory %r' % output)
        elif not force:
            abort('output file %r already exists (use --force to overwrite' % output)
    print('output: %r' % output, type(output))
    # verify compression
    compression = compress and ZIP_DEFLATED or ZIP_STORED
    print('compress: %r' % compression)
    #
    # ensure we have a directory bundle for source
    if source.isdir():
        remove_src = False
        source_dir = source
    else:
        remove_src = True
        source_dir = Path(mkdtemp())
        source.copy(source_dir/'main')
    #
    # add INCLUDE modules
    for module_name in include:
        if module_name in sys.modules:
            module = sys.modules[module_name]
        else:
            module = __import__(module_name)
        module_dir = Path(module.__file__).dirname
        module_dir.copytree(source_dir/module_dir.filename)
    #
    # create __main__ if missing
    if not source_dir.exists('__main__.py'):
        if not source_dir.exists('main.py'):
            abort('main.py or __main__.py is required')
        included = source_dir.listdir()
        new_main = []
        if shebang:
            new_main.append('#!%s' % python)
        new_main.append('\nimport sys')
        new_main.append('\nsaved_modules = []')
        for inc in included:
            if inc.ext == '.py' or inc.isdir():
                new_main.append('saved_modules.append(sys.modules.pop(%s, None))' % inc.filename)
        new_main.append('\nimport main')
        with open(source_dir/'__main__.py', 'w') as m:
            m.write('\n'.join(new_main))
    #
    # create the archive and add the files
    with ZipFile(output, 'w', compression=compression) as zf:
        for dirpath, dirnames, filenames in source.walk():
            for f in filenames:
                if f.endswith(('.swp','.pyc')):
                    continue
                f = (dirpath/f).relpath(source)
                print('adding %r' % f)
                zf.write(f)
    if remove_src:
        source_dir.rmtree()


# helpers

# do it

Run()
