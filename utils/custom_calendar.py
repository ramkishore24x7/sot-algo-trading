import calendar
from datetime import datetime, timedelta


class MyCalendar:
    def current_weekly_exipry_date():
        if datetime.today().weekday() == 3:
            return datetime.now().strftime("%d")
        
        today = datetime.now()
        next_thursday = today + timedelta((3 - today.weekday()) % 7)
        return next_thursday.date().strftime("%d")
    

    def is_last_week_of_month(date):
        last_day = (date.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return date.day > last_day.day - 7
        # today = datetime.now().date()
        # print(today)
        # print(is_last_week_of_month(today))

    
    def get_last_day_of_current_month():
        now = datetime.now()
        _, last_day = calendar.monthrange(now.year, now.month)
        return last_day


    def is_future_date(date_string):
        given_date = datetime.strptime(date_string, '%Y-%m-%d %H:%M:%S')
        current_date = datetime.now()
        return True if given_date >= current_date else False
        