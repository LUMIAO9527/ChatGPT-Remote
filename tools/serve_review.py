"""Serve only the review files. This never starts the Windows Agent."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('--host',default='127.0.0.1')
parser.add_argument('--port',type=int,default=4173)
parser.add_argument('--strictPort',action='store_true')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/', '/ChatGPT-Remote-Preview.html'):
            self.send_error(404)
            return
        self.path='/ChatGPT-Remote-Preview.html'
        super().do_GET()
    def log_message(self,*args):pass
server=ThreadingHTTPServer((args.host,args.port),partial(Handler,directory=str(root)))
print(f'Review server listening on port {args.port}',flush=True)
server.serve_forever()
