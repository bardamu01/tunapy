import copy
import unittest
from httputil import HttpRequest

class TestHttpRequest(unittest.TestCase):


	def setUp(self):
		self.buffer ="""GET http://www.serbanradulescu.ro/new/proiectele-mele/ HTTP/1.1\r
Host: www.serbanradulescu.ro\r
User-Agent: Mozilla/5.0 (X11; Linux x86_64; rv:17.0) Gecko/20100101 Firefox/17.0\r
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8\r
Accept-Language: en-US,en;q=0.5\r
Accept-Encoding: gzip, deflate\r
Connection: keep-alive\r
Cookie: __utma=256668681.8295899.1354958517.1354961422.1354992154.3; __utmz=256668681.1354958517.1.1.utmcsr=(direct)|utmccn=(direct)|utmcmd=(none); __utmb=256668681.6.10.1354992154; __utmc=256668681\r
Cache-Control: max-age=0\r\n\r\n"""

		self.httpRequest = HttpRequest.buildFromBuffer(self.buffer)

	def test_buildFromBuffer(self):
		httpRequest = self.httpRequest
		self.assertEquals("GET", httpRequest.requestType)
		self.assertEquals("http://www.serbanradulescu.ro/new/proiectele-mele/", httpRequest.requestedResource)
		self.assertTrue(httpRequest.options["Cache-Control"] == "max-age=0")

	def test_makeRelative(self):
		httpRequest = copy.copy(self.httpRequest)
		httpRequest.makeRelative()
		self.assertEquals("/new/proiectele-mele/", httpRequest.requestedResource)

	def test_toBuffer(self):
		self.assertEquals(self.httpRequest.toBuffer(), self.buffer)

