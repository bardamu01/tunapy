"""
A basic non-caching proxy.

Features:
* multi-process architecture

Architecture:
* one listener listens on a port and places requests on a request queue
* one or more connect workers make the connections
* several workers forward the packets. One worker can serve multiple connections.

"""
from optparse import OptionParser
import sys
import socket
import signal

from multiprocessing import Process, JoinableQueue

from net import Socket, Endpoint
from monitor import MonitorWorker
from workers import SwitchWorker, ForwardingWorker, ProxyWorker

LISTEN_ADDRESS = '127.0.0.1'
LISTEN_PORT = 8888

running = True

def signalHandler(signum, frame):
	global running
	print("Received %d signal from %s" % (signum, frame))
	if signum == signal.SIGTERM:
		running = False
		print("Quiting")


class ProxyOptions(OptionParser):
	def __init__(self):
		OptionParser.__init__(self)
		self.add_option("-p", "--port",  type=int,
						help="port to listen on, default %s" % LISTEN_PORT)
		self.add_option("-l", "--listen", metavar="ADDRESS",
						help="address to listen on, default %s" % LISTEN_ADDRESS)
		self.add_option("-f", "--forward", metavar="HOST:PORT",
						help="forward to the next proxy server")


def main():
	options, remainingArgs = ProxyOptions().parse_args()
	listenAddress = options.listen or LISTEN_ADDRESS
	listenPort = options.port or LISTEN_PORT

	proxy = ()
	if options.forward:
		split = options.forward.split(":")
		proxy = split[0],long(split[1])
		print("Will forward to: " + options.forward)

	signal.signal(signal.SIGTERM, signalHandler)

	connectRequestsQueue = JoinableQueue(20)
	forwardingQueue = JoinableQueue(20)
	proxyingQueue = JoinableQueue(20)
	statusQueue = JoinableQueue(20)

	processes = []
	print("Starting workers...")
	workers = [ SwitchWorker("Adam", connectRequestsQueue, forwardingQueue, proxyingQueue),
				ForwardingWorker("Fred", forwardingQueue),
				ProxyWorker("Perseus", proxyingQueue),
				ProxyWorker("Penelope1", proxyingQueue),
				ProxyWorker("Penelope2", proxyingQueue),
				ProxyWorker("Penelope3", proxyingQueue),
				MonitorWorker("Mo"),
			]
	for worker in workers:
		if isinstance(worker, SwitchWorker):
			worker.proxy = proxy
		worker.statusQueue = statusQueue
		p = Process(target=worker.work, args=())
		processes.append(p)
		p.start()

	listeningSocket = Socket(socket.AF_INET, socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
	listeningSocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

	listeningSocket.bind((listenAddress, listenPort))
	listeningSocket.listen(10)
	print("Listening on %s:%d" % (listenAddress, listenPort))

	try:
		while running:
			clientSocket, clientAddress = listeningSocket.accept()
			client = Endpoint(clientSocket, clientAddress)
			connectRequestsQueue.put(client.reduce())
	except KeyboardInterrupt:
		print("Quitting... requested by user")
	finally:
		listeningSocket.close()

	#wait for all of the child processes
	# TODO: should stop the processes if quitting not user requested
	print("Waiting for child processes...")
	for p in processes:
		p.join()
	print("Done!")

	return 0

if __name__ == "__main__":
	sys.exit(main())




