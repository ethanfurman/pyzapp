#!/usr/bin/env python

# imports

from __future__ import print_function

from _scription import *
from _antipathy import Path
from tempfile import mkdtemp
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

import sys

# API

@Command(
        source=Spec('script file or application directory', type=Path),
        output=Spec('output directory and/or name of pyzapp file', OPTION, type=Path),
        include=Spec('modules to include in pyzapp file', MULTI),
        shebang=Spec('shebang line for python interpreter (without the shebang)', OPTION),
        compress=Spec('compress with DEFLATE [default: no compression]', FLAG),
        force=Spec('overwrite existing output file', FLAG),
        )
def pyzapp(source, output, include, shebang, compress, force):
   # verify source
    if not source.exists():
        abort('unable to find %r' % source)
    source = Path.abspath(source)
    if not shebang:
        # get shebang from the file
        if source.isfile():
            with open(source) as s:
                line = s.readline()
        elif source.exists('__main__.py'):
            with open(source/'__main__') as s:
                line = s.readline()
        if line.startswith('#!'):
            shebang = line[2:].strip()
    print('source: %r' % source)
    print('output: %r' % output)
    print('shebang: %r' % shebang)
    # verify output
    if not output:
        if source.ext:
            output = source.strip_ext()
        else:
            output = source + '.pyz'
    if source == output:
        abort('refusing to overwrite source with output')
    if output.exists():
        if output.isdir():
            abort('refusing to overwrite directory %r' % output)
        elif not force:
            abort('output file %r already exists (use --force to overwrite' % output)
        else:
            output.chmod(0o700)
            output.unlink()
    print('output: %r' % output)
    # verify compression
    compression = compress and ZIP_DEFLATED or ZIP_STORED
    print('compress: %r' % compression)
    #
    # there are three possibilities:
    # - converting a single-script app into a pyzapp
    # - converting a directory bundle consisting of a __main__.py and maybe a test.py into a pyzapp
    # - converting a python package with a __main__.py, __init__.py, etc., into a pyzapp
    # create __main__ if missing
    if source.exists('__main__.py') and source.exists('__init__.py'):
        # option 3
        mode = 'package'
        print('mode =', mode)
        pass
    elif source.exists('__init__.py'):
        abort('unable to convert a package with no __main__.py')
    elif source.isdir():
        # option 2
        if not source.exists('__main__.py'):
            abort('directory conversions must have a __main__.py as one will not be created')
        mode = 'subdir'
        print('mode =', mode)
        pass
    else:
        # option 1
        mode = 'script'
        print('mode =', mode)
    #
    # ensure we have a directory bundle for source
    source_dir = Path(mkdtemp())
    module_name = None
    if mode == 'package':
        module_name = source.filename
        source.copytree(source_dir/module_name)
    elif mode == 'subdir':
        source_dir.rmdir()
        source.copytree(source_dir)
    else:
        source.copy(source_dir/'cli.py')
    print('working dir: %r' % source_dir)
    #
    # create __main__ if needed
    if not source_dir.exists('__main__.py'):
        included = source_dir.glob()
        new_main = []
        if shebang:
            new_main.append('#!%s' % shebang)
            new_main.append('')
        new_main.append('import sys')
        new_main.append('')
        new_main.append('# protect against different versions of modules being imported')
        new_main.append("# by Python's startup procedures")
        new_main.append('saved_modules = []')
        imports = []
        #
        # include files in source
        for inc in included:
            if inc.ext == '.py' or inc.isdir():
                new_main.append('saved_modules.append(sys.modules.pop("%s", None))' % inc.stem)
                imports.append(inc.stem)
                for dirpath, dirnames, filenames in source_dir.walk():
                    # no-op if inc is a file
                    if '.git' in dirnames:
                        dirnames.pop(dirnames.index('.git'))
                    if '__pycache__' in dirnames:
                        dirnames.pop(dirnames.index('__pycache__'))
                    for f in filenames:
                        if f in ('__init__.py','__main__.py') or not f.endswith('.py'):
                            continue
                        sub_imp = (dirpath/f-source_dir).lstrip('/').replace('/', '.').strip_ext()
                        print('adding inherent subimport %r' % sub_imp)
                        imports.append(sub_imp)
        #
        # include files from command line
        for inc_module_name in include:
            new_main.append('saved_modules.append(sys.modules.pop("%s", None))' % inc_module_name)
            imports.append(inc_module_name)
            if inc_module_name in sys.modules:
                module = sys.modules[inc_module_name]
            else:
                module = __import__(inc_module_name)
            module_file = Path(module.__file__).stem
            if module_file != '__init__':
                # single file, not a package
                continue
            module_dir = Path(module.__file__).dirname
            base_dir = module_dir.dirname
            for dirpath, dirnames, filenames in module_dir.walk():
                if '.git' in dirnames:
                    dirnames.pop(dirnames.index('.git'))
                if '__pycache__' in dirnames:
                    dirnames.pop(dirnames.index('__pycache__'))
                for f in filenames:
                    if f == '__init__.py' or not f.endswith('.py'):
                        continue
                    sub_imp = (dirpath/f-base_dir).lstrip('/').replace('/', '.').strip_ext()
                    print('adding included subimport %r' % sub_imp)
                    imports.append(sub_imp)
                    new_main.append('saved_modules.append(sys.modules.pop("%s", None))' % sub_imp)
        #
        new_main.append('')
        if mode == 'script':
            new_main.append('import cli')
        elif mode == 'package':
            new_main.append('from %s import __main__' % module_name)
        with open(source_dir/'__main__.py', 'w') as m:
            m.write('\n'.join(new_main) + '\n')
    #
    # create the archive and add source files
    with open(output, 'wb') as fd:
        if shebang:
            fd.write('#!%s\n' % shebang)
        with ZipFile(fd, 'w', compression=compression) as zf:
            for dirpath, dirnames, filenames in source_dir.walk():
                print('dirpath:', dirpath)
                if '.git' in dirnames:
                    dirnames.pop(dirnames.index('.git'))
                if '__pycache__' in dirnames:
                    dirnames.pop(dirnames.index('__pycache__'))
                print(dirpath, verbose=2)
                for f in filenames:
                    print('  ', f, verbose=2)
                    if f.endswith(('.swp','.pyc','.bak','.old')):
                        continue
                    arcname = (dirpath/f).relpath(source_dir)
                    f = (dirpath/f)
                    print('adding %r as %r' % (f, arcname))
                    zf.write(f, arcname=arcname)
            #
            # add INCLUDE modules
            for inc_module_name in include:
                print('including %r' % inc_module_name)
                if inc_module_name in sys.modules:
                    module = sys.modules[inc_module_name]
                else:
                    module = __import__(inc_module_name)
                module_file = Path(module.__file__).stem
                module_dir = Path(module.__file__).dirname
                if module_file != '__init__':
                    # single file, not a package
                    arcname = module_file + '.py'
                    f = module_dir/module_file + '.py'
                    print('adding %r as %r' % (f, arcname))
                    zf.write(f, arcname=arcname)
                    continue
                print('using %s from %r' % (inc_module_name, module.__file__))
                for dirpath, dirnames, filenames in module_dir.walk():
                    if '.git' in dirnames:
                        dirnames.pop(dirnames.index('.git'))
                    if '__pycache__' in dirnames:
                        dirnames.pop(dirnames.index('__pycache__'))
                    print(dirpath, verbose=2)
                    for f in filenames:
                        print('  ', f, verbose=2)
                        if f.endswith(('.swp','.pyc','.bak','.old')):
                            continue
                        arcname = (dirpath/f).relpath(module_dir.dirname)
                        f = (dirpath/f)
                        print('adding %r as %r' % (f, arcname))
                        zf.write(f, arcname=arcname)
    output.chmod(0o555)
    source_dir.rmtree()


# do it

Run()
