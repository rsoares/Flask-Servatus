# -*- coding: utf-8 -*-
"""
Copyright (c) Django Software Foundation and individual contributors.
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
notice, this list of conditions and the following disclaimer in the
documentation and/or other materials provided with the distribution.

3. Neither the name of Django nor the names of its contributors may be used
to endorse or promote products derived from this software without
specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

    flask_servatus.storages
    ---------------

    Defines the Base Storage used in `flask_servatus`.

    This is a port of Django's Storage sytsem.  It defined the base
    :class:`.Storage` that new storage systems should implement and a
    default :class:`.FileSystemStorage` class.

    Some changes have been made in places to use `werkzurgs` file system
    utilites in place of methods from django.

"""

import errno
import os
import itertools

from urlparse import urljoin
from urllib import quote
from io import UnsupportedOperation

from flask import current_app, safe_join

from werkzeug import secure_filename
from werkzeug.exceptions import NotFound

from flask_servatus.exceptions import SuspiciousFileOperation
from flask_servatus import locks


def chunked_iterator(content, chunk_size=None):
    """
    Read the file and yield chucks of ``chunk_size`` bytes (defaults to
    ``UploadedFile.DEFAULT_CHUNK_SIZE``).
    """
    if not chunk_size:
        chunk_size = 64 * 2 ** 10

    try:
        content.seek(0)
    except (AttributeError, UnsupportedOperation):
        pass

    while True:
        data = content.read(chunk_size)
        if not data:
            break
        yield data


def filepath_to_uri(path):
    if path is None:
        return path
    return quote(path.replace(b"\\", b"/"), safe=b"/~!*()'")


class Storage(object):
    """
    """

    def open(self, name, mode='rb'):
        """
        Retrieves the specified file from storage.
        """
        return self._open(name, mode)

    def save(self, name, content):
        """Saves new content to the file specified by name.  The ``content``
        should be any python file-like object ready to be read from
        the beginning.

        :param str name: name of the file being saved
        :param content: file-like object

        :returns: the name of the saved file
        """

        name = self.get_available_name(name)
        name = self._save(name, content)

        return name

    def get_available_name(self, name):
        """Returns a filename that's free on the target storage system, and
        available for new content to be written to.

        :param name: name of file being saved
        """

        dir_name, file_name = os.path.split(secure_filename(name))
        file_root, file_ext = os.path.splitext(file_name)
        # If the filename already exists, add an underscore and a number (before
        # the file extension, if one exists) to the filename until the generated
        # filename doesn't exist.
        count = itertools.count(1)
        while self.exists(name):
            # file_ext includes the dot.
            name = os.path.join(dir_name, "%s_%s%s" % (file_root,
                                                       next(count),
                                                       file_ext))
        return name


class FileSystemStorage(Storage):
    """
    """

    def __init__(self, location=None, base_url=None, file_permissions_mode=None,
                 directory_permissions_mode=None):

        self.base_url = base_url
        if base_url is None:
            self.base_url = current_app.config['SERVATUS_MEDIA_URL']

        self.location = location
        if location is None:
            self.location = current_app.config['SERVATUS_MEDIA_ROOT']

        self.file_permissions_mode = (
            file_permissions_mode if file_permissions_mode is not None
            else current_app.config['SERVATUS_FILE_UPLOAD_PERMISSIONS']
        )

        self.directory_permissions_mode = (
            directory_permissions_mode if directory_permissions_mode is not None
            else current_app.config['SERVATUS_UPLOAD_DIR_PERMISSIONS']
        )

    def path(self, name):
        """return file path for ``name`` using self.location. Calls
        :func:`werkzeug.security.safe_join`` to ensure `name` can be safely
        joined with self.location

        :param name: name of file

        :returns: path to file
        """

        try:
            path = safe_join(self.location, name)
        except NotFound:
            raise SuspiciousFileOperation("Attempted access to "
                                          "'%s' denied." % name)

        return os.path.normpath(path)

    def exists(self, name):
        """return a boolean indicating wether a file named ``name`` exists at
        :meth:``path``

        :returns: True if the file exists, False otherwise
        """
        return os.path.exists(self.path(name))

    def delete(self, name):
        """delete file named ``name`` from the filesystem if it exists

        :param name: name of the file being removed

        :returns: None
        """
        assert name, "The name argument is not allowed to be empty."
        name = self.path(name)

        if os.path.exists(name):
            try:
                os.remove(name)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    raise

    def _open(self, name, mode='rb'):
        return open(self.path(name), mode)

    def _save(self, name, content):
        """create a new file named using ``name`` from ``content``

        :param name: name of the file being saved
        :param content: :obj:`werkzeurg.datastructures.FileStorage` or file-like obj

        :returns: ``name`` used to save file
        """

        full_path = self.path(name)
        directory = os.path.dirname(full_path)

        directory = os.path.dirname(full_path)
        if not os.path.exists(directory):
            try:
                if self.directory_permissions_mode is not None:
                    # os.makedirs applies the global umask, so we reset it,
                    # for consistency with file_permissions_mode behavior.
                    old_umask = os.umask(0)
                    try:
                        os.makedirs(directory, self.directory_permissions_mode)
                    finally:
                        os.umask(old_umask)
                else:
                    os.makedirs(directory)
            except OSError as e:
                if e.errno != errno.EEXIST:
                    raise

        if not os.path.isdir(directory):
            raise IOError("%s exists and is not a directory." % directory)

        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, 'O_BINARY', 0))

        # The current umask value is masked out by os.open!
        fd = os.open(full_path, flags, 0o666)
        _file = None

        try:
            locks.lock(fd, locks.LOCK_EX)
            for chunk in chunked_iterator(content):
                if _file is None:
                    mode = 'wb' if isinstance(chunk, bytes) else 'wt'
                    _file = os.fdopen(fd, mode)
                _file.write(chunk)
        finally:
            content.close()
            locks.unlock(fd)
            if _file is not None:
                _file.close()
            else:
                os.close(fd)

        if self.file_permissions_mode is not None:
            os.chmod(full_path, self.file_permissions_mode)

        return name

    def url(self, name):
        if self.base_url is None:
            raise ValueError("This file is not accessible via a URL.")
        return urljoin(self.base_url, filepath_to_uri(name))

    def size(self, name):
        return os.path.getsize(self.path(name))
