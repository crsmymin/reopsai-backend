"""Flask extension singletons.

Extensions are created without an app and initialized inside the app factory.
This keeps import-time behavior compatible while making tests able to create
isolated Flask app instances.
"""

from flask_jwt_extended import JWTManager


jwt = JWTManager()
