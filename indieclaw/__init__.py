from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("indieclaw")
except PackageNotFoundError:
    __version__ = "dev"
