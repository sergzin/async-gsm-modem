
def fullname(o):
    """
    Print Full class name. Useful in logging.
    >>> a = asyncio.exceptions.TimeoutError()
    >>> a
    TimeoutError()
    >>> fullname(a)
    'asyncio.exceptions.TimeoutError'

    credit goes to https://stackoverflow.com/a/13653312/5405967
    """
    module = o.__class__.__module__
    if module is None or module == str.__class__.__module__:
        return o.__class__.__name__
    return module + '.' + o.__class__.__name__