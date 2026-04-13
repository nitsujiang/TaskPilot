import json
from datetime import datetime

class MetricsCollector:
    def __init__(self):
        self.logs = []
    
    def log_event(self,event_type,info):
        """
        Record an event payload.
        """
        pass 

    def save_to_file(self):
        """
        Write logs to a JSON file.
        """


        pass