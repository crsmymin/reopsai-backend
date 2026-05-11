"""Application package for the ReOpsAI backend."""


def __getattr__(name):
    if name == "create_app":
        from reopsai_backend.api.app_factory import create_app

        return create_app
    raise AttributeError(name)


__all__ = ["create_app"]
