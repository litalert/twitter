try:
    import urllib.request as urllib_request
    import urllib.error as urllib_error
    import io
except ImportError:
    import urllib2 as urllib_request
    import urllib2 as urllib_error
import json
from ssl import SSLError
import socket
import sys, select, time

from .api import TwitterCall, wrap_response, TwitterHTTPError

class TwitterJSONIter(object):

    def __init__(self, handle, uri, arg_data, block=True, timeout=None):
        self.decoder = json.JSONDecoder()
        self.handle = handle
        self.uri = uri
        self.arg_data = arg_data
        self.buf = b""
        self.block = block
        self.timeout = timeout
        self.timer = time.time()


    def recv_chunk(self, sock):
        buf = sock.recv(32)
        if buf:
            # Find the HTTP chunk size.
            crlf = buf.find(b'\r\n')
            if crlf > 0:
                remaining = int(buf[:crlf].decode(), 16)  # Decode the chunk size.
                chunk = bytearray(buf[crlf + 2:])  # Add the length of the length header CRLF pair.
                remaining -= len(chunk)

                while remaining > 0:
                    balance = sock.recv(remaining + 2)  # Add the length of the chunk's CRLF pair.
                    if balance:
                        chunk.extend(balance)
                        remaining -= len(balance)
                # If possible, remove the trailing CRLF pair. (This precludes an extra trip through the JSON parser.)
                if remaining == -2 and chunk[-2] == 0x0d and chunk[-1] == 0x0a:
                    del chunk[-2:]
                return chunk
        return b''


    def __iter__(self):
        if sys.version_info >= (3, 0):
            sock = self.handle.fp.raw._sock
        else:
            sock = self.handle.fp._sock.fp._sock
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if not self.block or self.timeout:
            sock.setblocking(False)
        while True:
            try:
                utf8_buf = self.buf.decode('utf8').lstrip()
                res, ptr = self.decoder.raw_decode(utf8_buf)
                self.buf = utf8_buf[ptr:].encode('utf8')
                yield wrap_response(res, self.handle.headers)
                self.timer = time.time()
                continue
            except ValueError as e:
                if self.block:
                    pass
                else:
                    yield None
            # this is a non-blocking read (ie, it will return if any data is available)
            try:
                if self.timeout:
                    ready_to_read = select.select([sock], [], [], self.timeout)
                    if ready_to_read[0]:
                        self.buf += self.recv_chunk(sock)
                        if time.time() - self.timer > self.timeout:
                            yield {"timeout":True}
                    else:
                        yield {"timeout":True}
                else:
                    self.buf += self.recv_chunk(sock)
            except SSLError as e:
                if (not self.block or self.timeout) and (e.errno == 2):
                    # Apparently this means there was nothing in the socket buf
                    pass
                else:
                    raise
            except urllib_error.HTTPError as e:
                raise TwitterHTTPError(e, self.uri, 'json', self.arg_data)

def handle_stream_response(req, uri, arg_data, block, timeout=None):
    handle = urllib_request.urlopen(req,)
    return iter(TwitterJSONIter(handle, uri, arg_data, block, timeout=timeout))

class TwitterStreamCallWithTimeout(TwitterCall):
    def _handle_response(self, req, uri, arg_data, _timeout=None):
        return handle_stream_response(req, uri, arg_data, block=True, timeout=self.timeout)

class TwitterStreamCall(TwitterCall):
    def _handle_response(self, req, uri, arg_data, _timeout=None):
        return handle_stream_response(req, uri, arg_data, block=True)

class TwitterStreamCallNonBlocking(TwitterCall):
    def _handle_response(self, req, uri, arg_data, _timeout=None):
        return handle_stream_response(req, uri, arg_data, block=False)

class TwitterStream(TwitterStreamCall):
    """
    The TwitterStream object is an interface to the Twitter Stream API
    (stream.twitter.com). This can be used pretty much the same as the
    Twitter class except the result of calling a method will be an
    iterator that yields objects decoded from the stream. For
    example::

        twitter_stream = TwitterStream(auth=OAuth(...))
        iterator = twitter_stream.statuses.sample()

        for tweet in iterator:
            ...do something with this tweet...

    The iterator will yield tweets forever and ever (until the stream
    breaks at which point it raises a TwitterHTTPError.)

    The `block` parameter controls if the stream is blocking. Default
    is blocking (True). When set to False, the iterator will
    occasionally yield None when there is no available message.
    """
    def __init__(
        self, domain="stream.twitter.com", secure=True, auth=None,
        api_version='1.1', block=True, timeout=None):
        uriparts = ()
        uriparts += (str(api_version),)

        if block:
            if timeout:
                call_cls = TwitterStreamCallWithTimeout
            else:
                call_cls = TwitterStreamCall
        else:
            call_cls = TwitterStreamCallNonBlocking

        TwitterStreamCall.__init__(
            self, auth=auth, format="json", domain=domain,
            callable_cls=call_cls,
            secure=secure, uriparts=uriparts, timeout=timeout, gzip=False)
