from functools import wraps
from time import sleep


def retry(retries=3, duration=10):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for _ in range(retries):
                try:
                    result = func(*args, **kwargs)
                except:
                    sleep(duration)
                    continue
                return result
            return func(*args, **kwargs)

        return wrapper

    return decorator
