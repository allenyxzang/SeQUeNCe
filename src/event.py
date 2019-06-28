import math

class Event:
    def __init__(self, time, process, priority=math.inf):
        self.time = time
        self.priority = priority
        self.process = process

    def __eq__(self, another):
        return (self.time == another.time) and (self.priority == another.priority)

    def __ne__(self, another):
        return (self.time != another.time) or (self.priority !=another.priority)

    def __gt__(self, another):
        return (self.time > another.time) or (self.time == another.time and self.priority > another.priority)

    def __lt__(self, another):
        return (self.time < another.time)  or (self.time == another.time and self.priority < another.priority)
