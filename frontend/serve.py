import os, sys
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.argv = ['server', '5200']
from http.server import SimpleHTTPRequestHandler, HTTPServer
HTTPServer(('', 5200), SimpleHTTPRequestHandler).serve_forever()
