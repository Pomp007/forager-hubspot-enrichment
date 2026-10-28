# app/__init__.py
from flask import Flask
from .config import Config


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    from .webhooks import webhooks_bp
    from .api import api_bp

    app.register_blueprint(webhooks_bp, url_prefix="/webhooks")
    app.register_blueprint(api_bp, url_prefix="/api")

    return app
