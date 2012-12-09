import socket
import sys

from multiprocessing.reduction import reduce_socket


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

	def __init__(self, realsocket, address):
		self.socket = realsocket
		# might be redundant because of realsocket.getpeername()
		# but at least we see host names not IPs
		self.address = address

	def __str__(self):
		return "%s:%s [%d]" % (self.address[0], self.address[1], self.socket.fileno())

	def shutdown(self, msgToSend=None):
		try:
			if msgToSend:
				self.socket.sendall(msgToSend)
			self.socket.shutdown(socket.SHUT_RDWR)
		except socket.error, why:
			sys.stderr.write("Failure while shutting down %s: %s" % (self, why.message))
		finally:
			self.socket.close()

	def rebuild(self):
		meth, args = self.socket
		self.socket = meth(*args)
		return self

	def reduce(self):
		self.socket = reduce_socket(self.socket)
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