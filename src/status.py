"""
Status helpers.
"""
import time

class Status(object):

	who = None
	status = None
	timestamp = None

	def __init__(self, who, status):
		self.who = who
		self.status = status
		self.timestamp = time.time()
