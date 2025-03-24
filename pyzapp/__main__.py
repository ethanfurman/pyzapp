#!/usr/bin/env python2
"""
Create python executable zip files.
"""
from __future__ import print_function

import aenum
import antipathy
import dbf
import pandaemonium
import stonemark
import scription
import xaml

from scription import *
from antipathy import Path
from tempfile import mkdtemp
from textwrap import dedent
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

import sys as _sys

aenum, antipathy, dbf, pandaemonium, stonemark, scription, xaml

try:
    ModuleNotFoundError
except NameError:
    class ModuleNotFoundError(ImportError):
        pass

version = 0, 5, 0

internal_modules = {
        'aenum': ('LICENSE', 'README', '__init__.py', '_py2.py', '_py3.py',
                   '_common.py', '_constant.py', '_enum.py', '_tuple.py',
                   'test.py', 'test_v3.py', 'test_v37.py',
                   ),
        'antipathy': ('LICENSE', 'README', '__init__.py', 'path.py'),
        'dbf': ('LICENSE', '__init__.py'),
        'pandaemonium': ('LICENSE', '__init__.py'),
        'scription': ('LICENSE', '__init__.py'),
        'stonemark': ('LICENSE', '__init__.py', '__main__.py'),
        'xaml': ('LICENSE', '__init__.py', '__main__.py'),
        }

## API

@Alias('create')
@Command(
        source=Spec('script file or application directory', type=Path),
        output=Spec('output directory and/or name of pyzapp file', OPTION, type=Path),
        include=Spec('modules to include in pyzapp file', MULTI),
        shebang=Spec('shebang line for python interpreter (without the shebang)', OPTION),
        compress=Spec('compress with DEFLATE [default: no compression]', FLAG),
        force=Spec('overwrite existing output file', FLAG),
        )
def convert(source, output, include, shebang, compress, force):
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
    # create __main__ if missing (or dance around if making ourself)
    if source.endswith('/pyzapp'):
        mode = 'subdir'
        print('mode = self-compilation')
    elif source.exists('__main__.py') and source.exists('__init__.py'):
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
        if source.endswith('/pyzapp'):
            source.unlink('__init__.py')
            (source_dir/'__main__.py').rename(source_dir/'cli.py')
    else:
        source.copy(source_dir/'cli.py')
    print('working dir: %r\n   %s' % (source_dir, '\n   '.join(source_dir.listdir())))
    #
    # create __main__ if needed
    if not source_dir.exists('__main__.py'):
        new_main = []
        if shebang:
            new_main.append('#!%s' % shebang)
            new_main.append('')
        new_main.append('import sys')
        new_main.append('')
        new_main.append('# protect against different versions of modules being imported')
        new_main.append("# by Python's startup procedures")
        new_main.append('saved_modules = []')

        to_import = set()
        # include files in source
        for dirpath, dirnames, filenames in source_dir.walk():
            # print('\n  '.join([dirpath]+filenames), verbose=2)
            if dirpath != source_dir:
                if '__init__.py' in filenames:
                    # it's a package
                    # print('--- %r' % (dirpath-source_dir), verbose=2)
                    sub_imp = (dirpath-source_dir).replace('/', '.')
                    to_import.add(sub_imp)
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
                sub_imp = (dirpath/f-source_dir).replace('/', '.').strip_ext()
                to_import.add(sub_imp)
                print('adding subimport %r' % sub_imp)
                new_main.append('saved_modules.append(sys.modules.pop("%s", None))' % sub_imp)
        #
        # include files from command line
        for inc_module_name in include:
            to_import.add(inc_module_name)
            new_main.append('saved_modules.append(sys.modules.pop("%s", None))' % inc_module_name)
            if inc_module_name in _sys.modules:
                module_path = Path(_sys.modules[inc_module_name].__file__)
            else:
                print('  searching', inc_module_name, verbose=2)
                module_type, module_path = Path(find_module_file(inc_module_name))
            if module_type is PACKAGE:
                # add any submodules
                base_dir = module_path.dirname
                for dirpath, dirnames, filenames in module_path.walk():
                    if '.git' in dirnames:
                        dirnames.pop(dirnames.index('.git'))
                    if '__pycache__' in dirnames:
                        dirnames.pop(dirnames.index('__pycache__'))
                    for f in filenames:
                        if f in ('__init__.py','__main__.py') or not f.endswith('.py'):
                            continue
                        sub_imp = (dirpath/f-base_dir).replace('/', '.').strip_ext()
                        to_import.add(sub_imp)
                        print('adding included subimport %r' % sub_imp)
                        new_main.append('saved_modules.append(sys.modules.pop("%s", None))' % sub_imp)
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

                try:
                """))
        if mode == 'package':
            new_main.append('    from %s import __main__' % module_name)
        elif source_dir.exists('cli.py'):
            new_main.append('    import cli')
            new_main.append('    cli.Run()')
        new_main.append('finally:\n    archive.close()')
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
                            f.endswith(('.swp','.pyc','.pyo','.bak','.old'))
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
                    module_file = Path(_sys.modules[inc_module_name].__file__)
                    module_path = module_file.dirname
                    module_type = PACKAGE if module_file.stem == '__init__'else MODULE
                    module_file = module_file.stem
                else:
                    module_type, module_path = Path(find_module_file(inc_module_name))
                    module_file = inc_module_name if module_type is MODULE else '__init__'
                if module_type is MODULE:
                    arcname = module_file + '.py'
                    f = module_path/module_file + '.py'
                    print('  adding %r as %r' % (f, arcname), verbose=3)
                    zf.write(f, arcname=arcname)
                    continue
                print('using %s from %r' % (inc_module_name, module_path))
                for dirpath, dirnames, filenames in module_path.walk():
                    if '.git' in dirnames:
                        dirnames.pop(dirnames.index('.git'))
                    if '__pycache__' in dirnames:
                        dirnames.pop(dirnames.index('__pycache__'))
                    print(' ', dirpath, verbose=3)
                    for f in filenames:
                        print('   ', f, verbose=3)
                        if f.endswith(('.swp','.pyc','.pyo','.bak','.old')):
                            continue
                        f = dirpath/f
                        arcname = f - module_path
                        print('    adding %r as %r' % (f, arcname, ), verbose=3)
                        zf.write(f, arcname=arcname)
    output.chmod(0o555)
    source_dir.rmtree()


@Command(
        app=Spec('name of folder for new script', type=Path)
        )
def init(app, _modules=None):
    """
    Create directory structure for new script NAME.
    """
    self = app == 'pyzapp'
    external_modules = {}
    if not _modules:
        print('initializing %s' % app)
        if app.exists():
            abort("%s already exists" % app)
        app.makedirs()
    else:
        for m in _modules:
            if m not in internal_modules:
                # non-standard module
                folder = Path(m)
                files = []
                if app.exists(m+'.py'):
                    files.append(m+'.py')
                elif app.exists(m):
                    # package already exists in source, so collect names to update
                    files = []
                    for dirpath, dirnames, filenames in (app/m).walk():

                        files.extend([
                                dirpath/f-app/m
                                for f in filenames
                                if not f.endswith(('.swp','.pyc','.pyo','.bak','.old'))
                                ])

                external_modules[m] = files
    echo(external_modules)
    for m, files in internal_modules.items() + external_modules.items():
        if _modules and m not in _modules:
            print('skipping %s' % m)
            continue
        print('processing %s' % m)
        external = m in external_modules
        internal = not external
        m = Path(m)
        if not _modules:
            # initial creation, m must be a folder
            app.mkdir(m)
        mtype = PACKAGE
        if external:
            # non-standard module/package, look for original files
            mtype, base_dir, src_files = find_module_files(m)
            files = files or src_files                                              # use src_files if new module/package
            print('   %s at %s' % (mtype, base_dir), verbose=2)
        print('  all files:\n    %s' % '\n    '.join(files), verbose=3)
        for filename in files:
            print('   %s' % filename, end=' . . . ')
            if internal and not self:
                print('using stored version')
                with open('PYZAPP'/m/filename, 'rb') as fh:
                    data = fh.read()
            else:
                src = base_dir/filename
                print('\n   grabbing version at', src, end=' . . . ')
                with open(src, 'rb') as fh:
                    data = fh.read()
            if mtype is MODULE:
                dest = app/filename
            else:
                dest = app/m/filename
            if not dest.dirname.exists():
                dest.dirname.mkdir()
            with open(dest, 'wb') as fh:
                fh.write(data)
            print('   copied')
    print('done')

@Command(
        app=Spec('pyzapp path/app to update', type=Path),
        external=Spec('include external modules', FLAG),
        modules=Spec('which modules to update', MULTI),
        )
def update(app, external, *modules):
    """
    update supported dependencies in app's source folder
    """
    if not app.exists():
        abort('unable to find "%s"' % app)
    if not modules:
        # get installed modules
        for _, modules, _ in app.walk():
            break
        if not external:
            modules = [m for m in modules if m in internal_modules]
    init(app, modules)

@Command(
        name=Spec('module name', ),
        )
def test(name):
    mtype, base_dir, files = find_module_files(name)
    echo(mtype)
    echo(base_dir)
    echo('\n'.join(f for f in files))

## helpers

MODULE, PACKAGE = aenum.Enum('FileType', 'MODULE PACKAGE')

def find_module_file(name):
    """
    Return the package's __init__ file, or the single file if not a package.
    """
    init = Path('__init__.py')
    for p in _sys.path:
        path = Path(p)
        if path.exists(name/init):
            return PACKAGE, path/name
        elif path.exists(name+'.py'):
            return MODULE, path/name+'.py'
    else:
        raise ModuleNotFoundError(name)

def find_module_files(name):
    """
    Return a list of all files for module/package `name`.
    """
    files = []
    module_type, path = find_module_file(name)
    if module_type is MODULE:
        base_dir = path.dirname
        print('find_module_files: %s  %s  %s' % (module_type, path.dirname, [path.filename]), verbose=3)
        return module_type, path.dirname, [path.filename]
    else:
        package_file = path.stem
        package_dir = path
        base_dir = path #.dirname
        print('11 ', base_dir, verbose=3)
        for dirpath, dirnames, filenames in package_dir.walk():
            if '.git' in dirnames:
                dirnames.remove('.git')
            if '__pycache__' in dirnames:
                dirnames.remove('__pycache__')
            print('12 ', dirpath, verbose=3)
            for f in filenames:
                print('13   ', f, verbose=3)
                if f.endswith(('.swp','.pyc','.pyo','.bak','.old')) or f[0:1] in '~.':
                    continue
                files.append(dirpath/f-base_dir)
        print('find_module_files: %s  %s  [%s]' % (module_type, base_dir, ', '.join(files)), verbose=3)
        return module_type, base_dir, files

## do it

Run()
