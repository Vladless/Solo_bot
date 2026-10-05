__all__ = ("root_router",)


def __getattr__(name: str):
    if name == "root_router":
        from api.v2.routes.root import router

        return router
    raise AttributeError(name)
