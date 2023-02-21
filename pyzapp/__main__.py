#!/usr/bin/env python2
"""
Create python executable zip files.
"""
# imports

from __future__ import print_function

from scription import *
from antipathy import Path
from tempfile import mkdtemp
from textwrap import dedent
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

import aenum, antipathy, dbf, pandaemonium, stonemark, scription, xaml
import sys as _sys

aenum, antipathy, dbf, pandaemonium, stonemark, scription, xaml

try:
    ModuleNotFoundError
except NameError:
    class ModuleNotFoundError(ImportError):
        pass

version = 0, 4, 0

# API

@Command(
        source=Spec('script file or application directory', type=Path),
        output=Spec('output directory and/or name of pyzapp file', OPTION, type=Path),
        include=Spec('modules to include in pyzapp file', MULTI),
        shebang=Spec('shebang line for python interpreter (without the shebang)', OPTION),
        compress=Spec('compress with DEFLATE [default: no compression]', FLAG),
        force=Spec('overwrite existing output file', FLAG),
        )
def create(source, output, include, shebang, compress, force):
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
            with open(source/'__main__.py') as s:
                line = s.readline()
        elif source.exists('cli.py'):
            with open(source/'cli.py') as s:
                line = s.readline()
        else:
            line = ''
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
        if not source.exists('__main__.py') and not source.exists('cli.py'):
            abort('directory conversions must have a `__main__.py` or a `cli.py`')
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
        # included = source_dir.glob()
        new_main = []
        if shebang:
            new_main.append('#!%s' % shebang)
            new_main.append('')
        new_main.append('import sys')
        new_main.append('')
        new_main.append('# protect against different versions of modules being imported')
        new_main.append("# by Python's startup procedures")
        new_main.append('saved_modules = []')

        # include files in source
        for dirpath, dirnames, filenames in source_dir.walk():
            if dirpath != source_dir:
                if '__init__.py' in filenames:
                    # it's a package
                    sub_imp = (dirpath-source_dir).lstrip('/').replace('/', '.').strip_ext()
                    print('adding subimport %r' % sub_imp)
                    new_main.append('saved_modules.append(sys.modules.pop("%s", None))' % sub_imp)
                else:
                    dirnames[:] = []
                    continue
            if '.git' in dirnames:
                dirnames.pop(dirnames.index('.git'))
            if '__pycache__' in dirnames:
                dirnames.pop(dirnames.index('__pycache__'))
            for f in filenames:
                if f in ('__init__.py','__main__.py') or not f.endswith('.py'):
                    continue
                sub_imp = (dirpath/f-source_dir).lstrip('/').replace('/', '.').strip_ext()
                print('adding subimport %r' % sub_imp)
                new_main.append('saved_modules.append(sys.modules.pop("%s", None))' % sub_imp)
        #
        # include files from command line
        for inc_module_name in include:
            new_main.append('saved_modules.append(sys.modules.pop("%s", None))' % inc_module_name)
            if inc_module_name in _sys.modules:
                module_path = Path(_sys.modules[inc_module_name].__file__)
            else:
                print('  importing', inc_module_name, verbose=2)
                module_path = Path(find_module(inc_module_name))
            module_file = module_path.stem
            if module_file != '__init__':
                # single file
                new_main.append('saved_modules.append(sys.modules.pop("%s", None))' % module_file)
            else:
                # package
                module_dir = module_path.dirname
                base_dir = module_dir.dirname
                for dirpath, dirnames, filenames in module_dir.walk():
                    if '.git' in dirnames:
                        dirnames.pop(dirnames.index('.git'))
                    if '__pycache__' in dirnames:
                        dirnames.pop(dirnames.index('__pycache__'))
                    for f in filenames:
                        if f in ('__init__.py','__main__.py') or not f.endswith('.py'):
                            continue
                        sub_imp = (dirpath/f-base_dir).lstrip('/').replace('/', '.').strip_ext()
                        print('adding included subimport %r' % sub_imp)
                        new_main.append('saved_modules.append(sys.modules.pop("%s", None))' % sub_imp)
        #
        new_main.append('')
        #
        # handle accessing non-py files inside zip archive (rudimentary)
        new_main.append(dedent("""\
                import os, os.path, errno
                os_path_exists = os.path.exists

                zip, _ = os.path.split(__file__)
                zip_dir = zip + os.path.sep

                def exists_in_pyzapp(filename):
                    try:
                        archive.getinfo(filename)
                        return True
                    except KeyError:
                        return False

                def pyzapp_exists(filename):
                    if filename.startswith('PYZAPP/'):
                        filename = filename[7:]
                        return exists_in_pyzapp(filename)
                    return os_path_exists(filename)
                os.path.exists = pyzapp_exists

                def pyzapp_open(filename, *args, **kwds):
                    if filename.startswith('PYZAPP/'):
                        filename = filename[7:]
                        if args:
                            mode = args[0]
                        else:
                            mode = 'r'
                        if mode == 'rb':
                            mode = 'r'
                        try:
                            return archive.open(filename, mode)
                        except KeyError:
                            raise OSError(errno.ENOENT, os.strerror(errno.ENOENT), filename)
                    else:
                        return bltn_open(filename, *args, **kwds)

                try:
                    import __builtin__
                    bltn_open = __builtin__.open
                    __builtin__.open = pyzapp_open
                    __builtin__.PYZAPP_ARCHIVE = zip_dir
                except ImportError:
                    import builtins
                    bltn_open = builtins.open
                    builtins.open = pyzapp_open
                    builtins.PYZAPP_ARCHIVE = zip_dir

                from zipfile import ZipFile
                archive = ZipFile(bltn_open(zip, 'rb'))
                """))
        if mode == 'package':
            new_main.append('from %s import __main__' % module_name)
        elif source_dir.exists('cli.py'):
            new_main.append('import cli')
        with open(source_dir/'__main__.py', 'w') as m:
            m.write('\n'.join(new_main) + '\n')
    #
    # create the archive and add source files
    with open(output, 'wb') as fd:
        if shebang:
            fd.write('#!%s\n' % shebang)
        with ZipFile(fd, 'w', compression=compression) as zf:
            for dirpath, dirnames, filenames in source_dir.walk():
                print('  dirpath:', dirpath, verbose=3)
                if '.git' in dirnames:
                    dirnames.pop(dirnames.index('.git'))
                if '.hg' in dirnames:
                    dirnames.pop(dirnames.index('.hg'))
                if '__pycache__' in dirnames:
                    dirnames.pop(dirnames.index('__pycache__'))
                for f in filenames:
                    print('   ', f, verbose=3)
                    if (
                            f.endswith(('.swp','.pyc','.bak','.old'))
                            or f.startswith(('.git','.hg'))
                        ):
                        continue
                    arcname = (dirpath/f).relpath(source_dir)
                    f = (dirpath/f)
                    print('      adding %s' % (arcname, ), verbose=3)
                    zf.write(f, arcname=arcname)
            #
            # add INCLUDE modules
            for inc_module_name in include:
                print('including %r' % inc_module_name)
                if inc_module_name in _sys.modules:
                    module_path = Path(_sys.modules[inc_module_name].__file__)
                else:
                    module_path = Path(find_module(inc_module_name))
                module_file = module_path.stem
                module_dir = module_path.dirname
                if module_file != '__init__':
                    # single file, not a package
                    arcname = module_file + '.py'
                    f = module_dir/module_file + '.py'
                    print('  adding %r as %r' % (f, arcname), verbose=3)
                    zf.write(f, arcname=arcname)
                    continue
                print('using %s from %r' % (inc_module_name, module_dir))
                for dirpath, dirnames, filenames in module_dir.walk():
                    if '.git' in dirnames:
                        dirnames.pop(dirnames.index('.git'))
                    if '__pycache__' in dirnames:
                        dirnames.pop(dirnames.index('__pycache__'))
                    print(' ', dirpath, verbose=3)
                    for f in filenames:
                        print('   ', f, verbose=3)
                        if f.endswith(('.swp','.pyc','.bak','.old')):
                            continue
                        arcname = (dirpath/f).relpath(module_dir.dirname)
                        f = (dirpath/f)
                        print('    adding %r as %r' % (f, arcname, ), verbose=3)
                        zf.write(f, arcname=arcname)
    output.chmod(0o555)
    source_dir.rmtree()


@Command(
        name=Spec('name of folder for new script', type=Path)
        )
def init(name, _modules=None):
    """
    Create directory structure for new script NAME.
    """
    if not _modules:
        print('initializing %s' % name)
        if name.exists():
            abort("%s already exists" % name)
        name.makedirs()
    for folder, files in (
            ('aenum', (('LICENSE', 'README', '__init__.py', '_py2.py', '_py3.py'))),
            ('antipathy', (('LICENSE', 'README', '__init__.py', 'path.py'))),
            ('dbf', (('LICENSE', '__init__.py'))),
            ('pandaemonium', (('LICENSE', '__init__.py'))),
            ('scription', (('LICENSE', '__init__.py'))),
            ('stonemark', (('LICENSE', '__init__.py', '__main__.py'))),
            ):
        if _modules and folder not in _modules:
            print('skipping %s' % folder)
            continue
        print('processing %s' % folder)
        folder = Path(folder)
        name.mkdir(folder)
        for filename in files:
            print('   %s/%s' % (folder, filename), end=' . . . ')
            with open('PYZAPP/pyzapp'/folder/filename, 'rb') as fh:
                data = fh.read()
            with open(name/folder/filename, 'wb') as fh:
                fh.write(data)
            print('copied')
    print('done')

@Command(
        app=Spec('pyzapp path/app to update', type=Path),
        modules=Spec('which modules to update',),
        )
def update(app, *modules):
    """
    update supported dependencies in app's source folder
    """
    echo('%r  %r ' % (app, modules))
    if not app.exists():
        abort('unable to find "%s"' % app)
    init(app, modules)


# helpers

def find_module(name):
    init = Path('__init__.py')
    for p in _sys.path:
        path = Path(p)
        if path.exists(name/init):
            return path/name/init
        elif path.exists(name+'.py'):
            return path/name
    else:
        raise ModuleNotFoundError(name)

# do it

Run()
