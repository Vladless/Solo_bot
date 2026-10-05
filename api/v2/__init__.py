VERSION = "2.0.0"
__all__ = ("router", "VERSION")


def __getattr__(name: str):
    if name == "router":
        from api.v2.router import router

        return router
    raise AttributeError(name)
