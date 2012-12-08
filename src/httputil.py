import re

CONNECT_RE = re.compile('CONNECT ([^ ]*):([1-9]{1,5}) HTTP/1.1')

HTTP_REQUEST_ENDING = "\r\n\r\n"

class HttpRequest(object):

	requestType = None
	requestedResource = None
	protocol = None
	options = None
	keys = None

	def __init__(self):
		self.keys = []
		self.options = dict()

	@staticmethod
	def buildFromBuffer(buffer):
		httpRequest = HttpRequest()

		ending = buffer.find(HTTP_REQUEST_ENDING)
		if ending <= 0:
			raise ValueError(buffer)

		lines = buffer[0:ending].splitlines()
		httpRequest.requestType, httpRequest.requestedResource, httpRequest.protocol = lines[0].split(" ")

		for line in lines[1:]:
			option, value = line.split(": ")
			httpRequest.options[option] = value
			httpRequest.keys.append(option)

		return httpRequest

	def makeRelative(self):
		rr = self.requestedResource
		pos = rr.find("://")
		if pos < 0:
			return
		uri = rr[pos+3:]
		self.requestedResource = uri[uri.find("/"):]

	def toBuffer(self):
		lines  = ["%s %s %s" % (self.requestType, self.requestedResource, self.protocol)]
		for key in self.keys:
			lines.append("%s: %s" % (key, self.options[key]))
		lines.append("\r\n")
		return "\r\n".join(lines)




