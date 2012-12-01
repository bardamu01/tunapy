from multiprocessing.reduction import rebuild_handle, reduce_handle
import socket
import sys
import re

HOST_RE = re.compile('Host: ([^ :\r\n]*)(:[0-9]{1,5})?')
CONNECT_RE = re.compile('CONNECT ([^ ]*):([1-9]{1,5}) HTTP/1.1')


def getAddressFromBuffer(buf):
	"""
	Returns the host,port found in a buffer.
	"""
	if buf.find("Host:") > 0:
		match = HOST_RE.search(buf)
		if match:
			host = match.group(1)
			port = 80
			return host,port
	return None, None


class Socket(socket.SocketType):
	"""
	A socket with tx/rx statistics.
	"""
	tx = 0
	rx = 0

	def __init__(self, *args, **kwargs):
		socket.SocketType.__init__(self, *args, **kwargs)

	def recv(self, *args, **kwargs):
		buf = socket.SocketType.recv(self,*args, **kwargs)
		if buf:
			self.rx+=len(buf)

	def sendall(self, data, **kwargs):
		self.tx+=len(data)
		return socket.SocketType.sendall(self, data, **kwargs)


class Endpoint(object):
	"""
	Abstracts one half of a connection.
	"""

	socket = None
	address = None
	handle = None

	def __init__(self, realsocket, address):
		self.socket = realsocket
		self.address = address

	def __str__(self):
		return str(self.address)

	def shutdown(self, msgToSend=None):
		try:
			if msgToSend:
				self.socket.sendall(msgToSend)
			self.socket.shutdown(socket.SHUT_RDWR)
		except socket.error, why:
			sys.stderr.write("Failure while shutting down %s: %s" % (self, why.message))
		self.socket.close()

	def rebuild(self):
		self.socket = socket.fromfd(rebuild_handle(self.handle), socket.AF_INET, socket.SOCK_STREAM)
		self.handle = None
		return self

	def reduce(self):
		self.handle = reduce_handle(self.socket.fileno())
		self.socket = None
		return self

	@staticmethod
	def connectTo(host, port):
		realsocket = socket.socket(socket.AF_INET, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
		realsocket.connect((host, port))
		return Endpoint(realsocket, (host,port))


class Connection(object):
	"""
	Abstracts a client - server connection.
	"""

	client = None
	server = None

	def __init__(self, client, server):
		self.client = client
		self.server = server

	def __str__(self):
		return "%s -> %s " % (self.client, self.server)

	def reduce(self):
		""" Prepares for serialization """
		self.client.reduce()
		self.server.reduce()
		return self

	def rebuild(self):
		self.client.rebuild()
		self.server.rebuild()
		return self

	def close(self):
		self.client.shutdown()
		self.server.shutdown()