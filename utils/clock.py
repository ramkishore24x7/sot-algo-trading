from datetime import datetime

import arrow
import pause


class Clock:
    def tictoc():
        return str(datetime.now()) + " : "

    def wait_until(hour, minute, second):
        startTime = arrow.now()
        EndAt = startTime.replace(hour = hour, minute = minute, second = second)
        print('Awaiting : ',EndAt,"\n")
        pause.until(EndAt.naive)
        pass

    def time_in_range(start, end, current):
        """Returns whether current is in the range [start, end]"""
        return start <= current <= end
    
    
    def is_time_less_than(hour: int, minute: int) -> bool:
        # Get the current time
        now = datetime.now()

        # Check if the current time is less than the input time
        if now.hour < hour or (now.hour == hour and now.minute < minute):
            return True
        else:
            return False