"""
A basic non-caching proxy.

Features:
* multi-process architecture

Architecture:
* one listener listens on a port and places requests on a request queue
* one or more connect workers make the connections
* several workers forward the packets. One worker can serve multiple connections.
"""

from multiprocessing.reduction import reduce_handle, rebuild_handle
import select

LISTEN_ADDRESS = '127.0.0.1'
LISTEN_PORT = 8888

import sys
import socket
import signal
import re

from multiprocessing import Process, JoinableQueue

running = True

def signalHandler(signum, frame):
	global running
	print("Received %d signal" % signum)
	if signum == signal.SIGTERM:
		running = False
		print("Quiting")


class Worker(object):
	"""
	Base class for workers.
	"""

	def __init__(self, name):
		self.name = name

	def __str__(self):
		return self.name

	def work(self):
		raise NotImplementedError()


class ConnectWorker(Worker):
	"""
	Handles establishing the connection between the target and the server

	From https://www.ietf.org/rfc/rfc2817.txt:

	5.2 Requesting a Tunnel with CONNECT

   	A CONNECT method requests that a proxy establish a tunnel connection
   	on its behalf. The Request-URI portion of the Request-Line is always
   	an 'authority' as defined by URI Generic Syntax [2], which is to say
   	the host name and port number destination of the requested connection
   	separated by a colon:

      CONNECT server.example.com:80 HTTP/1.1
      Host: server.example.com:80
	"""

	def __init__(self, name, connectRequestsQueue, forwardingQueue):
		Worker.__init__(self, "Connect worker " + name)
		self.connectRequestsQueue = connectRequestsQueue
		self.forwardingQueue = forwardingQueue

	def connectTo(self, host, port):
		serverSocket = socket.socket(socket.AF_INET, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
		serverSocket.connect((host, port))
		return serverSocket

	def work(self):
		print("%s started working..." % self)

		clientHandle, clientAddr = self.connectRequestsQueue.get()
		print("working with: ", clientAddr)
		fd = rebuild_handle(clientHandle)
		clientSocket = socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
		all = ""
		while 1:
			buf = clientSocket.recv(1024)
			print("Received %s" % buf)
			if buf:
				match = re.search('CONNECT ([^ ]*):([0-9]*) HTTP/1.1', buf)
				if match:
					host = match.group(1)
					port = long(match.group(2))
					print("Connect requested to: %s:%s" % (host, port))
					serverSocket = self.connectTo(host, port)
					clientSocket.sendall("HTTP/1.1 200 Connection established\r\nProxy-Agent: Proxy.PY/0.1\r\n\r\n")
					self.forwardingQueue.put( (reduce_handle(clientSocket.fileno()),
											   reduce_handle(serverSocket.fileno())) )
					break
				else:
					all.append(buf)
			else:
				clientSocket.close()
				break

		self.forwardingQueue.join()
		print("%s quitting..." % self)


class ForwardingWorker(Worker):
	"""
	Handles packets' forwarding between client and server.
	"""

	def __init__(self, name, forwardingQueue):
		Worker.__init__(self, "Forward worker "+ name)
		self.forwardingQueue = forwardingQueue

	def work(self):
		print("%s started working..." % self)

		clientHandle, serverHandle = self.forwardingQueue.get()
		clientSocket = socket.fromfd(rebuild_handle(clientHandle), socket.AF_INET, socket.SOCK_STREAM)
		serverSocket = socket.fromfd(rebuild_handle(serverHandle), socket.AF_INET, socket.SOCK_STREAM)
		self.forwardingQueue.task_done()
		print("Have a client")
		sockets = [clientSocket, serverSocket]
		while 1:
			try:
				readables, writables, exceptions = select.select(sockets, [], [])
			except ValueError, why:
				sys.stderr.write(str(why))
				clientSocket.close()
				serverSocket.close()
				break

			for readable in readables:
				if readable is clientSocket:
					buf = clientSocket.recv(1024)
					if buf:
						serverSocket.sendall(buf)
					else:
						clientSocket.close()
						serverSocket.close()
						break
				elif readable is serverSocket:
					buf = serverSocket.recv(1024)
					if buf:
						clientSocket.sendall(buf)
					else:
						clientSocket.close()
						serverSocket.close()
						break
		print("%s quitting...")


def main():

	signal.signal(signal.SIGTERM, signalHandler)

	connectRequestsQueue = JoinableQueue(20)
	forwardingQueue = JoinableQueue(20)

	processes = []
	print("Starting workers...")
	workers = [ ConnectWorker("first", connectRequestsQueue, forwardingQueue),
				ForwardingWorker("first", forwardingQueue)
			  ]
	for worker in workers:
		p = Process(target=worker.work, args=())
		processes.append(p)
		p.start()

	listeningSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
	listeningSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

	listeningSocket.bind((LISTEN_ADDRESS, LISTEN_PORT))
	listeningSocket.listen(1)

	try:
		while running:
			clientSocket, clientAddress = listeningSocket.accept()
			connectRequestsQueue.put(((reduce_handle(clientSocket.fileno())), clientAddress))
	except KeyboardInterrupt:
		print("Quitting... requested by user")
	finally:
		listeningSocket.close()

	#wait for all of the child processes
	# TODO: should stop the processes if not quitting not user requested
	print("Waiting for child processes...")
	for p in processes:
		p.join()
	print("Done!")

	return 0

if __name__ == "__main__":
	sys.exit(main())




