#!/usr/bin/env python
# coding: utf-8
#
# Copyright 2010 Alexandre Fiori
# based on the original Tornado by Facebook
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from twisted.python import log
from twisted.protocols import basic
from twisted.internet import defer, protocol
import cgi
import errno
import fcntl
import functools
import time
import urlparse

class HTTPConnection(basic.LineReceiver):
    """Handles a connection to an HTTP client, executing HTTP requests.

    We parse HTTP headers and bodies, and execute the request callback
    until the HTTP conection is closed.
    """
    delimiter = "\r\n"

    def connectionMade(self):
        self._headersbuffer = []
        self._contentbuffer = []
        self._finish_callback = None
        self.no_keep_alive = False
        self.content_length = None
        self.request_callback = self.factory
        self.xheaders = self.factory.settings.get('xheaders', False)
        self._request = None
        self._request_finished = False

    def connectionLost(self, reason):
        if self._finish_callback:
            self._finish_callback.callback(reason.getErrorMessage())
            self._finish_callback = None
    
    def notifyFinish(self):
        if self._finish_callback is None:
            self._finish_callback = defer.Deferred()
        return self._finish_callback

    def lineReceived(self, line):
        if line:
            self._headersbuffer.append(line+self.delimiter)
        else:
            self._on_headers(''.join(self._headersbuffer))
            self._headersbuffer = []
    
    def rawDataReceived(self, data):
        if self.content_length is not None:
            data, rest = data[:self.content_length], data[self.content_length:]
            self.content_length -= len(data)
        else:
            rest = ''
	
        self._contentbuffer.append(data)
        if self.content_length == 0:
            self._on_request_body(''.join(self._contentbuffer))
            self._contentbuffer = []
            self.content_length = None
            self.setLineMode(rest)
	    
    def write(self, chunk):
        assert self._request, "Request closed"
        self.transport.write(chunk)

    def finish(self):
        assert self._request, "Request closed"
        self._request_finished = True
        self._finish_request()

    def _on_write_complete(self):
        if self._request_finished:
            self._finish_request()

    def _finish_request(self):
        if self.no_keep_alive:
            disconnect = True
        else:
            connection_header = self._request.headers.get("Connection")
            if self._request.supports_http_1_1():
                disconnect = connection_header == "close"
            elif ("Content-Length" in self._request.headers
                    or self._request.method in ("HEAD", "GET")):
                disconnect = connection_header != "Keep-Alive"
            else:
                disconnect = True
        self._request = None
        self._request_finished = False
        if disconnect:
            self.transport.loseConnection()
            return

    def _on_headers(self, data):
        eol = data.find("\r\n")
        start_line = data[:eol]
        try:
            method, uri, version = start_line.split(" ")
        except:
            log.err("Malformed HTTP request: %s" % repr(start_line)[:100])
            return self.transport.loseConnection()
        if not version.startswith("HTTP/"):
            #raise Exception("Malformed HTTP version in HTTP Request-Line")
            log.err("Malformed HTTP version in HTTP Request-Line: %s" % repr(start_line)[:100])
            return self.transport.loseConnection()
        try:
            headers = HTTPHeaders.parse(data[eol:])
        except:
            log.err("Malformed HTTP headers: %s" % repr(data[eol:][:100]))
            return self.transport.loseConnection()
        self._request = HTTPRequest(
            connection=self, method=method, uri=uri, version=version,
            headers=headers, remote_ip=self.transport.getPeer().host)

        content_length = int(headers.get("Content-Length", 0))
        if content_length:
            if headers.get("Expect") == "100-continue":
                self.transport.write("HTTP/1.1 100 (Continue)\r\n\r\n")
            self.content_length = content_length
            self.setRawMode()
            return

        self.request_callback(self._request)

    def _on_request_body(self, data):
        self._request.body = data
        content_type = self._request.headers.get("Content-Type", "")
        if self._request.method == "POST":
            if content_type.startswith("application/x-www-form-urlencoded"):
                arguments = cgi.parse_qs(self._request.body)
                for name, values in arguments.iteritems():
                    values = [v for v in values if v]
                    if values:
                        self._request.arguments.setdefault(name, []).extend(values)
            elif content_type.startswith("multipart/form-data"):
                boundary = content_type[30:]
                if boundary:
                    self._parse_mime_body(boundary, data)
        self.request_callback(self._request)

    def _parse_mime_body(self, boundary, data):
        if data.endswith("\r\n"):
            footer_length = len(boundary) + 6
        else:
            footer_length = len(boundary) + 4
        parts = data[:-footer_length].split("--" + boundary + "\r\n")
        for part in parts:
            if not part: continue
            eoh = part.find("\r\n\r\n")
            if eoh == -1:
                log.err("multipart/form-data missing headers")
                continue
            headers = HTTPHeaders.parse(part[:eoh])
            name_header = headers.get("Content-Disposition", "")
            if not name_header.startswith("form-data;") or \
               not part.endswith("\r\n"):
                log.err("Invalid multipart/form-data")
                continue
            value = part[eoh + 4:-2]
            name_values = {}
            for name_part in name_header[10:].split(";"):
                name, name_value = name_part.strip().split("=", 1)
                name_values[name] = name_value.strip('"').decode("utf-8")
            if not name_values.get("name"):
                log.err("multipart/form-data value missing name")
                continue
            name = name_values["name"]
            if name_values.get("filename"):
                ctype = headers.get("Content-Type", "application/unknown")
                self._request.files.setdefault(name, []).append(dict(
                    filename=name_values["filename"], body=value,
                    content_type=ctype))
            else:
                self._request.arguments.setdefault(name, []).append(value)


class HTTPRequest(object):
    """A single HTTP request.

    GET/POST arguments are available in the arguments property, which
    maps arguments names to lists of values (to support multiple values
    for individual names). Names and values are both unicode always.

    File uploads are available in the files property, which maps file
    names to list of files. Each file is a dictionary of the form
    {"filename":..., "content_type":..., "body":...}. The content_type
    comes from the provided HTTP header and should not be trusted
    outright given that it can be easily forged.

    An HTTP request is attached to a single HTTP connection, which can
    be accessed through the "connection" attribute. Since connections
    are typically kept open in HTTP/1.1, multiple requests can be handled
    sequentially on a single connection.
    """
    def __init__(self, method, uri, version="HTTP/1.0", headers=None,
                 body=None, remote_ip=None, protocol=None, host=None,
                 files=None, connection=None):
        self.method = method
        self.uri = uri
        self.version = version
        self.headers = headers or HTTPHeaders()
        self.body = body or ""
        if connection and connection.xheaders:
            # Squid uses X-Forwarded-For, others use X-Real-Ip
            self.remote_ip = self.headers.get("X-Real-Ip",
                self.headers.get("X-Forwarded-For", remote_ip))
            self.protocol = self.headers.get("X-Scheme", protocol) or "http"
        else:
            self.remote_ip = remote_ip
            self.protocol = protocol or "http"
        self.host = host or self.headers.get("Host") or "127.0.0.1"
        self.files = files or {}
        self.connection = connection
        self._start_time = time.time()
        self._finish_time = None

        scheme, netloc, path, query, fragment = urlparse.urlsplit(uri)
        self.path = path
        self.query = query
        arguments = cgi.parse_qs(query)
        self.arguments = {}
        for name, values in arguments.iteritems():
            values = [v for v in values if v]
            if values:
                self.arguments[name] = values

    def supports_http_1_1(self):
        """Returns True if this request supports HTTP/1.1 semantics"""
        return self.version == "HTTP/1.1"

    def write(self, chunk):
        """Writes the given chunk to the response stream."""
        assert isinstance(chunk, str)
        self.connection.write(chunk)

    def finish(self):
        """Finishes this HTTP request on the open connection."""
        self.connection.finish()
        self._finish_time = time.time()

    def full_url(self):
        """Reconstructs the full URL for this request."""
        return self.protocol + "://" + self.host + self.uri

    def request_time(self):
        """Returns the amount of time it took for this request to execute."""
        if self._finish_time is None:
            return time.time() - self._start_time
        else:
            return self._finish_time - self._start_time

    def notifyFinish(self):
        return self.connection.notifyFinish()

    def __repr__(self):
        attrs = ("protocol", "host", "method", "uri", "version", "remote_ip",
                 "remote_ip", "body")
        args = ", ".join(["%s=%r" % (n, getattr(self, n)) for n in attrs])
        return "%s(%s, headers=%s)" % (
            self.__class__.__name__, args, dict(self.headers))


class HTTPHeaders(dict):
    """A dictionary that maintains Http-Header-Case for all keys."""
    def __setitem__(self, name, value):
        dict.__setitem__(self, self._normalize_name(name), value)

    def __getitem__(self, name):
        return dict.__getitem__(self, self._normalize_name(name))

    def _normalize_name(self, name):
        return "-".join([w.capitalize() for w in name.split("-")])

    @classmethod
    def parse(cls, headers_string):
        headers = cls()
        for line in headers_string.splitlines():
            if line:
                name, value = line.split(":", 1)
                headers[name] = value[1:] if value[0] == " " else value
        return headers
