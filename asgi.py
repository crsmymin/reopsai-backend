from a2wsgi import WSGIMiddleware
from app import app

application = WSGIMiddleware(app)