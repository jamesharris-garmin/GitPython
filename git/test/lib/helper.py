# helper.py
# Copyright (C) 2008, 2009 Michael Trier (mtrier@gmail.com) and contributors
#
# This module is part of GitPython and is released under
# the BSD License: http://www.opensource.org/licenses/bsd-license.php
from __future__ import print_function

from functools import wraps
import io
import logging
import os
import tempfile
import textwrap
import time
from unittest import TestCase
import unittest

from git.compat import string_types, is_win, PY3
from git.util import rmtree

import os.path as osp


ospd = osp.dirname

GIT_REPO = os.environ.get("GIT_PYTHON_TEST_GIT_REPO_BASE", ospd(ospd(ospd(ospd(__file__)))))
GIT_DAEMON_PORT = os.environ.get("GIT_PYTHON_TEST_GIT_DAEMON_PORT", "19418")

__all__ = (
    'fixture_path', 'fixture', 'StringProcessAdapter',
    'with_rw_directory', 'with_rw_repo', 'with_rw_and_rw_remote_repo', 'TestBase', 'TestCase',
    'GIT_REPO', 'GIT_DAEMON_PORT'
)

log = logging.getLogger(__name__)

#{ Routines


def fixture_path(name):
    return osp.join(ospd(ospd(__file__)), 'fixtures', name)


def fixture(name):
    with open(fixture_path(name), 'rb') as fd:
        return fd.read()

#} END routines

#{ Adapters


class StringProcessAdapter(object):

    """Allows to use strings as Process object as returned by SubProcess.Popen.
    Its tailored to work with the test system only"""

    def __init__(self, input_string):
        self.stdout = io.BytesIO(input_string)
        self.stderr = io.BytesIO()

    def wait(self):
        return 0

    poll = wait

#} END adapters

#{ Decorators


def with_rw_directory(func):
    """Create a temporary directory which can be written to, remove it if the
    test succeeds, but leave it otherwise to aid additional debugging"""

    @wraps(func)
    def wrapper(self):
        path = tempfile.mktemp(prefix=func.__name__)
        os.mkdir(path)
        keep = False
        try:
            try:
                return func(self, path)
            except Exception:
                log.info("Test %s.%s failed, output is at %r\n",
                         type(self).__name__, func.__name__, path)
                keep = True
                raise
        finally:
            # Need to collect here to be sure all handles have been closed. It appears
            # a windows-only issue. In fact things should be deleted, as well as
            # memory maps closed, once objects go out of scope. For some reason
            # though this is not the case here unless we collect explicitly.
            import gc
            gc.collect()
            if not keep:
                rmtree(path)

    return wrapper


def with_rw_repo(working_tree_ref, bare=False):
    """
    Same as with_bare_repo, but clones the rorepo as non-bare repository, checking
    out the working tree at the given working_tree_ref.

    This repository type is more costly due to the working copy checkout.

    To make working with relative paths easier, the cwd will be set to the working
    dir of the repository.
    """
    assert isinstance(working_tree_ref, string_types), "Decorator requires ref name for working tree checkout"

    def argument_passer(func):
        @wraps(func)
        def repo_creator(self):
            prefix = 'non_'
            if bare:
                prefix = ''
            # END handle prefix
            repo_dir = tempfile.mktemp("%sbare_%s" % (prefix, func.__name__))
            rw_repo = self.rorepo.clone(repo_dir, shared=True, bare=bare, n=True)

            rw_repo.head.commit = rw_repo.commit(working_tree_ref)
            if not bare:
                rw_repo.head.reference.checkout()
            # END handle checkout

            prev_cwd = os.getcwd()
            os.chdir(rw_repo.working_dir)
            try:
                try:
                    return func(self, rw_repo)
                except:
                    log.info("Keeping repo after failure: %s", repo_dir)
                    repo_dir = None
                    raise
            finally:
                os.chdir(prev_cwd)
                rw_repo.git.clear_cache()
                rw_repo = None
                import gc
                gc.collect()
                if repo_dir is not None:
                    rmtree(repo_dir)
                # END rm test repo if possible
            # END cleanup
        # END rw repo creator
        return repo_creator
    # END argument passer
    return argument_passer


def launch_git_daemon(base_path, ip, port):
    from git import Git
    if is_win:
        ## On MINGW-git, daemon exists in .\Git\mingw64\libexec\git-core\,
        #  but if invoked as 'git daemon', it detaches from parent `git` cmd,
        #  and then CANNOT DIE!
        #  So, invoke it as a single command.
        ## Cygwin-git has no daemon.  But it can use MINGW's.
        #
        daemon_cmd = ['git-daemon',
                      '--enable=receive-pack',
                      '--listen=%s' % ip,
                      '--port=%s' % port,
                      '--base-path=%s' % base_path,
                      base_path]
        gd = Git().execute(daemon_cmd, as_process=True)
    else:
        gd = Git().daemon(base_path,
                          enable='receive-pack',
                          listen=ip,
                          port=port,
                          base_path=base_path,
                          as_process=True)
    # yes, I know ... fortunately, this is always going to work if sleep time is just large enough
    time.sleep(0.5)
    return gd


def with_rw_and_rw_remote_repo(working_tree_ref):
    """
    Same as with_rw_repo, but also provides a writable remote repository from which the
    rw_repo has been forked as well as a handle for a git-daemon that may be started to
    run the remote_repo.
    The remote repository was cloned as bare repository from the rorepo, wheras
    the rw repo has a working tree and was cloned from the remote repository.

    remote_repo has two remotes: origin and daemon_origin. One uses a local url,
    the other uses a server url. The daemon setup must be done on system level
    and should be an inetd service that serves tempdir.gettempdir() and all
    directories in it.

    The following scetch demonstrates this::
     rorepo ---<bare clone>---> rw_remote_repo ---<clone>---> rw_repo

    The test case needs to support the following signature::
        def case(self, rw_repo, rw_remote_repo)

    This setup allows you to test push and pull scenarios and hooks nicely.

    See working dir info in with_rw_repo
    :note: We attempt to launch our own invocation of git-daemon, which will be shutdown at the end of the test.
    """
    from git import Git, Remote  # To avoid circular deps.

    assert isinstance(working_tree_ref, string_types), "Decorator requires ref name for working tree checkout"

    def argument_passer(func):

        @wraps(func)
        def remote_repo_creator(self):
            remote_repo_dir = tempfile.mktemp("remote_repo_%s" % func.__name__)
            repo_dir = tempfile.mktemp("remote_clone_non_bare_repo")

            rw_remote_repo = self.rorepo.clone(remote_repo_dir, shared=True, bare=True)
            # recursive alternates info ?
            rw_repo = rw_remote_repo.clone(repo_dir, shared=True, bare=False, n=True)
            rw_repo.head.commit = working_tree_ref
            rw_repo.head.reference.checkout()

            # prepare for git-daemon
            rw_remote_repo.daemon_export = True

            # this thing is just annoying !
            with rw_remote_repo.config_writer() as crw:
                section = "daemon"
                try:
                    crw.add_section(section)
                except Exception:
                    pass
                crw.set(section, "receivepack", True)

            # Initialize the remote - first do it as local remote and pull, then
            # we change the url to point to the daemon.
            d_remote = Remote.create(rw_repo, "daemon_origin", remote_repo_dir)
            d_remote.fetch()

            base_path, rel_repo_dir = osp.split(remote_repo_dir)

            remote_repo_url = Git.polish_url("git://localhost:%s/%s" % (GIT_DAEMON_PORT, rel_repo_dir))
            with d_remote.config_writer as cw:
                cw.set('url', remote_repo_url)

            try:
                gd = launch_git_daemon(Git.polish_url(base_path), '127.0.0.1', GIT_DAEMON_PORT)
            except Exception as ex:
                if is_win:
                    msg = textwrap.dedent("""
                    The `git-daemon.exe` must be in PATH.
                    For MINGW, look into .\Git\mingw64\libexec\git-core\), but problems with paths might appear.
                    CYGWIN has no daemon, but if one exists, it gets along fine (has also paths problems)
                    Anyhow, alternatively try starting `git-daemon` manually:""")
                else:
                    msg = "Please try starting `git-daemon` manually:"
                msg += textwrap.dedent("""
                    git daemon --enable=receive-pack  --base-path=%s  %s
                You can also run the daemon on a different port by passing --port=<port>"
                and setting the environment variable GIT_PYTHON_TEST_GIT_DAEMON_PORT to <port>
                """ % (base_path, base_path))
                raise AssertionError(ex, msg)
                # END make assertion
            else:
                # Try listing remotes, to diagnose whether the daemon is up.
                rw_repo.git.ls_remote(d_remote)

                # adjust working dir
                prev_cwd = os.getcwd()
                os.chdir(rw_repo.working_dir)

                try:
                    return func(self, rw_repo, rw_remote_repo)
                except:
                    log.info("Keeping repos after failure: repo_dir = %s, remote_repo_dir = %s",
                             repo_dir, remote_repo_dir)
                    repo_dir = remote_repo_dir = None
                    raise
                finally:
                    os.chdir(prev_cwd)

            finally:
                try:
                    log.debug("Killing git-daemon...")
                    gd.proc.kill()
                except:
                    ## Either it has died (and we're here), or it won't die, again here...
                    pass

                rw_repo.git.clear_cache()
                rw_remote_repo.git.clear_cache()
                rw_repo = rw_remote_repo = None
                import gc
                gc.collect()
                if repo_dir:
                    rmtree(repo_dir)
                if remote_repo_dir:
                    rmtree(remote_repo_dir)

                if gd is not None:
                    gd.proc.wait()
            # END cleanup
        # END bare repo creator
        return remote_repo_creator
        # END remote repo creator
    # END argument parser

    return argument_passer

#} END decorators


class TestBase(TestCase):

    """
    Base Class providing default functionality to all tests such as:

    - Utility functions provided by the TestCase base of the unittest method such as::
        self.fail("todo")
        self.failUnlessRaises(...)

    - Class level repository which is considered read-only as it is shared among
      all test cases in your type.
      Access it using::
       self.rorepo  # 'ro' stands for read-only

      The rorepo is in fact your current project's git repo. If you refer to specific
      shas for your objects, be sure you choose some that are part of the immutable portion
      of the project history ( to assure tests don't fail for others ).
    """

    if not PY3:
        assertRaisesRegex = unittest.TestCase.assertRaisesRegexp

    def _small_repo_url(self):
        """:return" a path to a small, clonable repository"""
        from git.cmd import Git
        return Git.polish_url(osp.join(self.rorepo.working_tree_dir, 'git/ext/gitdb/gitdb/ext/smmap'))

    @classmethod
    def setUpClass(cls):
        """
        Dynamically add a read-only repository to our actual type. This way
        each test type has its own repository
        """
        from git import Repo
        import gc
        gc.collect()
        cls.rorepo = Repo(GIT_REPO)

    @classmethod
    def tearDownClass(cls):
        cls.rorepo.git.clear_cache()
        cls.rorepo.git = None

    def _make_file(self, rela_path, data, repo=None):
        """
        Create a file at the given path relative to our repository, filled
        with the given data. Returns absolute path to created file.
        """
        repo = repo or self.rorepo
        abs_path = osp.join(repo.working_tree_dir, rela_path)
        with open(abs_path, "w") as fp:
            fp.write(data)
        return abs_path
